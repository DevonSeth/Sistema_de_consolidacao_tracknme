import hashlib
import io
import zipfile

import httpx
import pytest

import launcher
from config import manager


@pytest.fixture(autouse=True)
def _isola_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path


class _RespostaFake:
    def __init__(self, status_code, corpo):
        self.status_code = status_code
        self._corpo = corpo

    def json(self):
        return self._corpo


class _RespostaStreamFake:
    """Fake de `httpx.stream(...)`, usado como context manager."""

    def __init__(self, status_code, conteudo: bytes, content_length: int | None = None):
        self.status_code = status_code
        self._conteudo = conteudo
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, chunk_size):
        for i in range(0, len(self._conteudo), chunk_size):
            yield self._conteudo[i : i + chunk_size]


def _zip_de_teste(versao: str) -> bytes:
    """Zip com o executável fake direto na raiz (convenção do passo 1.6)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(launcher.NOME_EXECUTAVEL_PAINEL, f"exe fake da versao {versao}")
    return buffer.getvalue()


def _info_versao(versao: str, conteudo_zip: bytes) -> dict:
    return {
        "versao": versao,
        "url_download": "https://exemplo/nao-usado.zip",
        "sha256": hashlib.sha256(conteudo_zip).hexdigest(),
    }


# --- Diretórios de versão -----------------------------------------------------


def test_executavel_versao_resolve_dentro_de_localappdata(tmp_path):
    assert launcher._executavel_versao("2.0.0") == (
        tmp_path / manager.NOME_PASTA_DADOS_LOCAL / "versoes" / "2.0.0" / "PainelOperador.exe"
    )


# --- garantir_versao_local: fast path ------------------------------------------


def test_garantir_versao_local_fast_path_nao_baixa_nem_cria_janela(monkeypatch, tmp_path):
    exe_existente = launcher._executavel_versao("1.0.0")
    exe_existente.parent.mkdir(parents=True)
    exe_existente.write_text("ja instalado")

    def _stream_nao_deveria_ser_chamado(*a, **k):
        raise AssertionError("não deveria baixar nada no fast path")

    monkeypatch.setattr(httpx, "stream", _stream_nao_deveria_ser_chamado)

    def _janela_nao_deveria_ser_criada():
        raise AssertionError("não deveria criar janela no fast path")

    resultado = launcher.garantir_versao_local(
        "http://localhost:3000",
        _info_versao("1.0.0", b"zip nunca usado"),
        fabrica_janela=_janela_nao_deveria_ser_criada,
    )

    assert resultado == exe_existente


# --- garantir_versao_local: download feliz -------------------------------------


def test_garantir_versao_local_baixa_extrai_e_confere_sha256(monkeypatch):
    conteudo_zip = _zip_de_teste("2.0.0")
    info = _info_versao("2.0.0", conteudo_zip)

    def _stream_fake(method, url, timeout, follow_redirects):
        assert method == "GET"
        assert url == "http://localhost:3000/api/operador/download/2.0.0"
        return _RespostaStreamFake(200, conteudo_zip, content_length=len(conteudo_zip))

    monkeypatch.setattr(httpx, "stream", _stream_fake)

    resultado = launcher.garantir_versao_local("http://localhost:3000", info, fabrica_janela=None)

    assert resultado == launcher._executavel_versao("2.0.0")
    assert resultado.exists()
    assert resultado.read_text() == "exe fake da versao 2.0.0"


def test_garantir_versao_local_chama_callback_de_progresso_via_janela(monkeypatch):
    conteudo_zip = _zip_de_teste("2.0.1")
    info = _info_versao("2.0.1", conteudo_zip)
    monkeypatch.setattr(
        httpx, "stream",
        lambda *a, **k: _RespostaStreamFake(200, conteudo_zip, content_length=len(conteudo_zip)),
    )

    chamadas = []

    class _JanelaFake:
        def atualizar_progresso(self, baixado, total):
            chamadas.append((baixado, total))

        def fechar(self):
            chamadas.append("fechada")

    launcher.garantir_versao_local("http://localhost:3000", info, fabrica_janela=_JanelaFake)

    assert chamadas[-1] == "fechada"
    assert len(chamadas) > 1


# --- garantir_versao_local: falhas ---------------------------------------------


def test_garantir_versao_local_sha256_nao_bate_levanta_erro_e_nao_deixa_lixo(monkeypatch, tmp_path):
    conteudo_zip = _zip_de_teste("3.0.0")
    info = _info_versao("3.0.0", conteudo_zip)
    info["sha256"] = "0" * 64  # propositalmente errado

    monkeypatch.setattr(
        httpx, "stream",
        lambda *a, **k: _RespostaStreamFake(200, conteudo_zip, content_length=len(conteudo_zip)),
    )

    with pytest.raises(launcher.ErroIntegridade):
        launcher.garantir_versao_local("http://localhost:3000", info, fabrica_janela=None)

    assert not launcher._executavel_versao("3.0.0").exists()
    assert not launcher._diretorio_versao("3.0.0").exists()


def test_garantir_versao_local_download_falha_http_levanta_runtime_error(monkeypatch):
    info = _info_versao("4.0.0", b"nao importa")
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _RespostaStreamFake(404, b""))

    with pytest.raises(RuntimeError):
        launcher.garantir_versao_local("http://localhost:3000", info, fabrica_janela=None)


# --- buscar_versao_atual --------------------------------------------------------


def test_buscar_versao_atual_ok(monkeypatch):
    corpo = {"versao": "5.0.0", "url_download": "x", "sha256": "y"}
    monkeypatch.setattr(httpx, "get", lambda url, timeout: _RespostaFake(200, corpo))

    assert launcher.buscar_versao_atual("http://localhost:3000") == corpo


def test_buscar_versao_atual_http_erro_levanta_runtime_error(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, timeout: _RespostaFake(500, {}))

    with pytest.raises(RuntimeError):
        launcher.buscar_versao_atual("http://localhost:3000")


# --- ação da URI -----------------------------------------------------------------


@pytest.mark.parametrize(
    "uri, esperado",
    [
        (None, "abrir"),
        ("tracknme-operador://abrir", "abrir"),
        ("tracknme-operador://outra-coisa", "outra-coisa"),
    ],
)
def test_acao_da_uri(uri, esperado):
    assert launcher._acao_da_uri(uri) == esperado


# --- main: orquestração ----------------------------------------------------------


def test_main_uri_desconhecida_mostra_erro_e_nao_busca_versao(monkeypatch):
    monkeypatch.setattr(launcher, "registrar_protocolo_se_necessario", lambda: None)
    erros = []
    monkeypatch.setattr(launcher, "_mostrar_erro", lambda msg: erros.append(msg))

    def _nao_deveria_chamar(base_url):
        raise AssertionError("não deveria consultar versão com ação desconhecida")

    monkeypatch.setattr(launcher, "buscar_versao_atual", _nao_deveria_chamar)

    launcher.main(["tracknme-operador://outra-coisa"])

    assert len(erros) == 1
    assert "outra-coisa" in erros[0]


def test_main_fluxo_completo_feliz(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        launcher, "registrar_protocolo_se_necessario", lambda: chamadas.append("registrar")
    )
    monkeypatch.setattr(
        launcher, "buscar_versao_atual", lambda base_url: chamadas.append("buscar_versao") or {"versao": "6.0.0"}
    )
    monkeypatch.setattr(
        launcher, "garantir_versao_local",
        lambda base_url, info, fabrica_janela=None: chamadas.append("garantir_versao") or "caminho/exe",
    )
    monkeypatch.setattr(launcher, "abrir_painel", lambda exe: chamadas.append(("abrir", exe)))

    launcher.main([])

    assert chamadas == ["registrar", "buscar_versao", "garantir_versao", ("abrir", "caminho/exe")]


def test_main_abrir_painel_falha_mostra_erro_em_vez_de_propagar(monkeypatch):
    """Achado ao vivo (passo 1.5): um `PainelOperador.exe` inválido/corrompido
    faz `subprocess.Popen` levantar `OSError` (ex: WinError 216) — isso não
    pode virar uma stack trace sem tratamento pro usuário final."""
    monkeypatch.setattr(launcher, "registrar_protocolo_se_necessario", lambda: None)
    monkeypatch.setattr(launcher, "buscar_versao_atual", lambda base_url: {"versao": "8.0.0"})
    monkeypatch.setattr(
        launcher, "garantir_versao_local", lambda base_url, info, fabrica_janela=None: "caminho/exe"
    )

    def _abrir_falha(exe):
        raise OSError("WinError 216: binario invalido")

    monkeypatch.setattr(launcher, "abrir_painel", _abrir_falha)
    erros = []
    monkeypatch.setattr(launcher, "_mostrar_erro", lambda msg: erros.append(msg))

    launcher.main([])  # não deve propagar o OSError

    assert len(erros) == 1
    assert "WinError 216" in erros[0]


def test_main_erro_de_integridade_mostra_erro_e_nao_abre(monkeypatch):
    monkeypatch.setattr(launcher, "registrar_protocolo_se_necessario", lambda: None)
    monkeypatch.setattr(launcher, "buscar_versao_atual", lambda base_url: {"versao": "7.0.0"})

    def _garantir_falha(base_url, info, fabrica_janela=None):
        raise launcher.ErroIntegridade("sha256 não bate")

    monkeypatch.setattr(launcher, "garantir_versao_local", _garantir_falha)
    abriu = []
    monkeypatch.setattr(launcher, "abrir_painel", lambda exe: abriu.append(exe))
    erros = []
    monkeypatch.setattr(launcher, "_mostrar_erro", lambda msg: erros.append(msg))

    launcher.main([])

    assert not abriu
    assert erros == ["sha256 não bate"]


# --- Registro de protocolo -------------------------------------------------------


class _FakeWinreg:
    """Fake mínimo de `winreg` — chaves guardadas num dict em memória,
    mapeando caminho completo -> {nome_valor: valor}."""

    HKEY_CURRENT_USER = "HKEY_CURRENT_USER"
    REG_SZ = "REG_SZ"

    def __init__(self):
        self.registro = {}
        self.chaves_criadas = []

    def _chave_ctx(self, caminho):
        registro = self.registro
        fake = self

        class _Ctx:
            def __enter__(self_ctx):
                return caminho

            def __exit__(self_ctx, *exc):
                return False

        return _Ctx()

    def OpenKey(self, hive, caminho):
        if caminho not in self.registro:
            raise FileNotFoundError(caminho)
        return self._chave_ctx(caminho)

    def QueryValueEx(self, chave, nome):
        valores = self.registro.get(chave, {})
        if nome not in valores:
            raise FileNotFoundError(nome)
        return valores[nome], self.REG_SZ

    def CreateKey(self, hive, caminho):
        self.chaves_criadas.append(caminho)
        self.registro.setdefault(caminho, {})
        return self._chave_ctx(caminho)

    def SetValueEx(self, chave, nome, reservado, tipo, valor):
        self.registro.setdefault(chave, {})[nome] = valor


def test_registrar_protocolo_nao_escreve_se_ja_correto(monkeypatch):
    fake = _FakeWinreg()
    comando = launcher._comando_esperado()
    fake.registro[launcher.CHAVE_REGISTRO_PROTOCOLO + r"\shell\open\command"] = {None: comando}
    monkeypatch.setattr(launcher, "winreg", fake)

    launcher.registrar_protocolo_se_necessario()

    assert fake.chaves_criadas == []


def test_registrar_protocolo_escreve_quando_ausente(monkeypatch):
    fake = _FakeWinreg()
    monkeypatch.setattr(launcher, "winreg", fake)

    launcher.registrar_protocolo_se_necessario()

    caminho_comando = launcher.CHAVE_REGISTRO_PROTOCOLO + r"\shell\open\command"
    assert fake.registro[launcher.CHAVE_REGISTRO_PROTOCOLO]["URL Protocol"] == ""
    assert fake.registro[caminho_comando][None] == launcher._comando_esperado()


def test_registrar_protocolo_erro_nao_propaga(monkeypatch):
    class _FakeWinregQuebrado(_FakeWinreg):
        def CreateKey(self, hive, caminho):
            raise OSError("sem permissão")

    monkeypatch.setattr(launcher, "winreg", _FakeWinregQuebrado())

    launcher.registrar_protocolo_se_necessario()  # não deve levantar
