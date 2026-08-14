"""
Cliente da API Newmo/Zapio (https://model.newmo.com.br/api/v2, produção).

Referência confirmada em 2026-08-10 (Passo 1 do plano de F.1) contra o
Swagger real do fornecedor, salvo em
`referencia_legado/API Newmo (03_08_2026 13：27：24).html` (OAS 3.0, "API
Newmo 2.0.0") — não é suposição, é o schema documentado pelo próprio
Newmo. Auth: Bearer Token (header `Authorization`), conforme já registrado
em `README.md`; o Swagger não expõe o nome exato do scheme no HTML
estático (botão "Authorize" da UI não renderiza o modal sem JS ao vivo).

Endpoints relevantes:
    GET  /canal?status={todos|ativo|inativo}
        -> {"erro": false, "canais": [{"id" (GUID str), "nome", "numero",
            "status" (bool)}]}
    GET  /setor?status={todos|ativo|inativo}&canal={guid}
        -> {"erro": false, "setores": [{"id" (int), "nome", "status" (str),
            "canal" (GUID str), "canalNome"}]}
    POST /mensagem/template
        body: {"canal": guid, "destinatario": "55DDNNNNNNNNN" (SEM "+"),
               "abrirAtendimento": bool, "setor": int, "bot": bool,
               "ignorarSePossuiAtendimento": bool,
               "template": {"codigo": int, "variaveis": [str, ...]}}
        -> {"erro": false, "atendimentoId": int, "mensagemId": int,
            "mensagem": str, "resultado": {"cod": int, "mensagem": str}}
        **`template.codigo` é numérico** (confirmado no Swagger) — não é o
        nome do template (`pendencia_instalacao_manutencao_base_normal`
        etc., ver `docs/templates_whatsapp.md` seção 7). Esse número só
        existe depois que a Meta aprovar o template e ele for cadastrado
        no painel do Newmo — ainda não temos os 14 valores.
    POST /mensagem/enviar — mensagem livre (texto/botão/lista), mesmo
        formato de resposta de `/mensagem/template`. Não usado por F.1
        (que só dispara template), documentado aqui só por completude.
    POST /atendimento/finalizar — {"id" int ou "numero" str, "bot": bool}
        -> {"erro": false, "resultado": str}
    POST /chatbot/iniciar — endpoint extra encontrado no Swagger, não
        citado em nenhum doc/decisão anterior do projeto. Não é usado por
        F.1 (dispara um fluxo de chatbot Newmo, não um template avulso) —
        registrado aqui só pra não ficar "descoberto" de novo depois.

    Erro genérico (400): {"erro": true, "descricao": str}. Centralizado em
    `_corpo()` — sempre que a resposta tem `resultado`/`canais`/`setores`,
    `erro` já vem `false` (confirmado no Swagger), então essa checagem
    nunca conflita com a classificação de `resultado.cod` abaixo.

`resultado.cod` — enum completo confirmado (8 valores, igual pra
`/mensagem/enviar` e `/mensagem/template`):
    1 "Mensagem enviada com sucesso"                                  -> sucesso
    2 "Erro no envio da mensagem"                                     -> falha
    3 "Canal não encontrado"                                          -> falha (config errada)
    4 "Canal offline"                                                 -> falha temporária, retry
    5 "Canal não suportado"                                           -> falha (config errada)
    6 "Ocorreu um erro inesperado, tente novamente"                   -> falha temporária, retry
    7 "O número do destinatário informado não está registrado no Whatsapp" -> 'contato_invalido', sem consumir tentativa
    8 "Tipo de mensagem inválido"                                     -> falha (bug de payload)

**Achado de integração (Passo 1)**: `core.normalizacao.
normalizar_telefone_e164` devolve o telefone com prefixo "+"
(`"+5581987654321"`), mas o Swagger do Newmo mostra `destinatario`/
`numero` SEM o "+" (ex: `"5521988887777"`). `enviar_template` remove o "+"
internamente (`_sem_prefixo_mais`) — quem chama passa o `e164` de
`normalizar_telefone_e164` direto, sem se preocupar com o formato.

**Implementado (Passo 4, 2026-08-10)**:
    enviar_template(destinatario, template_codigo, variaveis, canal, setor) -> dict
        Retry imediato (até 3 tentativas, backoff 2/4s — mesma progressão
        de `integrations.playwright_utils`) tanto para falha de transporte
        (`httpx.HTTPError`) quanto para os cods 4/6 (falha temporária da
        própria Newmo). Os demais cods (2, 3, 5, 7, 8) voltam classificados
        num dict, sem retry — decidir o que fazer com cada classificação
        (ex: pausar a esteira em `contato_invalido`) é responsabilidade de
        quem chama (`etapa_disparo_mensagens`, Passo 6, ainda não
        implementado), não deste módulo. Retorno:
            {"sucesso": bool,
             "classificacao": "sucesso"|"falha_temporaria"|
                               "contato_invalido"|"falha_permanente",
             "cod": int, "mensagem": str,
             "atendimento_id": int|None, "mensagem_id": int|None}

    listar_canais() / listar_setores(canal)
        Chamadas pontuais/manuais de descoberta (sem retry) — usadas uma
        vez para obter os GUIDs/IDs reais que vão em `config.manager`
        (`ConfigNewmo.canal_guid`/`setor_id`).

    finalizar_atendimento(atendimento_id) -> str
        POST /atendimento/finalizar, sem retry (chamada pontual, não faz
        parte do fluxo de envio). `bot=False` fixo (mesmo motivo de
        `enviar_template` — não usamos o chatbot da Newmo). Devolve
        `resultado["resultado"]` (string descritiva do desfecho, formato
        diferente do `resultado.cod` de `/mensagem/template`). Validado ao
        vivo via cliente HTTP bruto em 2026-08-10 (ver `_handoff/
        HANDOFF.md`, achado do `cod=13`) — esta função só formaliza a
        mesma chamada. Uso previsto: rotina noturna de `etapa_disparo_
        mensagens` (Passo 6, ainda não implementado), que finaliza 1x por
        dia quem ainda seria elegível pra outra tentativa de WhatsApp
        (critério em `docs/templates_whatsapp.md` seção 9).

    Payload de `enviar_template` fixa `abrirAtendimento=True` (precisa do
    `atendimentoId` para casar a resposta do webhook depois — ver
    `integrations.supabase_client.atualizar_apos_envio`/
    `buscar_por_atendimento_id`), `bot=False` (não usamos o chatbot da
    Newmo) e `ignorarSePossuiAtendimento=True` (decisão do usuário,
    2026-08-10: as 3 tentativas da mesma pendência devem aparecer como um
    atendimento contínuo no painel do Newmo, não 3 separados) — nenhum dos
    3 é parâmetro exposto, são sempre esses valores.

Ainda pendente (dado, não decisão — ver `_handoff/HANDOFF.md`):
    - Credenciais reais (Bearer token) — usuário já tem, vai gravar no
      keyring por conta própria (`config.manager.salvar_config`) fora
      deste chat. Sem validação ao vivo ainda.
    - Os 14 valores de `template.codigo` (inteiros), um por template de
      `docs/templates_whatsapp.md` — só existem depois da aprovação Meta +
      cadastro no painel Newmo.
    - GUID do canal e ID do setor reais (via `listar_canais()`/
      `listar_setores()`, uma vez, depois que o token estiver salvo).
"""

import time
from functools import lru_cache

import httpx

from config import manager

BASE_URL = "https://model.newmo.com.br/api/v2"
TIMEOUT_SEGUNDOS = 15
MAX_TENTATIVAS_TEMPORARIAS = 3

_COD_SUCESSO = 1
_COD_CONTATO_INVALIDO = 7
_CODS_RETRY_TEMPORARIO = {4, 6}


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    """Cliente HTTP, criado uma única vez (singleton). Se a config for
    resalva com token novo em runtime, chamar `_client.cache_clear()`
    antes da próxima chamada — mesmo padrão de
    `integrations.supabase_client.get_client`."""
    token = manager.carregar_config()["newmo"]["token"]
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT_SEGUNDOS,
    )


def _corpo(resposta: httpx.Response) -> dict:
    try:
        corpo = resposta.json()
    except ValueError:
        raise RuntimeError(
            f"Resposta inesperada do Newmo (status {resposta.status_code}): "
            f"{resposta.text[:200]!r}"
        )
    if corpo.get("erro"):
        raise RuntimeError(corpo.get("descricao", "Erro desconhecido do Newmo"))
    return corpo


def _get(path: str, params: dict) -> dict:
    return _corpo(_client().get(path, params=params))


def _post(path: str, payload: dict) -> dict:
    return _corpo(_client().post(path, json=payload))


def _sem_prefixo_mais(telefone: str) -> str:
    return telefone.removeprefix("+")


def listar_canais() -> list[dict]:
    return _get("/canal", {"status": "todos"})["canais"]


def listar_setores(canal: str) -> list[dict]:
    return _get("/setor", {"status": "todos", "canal": canal})["setores"]


def finalizar_atendimento(atendimento_id: int) -> str:
    return _post("/atendimento/finalizar", {"id": atendimento_id, "bot": False})["resultado"]


def _classificar_envio(resposta: dict, resultado: dict) -> dict:
    cod = resultado.get("cod")
    if cod == _COD_SUCESSO:
        classificacao = "sucesso"
    elif cod == _COD_CONTATO_INVALIDO:
        classificacao = "contato_invalido"
    elif cod in _CODS_RETRY_TEMPORARIO:
        classificacao = "falha_temporaria"
    else:
        classificacao = "falha_permanente"
    return {
        "sucesso": cod == _COD_SUCESSO,
        "classificacao": classificacao,
        "cod": cod,
        "mensagem": resultado.get("mensagem"),
        "atendimento_id": resposta.get("atendimentoId"),
        "mensagem_id": resposta.get("mensagemId"),
    }


def enviar_template(
    destinatario: str, template_codigo: int, variaveis: list, canal: str, setor: int
) -> dict:
    payload = {
        "canal": canal,
        "destinatario": _sem_prefixo_mais(destinatario),
        "abrirAtendimento": True,
        "setor": setor,
        "bot": False,
        "ignorarSePossuiAtendimento": True,
        "template": {"codigo": template_codigo, "variaveis": variaveis},
    }

    for tentativa in range(1, MAX_TENTATIVAS_TEMPORARIAS + 1):
        ultima_tentativa = tentativa == MAX_TENTATIVAS_TEMPORARIAS
        try:
            resposta = _post("/mensagem/template", payload)
        except httpx.HTTPError:
            if ultima_tentativa:
                raise
            time.sleep(2**tentativa)
            continue

        resultado = resposta.get("resultado", {})
        if resultado.get("cod") in _CODS_RETRY_TEMPORARIO and not ultima_tentativa:
            time.sleep(2**tentativa)
            continue

        return _classificar_envio(resposta, resultado)
