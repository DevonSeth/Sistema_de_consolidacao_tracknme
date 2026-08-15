"""
Cliente do provisionamento de máquina nova (Fase 0, passos 0.9/0.10) —
fala com `POST /api/operador/provisionar` e `GET /api/operador/
credenciais/versao` (implementados em `webapp/src/app/api/operador/`),
nunca com o Vault/Supabase diretamente (o Painel Operador não tem
credencial de service_role nenhuma).

Duas entradas:
    provisionar_maquina(base_url, token) — 1ª execução numa máquina nova
        (`main.py --provisionar <token> --base-url <url>`). Grava tudo
        via `config.manager.salvar_config` (mesma função que a tela de
        Configuração usa há muito tempo — nenhuma lógica de
        keyring/arquivo local nova).
    verificar_e_sincronizar() — chamada na abertura normal do app;
        no-op silencioso se a máquina nunca foi provisionada por este
        fluxo (`provisionamento.chave_maquina` vazia — inclui a máquina
        de desenvolvimento), e nunca derruba a aplicação em erro de rede
        (mesmo espírito soft-fail do watchdog).

`google_sheets.credenciais_path` do Vault é o caminho da máquina que
migrou primeiro — nunca reaproveitado como veio. Este módulo sempre
escreve `google_sheets_arquivo_credenciais` (conteúdo cru do .json da
service account) num arquivo LOCAL novo e aponta `credenciais_path` pra
ele antes de chamar `salvar_config`.
"""

import httpx

from config import manager

TIMEOUT_SEGUNDOS = 15
NOME_ARQUIVO_GOOGLE_SHEETS_CREDENCIAIS = "google_sheets_credenciais.json"

_SECOES_SIMPLES = ("tracknme", "newmo", "supabase")


def _escrever_arquivo_google_sheets(conteudo: str) -> str:
    caminho = manager._diretorio_config() / NOME_ARQUIVO_GOOGLE_SHEETS_CREDENCIAIS
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(conteudo, encoding="utf-8")
    return str(caminho)


def _aplicar_credenciais(credenciais: dict, secoes: set[str]) -> None:
    """Grava localmente só as seções pedidas (via `salvar_config`, nunca
    escrevendo keyring/arquivo diretamente). `"google_sheets"` sempre
    reescreve o arquivo local do service account antes, porque
    `credenciais_path` depende dele existir."""
    for secao in _SECOES_SIMPLES:
        if secao in secoes:
            manager.salvar_config(secao, credenciais[secao])
    if "google_sheets" in secoes:
        caminho = _escrever_arquivo_google_sheets(credenciais["google_sheets_arquivo_credenciais"])
        manager.salvar_config(
            "google_sheets", {**credenciais["google_sheets"], "credenciais_path": caminho}
        )


def _erro_da_resposta(resposta: httpx.Response) -> str:
    try:
        return resposta.json().get("erro", f"HTTP {resposta.status_code}")
    except ValueError:
        return f"HTTP {resposta.status_code}"


def provisionar_maquina(base_url: str, token: str) -> None:
    """Provisiona esta máquina com um token de uso único gerado pelo
    Painel Admin. Levanta `RuntimeError` em qualquer falha — chamado só
    manualmente (`main.py --provisionar`), não deve falhar em silêncio.
    """
    resposta = httpx.post(
        f"{base_url}/api/operador/provisionar", json={"token": token}, timeout=TIMEOUT_SEGUNDOS
    )
    if resposta.status_code != 200:
        raise RuntimeError(f"Falha ao provisionar: {_erro_da_resposta(resposta)}")

    corpo = resposta.json()
    credenciais = corpo["credenciais"]
    _aplicar_credenciais(credenciais, {*_SECOES_SIMPLES, "google_sheets"})
    manager.salvar_config(
        "provisionamento",
        {
            "base_url": base_url,
            "chave_maquina": corpo["chave_maquina"],
            "versoes_conhecidas": {},
        },
    )


def verificar_e_sincronizar() -> None:
    """Chamada na abertura normal do app. Nunca derruba a aplicação —
    qualquer falha (máquina não provisionada, rede fora, servidor
    fora) só imprime um aviso e retorna."""
    config = manager.carregar_config()
    prov = config.get("provisionamento") or {}
    chave_maquina = prov.get("chave_maquina")
    base_url = prov.get("base_url")
    if not chave_maquina or not base_url:
        return

    try:
        resposta = httpx.get(
            f"{base_url}/api/operador/credenciais/versao",
            headers={"Authorization": f"Bearer {chave_maquina}"},
            timeout=TIMEOUT_SEGUNDOS,
        )
        if resposta.status_code != 200:
            print(f"[provisionamento] checagem de versão falhou: {_erro_da_resposta(resposta)}")
            return
        corpo = resposta.json()
    except httpx.HTTPError as e:
        print(f"[provisionamento] checagem de versão falhou (rede): {e}")
        return

    versoes_novas = corpo["versoes"]
    versoes_conhecidas = prov.get("versoes_conhecidas") or {}
    diferentes = {s for s, v in versoes_novas.items() if versoes_conhecidas.get(s) != v}
    if not diferentes:
        return

    secoes_para_aplicar = diferentes & set(_SECOES_SIMPLES)
    if diferentes & {"google_sheets", "google_sheets_arquivo_credenciais"}:
        secoes_para_aplicar.add("google_sheets")

    _aplicar_credenciais(corpo["credenciais"], secoes_para_aplicar)
    manager.salvar_config(
        "provisionamento",
        {"base_url": base_url, "chave_maquina": chave_maquina, "versoes_conhecidas": versoes_novas},
    )
    print(f"[provisionamento] credenciais sincronizadas: {sorted(secoes_para_aplicar)}")
