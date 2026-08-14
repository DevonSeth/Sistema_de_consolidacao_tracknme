"""
Métricas "emprestadas" do Admin/Dashboard Cliente, expostas no Painel
Operador só quando o Admin marca `dashboard_metricas_cliente.
visivel_operador = true` pra aquela chave (mecanismo novo desta sessão —
o Admin decide o que o Operador vê, mesma tabela/espírito do toggle
"Visível no Dashboard Cliente" que já existia).

Módulo separado de `orchestrator/dashboards_operador.py` de propósito:
aquele cobre os 6 widgets NATIVOS do Operador (sempre visíveis, sem
toggle); este cobre só o que vem do catálogo de métricas do Admin
(`webapp/src/lib/dashboard-metricas-meta.ts`), contrato e ciclo de vida
diferentes.

Candidatas implementadas:
    pendencias_em_aberto  -> número simples
    encaminhadas_puma     -> número simples
    pendentes_por_cidade  -> lista
    tendencia_diaria      -> gráfico (série diária, janela fixa de 30 dias)
    estado_por_origem     -> gráfico (pendente/andamento/concluído por origem)
    distribuicao_urgencia -> gráfico (contagem por nível 1-5)
    evolucao_backlog      -> gráfico (série diária acumulada desde o início)
    pendentes_por_tipo    -> gráfico (reaproveita estado_por_origem)

As 5 últimas (`_METRICAS_GRAFICO`) são "de período" na origem (TS), mas o
Operador não tem seletor de data — decisão fechada com o usuário
(2026-08-13): `tendencia_diaria` usa uma janela FIXA de 30 dias
terminando hoje (só "hoje" viraria 1 ponto, sem servir como tendência);
`evolucao_backlog` sempre "acumulado desde o início" (sem o toggle
diário/acumulado que existe no Dashboard Cliente — não há período
filtrado aqui pra alternar). As outras 3 já são "agora" por natureza,
sem essa decisão de janela.

Cada função abaixo é uma PORTA FIEL da função TS equivalente em
`webapp/src/lib/dashboard-metrics.ts` — mesma regra de negócio, só
reescrita em Python contra a mesma tabela/RPC do Supabase (nunca reaproveita
funções Operador-nativas como `supabase_client.contar_pendencias_por_origem`,
que tem uma definição DIFERENTE — exclui `encaminhado_puma` sempre; aqui a
regra é a mesma de `dashboard_estado_em`, que só exclui o que já foi
concluído de verdade).
"""

from datetime import date, datetime, timedelta, timezone

from core.constants import STATUS_ENCAMINHADO_PUMA, STATUS_FINALIZADO, STATUS_PENDENTE
from integrations.supabase_client import get_client

_ORIGENS = ("instalacao", "remocao", "manutencao")

_METRICAS_SIMPLES = ("pendencias_em_aberto", "encaminhadas_puma")
_METRICAS_LISTA = ("pendentes_por_cidade",)
_METRICAS_GRAFICO = (
    "tendencia_diaria",
    "estado_por_origem",
    "distribuicao_urgencia",
    "evolucao_backlog",
    "pendentes_por_tipo",
)


def _pendencias_em_aberto_agora() -> int:
    """Porta fiel de `buscarAbertoAgora` (TS) — RPC `dashboard_estado_em`
    no momento real da consulta, soma os buckets `pendente`+`em_andamento`
    (tudo que não é `concluido` pela regra de precedência única já usada
    lá: concluído > pendente > em andamento)."""
    client = get_client()
    agora_iso = datetime.now(timezone.utc).isoformat()
    linhas = client.rpc("dashboard_estado_em", {"p_data": agora_iso}).execute().data or []
    return sum(int(linha["quantidade"]) for linha in linhas if linha["bucket"] in ("pendente", "em_andamento"))


def _encaminhadas_para_puma_agora() -> int:
    """Porta fiel de `buscarEncaminhadasParaPumaAgora` (TS) — conta
    `tratativas.status='encaminhado_puma'` excluindo as que a Puma já
    concluiu (`tratativas.status` nunca sai de `'encaminhado_puma'`
    mesmo depois de concluído, só `puma_encaminhamentos.status` muda —
    mesma ressalva já documentada em `dashboard-metrics.ts`)."""
    client = get_client()
    encaminhadas = (
        client.table("tratativas")
        .select("id")
        .eq("status", STATUS_ENCAMINHADO_PUMA)
        .execute()
        .data
    )
    if not encaminhadas:
        return 0

    ids = [linha["id"] for linha in encaminhadas]
    concluidas = (
        client.table("puma_encaminhamentos")
        .select("tratativa_id")
        .eq("status", "concluido")
        .in_("tratativa_id", ids)
        .execute()
        .data
    )
    ids_concluidos = {linha["tratativa_id"] for linha in concluidas}
    return len([i for i in ids if i not in ids_concluidos])


def _pendentes_por_cidade() -> list[dict]:
    """Porta fiel de `buscarPendentesPorCidade` (TS) — `status='pendente'`
    agrupado por cidade, ordem alfabética, cidade vazia vira "Sem cidade
    cadastrada"."""
    client = get_client()
    linhas = client.table("tratativas").select("cidade").eq("status", STATUS_PENDENTE).execute().data or []

    contagem: dict[str, int] = {}
    for linha in linhas:
        cidade = (linha.get("cidade") or "").strip() or "Sem cidade cadastrada"
        contagem[cidade] = contagem.get(cidade, 0) + 1

    return [{"cidade": cidade, "quantidade": quantidade} for cidade, quantidade in sorted(contagem.items())]


def _tratativas_abertas_agora(client) -> list[dict]:
    """Porta fiel de `buscarAbertasAgora` (TS) — `status != 'finalizado'`,
    menos as já concluídas pela Puma (mesma exclusão de 2 queries usada
    por `_encaminhadas_para_puma_agora`). Reaproveitado por
    `_distribuicao_urgencia_agora`."""
    abertas = (
        client.table("tratativas")
        .select("id, codigo_regra")
        .neq("status", STATUS_FINALIZADO)
        .execute()
        .data
        or []
    )
    if not abertas:
        return []

    ids = [linha["id"] for linha in abertas]
    concluidas_puma = (
        client.table("puma_encaminhamentos")
        .select("tratativa_id")
        .eq("status", "concluido")
        .in_("tratativa_id", ids)
        .execute()
        .data
        or []
    )
    ids_concluidos = {linha["tratativa_id"] for linha in concluidas_puma}
    return [linha for linha in abertas if linha["id"] not in ids_concluidos]


def _tendencia_diaria_30_dias() -> list[dict]:
    """Porta fiel de `buscarSerieDiaria` (TS) — RPC `dashboard_serie_diaria`,
    janela fixa de 30 dias terminando hoje (decisão fechada: sem seletor
    de data no Operador, ver docstring do módulo)."""
    client = get_client()
    hoje = datetime.now(timezone.utc).date()
    desde_iso = f"{(hoje - timedelta(days=29)).isoformat()}T00:00:00.000Z"
    ate_iso = f"{hoje.isoformat()}T23:59:59.999Z"
    return client.rpc("dashboard_serie_diaria", {"p_desde": desde_iso, "p_ate": ate_iso}).execute().data or []


def _novo_estado_origem() -> dict:
    return {"pendente": 0, "em_andamento": 0, "concluido": 0}


def _estado_por_origem_agora() -> dict:
    """Porta fiel do trecho de `buscarMetricas` (TS) que monta
    `estadoPorOrigem` — RPC `dashboard_estado_em`, sempre com `p_data`
    = agora (o TS usa o fim do período filtrado; aqui não há período)."""
    client = get_client()
    agora_iso = datetime.now(timezone.utc).isoformat()
    linhas = client.rpc("dashboard_estado_em", {"p_data": agora_iso}).execute().data or []

    estado_por_origem = {origem: _novo_estado_origem() for origem in _ORIGENS}
    for linha in linhas:
        quantidade = int(linha["quantidade"])
        alvo = estado_por_origem.setdefault(linha["origem"], _novo_estado_origem())
        if linha["bucket"] == "concluido":
            alvo["concluido"] += quantidade
        elif linha["bucket"] == "pendente":
            alvo["pendente"] += quantidade
        else:
            alvo["em_andamento"] += quantidade
    return estado_por_origem


def _distribuicao_urgencia_agora() -> dict:
    """Porta fiel de `buscarDistribuicaoUrgencia` (TS) — tratativas
    abertas agora, contadas por `rule_templates.nivel_urgencia` (via
    `codigo_regra`); ignora linhas sem nível conhecido."""
    client = get_client()
    abertas = _tratativas_abertas_agora(client)
    templates = client.table("rule_templates").select("codigo_regra, nivel_urgencia").execute().data or []
    urgencia_por_regra = {linha["codigo_regra"]: linha["nivel_urgencia"] for linha in templates}

    distribuicao: dict[int, int] = {}
    for linha in abertas:
        nivel = urgencia_por_regra.get(linha.get("codigo_regra"))
        if nivel is None:
            continue
        distribuicao[nivel] = distribuicao.get(nivel, 0) + 1
    return distribuicao


def _contar_por_dia(datas: list[str | None]) -> dict[str, int]:
    contagem: dict[str, int] = {}
    for valor in datas:
        if not valor:
            continue
        dia = valor[:10]
        contagem[dia] = contagem.get(dia, 0) + 1
    return contagem


def _evolucao_backlog_acumulado() -> list[dict]:
    """Porta fiel de `buscarEvolucaoBacklog` (TS) — sempre "acumulado
    desde o início" (decisão fechada: sem o toggle diário/acumulado do
    Dashboard Cliente, o Operador não tem período filtrado pra
    alternar). `abertas` é saldo (sobe/desce); `concluidas` é total
    acumulado (só sobe), união de `finalizado_em` (tratativas) e
    `concluido_em` (puma_encaminhamentos)."""
    client = get_client()
    primeira = (
        client.table("tratativas").select("created_at").order("created_at", desc=False).limit(1).execute().data
    )
    if not primeira:
        return []

    criadas = client.table("tratativas").select("created_at").execute().data or []
    finalizadas = (
        client.table("tratativas").select("finalizado_em").not_.is_("finalizado_em", "null").execute().data or []
    )
    puma_concluidas = (
        client.table("puma_encaminhamentos")
        .select("concluido_em")
        .not_.is_("concluido_em", "null")
        .execute()
        .data
        or []
    )

    criadas_por_dia = _contar_por_dia([linha["created_at"] for linha in criadas])
    concluidas_por_dia = _contar_por_dia(
        [linha["finalizado_em"] for linha in finalizadas] + [linha["concluido_em"] for linha in puma_concluidas]
    )

    cursor = date.fromisoformat(primeira[0]["created_at"][:10])
    fim = datetime.now(timezone.utc).date()

    serie = []
    criadas_acumulado = 0
    concluidas_acumulado = 0
    while cursor <= fim:
        dia = cursor.isoformat()
        criadas_acumulado += criadas_por_dia.get(dia, 0)
        concluidas_acumulado += concluidas_por_dia.get(dia, 0)
        serie.append({
            "dia": dia,
            "abertas": criadas_acumulado - concluidas_acumulado,
            "concluidas": concluidas_acumulado,
        })
        cursor += timedelta(days=1)
    return serie


def _pendentes_por_tipo_agora() -> dict:
    """Não é uma porta de função TS própria — `dashboard-metrics.ts` não
    tem uma `buscarPendentesPorTipo` dedicada, a UI (`DashboardsClient.
    tsx`) só reaproveita `estadoPorOrigem[origem].pendente`. Mesma coisa
    aqui: reaproveita `_estado_por_origem_agora()`, sem query nova."""
    return {origem: dados["pendente"] for origem, dados in _estado_por_origem_agora().items()}


_CALCULO_POR_CHAVE = {
    "pendencias_em_aberto": _pendencias_em_aberto_agora,
    "encaminhadas_puma": _encaminhadas_para_puma_agora,
    "pendentes_por_cidade": _pendentes_por_cidade,
    "tendencia_diaria": _tendencia_diaria_30_dias,
    "estado_por_origem": _estado_por_origem_agora,
    "distribuicao_urgencia": _distribuicao_urgencia_agora,
    "evolucao_backlog": _evolucao_backlog_acumulado,
    "pendentes_por_tipo": _pendentes_por_tipo_agora,
}


def montar_metricas_admin_operador() -> dict:
    """Lê `dashboard_metricas_cliente` (só as chaves com `visivel_operador
    = true` e que já têm cálculo implementado aqui) e devolve
    `{"metricas_simples": {chave: valor}, "metricas_lista": {chave: [...]},
    "metricas_grafico": {chave: ...}}` — uma chave só aparece se estiver
    marcada visível."""
    client = get_client()
    chaves_conhecidas = list(_CALCULO_POR_CHAVE.keys())
    linhas = (
        client.table("dashboard_metricas_cliente")
        .select("chave, visivel_operador")
        .in_("chave", chaves_conhecidas)
        .execute()
        .data
        or []
    )
    chaves_visiveis = {linha["chave"] for linha in linhas if linha.get("visivel_operador")}

    metricas_simples = {}
    metricas_lista = {}
    metricas_grafico = {}
    for chave in chaves_visiveis:
        valor = _CALCULO_POR_CHAVE[chave]()
        if chave in _METRICAS_SIMPLES:
            metricas_simples[chave] = valor
        elif chave in _METRICAS_LISTA:
            metricas_lista[chave] = valor
        elif chave in _METRICAS_GRAFICO:
            metricas_grafico[chave] = valor

    return {"metricas_simples": metricas_simples, "metricas_lista": metricas_lista, "metricas_grafico": metricas_grafico}
