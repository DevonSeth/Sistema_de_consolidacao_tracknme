"""
"Painel de apoio" do Painel Operador (tela "Operação", Fase 4) — widgets
"agora" (sem filtro de período, mesmo espírito das métricas "(agora)" já
usadas no Dashboard Admin/Cliente) que ajudam o atendente a decidir por
onde começar, em vez de só listar dado bruto.

Mesma camada de `orchestrator/pipeline.py` (composição sobre
`integrations/`), mas sem nenhuma etapa de execução — só leitura/derivação
a partir de UMA busca em lote
(`supabase_client.buscar_tratativas_abertas_para_dashboard_operador`),
evitando um round-trip por widget.
"""

from datetime import date, datetime

import holidays

from core import escalonamento
from core.constants import CODIGOS_REMOCAO_ATIVA, STATUS_CONTATO_INVALIDO
from integrations import supabase_client

_TOP_FILA_PRIORIDADE = 50


def _feriados_pe(ano: int) -> frozenset[date]:
    """Mesmo cálculo de `orchestrator.pipeline._feriados_pe` — duplicado
    de propósito (pequeno, já é precedente no projeto) em vez de importar
    uma função privada de outro módulo só pra isso."""
    return frozenset(holidays.Brazil(years=ano, subdiv="PE").keys())


def _feriados_para_intervalo(inicio: date, fim: date) -> set[date]:
    return set().union(*(_feriados_pe(ano) for ano in {inicio.year, fim.year}))


def _dias_uteis_desde(marco: date, agora: date) -> int:
    feriados = _feriados_para_intervalo(marco, agora)
    return escalonamento.dias_uteis_entre(marco, agora, feriados)


def _ultimo_contato(linha: dict) -> date | None:
    """Mesmo critério de `orchestrator.pipeline._ultimo_contato_tratativa`
    (mais recente pro mais antigo — preenchidos em sequência
    cronológica, então o primeiro não-vazio já é o marco certo)."""
    for campo in ("tentativa_3", "tentativa_2", "tentativa_1", "created_at"):
        valor = linha.get(campo)
        if not valor:
            continue
        try:
            return datetime.fromisoformat(valor).date()
        except (TypeError, ValueError):
            continue
    return None


def _dias_sem_contato(linha: dict, agora: date) -> int | None:
    ultimo = _ultimo_contato(linha)
    if ultimo is None:
        return None
    return _dias_uteis_desde(ultimo, agora)


def _nivel_urgencia(linha: dict, templates: dict) -> int:
    return templates.get(linha.get("codigo_regra"), {}).get("nivel_urgencia") or 0


def _fila_prioridade(linhas: list[dict], templates: dict, agora: date) -> list[dict]:
    """Widget A — top 50 pendências, ordenadas por nível de urgência
    (mais alto primeiro) e, em empate, por dias sem contato (mais
    parada primeiro)."""
    enriquecidas = [
        {
            **linha,
            "nivel_urgencia": _nivel_urgencia(linha, templates),
            "dias_sem_contato": _dias_sem_contato(linha, agora),
        }
        for linha in linhas
    ]
    enriquecidas.sort(key=lambda l: (-l["nivel_urgencia"], -(l["dias_sem_contato"] or 0)))
    return enriquecidas[:_TOP_FILA_PRIORIDADE]


def _sem_atendimento(linhas: list[dict]) -> list[dict]:
    """Widget B — `atendimento` vazio nunca entra na esteira de disparo
    (gate real em `pipeline.etapa_disparo_mensagens`), hoje invisível
    pro atendente sem este widget."""
    return [linha for linha in linhas if not (linha.get("atendimento") or "")]


def _funil_status(linhas: list[dict]) -> dict:
    """Widget C — contagem agrupada por `status`."""
    contagem: dict[str, int] = {}
    for linha in linhas:
        status = linha.get("status") or ""
        contagem[status] = contagem.get(status, 0) + 1
    return contagem


def _situacao_manual_parada(linhas: list[dict], agora: date) -> list[dict]:
    """Widget D — ordenada por dias desde que `situacao_manual` foi
    definida (`situacao_manual_definida_em`, coluna nova). Sem corte por
    limiar — o atendente vê os dias e decide."""
    parados = []
    for linha in linhas:
        if not (linha.get("situacao_manual") or ""):
            continue
        definida_em = linha.get("situacao_manual_definida_em")
        if not definida_em:
            continue
        try:
            marco = datetime.fromisoformat(definida_em).date()
        except (TypeError, ValueError):
            continue
        parados.append({**linha, "dias_situacao_manual": _dias_uteis_desde(marco, agora)})
    parados.sort(key=lambda l: -l["dias_situacao_manual"])
    return parados


def _contato_invalido(linhas: list[dict]) -> list[dict]:
    """Widget E — telefone não registrado no WhatsApp (cód. 7 Newmo),
    aguardando "Telefone corrigido" na planilha."""
    return [linha for linha in linhas if linha.get("status_contato") == STATUS_CONTATO_INVALIDO]


def _discrepancia_remocao_nao_revisada(linhas: list[dict]) -> list[dict]:
    """Widget F — rastreador ainda ativo mas consta na lista de retirada,
    e o atendente ainda não marcou `discrepancia_revisada` (que suprime a
    exibição na aba Tratativas, `pipeline.etapa_publicar_fila_operacional`)."""
    return [
        linha
        for linha in linhas
        if linha.get("codigo_regra") in CODIGOS_REMOCAO_ATIVA and not linha.get("discrepancia_revisada")
    ]


def montar_dashboards_operador(agora: datetime | None = None) -> dict:
    """Widgets "agora" pra tela Operação — 1 busca em lote + derivação
    local (urgência via `rule_templates`, dias sem contato via
    `core.escalonamento.dias_uteis_entre` — mesma aproximação já
    documentada no Dashboard Admin/Cliente, feriados de PE)."""
    agora_data = (agora or datetime.now()).date()
    linhas = supabase_client.buscar_tratativas_abertas_para_dashboard_operador()
    templates = supabase_client.buscar_rule_templates()

    return {
        "fila_prioridade": _fila_prioridade(linhas, templates, agora_data),
        "sem_atendimento": _sem_atendimento(linhas),
        "funil_status": _funil_status(linhas),
        "situacao_manual_parada": _situacao_manual_parada(linhas, agora_data),
        "contato_invalido": _contato_invalido(linhas),
        "discrepancia_remocao_nao_revisada": _discrepancia_remocao_nao_revisada(linhas),
    }
