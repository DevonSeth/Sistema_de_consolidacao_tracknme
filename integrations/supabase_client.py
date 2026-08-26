"""
Cliente de acesso ao Supabase — única fonte de verdade do sistema.

Tabelas já existem em produção (não recriar). Schema real (via introspecção
OpenAPI do PostgREST, confirmado em conversa com o cliente para os campos de
texto livre):

    tratativas
        id (uuid, pk), chave_unica (text, chave de dedup — ver core.dedup),
        origem (text: 'instalacao' | 'remocao' | 'manutencao'),
        identificador (text — placa OU chassi, conforme tipo_identificador),
        chassi (text, sempre disponível como backup),
        placa (text — placa crua do veículo, pode ser fictícia/vazia),
        modelo (text — modelo do veículo, fallback de `placa` na mensagem
            de WhatsApp quando ela é fictícia/ausente, ver core.mensagens),
        tipo_identificador (text: 'placa' | 'chassi' — uso interno do motor,
            não aparece pro atendente),
        cliente, telefone, cidade, bairro, data_referencia,
        sga (text — situação vinda do SGA),
        acao_sugerida, observacao_sistema (preenchidos por rule_templates),
        codigo_regra (text — qual codigo_regra de rule_templates gerou esta
            linha; usado pra derivar nivel_urgencia no relatório via join,
            não é denormalizado aqui),
        selecionado (bool — atendente marca manualmente pra disparo),
        atendimento (text: 'base' | 'rota' | 'acao' — preenchido pelo
            atendente, obrigatório antes do 1º disparo, Fase F.1),
        base_id (uuid, fk -> bases.id, só quando atendimento='base'),
        ponto_acao_id (uuid, fk -> pontos_acao.id, só quando
            atendimento='acao'),
        retorno_associado (text — preenchido pelo webhook, Fase F.2, ainda
            não implementada; grava a discrepância que o associado
            reportou clicando num botão de "Já foi realizado"),
        status_contato (text ou null: null = normal,
            'contato_invalido' = telefone não registrado no WhatsApp,
            cód. 7 da Newmo — NÃO é o mesmo controle que `status`),
        situacao_manual, data_agendada, observacao_manual,
        tecnico (text — atribuído pelo atendente na planilha Operacional,
            dropdown nativo do Google Sheets; persistido aqui pra não se
            perder quando `reescrever_aba` regravar a aba do zero),
        status (text, ciclo de vida completo: 'pendente',
            'aguardando_resposta', 'respondido', 'aguardando_ligacao',
            'encaminhado_puma', 'finalizado', 'bloqueado_sga'),
        tentativas (int, 0-3), tentativa_1/2/3 (timestamptz de cada disparo),
        ultimo_disparo, atendimento_id, mensagem_id (da Newmo, casam com o
            webhook), resposta, data_resposta, created_at, updated_at

    bases (Fase F.1 — WhatsApp)
        id (uuid, pk), nome, endereco, ativo (bool)

    pontos_acao (Fase F.1 — WhatsApp)
        id (uuid, pk), nome_local, endereco, data, ativo (bool)

    ligacoes
        id (uuid, pk), tratativa_id (fk -> tratativas.id), data_contato,
        retornou (bool), conseguiu_agendar (bool), observacao,
        registrado_por, created_at

    puma_encaminhamentos
        id (uuid, pk), tratativa_id (fk -> tratativas.id), motivo,
        data_encaminhamento, status (text: 'aguardando_acao' |
        'em_andamento' | 'concluido'), observacao_puma

    system_parameters
        chave (pk), valor (text puro — precisa coerção de tipo na leitura,
        ex: "48" -> 48, "true" -> True), descricao

    rule_templates
        id (uuid, pk), codigo_regra, ativo (bool), prioridade (int),
        template_acao, template_observacao, nivel_urgencia (int, 1-5;
        NULL para REGRA_4/REGRA_4_TIMESTAMP — dedup silencioso, nunca vira
        linha visível)

    situacao_veiculo_sga (2026-08-06, Instalação/Remoção)
        chassi (text, pk), status (text — último status do SGA visto),
        desde (timestamptz — quando ESSE status começou), atualizado_em
        (timestamptz — última consulta, sempre atualiza). Ver
        core.motor_regras_instalacao_remocao.atualizar_situacao_sga pra
        a lógica pura de quando `desde` reinicia.

Funções previstas — uma por operação de negócio, não um wrapper genérico de
SQL, para manter o resto do código legível.

    log_execucoes (Observabilidade, fatia 1, 2026-08-14)
        id (uuid, pk), execucao_id (uuid — agrupa todas as etapas de uma
        mesma rodada de `orchestrator.catalogo_etapas.executar_etapas_
        com_contexto`, inclusive retomada pós-reconexão), etapa_id (text),
        maquina (text), iniciado_em/finalizado_em (timestamptz),
        duracao_ms (int), sucesso (bool), motivo_parada (text ou null:
        null | 'falha' | 'cancelada' | 'aguardando_reconexao'), mensagem
        (text), created_at.

`log_acoes_automaticas` (auditoria por ação individual) ainda NÃO existe
no banco — fica pra uma fatia 2 da Observabilidade, depois do diagnóstico
de eficiência do SGA/Track N'Me.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from time import sleep

import httpx
from supabase import Client, ClientOptions, create_client

from config import manager
from integrations.retry_utils import ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS, retry_erro_transitorio_windows
from core.constants import (
    ORIGEM_INSTALACAO,
    ORIGEM_MANUTENCAO,
    ORIGEM_REMOCAO,
    PUMA_STATUS_AGUARDANDO_ACAO,
    PUMA_STATUS_CONCLUIDO,
    PUMA_STATUS_EM_ANDAMENTO,
    STATUS_AGUARDANDO_LIGACAO,
    STATUS_AGUARDANDO_RESPOSTA,
    STATUS_BLOQUEADO_SGA,
    STATUS_CONTATO_INVALIDO,
    STATUS_ENCAMINHADO_PUMA,
    STATUS_FINALIZADO,
    STATUS_PENDENTE,
    STATUS_RESPONDIDO,
    TIPO_IDENTIFICADOR_CHASSI,
    TIPO_IDENTIFICADOR_PLACA,
)

# Valores de origem/status vêm de core.constants (core/ não pode importar
# integrations/, mas o inverso é permitido — ver docstring daquele módulo).
# Reexportados aqui para não quebrar quem já importa daqui.
__all__ = [
    "ORIGEM_INSTALACAO",
    "ORIGEM_REMOCAO",
    "ORIGEM_MANUTENCAO",
    "TIPO_IDENTIFICADOR_PLACA",
    "TIPO_IDENTIFICADOR_CHASSI",
    "STATUS_PENDENTE",
    "STATUS_AGUARDANDO_RESPOSTA",
    "STATUS_RESPONDIDO",
    "STATUS_AGUARDANDO_LIGACAO",
    "STATUS_ENCAMINHADO_PUMA",
    "STATUS_FINALIZADO",
    "STATUS_BLOQUEADO_SGA",
    "STATUS_CONTATO_INVALIDO",
    "PUMA_STATUS_AGUARDANDO_ACAO",
    "PUMA_STATUS_EM_ANDAMENTO",
    "PUMA_STATUS_CONCLUIDO",
]

# Estados de `tratativas.status` ainda dentro do ciclo de disparo de WhatsApp
# (usados por buscar_elegiveis_para_disparo — filtragem fina por horário de
# corte e nº de tentativas é feita por core.escalonamento a cada item).
_STATUS_ELEGIVEIS_PARA_DISPARO = [STATUS_PENDENTE, STATUS_AGUARDANDO_RESPOSTA]


def _agora_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@retry_erro_transitorio_windows()
def _registrar_transicao_status(tratativa_id: str, status_novo: str) -> None:
    """Histórico de mudança de status de uma tratativa (`historico_status_
    tratativa`, Passo 1 do Dashboard) — alimenta a reconstrução "como
    estava numa data X" (`dashboard_estado_em`, RPC). Só `status_novo` +
    timestamp: nenhuma das funções que chamam isto lê o status atual antes
    de escrever, e o "anterior" é sempre o `status_novo` da linha
    imediatamente anterior — não precisa gravar 2x."""
    get_client().table("historico_status_tratativa").insert(
        {"tratativa_id": tratativa_id, "status_novo": status_novo}
    ).execute()


@retry_erro_transitorio_windows()
def _registrar_transicao_puma(puma_id: str, tratativa_id: str, status_novo: str) -> None:
    """Espelho de `_registrar_transicao_status` para `puma_encaminhamentos`
    (`historico_status_puma`) — `tratativa_id` denormalizado aqui de
    propósito, evita join extra na hora de reconstruir o estado."""
    get_client().table("historico_status_puma").insert(
        {
            "puma_encaminhamento_id": puma_id,
            "tratativa_id": tratativa_id,
            "status_novo": status_novo,
        }
    ).execute()


def _diretorio_logs() -> Path:
    """Mesma convenção de `orchestrator.catalogo_etapas._diretorio_logs`
    (duplicada de propósito, mesmo padrão já usado por
    `config.manager._diretorio_config`/`orchestrator.pipeline._diretorio_
    downloads`): pasta `logs/` ao lado do código-fonte em dev; quando
    empacotado, em `%LOCALAPPDATA%\\ConsolidacaoTrackNMe\\logs`."""
    if getattr(sys, "frozen", False):
        base = manager._diretorio_dados_local()
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "logs"


def caminho_log_diagnostico_http() -> Path:
    return _diretorio_logs() / "diagnostico_http_supabase.log"


def _registrar_resposta_de_erro(response: httpx.Response) -> None:
    """Event hook de resposta do `httpx.Client` (achado 2026-08-21):
    quando o `postgrest-py` monta `APIError` a partir de uma resposta de
    erro, ele descarta os headers HTTP (`postgrest/exceptions.py::
    generate_default_error_message` só guarda `status_code` e
    `str(r.content)`) -- sem eles, uma rejeição do gateway (Cloudflare,
    identificável pelo header `cf-ray`) e um erro real do Postgres ficam
    indistinguíveis na tela, e cada uma vira uma nova rodada de
    investigação do zero. Este hook dispara em TODA resposta de QUALQUER
    chamada ao Supabase feita por este cliente (presente ou futura, não só
    `tratativas`) e grava o necessário pra diagnóstico definitivo -- nunca
    derruba a chamada real mesmo se o log falhar (arquivo cheio, disco
    sem espaço etc)."""
    if response.status_code < 400:
        return
    try:
        response.read()
        registro = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_code": response.status_code,
            "method": response.request.method,
            "url": str(response.request.url),
            "cf_ray": response.headers.get("cf-ray"),
            "server": response.headers.get("server"),
            "date": response.headers.get("date"),
            "content_type": response.headers.get("content-type"),
            "corpo": response.text[:2000],
        }
        caminho = caminho_log_diagnostico_http()
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - diagnóstico nunca pode derrubar a chamada real
        pass


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Cliente Supabase, criado uma única vez (singleton). Se a config for
    resalva com credenciais novas em runtime, chamar `get_client.cache_clear()`
    antes da próxima chamada.

    `http2=False` explícito (achado 2026-08-21, investigação de causa raiz
    depois de 6 erros de rede diferentes em 24h na mesma etapa): o default
    do `postgrest-py` é HTTP/2, e o pool de conexões do `httpcore` tem um
    bug documentado (PRs abertos #679/#683, ainda sem fix completo em
    2025) onde uma conexão HTTP/2 terminada por GOAWAY do servidor (comum
    atrás de Cloudflare, que recicla conexões após um número fixo de
    requisições) às vezes não é detectada como morta antes de ser
    reusada, surgindo como `httpx.RemoteProtocolError`/`ConnectionTerminated`
    mesmo com retry (o retry reusa o mesmo cliente singleton, então pode
    reusar a mesma conexão quebrada). HTTP/1.1 não tem essa classe de bug
    -- elimina a causa raiz em vez de só ampliar o retry, que continua
    cobrindo drops residuais de conexão (ver `retry_erro_transitorio_
    windows`). Timeout de 30s (achado 2026-08-17, antes via
    `postgrest_client_timeout`) agora fica no `httpx.Client` diretamente,
    porque esse parâmetro é ignorado pelo `postgrest-py` quando um
    `httpx_client` customizado é passado. `event_hooks={"response": [...]}`
    grava diagnóstico completo (headers + corpo) de toda resposta de erro
    em arquivo local — ver `_registrar_resposta_de_erro`.
    """
    cfg = manager.carregar_config()["supabase"]
    cliente_http = httpx.Client(
        http2=False, timeout=30, event_hooks={"response": [_registrar_resposta_de_erro]}
    )
    return create_client(
        cfg["url"], cfg["service_role_key"], options=ClientOptions(httpx_client=cliente_http)
    )


@retry_erro_transitorio_windows()
def buscar_elegiveis_para_disparo() -> list[dict]:
    """Tratativas marcadas pelo atendente (`selecionado=True`) e ainda dentro
    do ciclo de mensagens automáticas (não bloqueadas por SGA, não escaladas
    para ligação, com telefone válido). A elegibilidade fina por item
    (tentativas < 3, horário de corte) é responsabilidade de
    `core.escalonamento.elegivel_para_disparo`, chamada a cada item do loop.
    """
    client = get_client()
    resposta = (
        client.table("tratativas")
        .select("*")
        .eq("selecionado", True)
        .in_("status", _STATUS_ELEGIVEIS_PARA_DISPARO)
        .is_("status_contato", "null")
        .execute()
    )
    return resposta.data


@retry_erro_transitorio_windows()
def buscar_candidatas_escalonamento_ligacao() -> list[dict]:
    """Tratativas ainda no ciclo de mensagens (`status='aguardando_resposta'`)
    — elegibilidade fina (tentativas >= 3, sem `situacao_manual`) é
    responsabilidade de `core.escalonamento.deve_escalar_para_ligacao`,
    chamada a cada item pelo orchestrator (mesmo padrão de
    `buscar_elegiveis_para_disparo`, que também deixa o filtro fino pro
    core).
    """
    client = get_client()
    resposta = (
        client.table("tratativas")
        .select("*")
        .eq("status", STATUS_AGUARDANDO_RESPOSTA)
        .execute()
    )
    return resposta.data


@retry_erro_transitorio_windows()
def buscar_candidatas_finalizacao_atendimento() -> list[dict]:
    """Tratativas com um atendimento Newmo aberto (`atendimento_id IS NOT
    NULL`) e ainda no ciclo de mensagens — candidatas da rotina noturna
    de `orchestrator.pipeline.etapa_finalizar_atendimentos_diarios`. O
    critério fino (tentativas < 3, sem `situacao_manual`/`retorno_
    associado` pendente) é responsabilidade do orchestrator, mesmo padrão
    de `buscar_elegiveis_para_disparo`/`buscar_candidatas_escalonamento_
    ligacao`."""
    client = get_client()
    resposta = (
        client.table("tratativas")
        .select("*")
        .in_("status", _STATUS_ELEGIVEIS_PARA_DISPARO)
        .not_.is_("atendimento_id", "null")
        .execute()
    )
    return resposta.data


_TAMANHO_LOTE_TRATATIVAS = 200


def _em_lotes(lista: list, tamanho: int):
    for inicio in range(0, len(lista), tamanho):
        yield lista[inicio:inicio + tamanho]


def _total_lotes(n: int, tamanho: int) -> int:
    return (n + tamanho - 1) // tamanho if n else 0


@retry_erro_transitorio_windows()
def upsert_tratativas_em_lote(lista_dados: list[dict]) -> None:
    """Insere/atualiza tratativas em lote (upsert por `chave_unica` —
    requer a constraint UNIQUE, ver `_handoff/sql_unique_chave_unica_
    tratativas.sql`) — substitui a antiga `upsert_tratativa` (até 3 idas
    ao Supabase POR linha: 1 select pra decidir insert/update + 1
    insert/update + 1 insert de histórico genesis) pelo mesmo motivo já
    resolvido pro SGA em `upsert_situacoes_veiculo_sga_em_lote` (achado
    2026-08-17): reduz N*3 chamadas de rede síncronas pra poucas chamadas
    totais, independente de N — contribuiu pro `WinError 10035` (achado
    2026-08-20, muitas idas de rede síncronas seguidas na mesma etapa).

    **Dividido em mini-lotes de `_TAMANHO_LOTE_TRATATIVAS` itens** (achado
    2026-08-20, mesmo dia: mandar a fila INTEIRA numa única requisição
    devolveu `{'message': 'JSON could not be generated', 'code': '400',
    'details': "b'Bad Request'"}` — assinatura de um `400` cru do gateway
    antes de chegar no PostgREST, não um erro de negócio; a comunidade
    Supabase recomenda ~500 linhas por chamada de upsert em lote, 200 é
    conservador o suficiente pra nunca chegar perto do limite de tamanho
    de corpo da requisição, mesmo com filas grandes).

    `status` é buscado por linha já existente e incluído explicitamente
    em TODO payload (nunca omitido) — um upsert em lote heterogêneo do
    PostgREST monta a lista de colunas do `ON CONFLICT DO UPDATE` a
    partir da união das chaves presentes em QUALQUER linha do lote; se
    só as linhas novas tivessem `status` no payload, a coluna ainda
    entraria no `UPDATE SET` pras linhas existentes, sobrescrevendo com
    `NULL` quem não a tivesse. Por isso o valor atual é preservado
    explicitamente pras existentes, e só as novas recebem `STATUS_
    PENDENTE`.
    """
    if not lista_dados:
        return
    for dados in lista_dados:
        if "chave_unica" not in dados:
            raise ValueError("dados precisa conter 'chave_unica' para upsert_tratativas_em_lote")

    client = get_client()
    chaves = [dados["chave_unica"] for dados in lista_dados]
    status_existente_por_chave: dict[str, str] = {}
    total_lotes_select = _total_lotes(len(chaves), _TAMANHO_LOTE_TRATATIVAS)
    for indice, lote_chaves in enumerate(_em_lotes(chaves, _TAMANHO_LOTE_TRATATIVAS), start=1):
        try:
            linhas = (
                client.table("tratativas")
                .select("chave_unica, status")
                .in_("chave_unica", lote_chaves)
                .execute()
                .data
            )
        except Exception as e:
            e.add_note(
                f"upsert_tratativas_em_lote: select existentes, lote {indice}/{total_lotes_select} "
                f"(tamanho={len(lote_chaves)}, chaves {lote_chaves[0]}..{lote_chaves[-1]})"
            )
            raise
        for linha in linhas:
            status_existente_por_chave[linha["chave_unica"]] = linha["status"]
        sleep(ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS)

    agora_iso = _agora_utc_iso()
    payloads = []
    for dados in lista_dados:
        payload = dict(dados)
        payload["updated_at"] = agora_iso
        payload["status"] = status_existente_por_chave.get(dados["chave_unica"], STATUS_PENDENTE)
        payloads.append(payload)

    linhas_upsertadas = []
    total_lotes_upsert = _total_lotes(len(payloads), _TAMANHO_LOTE_TRATATIVAS)
    for indice, lote_payloads in enumerate(_em_lotes(payloads, _TAMANHO_LOTE_TRATATIVAS), start=1):
        try:
            resultado = client.table("tratativas").upsert(lote_payloads, on_conflict="chave_unica").execute()
        except Exception as e:
            chaves_lote = [p["chave_unica"] for p in lote_payloads]
            e.add_note(
                f"upsert_tratativas_em_lote: upsert, lote {indice}/{total_lotes_upsert} "
                f"(tamanho={len(lote_payloads)}, chaves {chaves_lote[0]}..{chaves_lote[-1]})"
            )
            raise
        linhas_upsertadas.extend(resultado.data)
        sleep(ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS)

    # Linha "genesis" do histórico só pras tratativas novas — garante que
    # toda tratativa tem ao menos 1 linha, então `dashboard_estado_em`
    # sempre acha resposta pra qualquer data >= criação.
    linhas_novas = [
        linha for linha in linhas_upsertadas if linha["chave_unica"] not in status_existente_por_chave
    ]
    total_lotes_genesis = _total_lotes(len(linhas_novas), _TAMANHO_LOTE_TRATATIVAS)
    for indice, lote_novas in enumerate(_em_lotes(linhas_novas, _TAMANHO_LOTE_TRATATIVAS), start=1):
        try:
            get_client().table("historico_status_tratativa").insert(
                [{"tratativa_id": linha["id"], "status_novo": linha["status"]} for linha in lote_novas]
            ).execute()
        except Exception as e:
            chaves_lote = [linha["chave_unica"] for linha in lote_novas]
            e.add_note(
                f"upsert_tratativas_em_lote: insert genesis, lote {indice}/{total_lotes_genesis} "
                f"(tamanho={len(lote_novas)}, chaves {chaves_lote[0]}..{chaves_lote[-1]})"
            )
            raise
        sleep(ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS)


@retry_erro_transitorio_windows()
def buscar_estado_disparo_por_chaves(chaves: list[str]) -> dict[str, dict]:
    """Estado do ciclo de disparo (Fase F, ainda não implementada) por
    `chave_unica` — usado pela Fase E (`orchestrator.pipeline.
    etapa_publicar_fila_operacional`) pra exibir `status`/`status_contato`/
    `tentativa_1/2/3`/`resposta`/`data_resposta` na aba `Tratativas`
    sempre frescos do Supabase, nunca do snapshot da aba anterior —
    diferente dos campos de atendente (`sincronizar_campos_atendente`),
    esses são escritos por OUTROS processos (disparo, webhook), não pelo
    atendente editando a planilha, então só ficam corretos se lidos de
    volta do Supabase a cada ciclo.

    **Dividido em mini-lotes de `_TAMANHO_LOTE_TRATATIVAS` itens** (achado
    2026-08-21, causa raiz confirmada pelo log de diagnóstico HTTP: com a
    fila real girando ~1.900 itens, esta era a ÚNICA chamada de rede da
    Fase E que nunca tinha ganhado chunking -- o filtro `.in_("chave_
    unica", chaves)` é resolvido via query string (requisição GET), e a
    lista inteira gerava uma URL de ~35KB. O Cloudflare (gateway da
    Supabase, confirmado pelo header `server: cloudflare` no log) rejeita
    isso ANTES de chegar no PostgREST/Postgres com um `400` cru -- por
    isso nenhuma quantidade de retry resolvia: a URL nunca mudava de
    tamanho entre tentativas, o limite era sempre o mesmo).

    Devolve `{chave_unica: {...}}`; `{}` se `chaves` for vazio (evita
    round-trip sem necessidade).
    """
    if not chaves:
        return {}
    client = get_client()
    estado_por_chave: dict[str, dict] = {}
    total_lotes = _total_lotes(len(chaves), _TAMANHO_LOTE_TRATATIVAS)
    for indice, lote_chaves in enumerate(_em_lotes(chaves, _TAMANHO_LOTE_TRATATIVAS), start=1):
        try:
            linhas = (
                client.table("tratativas")
                .select(
                    "chave_unica, status, status_contato, tentativa_1, tentativa_2, tentativa_3, "
                    "resposta, data_resposta, retorno_associado, created_at"
                )
                .in_("chave_unica", lote_chaves)
                .execute()
                .data
            )
        except Exception as e:
            e.add_note(
                f"buscar_estado_disparo_por_chaves: lote {indice}/{total_lotes} "
                f"(tamanho={len(lote_chaves)}, chaves {lote_chaves[0]}..{lote_chaves[-1]})"
            )
            raise
        for linha in linhas:
            estado_por_chave[linha["chave_unica"]] = linha
        sleep(ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS)
    return estado_por_chave


@retry_erro_transitorio_windows()
def buscar_situacao_manual_atual_por_chaves(chaves: list[str]) -> dict[str, str]:
    """Valor ATUAL de `situacao_manual` por `chave_unica`, antes da
    sincronização do ciclo — usada por `orchestrator.pipeline.
    _sincronizar_atendente_da_aba` pra só gravar `situacao_manual_definida_em`
    quando o valor muda de verdade (sem isso, `updated_at`/esse timestamp
    seriam tocados todo ciclo, mesmo sem mudança).

    **Dividido em mini-lotes de `_TAMANHO_LOTE_TRATATIVAS` itens** (mesmo
    achado 2026-08-21 de `buscar_estado_disparo_por_chaves`: `chaves` aqui
    é TODA a aba Tratativas atual, e o mesmo `.in_(...)` via query string
    de uma requisição GET pode gerar uma URL grande o bastante pro
    Cloudflare rejeitar antes de chegar no Postgres).

    Devolve `{chave_unica: situacao_manual}`; `{}` se `chaves` for vazio.
    """
    if not chaves:
        return {}
    client = get_client()
    situacao_por_chave: dict[str, str] = {}
    total_lotes = _total_lotes(len(chaves), _TAMANHO_LOTE_TRATATIVAS)
    for indice, lote_chaves in enumerate(_em_lotes(chaves, _TAMANHO_LOTE_TRATATIVAS), start=1):
        try:
            linhas = (
                client.table("tratativas")
                .select("chave_unica, situacao_manual")
                .in_("chave_unica", lote_chaves)
                .execute()
                .data
            )
        except Exception as e:
            e.add_note(
                f"buscar_situacao_manual_atual_por_chaves: lote {indice}/{total_lotes} "
                f"(tamanho={len(lote_chaves)}, chaves {lote_chaves[0]}..{lote_chaves[-1]})"
            )
            raise
        for linha in linhas:
            situacao_por_chave[linha["chave_unica"]] = linha.get("situacao_manual") or ""
        sleep(ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS)
    return situacao_por_chave


@retry_erro_transitorio_windows()
def buscar_estado_atendente_por_chaves(chaves: list[str]) -> dict[str, dict]:
    """Linha COMPLETA (`select("*")`) por `chave_unica` — usado por
    `sincronizar_campos_atendente_em_lote` pra completar, em todo payload
    de um lote heterogêneo, TODAS as colunas que a linha da planilha não
    tocou (ver docstring de `sincronizar_campos_atendente_em_lote`,
    achado 2026-08-26).

    **Dividido em mini-lotes de `_TAMANHO_LOTE_TRATATIVAS` itens** (mesmo
    achado 2026-08-21 de `buscar_estado_disparo_por_chaves`).

    Devolve `{chave_unica: {...}}`; `{}` se `chaves` for vazio.
    """
    if not chaves:
        return {}
    client = get_client()
    estado_por_chave: dict[str, dict] = {}
    total_lotes = _total_lotes(len(chaves), _TAMANHO_LOTE_TRATATIVAS)
    for indice, lote_chaves in enumerate(_em_lotes(chaves, _TAMANHO_LOTE_TRATATIVAS), start=1):
        try:
            linhas = (
                client.table("tratativas")
                .select("*")
                .in_("chave_unica", lote_chaves)
                .execute()
                .data
            )
        except Exception as e:
            e.add_note(
                f"buscar_estado_atendente_por_chaves: lote {indice}/{total_lotes} "
                f"(tamanho={len(lote_chaves)}, chaves {lote_chaves[0]}..{lote_chaves[-1]})"
            )
            raise
        for linha in linhas:
            estado_por_chave[linha["chave_unica"]] = linha
        sleep(ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS)
    return estado_por_chave


@retry_erro_transitorio_windows()
def buscar_tratativa_por_chave(chave_unica: str) -> dict | None:
    """Tratativa completa por `chave_unica` — usado pela Fase F.4
    (`orchestrator.pipeline.etapa_processar_resultado_ligacao`) pra
    achar o `id` uuid real a partir do "ID (hash)" mostrado na aba
    "Pendente de Ligação". `None` se a chave não existir (ex: linha de
    exemplo/placeholder ainda na planilha).
    """
    client = get_client()
    linhas = (
        client.table("tratativas").select("*").eq("chave_unica", chave_unica).execute().data
    )
    return linhas[0] if linhas else None


@retry_erro_transitorio_windows()
def sincronizar_campos_atendente(chave_unica: str, campos: dict) -> None:
    """Grava de volta no Supabase, PRA UMA linha só, campos que só o
    atendente edita/aciona — usado pelos fluxos pontuais que reagem a 1
    tratativa por vez (reconciliação de tratativa ausente, Fase F.4 de
    retorno da ligação, Passo 4 de reprocessar retorno) onde não faz
    sentido lotear. Pro caso de volume real (Fase E, `_sincronizar_
    atendente_da_aba`, ~1.900 linhas de uma vez), ver `sincronizar_campos_
    atendente_em_lote` (achado 2026-08-25: essa versão por-linha era o
    gargalo de ~10-11min quando chamada em loop pra aba inteira).

    **Update puro, sem fallback de insert**: se `chave_unica` não
    existir (ex: linha de exemplo/placeholder ainda na planilha, ou
    linha já removida), a chamada não faz nada — nunca cria uma linha
    nova só com campos de atendente, sem os campos do motor.

    Quando `campos` inclui `"status"` (hoje só `STATUS_FINALIZADO`, ver
    `orchestrator/pipeline.py`), também seta `finalizado_em` (se ainda
    não vier em `campos`) e grava a transição no histórico — usa
    `buscar_tratativa_por_chave` DEPOIS do update pra achar o `id` (e
    também herda de quebra a mesma guarda de "chave não existe = não
    faz nada", já que a busca simplesmente não acha nada).
    """
    client = get_client()
    campos = dict(campos)
    if campos.get("status") == STATUS_FINALIZADO:
        campos.setdefault("finalizado_em", _agora_utc_iso())
    client.table("tratativas").update(campos).eq("chave_unica", chave_unica).execute()

    if "status" in campos:
        tratativa = buscar_tratativa_por_chave(chave_unica)
        if tratativa is not None:
            _registrar_transicao_status(tratativa["id"], campos["status"])


@retry_erro_transitorio_windows()
def sincronizar_campos_atendente_em_lote(atualizacoes: dict[str, dict]) -> None:
    """Grava de volta no Supabase, em lote, os campos que só o atendente
    edita na planilha Operacional (`Selecionado`, `Técnico`, `Situação
    Manual`, `Data Agendada`, `Observação Manual`, `Discrepância
    revisada`, e `status` quando `Finalizado` estiver marcado) — chamado
    pela Fase E (`orchestrator.pipeline._sincronizar_atendente_da_aba`)
    antes de `upsert_tratativa` reescrever os campos do motor, pra não
    perder esse trabalho quando `reescrever_aba` limpar a aba inteira.

    Recebe `{chave_unica: campos}` — 1 dict por linha da aba, montado
    por `_sincronizar_atendente_da_aba`. Substitui, SÓ pra esse fluxo em
    volume, o uso em loop de `sincronizar_campos_atendente` (1 update
    síncrono + 1 select por LINHA — achado 2026-08-25: com a aba no
    volume real pós-reset (~1.900 linhas), isso sozinho levava ~10-11min
    tanto na Fase E quanto na etapa avulsa "Sincronizar seleção do
    atendente", que por rodar síncrona no catálogo nem dava pra cancelar
    no meio) pelo mesmo padrão já usado em `upsert_tratativas_em_lote`/
    `buscar_estado_disparo_por_chaves`: poucas chamadas de rede em lote,
    independente de N linhas. Os outros fluxos pontuais (1 tratativa por
    vez) continuam usando `sincronizar_campos_atendente`.

    **`campos` é heterogêneo por linha** (`data_agendada`/`status`/
    `status_contato`/`situacao_manual_definida_em` só entram
    condicionalmente — ver `orchestrator.pipeline._sincronizar_atendente_
    da_aba`). Um upsert em lote do PostgREST sempre monta um `INSERT ...
    ON CONFLICT DO UPDATE` por baixo — e o Postgres valida as colunas
    `NOT NULL` da tabela contra a cláusula `INSERT` ANTES de sequer
    avaliar o conflito, mesmo quando a linha já existe e vai só
    atualizar. **Achado real em produção 2026-08-26** (1ª rodada numa
    máquina nova): um lote cujo payload só tinha os campos de atendente
    (sem `origem`, `chassi` etc.) quebrou com `null value in column
    "origem" violates not-null constraint" — a versão anterior só
    preservava 4 colunas (as heterogêneas), não a linha inteira,
    então QUALQUER outra coluna `NOT NULL` da tabela ausente do payload
    também quebraria (não só `status`/`data_agendada`/etc., como o
    achado original de 2026-08-25 já alertava — só não cobria TODAS as
    colunas). **Corrigido**: `buscar_estado_atendente_por_chaves` agora
    busca a linha INTEIRA (`select("*")`) e cada payload começa como uma
    cópia completa dela, com `campos` sobrepondo só o que realmente
    mudou — mesmo princípio (nunca upsertar um payload parcial) já usado
    em `upsert_tratativas_em_lote`, só que a "base completa" vem de uma
    leitura fresca em vez de já estar pronta na mão de quem chama.

    **Update puro, sem fallback de insert**: `chave_unica` que não
    existir (ex: linha de exemplo/placeholder ainda na planilha, ou
    linha já removida) não entra no upsert — nunca cria uma linha nova
    só com campos de atendente, sem os campos do motor.

    Quando `campos` de uma linha inclui `"status"` (hoje só `STATUS_
    FINALIZADO`), também seta `finalizado_em` (se ainda não vier em
    `campos`) e grava a transição no histórico pra cada uma dessas
    linhas (subconjunto pequeno — só quem tem "Finalizado" marcado, não
    a aba inteira).
    """
    if not atualizacoes:
        return
    estado_atual = buscar_estado_atendente_por_chaves(list(atualizacoes))

    payloads = []
    for chave, campos in atualizacoes.items():
        estado = estado_atual.get(chave)
        if estado is None:
            continue
        payload = {**estado, **campos, "updated_at": _agora_utc_iso()}
        if "status" in campos:
            payload.setdefault("finalizado_em", _agora_utc_iso())
        payloads.append(payload)

    client = get_client()
    total_lotes = _total_lotes(len(payloads), _TAMANHO_LOTE_TRATATIVAS)
    for indice, lote_payloads in enumerate(_em_lotes(payloads, _TAMANHO_LOTE_TRATATIVAS), start=1):
        try:
            client.table("tratativas").upsert(lote_payloads, on_conflict="chave_unica").execute()
        except Exception as e:
            chaves_lote = [p["chave_unica"] for p in lote_payloads]
            e.add_note(
                f"sincronizar_campos_atendente_em_lote: upsert, lote {indice}/{total_lotes} "
                f"(tamanho={len(lote_payloads)}, chaves {chaves_lote[0]}..{chaves_lote[-1]})"
            )
            raise
        sleep(ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS)

    for chave, campos in atualizacoes.items():
        if "status" not in campos:
            continue
        estado = estado_atual.get(chave)
        if estado is not None:
            _registrar_transicao_status(estado["id"], campos["status"])


@retry_erro_transitorio_windows()
def atualizar_apos_envio(
    tratativa_id: str, atendimento_id: int, mensagem_id: int, status: str
) -> None:
    """Registra o resultado de um disparo Newmo bem-sucedido: grava
    atendimento_id/mensagem_id (para casar com a resposta do webhook depois),
    incrementa `tentativas` e marca o timestamp da tentativa correspondente
    (`tentativa_1`, `tentativa_2` ou `tentativa_3`).
    """
    client = get_client()
    atual = (
        client.table("tratativas")
        .select("tentativas")
        .eq("id", tratativa_id)
        .execute()
        .data
    )
    if not atual:
        raise ValueError(f"tratativa {tratativa_id!r} não encontrada")

    tentativas_atual = atual[0]["tentativas"]
    numero_tentativa = min(tentativas_atual + 1, 3)
    agora = _agora_utc_iso()

    client.table("tratativas").update(
        {
            "atendimento_id": atendimento_id,
            "mensagem_id": mensagem_id,
            "status": status,
            "tentativas": tentativas_atual + 1,
            f"tentativa_{numero_tentativa}": agora,
            "ultimo_disparo": agora,
            "updated_at": agora,
        }
    ).eq("id", tratativa_id).execute()
    _registrar_transicao_status(tratativa_id, status)


@retry_erro_transitorio_windows()
def marcar_contato_invalido(tratativa_id: str) -> None:
    """Cod 7 da Newmo (telefone não registrado no WhatsApp) — marca
    `status_contato`, **sem** tocar `tentativas`/`ultimo_disparo` (decisão
    já fechada: esse cod não consome tentativa, por isso não reaproveita
    `atualizar_apos_envio`). Bloqueia `buscar_elegiveis_para_disparo` até
    "Telefone corrigido" limpar o campo de novo."""
    client = get_client()
    client.table("tratativas").update(
        {"status_contato": STATUS_CONTATO_INVALIDO, "updated_at": _agora_utc_iso()}
    ).eq("id", tratativa_id).execute()


@retry_erro_transitorio_windows()
def buscar_por_atendimento_id(atendimento_id: int) -> dict | None:
    """Usado pelo fluxo de resposta do webhook (a Edge Function grava em
    `tratativas`; o app só lê o resultado já gravado por `atendimento_id`,
    que é a chave de correspondência — telefone é só fallback).
    """
    client = get_client()
    linhas = (
        client.table("tratativas")
        .select("*")
        .eq("atendimento_id", atendimento_id)
        .execute()
        .data
    )
    return linhas[0] if linhas else None


# Status em que uma tratativa já saiu do ciclo de mensagens — uma resposta
# tardia do associado (`retorno_associado`) nesses casos não tem mais lugar
# pra aparecer em `Tratativas`, então alimenta a aba "Alertas" (Fase F.5).
_STATUS_RETORNO_TARDIO = [STATUS_AGUARDANDO_LIGACAO, STATUS_ENCAMINHADO_PUMA, STATUS_FINALIZADO]


@retry_erro_transitorio_windows()
def buscar_candidatas_alertas() -> dict[str, list[dict]]:
    """As duas fontes da aba "Alertas" (Fase F.5), já separadas por tipo —
    evita o orchestrator precisar redetectar qual caso é qual a partir dos
    campos crus:

    - `retorno_tardio`: resposta do associado (`retorno_associado`)
      chegou depois da tratativa já ter saído de `Tratativas`.
    - `agendado_sem_data`: associado confirmou agendamento por WhatsApp
      (`situacao_manual = 'Agendado'`), mas a data combinada ainda não
      foi preenchida pelo atendente.
    """
    client = get_client()
    retorno_tardio = (
        client.table("tratativas")
        .select("*")
        .not_.is_("retorno_associado", "null")
        .in_("status", _STATUS_RETORNO_TARDIO)
        .execute()
        .data
    )
    agendado_sem_data = (
        client.table("tratativas")
        .select("*")
        .eq("situacao_manual", "Agendado")
        .is_("data_agendada", "null")
        .execute()
        .data
    )
    return {"retorno_tardio": retorno_tardio, "agendado_sem_data": agendado_sem_data}


@retry_erro_transitorio_windows()
def marcar_aguardando_ligacao(tratativa_id: str) -> None:
    """3 tentativas de mensagem sem resposta -> escala para ligação
    (tentativa única, feita pelo atendente)."""
    client = get_client()
    client.table("tratativas").update(
        {"status": STATUS_AGUARDANDO_LIGACAO, "updated_at": _agora_utc_iso()}
    ).eq("id", tratativa_id).execute()
    _registrar_transicao_status(tratativa_id, STATUS_AGUARDANDO_LIGACAO)


@retry_erro_transitorio_windows()
def registrar_ligacao(tratativa_id: str, dados_ligacao: dict) -> None:
    """Grava a tentativa de ligação em `ligacoes` (data_contato, retornou,
    conseguiu_agendar, observacao, registrado_por).

    Se `conseguiu_agendar` for True, finaliza a tratativa diretamente
    (`status='finalizado'`, com `finalizado_em`). Se for False, o status
    não é alterado aqui — cabe ao chamador (orchestrator) escalar para
    `encaminhar_puma` em seguida, conforme a regra "ligação sem sucesso em
    agendar -> Puma".
    """
    client = get_client()
    payload = dict(dados_ligacao)
    payload["tratativa_id"] = tratativa_id
    client.table("ligacoes").insert(payload).execute()

    if dados_ligacao.get("conseguiu_agendar"):
        agora = _agora_utc_iso()
        client.table("tratativas").update(
            {"status": STATUS_FINALIZADO, "updated_at": agora, "finalizado_em": agora}
        ).eq("id", tratativa_id).execute()
        _registrar_transicao_status(tratativa_id, STATUS_FINALIZADO)


@retry_erro_transitorio_windows()
def encaminhar_puma(tratativa_id: str, motivo: str) -> None:
    """Ligação sem sucesso em agendar -> encaminha automaticamente pro Puma:
    grava em `puma_encaminhamentos` e marca a tratativa como
    `status='encaminhado_puma'`.
    """
    client = get_client()
    resultado = client.table("puma_encaminhamentos").insert(
        {"tratativa_id": tratativa_id, "motivo": motivo}
    ).execute()
    puma_id = resultado.data[0]["id"]
    _registrar_transicao_puma(puma_id, tratativa_id, PUMA_STATUS_AGUARDANDO_ACAO)

    client.table("tratativas").update(
        {"status": STATUS_ENCAMINHADO_PUMA, "updated_at": _agora_utc_iso()}
    ).eq("id", tratativa_id).execute()
    _registrar_transicao_status(tratativa_id, STATUS_ENCAMINHADO_PUMA)


@retry_erro_transitorio_windows()
def _puma_id_mais_recente(tratativa_id: str) -> str:
    """Id do encaminhamento MAIS RECENTE da tratativa (`data_
    encaminhamento` desc) em `puma_encaminhamentos` — compartilhado por
    `sincronizar_status_puma`/`sincronizar_observacao_puma`. Levanta erro
    se não achar nenhum (situação impossível no fluxo normal, só chega
    aqui depois de `encaminhar_puma` já ter rodado)."""
    client = get_client()
    encontrados = (
        client.table("puma_encaminhamentos")
        .select("id")
        .eq("tratativa_id", tratativa_id)
        .order("data_encaminhamento", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not encontrados:
        raise ValueError(
            f"nenhum encaminhamento pra Puma encontrado para tratativa {tratativa_id!r}"
        )
    return encontrados[0]["id"]


@retry_erro_transitorio_windows()
def sincronizar_status_puma(tratativa_id: str, status_novo: str) -> None:
    """Sincroniza de volta pro Supabase o status real de um encaminhamento
    pra Puma — hoje só existe editado manualmente na coluna "Status" da
    aba "Encaminhar pra Puma" (por humano, ou por
    `orchestrator.pipeline._resolver_acao_alerta` via a aba Alertas), sem
    nenhum caminho de volta pro Supabase até agora (`puma_encaminhamentos.
    status` nasce com o default `'aguardando_acao'` e nunca era
    atualizado). Chamado por `etapa_processar_resultado_ligacao`, único
    ponto que lê essa coluna da planilha.
    """
    client = get_client()
    puma_id = _puma_id_mais_recente(tratativa_id)
    payload = {"status": status_novo}
    if status_novo == PUMA_STATUS_CONCLUIDO:
        payload["concluido_em"] = _agora_utc_iso()
    client.table("puma_encaminhamentos").update(payload).eq("id", puma_id).execute()
    _registrar_transicao_puma(puma_id, tratativa_id, status_novo)


@retry_erro_transitorio_windows()
def sincronizar_observacao_puma(tratativa_id: str, observacao: str) -> None:
    """Sincroniza de volta pro Supabase o texto que o time da Puma anota
    na coluna "Observação Puma" da aba "Encaminhar pra Puma" — igual
    "Status" (`sincronizar_status_puma`), nunca tinha caminho de volta
    até 2026-08-14. Roda todo ciclo, mesmo com `observacao == ""`
    (permite apagar uma anotação antiga) — mesma filosofia incondicional
    do resto dos campos de atendente (`sincronizar_campos_atendente`)."""
    client = get_client()
    puma_id = _puma_id_mais_recente(tratativa_id)
    client.table("puma_encaminhamentos").update({"observacao_puma": observacao}).eq("id", puma_id).execute()


def _coagir_valor(valor: str):
    """`system_parameters.valor` é sempre text no banco — coerção genérica
    de tipo (bool/int) na leitura; listas (ex: placas_genericas, CSV) ficam a
    cargo de quem consome o parâmetro, que sabe qual chave é lista.
    """
    valor_normalizado = valor.strip()
    if valor_normalizado.lower() in ("true", "false"):
        return valor_normalizado.lower() == "true"
    if valor_normalizado.lstrip("-").isdigit():
        return int(valor_normalizado)
    return valor_normalizado


@retry_erro_transitorio_windows()
def buscar_parametros() -> dict:
    """system_parameters -> dict {chave: valor} com coerção básica de tipo."""
    client = get_client()
    linhas = client.table("system_parameters").select("chave, valor").execute().data
    return {linha["chave"]: _coagir_valor(linha["valor"]) for linha in linhas}


@retry_erro_transitorio_windows()
def buscar_rule_templates() -> dict:
    """rule_templates (ativos) -> dict {codigo_regra: {...}} para consulta
    direta pelo motor de regras (core.motor_regras)."""
    client = get_client()
    linhas = (
        client.table("rule_templates")
        .select("codigo_regra, prioridade, template_acao, template_observacao, nivel_urgencia")
        .eq("ativo", True)
        .execute()
        .data
    )
    return {linha["codigo_regra"]: linha for linha in linhas}


@retry_erro_transitorio_windows()
def buscar_bases_ativas() -> list[dict]:
    """`bases` ativas — usado pra resolver o dropdown "Base" da aba
    Tratativas pro uuid real (`tratativas.base_id`) e pra montar as
    variáveis de endereço dos templates de WhatsApp (Fase F.1)."""
    client = get_client()
    return client.table("bases").select("id, nome, endereco").eq("ativo", True).execute().data


@retry_erro_transitorio_windows()
def buscar_pontos_acao_ativos() -> list[dict]:
    """`pontos_acao` ativos — mesmo uso de `buscar_bases_ativas`, pro
    dropdown "Ponto de Ação" (`tratativas.ponto_acao_id`)."""
    client = get_client()
    return (
        client.table("pontos_acao")
        .select("id, nome_local, endereco, data")
        .eq("ativo", True)
        .execute()
        .data
    )


_TAMANHO_LOTE_SGA = 200


@retry_erro_transitorio_windows()
def buscar_situacoes_veiculo_sga_em_lote(chassis: list[str]) -> dict[str, dict]:
    """Último status do SGA conhecido pra cada um de `chassis`, numa leitura
    só (`.in_(...)`) — achado 2026-08-17: a versão anterior lia 1 chassi
    por vez; na escala real (milhares de veículos), isso sozinho já
    significava milhares de idas sequenciais ao Supabase, contribuindo
    pras execuções de horas na Fase D. Devolve `{chassi: registro}` — só
    entram chassis que já tinham situação conhecida. Ver
    `core.motor_regras_instalacao_remocao.atualizar_situacao_sga` pra a
    lógica pura que decide se `desde` reinicia ou não.

    **Dividido em mini-lotes de `_TAMANHO_LOTE_SGA` itens** (achado
    2026-08-21, mesma causa raiz de `buscar_estado_disparo_por_chaves`:
    com "milhares de veículos", o `.in_(...)` via query string de uma
    requisição GET pode gerar uma URL grande o bastante pro Cloudflare
    rejeitar antes de chegar no Postgres — nunca bateu aqui na prática
    porque o checkpoint de Fase D (`_situacoes_veiculo_sga_recentes`) hoje
    reduz a lista antes de chegar aqui, mas o risco é real e crescente com
    o volume de Instalação/Remoção).

    `desde`/`atualizado_em` voltam do Postgres como string ISO — convertidos
    pra `datetime` aqui (achado 2026-08-25: sem isso, `atualizar_situacao_
    sga` reaproveita `desde` cru quando o status não muda, e `core.
    motor_regras_instalacao_remocao._classificar_remocao` faz aritmética de
    data em cima — `TypeError` só aparecia na prática quando um chassi já
    tinha `registro_anterior` real, o que exige pelo menos uma consulta SGA
    bem-sucedida anterior; nunca bateu em teste isolado/reset completo).
    Espelha a conversão inversa já feita em `upsert_situacoes_veiculo_sga_
    em_lote`.
    """
    if not chassis:
        return {}
    client = get_client()
    situacao_por_chassi: dict[str, dict] = {}
    total_lotes = _total_lotes(len(chassis), _TAMANHO_LOTE_SGA)
    for indice, lote_chassis in enumerate(_em_lotes(chassis, _TAMANHO_LOTE_SGA), start=1):
        try:
            linhas = (
                client.table("situacao_veiculo_sga")
                .select("*")
                .in_("chassi", lote_chassis)
                .execute()
                .data
            )
        except Exception as e:
            e.add_note(
                f"buscar_situacoes_veiculo_sga_em_lote: lote {indice}/{total_lotes} "
                f"(tamanho={len(lote_chassis)}, chassis {lote_chassis[0]}..{lote_chassis[-1]})"
            )
            raise
        for linha in linhas:
            for campo in ("desde", "atualizado_em"):
                if isinstance(linha.get(campo), str):
                    linha[campo] = datetime.fromisoformat(linha[campo])
            situacao_por_chassi[linha["chassi"]] = linha
        sleep(ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS)
    return situacao_por_chassi


@retry_erro_transitorio_windows()
def upsert_situacoes_veiculo_sga_em_lote(registros: list[dict]) -> None:
    """Insere/atualiza `situacao_veiculo_sga` pra todos os `registros` numa
    gravação só (upsert em lote pela chave primária `chassi`) — mesmo
    motivo de `buscar_situacoes_veiculo_sga_em_lote`: a versão anterior
    fazia 1 leitura + 1 gravação POR chassi (3 idas ao Supabase por
    veículo, contando a leitura própria de `upsert_situacao_veiculo_sga`
    de decidir insert vs. update). Espera uma lista de dicts no formato de
    `core.motor_regras_instalacao_remocao.atualizar_situacao_sga`;
    `desde`/`atualizado_em` podem vir como `datetime` (o core não conhece
    formato de Supabase, converte-se aqui pra ISO).

    **Dividido em mini-lotes de `_TAMANHO_LOTE_SGA` itens** (achado
    2026-08-21, mesmo motivo do chunking já aplicado a `upsert_tratativas_
    em_lote`: mandar milhares de registros num único POST pode gerar um
    corpo grande o bastante pro Cloudflare rejeitar antes do Postgres)."""
    if not registros:
        return
    payloads = []
    for dados in registros:
        if "chassi" not in dados:
            raise ValueError("dados precisa conter 'chassi' para upsert_situacoes_veiculo_sga_em_lote")
        payload = dict(dados)
        for campo in ("desde", "atualizado_em"):
            if isinstance(payload.get(campo), datetime):
                payload[campo] = payload[campo].isoformat()
        payloads.append(payload)
    client = get_client()
    total_lotes = _total_lotes(len(payloads), _TAMANHO_LOTE_SGA)
    for indice, lote_payloads in enumerate(_em_lotes(payloads, _TAMANHO_LOTE_SGA), start=1):
        try:
            client.table("situacao_veiculo_sga").upsert(lote_payloads, on_conflict="chassi").execute()
        except Exception as e:
            chassis_lote = [p["chassi"] for p in lote_payloads]
            e.add_note(
                f"upsert_situacoes_veiculo_sga_em_lote: lote {indice}/{total_lotes} "
                f"(tamanho={len(lote_payloads)}, chassis {chassis_lote[0]}..{chassis_lote[-1]})"
            )
            raise
        sleep(ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS)


@retry_erro_transitorio_windows()
def registrar_log_execucao(
    execucao_id: str,
    etapa_id: str,
    maquina: str,
    iniciado_em: datetime,
    finalizado_em: datetime,
    sucesso: bool,
    motivo_parada: str | None,
    mensagem: str,
) -> None:
    """1 linha em `log_execucoes` por etapa executada — `duracao_ms` é
    derivado aqui (não confiar no chamador pra fazer a conta). Chamado por
    `orchestrator.catalogo_etapas.executar_etapas_com_contexto`/
    `continuar_apos_reconexao`, sempre envolvido em try/except no
    chamador (uma falha de rede ao registrar telemetria não pode derrubar
    a etapa real nem vazar a trava de execução)."""
    duracao_ms = int((finalizado_em - iniciado_em).total_seconds() * 1000)
    get_client().table("log_execucoes").insert(
        {
            "execucao_id": execucao_id,
            "etapa_id": etapa_id,
            "maquina": maquina,
            "iniciado_em": iniciado_em.isoformat(),
            "finalizado_em": finalizado_em.isoformat(),
            "duracao_ms": duracao_ms,
            "sucesso": sucesso,
            "motivo_parada": motivo_parada,
            "mensagem": mensagem,
        }
    ).execute()


@retry_erro_transitorio_windows()
def buscar_execucao_lock_atual() -> dict:
    """Estado atual da trava única (`execucao_lock`, linha seed
    `id='pipeline_diario'`) — usado pelo watchdog pra decidir se a
    execução está travada há mais tempo que o normal (`em_execucao`,
    `iniciado_em`)."""
    return (
        get_client()
        .table("execucao_lock")
        .select("em_execucao, maquina, iniciado_em")
        .eq("id", _ID_EXECUCAO_LOCK)
        .single()
        .execute()
        .data
    )


@retry_erro_transitorio_windows()
def buscar_ultimas_execucoes(limite_por_etapa: int = 5) -> dict[str, list[dict]]:
    """Últimas execuções de `log_execucoes`, agrupadas por `etapa_id`
    (mais recente primeiro) — alimenta o watchdog (etapa que falhou,
    etapa mais lenta que a média histórica) e o diagnóstico de eficiência
    do SGA/Track N'Me. PostgREST não faz "top N por grupo" num único
    round-trip; busca um limite total generoso ordenado por
    `etapa_id, iniciado_em desc` e agrupa em Python."""
    client = get_client()
    linhas = (
        client.table("log_execucoes")
        .select("etapa_id, iniciado_em, finalizado_em, duracao_ms, sucesso, motivo_parada, mensagem")
        .order("etapa_id")
        .order("iniciado_em", desc=True)
        .limit(2000)
        .execute()
        .data
    )
    agrupado: dict[str, list[dict]] = {}
    for linha in linhas:
        lista = agrupado.setdefault(linha["etapa_id"], [])
        if len(lista) < limite_por_etapa:
            lista.append(linha)
    return agrupado


_ID_EXECUCAO_LOCK = "pipeline_diario"
# Segurança: acima disso, considera a trava travada por engano (ex: a máquina
# que travou caiu no meio da execução) e libera sozinha, sem exigir
# intervenção manual. Valor de partida — ajustar se a duração real do
# pipeline completo mostrar que 2h é curto/longo demais.
TTL_EXECUCAO_LOCK_MINUTOS = 120


@retry_erro_transitorio_windows()
def adquirir_execucao_lock(maquina: str, ttl_minutos: int = TTL_EXECUCAO_LOCK_MINUTOS) -> bool:
    """Trava a execução única do pipeline diário (linha `id='pipeline_diario'`
    em `execucao_lock`, Fase 2 — evita 2 máquinas processando a mesma fila do
    dia ao mesmo tempo, não importa qual disparou). Chamar antes de
    "Executar tudo"/"a partir daqui" no Painel Operador; sempre parear com
    `liberar_execucao_lock` num `finally`.

    Se já houver uma execução marcada mas `iniciado_em` for mais antigo que
    `ttl_minutos`, trata como travada por engano e adquire mesmo assim.

    Retorna True se conseguiu travar (pode seguir), False se outra máquina
    já está executando de verdade agora.
    """
    client = get_client()
    linha = (
        client.table("execucao_lock")
        .select("em_execucao, iniciado_em")
        .eq("id", _ID_EXECUCAO_LOCK)
        .single()
        .execute()
        .data
    )

    if linha["em_execucao"] and linha["iniciado_em"]:
        iniciado_em = datetime.fromisoformat(linha["iniciado_em"])
        if datetime.now(timezone.utc) - iniciado_em < timedelta(minutes=ttl_minutos):
            return False

    client.table("execucao_lock").update(
        {
            "em_execucao": True,
            "maquina": maquina,
            "iniciado_em": _agora_utc_iso(),
        }
    ).eq("id", _ID_EXECUCAO_LOCK).execute()
    return True


@retry_erro_transitorio_windows()
def liberar_execucao_lock() -> None:
    """Libera a trava ao final da execução — sempre num `finally`, pra não
    deixar travada pra sempre se o pipeline quebrar no meio."""
    client = get_client()
    client.table("execucao_lock").update({"em_execucao": False}).eq(
        "id", _ID_EXECUCAO_LOCK
    ).execute()


_STATUS_FORA_DE_PENDENCIA = [STATUS_FINALIZADO, STATUS_ENCAMINHADO_PUMA]

_TAMANHO_PAGINA_TRATATIVAS = 1000


def _buscar_tratativas_paginado(construir_consulta, tamanho_pagina: int = _TAMANHO_PAGINA_TRATATIVAS) -> list[dict]:
    """Pagina uma consulta em `tratativas` (achado 2026-08-21, mesma causa
    raiz já corrigida no dashboard do cliente: `select()` sem `.range()`
    trunca em 1.000 linhas silenciosamente, sem erro nenhum pra avisar).
    `construir_consulta()` devolve o builder já com `.select()`/filtros
    aplicados, faltando só `.range()`/`.execute()` (aplicados aqui, um novo
    builder por página)."""
    linhas = []
    pagina = 0
    while True:
        inicio, fim = pagina * tamanho_pagina, (pagina + 1) * tamanho_pagina - 1
        bloco = construir_consulta().range(inicio, fim).execute().data
        linhas.extend(bloco)
        if len(bloco) < tamanho_pagina:
            break
        pagina += 1
    return linhas


@retry_erro_transitorio_windows()
def contar_pendencias_por_origem() -> dict:
    """Contagem "agora" de tratativas ainda em aberto (fora do ciclo já
    concluído), agrupada por origem — alimenta os 3 cards de resumo do
    Painel Operador (tela "Operação", Fase 4)."""
    client = get_client()
    linhas = _buscar_tratativas_paginado(
        lambda: client.table("tratativas").select("origem").not_.in_("status", _STATUS_FORA_DE_PENDENCIA)
    )
    contagem = {ORIGEM_MANUTENCAO: 0, ORIGEM_INSTALACAO: 0, ORIGEM_REMOCAO: 0}
    for linha in linhas:
        if linha.get("origem") in contagem:
            contagem[linha["origem"]] += 1
    return contagem


# Status em que o motor (fila_operacional) segue responsável por regenerar a
# tratativa a cada rodada real — fora daqui, a chave saiu do ciclo por um
# motivo já tratado em outro lugar (ligação, Puma, ou já concluída), não por
# ter "sumido" (`orchestrator.pipeline._reconciliar_tratativas_ausentes`,
# Bloco H). Mesmos 3 valores de `_STATUS_RETORNO_TARDIO`, propósito diferente
# — mantido como constante própria pra não acoplar 2 usos por coincidência.
_STATUS_FORA_DA_FILA_MOTOR = [STATUS_AGUARDANDO_LIGACAO, STATUS_ENCAMINHADO_PUMA, STATUS_FINALIZADO]


@retry_erro_transitorio_windows()
def buscar_tratativas_abertas_no_motor() -> list[dict]:
    """Todas as tratativas que o motor ainda é responsável por regenerar a
    cada rodada real (`orchestrator.pipeline._reconciliar_tratativas_
    ausentes`, Bloco H)."""
    client = get_client()
    return _buscar_tratativas_paginado(
        lambda: client.table("tratativas")
        .select("chave_unica, status, rodadas_ausente_fila")
        .not_.in_("status", _STATUS_FORA_DA_FILA_MOTOR)
    )


_COLUNAS_DASHBOARD_OPERADOR = (
    "chave_unica, origem, identificador, chassi, cliente, telefone, codigo_regra, status, "
    "atendimento, status_contato, situacao_manual, situacao_manual_definida_em, "
    "discrepancia_revisada, tentativa_1, tentativa_2, tentativa_3, created_at"
)


@retry_erro_transitorio_windows()
def buscar_tratativas_abertas_para_dashboard_operador() -> list[dict]:
    """1 busca só, reaproveitada por todos os widgets A-F do "Painel de
    apoio" do Operador (`orchestrator.dashboards_operador.
    montar_dashboards_operador`) — evita um round-trip por widget."""
    client = get_client()
    return _buscar_tratativas_paginado(
        lambda: client.table("tratativas")
        .select(_COLUNAS_DASHBOARD_OPERADOR)
        .not_.in_("status", _STATUS_FORA_DE_PENDENCIA)
    )
