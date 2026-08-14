"""
Watchdog — Observabilidade, fatia 1. Vigia 3 sinais a partir de
`log_execucoes`/`execucao_lock` (Supabase), pra alertar o mais rápido
possível quando algo dá errado na esteira, sem precisar de ninguém
investigando ativamente:

    1. Execução travada/presa — `execucao_lock.em_execucao=True` há mais
       tempo que `watchdog_minutos_alerta_travado` (system_parameters).
    2. Etapa(s) cuja execução mais recente terminou em falha real (não
       cancelada pelo usuário, não aguardando reconexão manual).
    3. Etapa(s) muito mais lentas que a própria média histórica de
       sucesso (`watchdog_fator_lentidao` × média — precisa de pelo menos
       `_MINIMO_HISTORICO_PARA_LENTIDAO` execuções de sucesso anteriores
       pra ter baseline; sem isso, a etapa é ignorada, não é bug).

NÃO cobre "pipeline não rodou no dia esperado" (heartbeat) — fora de
escopo desta fatia (decisão do usuário). Alerta é só banner na tela
(Painel Admin web + Painel Operador local) por enquanto — notificação
ativa (WhatsApp pro admin) fica pra depois.
"""

from datetime import datetime, timezone

from integrations import supabase_client

_LIMITE_HISTORICO_POR_ETAPA = 10
_MINIMO_HISTORICO_PARA_LENTIDAO = 3


def _avaliar_execucao_travada(agora: datetime, minutos_alerta: float) -> dict | None:
    lock = supabase_client.buscar_execucao_lock_atual()
    if not lock.get("em_execucao") or not lock.get("iniciado_em"):
        return None
    iniciado_em = datetime.fromisoformat(lock["iniciado_em"])
    minutos_decorridos = (agora - iniciado_em).total_seconds() / 60
    if minutos_decorridos < minutos_alerta:
        return None
    return {"maquina": lock.get("maquina"), "desde": lock["iniciado_em"], "minutos": int(minutos_decorridos)}


def _avaliar_etapas_com_falha(execucoes_por_etapa: dict[str, list[dict]]) -> list[dict]:
    """Só a execução MAIS RECENTE de cada etapa conta — se a etapa já
    teve sucesso depois de uma falha antiga, essa falha foi corrigida e
    não deve continuar acendendo o alerta."""
    falhas = []
    for etapa_id, execucoes in execucoes_por_etapa.items():
        if not execucoes:
            continue
        mais_recente = execucoes[0]
        if mais_recente["motivo_parada"] != "falha":
            continue
        falhas.append({
            "etapa_id": etapa_id,
            "quando": mais_recente["iniciado_em"],
            "mensagem": mais_recente["mensagem"],
        })
    return sorted(falhas, key=lambda f: f["quando"], reverse=True)


def _avaliar_etapas_lentas(execucoes_por_etapa: dict[str, list[dict]], fator: float) -> list[dict]:
    lentas = []
    for etapa_id, execucoes in execucoes_por_etapa.items():
        sucessos = [e for e in execucoes if e["sucesso"]]
        if not sucessos:
            continue
        atual, historico = sucessos[0], sucessos[1:]
        if len(historico) < _MINIMO_HISTORICO_PARA_LENTIDAO:
            continue  # sem baseline suficiente ainda — não é bug
        media_historica_ms = sum(e["duracao_ms"] for e in historico) / len(historico)
        if atual["duracao_ms"] > media_historica_ms * fator:
            lentas.append({
                "etapa_id": etapa_id,
                "duracao_atual_ms": atual["duracao_ms"],
                "media_historica_ms": round(media_historica_ms),
            })
    return lentas


def avaliar_watchdog(agora: datetime | None = None) -> dict:
    """`agora` (opcional, default UTC now) só existe pra permitir teste
    determinístico do sinal de execução travada."""
    agora = agora or datetime.now(timezone.utc)
    parametros = supabase_client.buscar_parametros()
    minutos_alerta = float(parametros.get("watchdog_minutos_alerta_travado", 60))
    fator_lentidao = float(parametros.get("watchdog_fator_lentidao", 2))

    execucoes_por_etapa = supabase_client.buscar_ultimas_execucoes(limite_por_etapa=_LIMITE_HISTORICO_POR_ETAPA)

    return {
        "execucao_travada": _avaliar_execucao_travada(agora, minutos_alerta),
        "etapas_com_falha": _avaliar_etapas_com_falha(execucoes_por_etapa),
        "etapas_lentas": _avaliar_etapas_lentas(execucoes_por_etapa, fator_lentidao),
    }
