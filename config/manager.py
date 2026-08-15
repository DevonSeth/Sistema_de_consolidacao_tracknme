"""
Camada única de leitura/escrita de configuração local.

Responsabilidades:

1. Carregar a configuração ativa da máquina (arquivo local não-sensível +
   segredos via `keyring` do SO).
2. Na primeira execução em uma máquina nova (config local inexistente),
   tentar carregar o "seed" (arquivo criptografado embutido no instalador) e
   gravar os valores no keyring/arquivo local — depois disso, o seed não é
   mais consultado. `seed.py` ainda não está implementado (aguardando as
   credenciais reais de produção), então esse passo hoje é um no-op seguro:
   captura `NotImplementedError` e segue com config vazia.
3. Nunca expor segredo em texto puro em log, exceção ou print.
4. Fornecer uma função só de leitura por integração, para que o resto do
   sistema nunca precise saber COMO o dado é armazenado.

Onde cada campo mora:
    Keyring do SO (segredo, nunca em disco em texto puro):
        tracknme.senha, newmo.token, supabase.service_role_key,
        gmail.refresh_token
    Arquivo local `config/config.json` (não-sensível):
        tracknme.usuario, newmo.canal_guid, newmo.setor_id,
        newmo.templates (dict), supabase.url,
        google_sheets.credenciais_path (só o caminho, o .json da service
        account em si fica fora do keyring e fora deste arquivo),
        google_sheets.planilha_administrador_id,
        google_sheets.planilha_operacional_id,
        gmail.email_remetente

    parametros.* -> NÃO fica aqui. Parâmetros de negócio (limiar de horas,
                     horário de corte, destinatários) vivem em
                     `system_parameters` no Supabase, não na config local.

SGA não tem credencial nenhuma aqui: login é sempre manual (recaptcha).

O arquivo `config/config.json` e o `config/seed.enc` ficam dentro da pasta
`config/` do próprio projeto/instalação (já cobertos pelo `.gitignore`:
`config/*.enc` e `*.json`), resolvidos ao lado do executável quando
empacotado via PyInstaller.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import keyring

from config import seed as _seed

SERVICO_KEYRING = "sistema_pendencias_puma"

NOME_ARQUIVO_CONFIG = "config.json"
NOME_ARQUIVO_SEED = "seed.enc"


@dataclass
class ConfigTrackNMe:
    usuario: str
    senha: str


@dataclass
class ConfigNewmo:
    token: str
    canal_guid: str
    setor_id: int
    templates: dict


@dataclass
class ConfigSupabase:
    url: str
    service_role_key: str


@dataclass
class ConfigGoogleSheets:
    credenciais_path: str
    planilha_administrador_id: str
    planilha_operacional_id: str


@dataclass
class ConfigGmail:
    refresh_token: str
    email_remetente: str


@dataclass
class ConfigProvisionamento:
    base_url: str
    chave_maquina: str


# Campos obrigatórios por seção (usado para validar `salvar_config`).
CAMPOS_OBRIGATORIOS = {
    "tracknme": ["usuario", "senha"],
    "newmo": ["token", "canal_guid", "setor_id", "templates"],
    "supabase": ["url", "service_role_key"],
    "google_sheets": [
        "credenciais_path",
        "planilha_administrador_id",
        "planilha_operacional_id",
    ],
    "gmail": ["refresh_token", "email_remetente"],
    # "versoes_conhecidas" (dict, não-secreto) também mora aqui — não é
    # obrigatório porque uma máquina pode ter sido provisionada antes de
    # qualquer rotação existir; ver integrations/provisionamento_client.py.
    "provisionamento": ["base_url", "chave_maquina"],
}

# Campos que NUNCA vão para o arquivo local em texto puro — só keyring.
CAMPOS_SECRETOS = {
    "tracknme": ["senha"],
    "newmo": ["token"],
    "supabase": ["service_role_key"],
    "gmail": ["refresh_token"],
    "provisionamento": ["chave_maquina"],
}

SECOES_CONHECIDAS = list(CAMPOS_OBRIGATORIOS.keys())


def _diretorio_config() -> Path:
    """Pasta `config/` a usar, tanto rodando a partir do código-fonte quanto
    a partir do executável empacotado.

    Dev:         <raiz-do-projeto>/config/
    Empacotado:  <pasta-do-.exe>/config/  (nunca a pasta temporária do
                 PyInstaller, que é apagada a cada execução)
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "config"


def _arquivo_config() -> Path:
    return _diretorio_config() / NOME_ARQUIVO_CONFIG


def _ler_arquivo_local() -> dict:
    caminho = _arquivo_config()
    if not caminho.exists():
        return {}
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)


def _gravar_arquivo_local(dados: dict) -> None:
    caminho = _arquivo_config()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2, sort_keys=True)


def _chave_keyring(secao: str, campo: str) -> str:
    return f"{secao}.{campo}"


def _ler_segredo(secao: str, campo: str) -> str | None:
    return keyring.get_password(SERVICO_KEYRING, _chave_keyring(secao, campo))


def _gravar_segredo(secao: str, campo: str, valor: str) -> None:
    keyring.set_password(SERVICO_KEYRING, _chave_keyring(secao, campo), str(valor))


def _tentar_bootstrap_seed(dados_locais: dict) -> dict:
    """Primeira execução nesta máquina: tenta carregar o seed embutido no
    instalador. Enquanto `seed.py` não estiver implementado (ver docstring
    do módulo), isso é um no-op seguro — nunca derruba a aplicação.
    """
    caminho_seed = _diretorio_config() / NOME_ARQUIVO_SEED
    if not caminho_seed.exists():
        return dados_locais
    try:
        valores_por_secao = _seed.carregar_seed(str(caminho_seed))
    except NotImplementedError:
        return dados_locais
    for secao, valores in valores_por_secao.items():
        salvar_config(secao, valores)
    return _ler_arquivo_local()


def carregar_config() -> dict:
    """Carrega toda a configuração ativa (arquivo local + keyring), com
    fallback de seed na primeira execução (arquivo local ainda inexistente).
    """
    primeira_execucao = not _arquivo_config().exists()
    dados_locais = _ler_arquivo_local()

    if primeira_execucao:
        dados_locais = _tentar_bootstrap_seed(dados_locais)
        _gravar_arquivo_local(dados_locais)

    config = {secao: dict(dados_locais.get(secao, {})) for secao in SECOES_CONHECIDAS}
    for secao, campos_secretos in CAMPOS_SECRETOS.items():
        for campo in campos_secretos:
            config[secao][campo] = _ler_segredo(secao, campo)
    return config


def salvar_config(secao: str, valores: dict) -> None:
    """Grava valores de uma seção (ex: 'tracknme', 'newmo') — segredos no
    keyring, o restante no arquivo local. Nunca loga `valores`.
    """
    obrigatorios = CAMPOS_OBRIGATORIOS.get(secao)
    if obrigatorios is None:
        raise ValueError(f"Seção de configuração desconhecida: {secao!r}")

    faltando = [campo for campo in obrigatorios if campo not in valores]
    if faltando:
        raise ValueError(f"Campos obrigatórios faltando em '{secao}': {faltando}")

    secretos = CAMPOS_SECRETOS.get(secao, [])
    dados_locais = _ler_arquivo_local()
    secao_local = dict(dados_locais.get(secao, {}))
    for campo, valor in valores.items():
        if campo in secretos:
            _gravar_segredo(secao, campo, valor)
        else:
            secao_local[campo] = valor

    dados_locais[secao] = secao_local
    _gravar_arquivo_local(dados_locais)


def testar_conexao(secao: str) -> bool:
    """Dispara um teste de conectividade rápido para a integração indicada.
    Usado pelo botão "Testar conexão" na tela de configuração.

    Design por seção (decisão 2026-08-06 — sempre a operação mais barata
    e só-leitura que já prova que a credencial funciona, nunca uma ação
    de negócio):

        'supabase'      -> select trivial (ex: `system_parameters` limit 1)
        'google_sheets' -> abrir as duas planilhas por ID (`gc.open_by_key`),
                            sem ler aba nenhuma
        'tracknme'      -> tentar o login automático já existente em
                            `integrations.tracknme_bot`, com timeout curto
        'newmo'         -> `GET /canal` (mesma chamada que já é feita uma
                            vez pra descobrir os IDs de config)
        'sga'           -> **não aplicável** — login é sempre manual
                            (reCAPTCHA), não tem credencial pra testar aqui;
                            retornar/expor como "sempre manual", não tentar
                            nada
        'gmail'         -> bloqueado (refresh_token ainda não existe) —
                            N/A até essa integração sair do bloqueio

    TODO: implementar um teste por integração em `integrations/`, quando
    cada cliente estiver pronto.
    """
    if secao == "sga":
        raise ValueError("SGA é sempre manual (reCAPTCHA) — não há conexão automática para testar")
    if secao == "gmail":
        raise ValueError("Gmail foi descartado do projeto — sem teste de conexão")
    if secao == "supabase":
        from integrations import supabase_client

        supabase_client.get_client.cache_clear()
        supabase_client.get_client().table("system_parameters").select("chave").limit(1).execute()
        return True
    if secao == "google_sheets":
        from integrations import google_sheets_client

        google_sheets_client._cliente.cache_clear()
        cfg = carregar_config()["google_sheets"]
        cliente = google_sheets_client._cliente()
        cliente.open_by_key(cfg["planilha_administrador_id"])
        cliente.open_by_key(cfg["planilha_operacional_id"])
        return True
    if secao == "newmo":
        from integrations import newmo_client

        newmo_client._client.cache_clear()
        newmo_client.listar_canais()
        return True
    if secao == "tracknme":
        import asyncio

        from integrations import tracknme_bot

        return asyncio.run(tracknme_bot.testar_login())
    raise ValueError(f"Seção de configuração desconhecida: {secao!r}")
