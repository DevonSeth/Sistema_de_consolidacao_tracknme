"""
Launcher/auto-update do Painel Operador (Fase 1, passo 1.4).

O único `.exe` que o usuário final abre no dia a dia. Fluxo:
1. Registra (idempotente) o protocolo `tracknme-operador://` no Windows,
   pra o link "Abrir Painel Operador" do Painel Admin funcionar
   (`webapp/src/app/admin/layout.tsx`).
2. Consulta `GET /api/operador/versao-atual` pra saber a versão mais
   nova publicada.
3. Se essa versão já está baixada localmente (`versoes/<versao>/`
   dentro da pasta fixa por máquina, ver `config.manager.
   _diretorio_dados_local`), só executa o `PainelOperador.exe` de lá.
4. Senão, baixa o pacote via `GET /api/operador/download/{versao}`
   (redirect assinado — o repositório GitHub é privado, o Launcher
   nunca carrega token nenhum, ver docs/deploy_vercel.md seção 1.4),
   confere o SHA256 publicado, extrai e executa.

Cada versão vive na sua própria pasta — nunca sobrescreve um `.exe` em
execução (decisão já fechada, frágil no Windows).
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import winreg
import zipfile
from pathlib import Path

import httpx

from config import manager

BASE_URL_PRODUCAO = "https://sistema-de-consolidacao-tracknme.vercel.app"

NOME_PASTA_VERSOES = "versoes"
NOME_EXECUTAVEL_PAINEL = "PainelOperador.exe"

CHAVE_REGISTRO_PROTOCOLO = r"Software\Classes\tracknme-operador"

TIMEOUT_VERSAO_ATUAL = 15
TIMEOUT_DOWNLOAD = 300  # o pacote pode ter centenas de MB (Chromium embutido)


class ErroIntegridade(Exception):
    """SHA256 do pacote baixado não bate com o valor publicado."""


# --- Registro do protocolo tracknme-operador:// ---------------------------


def _comando_esperado() -> str:
    exe = str(Path(sys.executable).resolve())
    return f'"{exe}" "%1"'


def _protocolo_ja_registrado_corretamente() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, CHAVE_REGISTRO_PROTOCOLO + r"\shell\open\command"
        ) as chave:
            valor, _ = winreg.QueryValueEx(chave, None)
        return valor == _comando_esperado()
    except FileNotFoundError:
        return False


def registrar_protocolo_se_necessario() -> None:
    """`HKEY_CURRENT_USER` — não precisa admin. Não escreve se já está
    correto (idempotente). Soft-fail: erro de registro nunca impede o
    resto do fluxo (mesmo espírito de
    `provisionamento_client.verificar_e_sincronizar`)."""
    if _protocolo_ja_registrado_corretamente():
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CHAVE_REGISTRO_PROTOCOLO) as chave:
            winreg.SetValueEx(chave, None, 0, winreg.REG_SZ, "URL:TrackNMe Operador Protocol")
            winreg.SetValueEx(chave, "URL Protocol", 0, winreg.REG_SZ, "")
        caminho_comando = CHAVE_REGISTRO_PROTOCOLO + r"\shell\open\command"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, caminho_comando) as chave:
            winreg.SetValueEx(chave, None, 0, winreg.REG_SZ, _comando_esperado())
    except OSError as e:
        print(f"[launcher] falha ao registrar protocolo (não bloqueia): {e}")


def _acao_da_uri(uri: str | None) -> str:
    """`None` (execução manual, sem protocolo) -> "abrir". Uma URI do tipo
    `tracknme-operador://abrir` -> "abrir". Só existe essa ação hoje;
    qualquer outra é reportada como erro em `main`, não tratada aqui."""
    if uri is None:
        return "abrir"
    return uri.split("://", 1)[-1].strip("/") or "abrir"


# --- Diretórios de versão ----------------------------------------------------


def _diretorio_versoes() -> Path:
    return manager._diretorio_dados_local() / NOME_PASTA_VERSOES


def _diretorio_versao(versao: str) -> Path:
    return _diretorio_versoes() / versao


def _executavel_versao(versao: str) -> Path:
    return _diretorio_versao(versao) / NOME_EXECUTAVEL_PAINEL


# --- Rede: versão mais nova + download ---------------------------------------


def buscar_versao_atual(base_url: str) -> dict:
    """`GET /api/operador/versao-atual` -> {"versao", "url_download", "sha256"}.
    `url_download` não é usado pelo resto do fluxo (é a URL pública de
    navegador da tabela, sem uso pra API do GitHub) — o download real usa
    a rota de proxy, ver `garantir_versao_local`."""
    resposta = httpx.get(f"{base_url}/api/operador/versao-atual", timeout=TIMEOUT_VERSAO_ATUAL)
    if resposta.status_code != 200:
        raise RuntimeError(f"Falha ao consultar versão mais recente: HTTP {resposta.status_code}")
    return resposta.json()


def _baixar_com_hash(url: str, destino: Path, callback_progresso=None) -> str:
    hash_sha256 = hashlib.sha256()
    with httpx.stream("GET", url, timeout=TIMEOUT_DOWNLOAD, follow_redirects=True) as resposta:
        if resposta.status_code != 200:
            raise RuntimeError(f"Falha ao baixar pacote: HTTP {resposta.status_code}")
        total = int(resposta.headers.get("content-length") or 0) or None
        baixado = 0
        with open(destino, "wb") as f:
            for pedaco in resposta.iter_bytes(chunk_size=256 * 1024):
                f.write(pedaco)
                hash_sha256.update(pedaco)
                baixado += len(pedaco)
                if callback_progresso:
                    callback_progresso(baixado, total)
    return hash_sha256.hexdigest()


def garantir_versao_local(base_url: str, info_versao: dict, fabrica_janela=None) -> Path:
    """Se a versão já existe localmente, retorna direto (fast path —
    `fabrica_janela` nunca é chamada, abertura silenciosa e instantânea).
    Senão, baixa via a rota de proxy, confere SHA256, extrai pra uma
    pasta de staging com nome único por processo e promove atomicamente
    (`os.replace`) — corrige a corrida de 2 Launchers baixando a mesma
    versão nova ao mesmo tempo (o perdedor trata a falha do replace como
    sucesso, já que a versão já está lá)."""
    versao = info_versao["versao"]
    exe_final = _executavel_versao(versao)
    if exe_final.exists():
        return exe_final

    diretorio_versoes = _diretorio_versoes()
    diretorio_versoes.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=diretorio_versoes, prefix=f".staging-{versao}-"))
    janela = fabrica_janela() if fabrica_janela else None
    try:
        caminho_zip = staging / "pacote.zip"
        url_download = f"{base_url}/api/operador/download/{versao}"
        callback = (lambda b, t: janela.atualizar_progresso(b, t)) if janela else None
        sha_obtido = _baixar_com_hash(url_download, caminho_zip, callback)

        if sha_obtido != info_versao["sha256"]:
            raise ErroIntegridade(
                f"SHA256 não confere para a versão {versao} "
                f"(esperado {info_versao['sha256']}, obtido {sha_obtido}) — pacote descartado."
            )

        pasta_extraida = staging / "extraido"
        with zipfile.ZipFile(caminho_zip) as zf:
            zf.extractall(pasta_extraida)

        try:
            os.replace(pasta_extraida, _diretorio_versao(versao))
        except OSError:
            # Outro processo já promoveu esta versão primeiro — a versão
            # já está no lugar certo, não é uma falha real.
            if not exe_final.exists():
                raise
        return exe_final
    finally:
        if janela:
            janela.fechar()
        shutil.rmtree(staging, ignore_errors=True)


# --- UI mínima (tkinter, só durante download) --------------------------------


class _JanelaProgresso:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk

        self.raiz = tk.Tk()
        self.raiz.title("Painel Operador — atualizando")
        self.raiz.geometry("360x110")
        self.raiz.resizable(False, False)
        self.rotulo = tk.Label(self.raiz, text="Baixando atualização...")
        self.rotulo.pack(pady=(15, 5))
        self.barra = ttk.Progressbar(self.raiz, orient="horizontal", length=300, mode="determinate")
        self.barra.pack(pady=5)
        self.raiz.update()

    def atualizar_progresso(self, baixado: int, total: int | None) -> None:
        mb_baixado = baixado // (1024 * 1024)
        if total:
            self.barra["value"] = 100 * baixado / total
            mb_total = total // (1024 * 1024)
            self.rotulo.config(text=f"Baixando atualização... {mb_baixado}MB / {mb_total}MB")
        else:
            self.rotulo.config(text=f"Baixando atualização... {mb_baixado}MB")
        self.raiz.update()

    def fechar(self) -> None:
        self.raiz.destroy()


def _mostrar_erro(mensagem: str) -> None:
    import tkinter as tk
    from tkinter import messagebox

    raiz = tk.Tk()
    raiz.withdraw()
    messagebox.showerror("Painel Operador — erro ao abrir", mensagem)
    raiz.destroy()


# --- Execução -----------------------------------------------------------------


def abrir_painel(caminho_exe: Path) -> subprocess.Popen:
    """Não bloqueia — não espera o Painel fechar."""
    return subprocess.Popen([str(caminho_exe)], cwd=str(caminho_exe.parent))


def main(argv=None):
    """`argv=None` (padrão) faz o `argparse` ler o `sys.argv` real do
    processo — passar uma lista explícita evita depender do `sys.argv`
    de quem chamou `main()` programaticamente (ex: testes)."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "uri", nargs="?", default=None,
        help="URI tracknme-operador://<acao> passada pelo Windows via protocolo registrado",
    )
    parser.add_argument(
        "--base-url", default=BASE_URL_PRODUCAO,
        help="Override pra teste local (padrão: produção)",
    )
    args = parser.parse_args(argv)

    registrar_protocolo_se_necessario()

    acao = _acao_da_uri(args.uri)
    if acao != "abrir":
        _mostrar_erro(f"Ação desconhecida: {acao!r}")
        return

    try:
        info = buscar_versao_atual(args.base_url)
    except (httpx.HTTPError, RuntimeError) as e:
        _mostrar_erro(f"Não foi possível checar a versão mais recente do Painel Operador:\n{e}")
        return

    try:
        caminho_exe = garantir_versao_local(args.base_url, info, fabrica_janela=_JanelaProgresso)
    except ErroIntegridade as e:
        _mostrar_erro(str(e))
        return
    except (httpx.HTTPError, RuntimeError) as e:
        _mostrar_erro(f"Falha ao baixar a atualização do Painel Operador:\n{e}")
        return

    try:
        abrir_painel(caminho_exe)
    except OSError as e:
        _mostrar_erro(f"Não foi possível abrir o Painel Operador:\n{e}")


if __name__ == "__main__":
    main()
