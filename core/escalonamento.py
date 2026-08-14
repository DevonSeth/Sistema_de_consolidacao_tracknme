"""
Regras de elegibilidade e escalonamento — puras, consomem/retornam dicts,
não conhecem Supabase nem Newmo diretamente (isso é papel de `integrations/`
e `orchestrator/`). Vale igualmente pras 3 origens (instalacao/remocao/
manutencao) — confirmado pelo usuário, 2026-08-06.

Decisões de negócio (2026-08-06, ensinadas pelo usuário — não é um timer
automático):

    `Selecionado` é controlado manualmente pelo atendente (fica marcado
    até ele desmarcar — não reseta sozinho depois de disparar), por causa
    de restrições operacionais reais (ex: roteirização de técnico por
    região) que fazem o atendente decidir dia a dia o que entra no lote.
    Por isso a trava contra disparo duplicado no mesmo dia (`ultimo_disparo`)
    é essencial.

    Corte de horário: 17:30 em America/Recife, todo dia — depois disso o
    sistema nunca mais dispara naquele dia.

    Só dia útil, exceto feriado em Recife (biblioteca automática de
    feriados BR, não lista manual) — `permitir_excecao` existe pra um
    toggle futuro do painel administrativo ("Disparar em feriados e
    final de semana", ainda não implementado em `ui/`).

    O que interrompe a esteira antes das 3 tentativas: `Finalizado`
    (some do status elegível — ver `core.constants.STATUS_TRATATIVA`) OU
    `Situação Manual` preenchida (`Agendado`/`Cancelado`/`Solicitação
    operacional`).

    Escalonamento pra ligação é automático: 3 tentativas sem resposta e
    sem resolução manual -> `aguardando_ligacao`, sem ação humana.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from core.constants import (
    STATUS_AGUARDANDO_RESPOSTA,
    STATUS_BLOQUEADO_SGA,
    STATUS_ENCAMINHADO_PUMA,
    STATUS_FINALIZADO,
)


def elegivel_para_disparo(tratativa: dict, agora: datetime, limite_tentativas: int = 3) -> bool:
    """`selecionado=True`, `tentativas<limite_tentativas`, não disparado
    hoje, não bloqueado por SGA, sem Situação Manual definida. Não checa
    horário de corte nem dia útil — isso é responsabilidade de
    `passou_do_horario_corte`/`dia_permite_disparo`, chamadas
    separadamente (a cada item do loop, pra não perder o resto do lote
    se o corte acontecer no meio da execução). `limite_tentativas` vem
    de `system_parameters.limite_tentativas_disparo` (extraído por quem
    chama, mesmo padrão de `permitir_excecao`/`dia_permite_disparo`) —
    mesmo valor usado em `deve_escalar_para_ligacao`, pra nunca divergir."""
    if not tratativa.get("selecionado"):
        return False
    if tratativa.get("tentativas", 0) >= limite_tentativas:
        return False
    if tratativa.get("status") == STATUS_BLOQUEADO_SGA:
        return False
    if (tratativa.get("situacao_manual") or "").strip():
        return False

    ultimo_disparo = tratativa.get("ultimo_disparo")
    if ultimo_disparo:
        if isinstance(ultimo_disparo, str):
            ultimo_disparo = datetime.fromisoformat(ultimo_disparo)
        if ultimo_disparo.date() == agora.date():
            return False
    return True


def passou_do_horario_corte(agora: datetime, horario_corte: str, fuso: str) -> bool:
    """Converte `agora` pro `fuso` informado antes de comparar com
    `horario_corte` ('17:30') — nunca compara no fuso original de
    `agora`. Se `agora` não tiver tzinfo, assume que já está no fuso
    certo (sem conversão)."""
    agora_local = agora.astimezone(ZoneInfo(fuso)) if agora.tzinfo else agora
    hora_corte = datetime.strptime(horario_corte, "%H:%M").time()
    return agora_local.time() >= hora_corte


def dia_permite_disparo(data: date, feriados: set[date], permitir_excecao: bool = False) -> bool:
    """Segunda a sexta, exceto feriado. `permitir_excecao=True` ignora
    essa checagem por completo (toggle futuro do painel administrativo).
    `feriados` é calculado por quem chama (orchestrator, via biblioteca
    de feriados BR) — este módulo continua sem I/O."""
    if permitir_excecao:
        return True
    return data.weekday() < 5 and data not in feriados


def deve_escalar_para_ligacao(tratativa: dict, limite_tentativas: int = 3) -> bool:
    """`limite_tentativas` (default 3) sem resposta e sem resolução
    manual -> escala pra ligação, automático. Mesmo valor de
    `system_parameters.limite_tentativas_disparo` usado em
    `elegivel_para_disparo` — extraído por quem chama."""
    return (
        tratativa.get("tentativas", 0) >= limite_tentativas
        and tratativa.get("status") == STATUS_AGUARDANDO_RESPOSTA
        and not (tratativa.get("situacao_manual") or "").strip()
    )


def dias_uteis_entre(inicio: date, fim: date, feriados: set[date]) -> int:
    """Conta dias úteis (seg-sex, exceto feriado) estritamente depois de
    `inicio` até `fim`, inclusive. `fim <= inicio` -> 0. Usado pro
    indicador "Dias sem contato" (2026-08-07) — mesmo conceito de dia
    útil de `dia_permite_disparo`, só que contando um intervalo em vez
    de checar um dia isolado."""
    if fim <= inicio:
        return 0
    total = 0
    dia = inicio + timedelta(days=1)
    while dia <= fim:
        if dia.weekday() < 5 and dia not in feriados:
            total += 1
        dia += timedelta(days=1)
    return total


def resultado_ligacao(conseguiu_agendar: bool) -> str:
    """Ligação é tentativa única: conseguiu agendar -> finalizado; não
    conseguiu -> encaminhado pra Puma."""
    return STATUS_FINALIZADO if conseguiu_agendar else STATUS_ENCAMINHADO_PUMA
