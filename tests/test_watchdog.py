from datetime import datetime, timedelta, timezone

import pytest

from orchestrator import watchdog


def _linha(iniciado_em, sucesso=True, duracao_ms=1000, motivo_parada=None, mensagem=""):
    return {
        "iniciado_em": iniciado_em, "duracao_ms": duracao_ms,
        "sucesso": sucesso, "motivo_parada": motivo_parada, "mensagem": mensagem,
    }


@pytest.fixture(autouse=True)
def _parametros_padrao(monkeypatch):
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_parametros",
        lambda: {"watchdog_minutos_alerta_travado": 60, "watchdog_fator_lentidao": 2},
    )


# --------------------------------------------------------------------------
# execução travada
# --------------------------------------------------------------------------

def test_execucao_travada_none_quando_nao_esta_em_execucao(monkeypatch):
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": False, "maquina": None, "iniciado_em": None},
    )
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: {})

    resultado = watchdog.avaliar_watchdog()

    assert resultado["execucao_travada"] is None


def test_execucao_travada_none_quando_dentro_do_tempo_normal(monkeypatch):
    agora = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
    iniciado_em = agora - timedelta(minutes=30)
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": True, "maquina": "PC-1", "iniciado_em": iniciado_em.isoformat()},
    )
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: {})

    resultado = watchdog.avaliar_watchdog(agora=agora)

    assert resultado["execucao_travada"] is None


def test_execucao_travada_preenchida_quando_passa_do_limite(monkeypatch):
    agora = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
    iniciado_em = agora - timedelta(minutes=90)
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": True, "maquina": "PC-1", "iniciado_em": iniciado_em.isoformat()},
    )
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: {})

    resultado = watchdog.avaliar_watchdog(agora=agora)

    assert resultado["execucao_travada"] == {"maquina": "PC-1", "desde": iniciado_em.isoformat(), "minutos": 90}


# --------------------------------------------------------------------------
# etapas com falha
# --------------------------------------------------------------------------

def test_etapa_com_falha_aparece_quando_execucao_mais_recente_falhou(monkeypatch):
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": False, "maquina": None, "iniciado_em": None},
    )
    execucoes = {
        "baixar_relatorios": [_linha("2026-08-14T10:00:00+00:00", sucesso=False, motivo_parada="falha", mensagem="timeout")],
    }
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: execucoes)

    resultado = watchdog.avaliar_watchdog()

    assert resultado["etapas_com_falha"] == [
        {"etapa_id": "baixar_relatorios", "quando": "2026-08-14T10:00:00+00:00", "mensagem": "timeout"},
    ]


def test_etapa_com_falha_some_quando_sucesso_mais_recente_corrigiu(monkeypatch):
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": False, "maquina": None, "iniciado_em": None},
    )
    execucoes = {
        "baixar_relatorios": [
            _linha("2026-08-14T11:00:00+00:00", sucesso=True),  # mais recente: sucesso, corrigiu
            _linha("2026-08-14T10:00:00+00:00", sucesso=False, motivo_parada="falha", mensagem="timeout"),
        ],
    }
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: execucoes)

    resultado = watchdog.avaliar_watchdog()

    assert resultado["etapas_com_falha"] == []


def test_etapa_com_falha_ignora_cancelada_e_aguardando_reconexao(monkeypatch):
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": False, "maquina": None, "iniciado_em": None},
    )
    execucoes = {
        "escalonar_ligacao": [_linha("2026-08-14T10:00:00+00:00", sucesso=False, motivo_parada="cancelada")],
        "enriquecimento_sga": [_linha("2026-08-14T10:00:00+00:00", sucesso=False, motivo_parada="aguardando_reconexao")],
    }
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: execucoes)

    resultado = watchdog.avaliar_watchdog()

    assert resultado["etapas_com_falha"] == []


# --------------------------------------------------------------------------
# etapas lentas
# --------------------------------------------------------------------------

def test_etapa_lenta_ignora_sem_baseline_suficiente(monkeypatch):
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": False, "maquina": None, "iniciado_em": None},
    )
    execucoes = {
        "enriquecimento_sga": [
            _linha("2026-08-14T11:00:00+00:00", duracao_ms=100_000),
            _linha("2026-08-14T10:00:00+00:00", duracao_ms=1_000),
        ],
    }
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: execucoes)

    resultado = watchdog.avaliar_watchdog()

    assert resultado["etapas_lentas"] == []


def test_etapa_lenta_detectada_com_baseline_suficiente(monkeypatch):
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": False, "maquina": None, "iniciado_em": None},
    )
    execucoes = {
        "enriquecimento_sga": [
            _linha("2026-08-14T13:00:00+00:00", duracao_ms=10_000),  # hoje: 10s
            _linha("2026-08-13T10:00:00+00:00", duracao_ms=1_000),
            _linha("2026-08-12T10:00:00+00:00", duracao_ms=1_000),
            _linha("2026-08-11T10:00:00+00:00", duracao_ms=1_000),  # média histórica: 1s
        ],
    }
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: execucoes)

    resultado = watchdog.avaliar_watchdog()

    assert resultado["etapas_lentas"] == [
        {"etapa_id": "enriquecimento_sga", "duracao_atual_ms": 10_000, "media_historica_ms": 1_000},
    ]


def test_etapa_lenta_nao_detectada_quando_dentro_do_fator(monkeypatch):
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": False, "maquina": None, "iniciado_em": None},
    )
    execucoes = {
        "enriquecimento_sga": [
            _linha("2026-08-14T13:00:00+00:00", duracao_ms=1_500),  # só 1.5x a média, fator é 2
            _linha("2026-08-13T10:00:00+00:00", duracao_ms=1_000),
            _linha("2026-08-12T10:00:00+00:00", duracao_ms=1_000),
            _linha("2026-08-11T10:00:00+00:00", duracao_ms=1_000),
        ],
    }
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: execucoes)

    resultado = watchdog.avaliar_watchdog()

    assert resultado["etapas_lentas"] == []


def test_etapa_lenta_ignora_falhas_ao_calcular_media_e_atual(monkeypatch):
    """`sucessos` filtra fora as falhas antes de escolher `atual`/`historico`
    — uma falha recente não deve virar "duração atual" nem entrar na média."""
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": False, "maquina": None, "iniciado_em": None},
    )
    execucoes = {
        "enriquecimento_sga": [
            _linha("2026-08-14T14:00:00+00:00", sucesso=False, motivo_parada="falha", duracao_ms=999_999),
            _linha("2026-08-14T13:00:00+00:00", duracao_ms=10_000),
            _linha("2026-08-13T10:00:00+00:00", duracao_ms=1_000),
            _linha("2026-08-12T10:00:00+00:00", duracao_ms=1_000),
            _linha("2026-08-11T10:00:00+00:00", duracao_ms=1_000),
        ],
    }
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: execucoes)

    resultado = watchdog.avaliar_watchdog()

    assert resultado["etapas_lentas"] == [
        {"etapa_id": "enriquecimento_sga", "duracao_atual_ms": 10_000, "media_historica_ms": 1_000},
    ]


# --------------------------------------------------------------------------
# parâmetros — respeita os valores de system_parameters, não valores fixos
# --------------------------------------------------------------------------

def test_usa_parametros_customizados_de_watchdog(monkeypatch):
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_parametros",
        lambda: {"watchdog_minutos_alerta_travado": 10, "watchdog_fator_lentidao": 10},
    )
    agora = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
    iniciado_em = agora - timedelta(minutes=15)
    monkeypatch.setattr(
        watchdog.supabase_client, "buscar_execucao_lock_atual",
        lambda: {"em_execucao": True, "maquina": "PC-1", "iniciado_em": iniciado_em.isoformat()},
    )
    execucoes = {
        "enriquecimento_sga": [
            _linha("2026-08-14T13:00:00+00:00", duracao_ms=10_000),  # 10x a média — fator custom é 10, não alerta
            _linha("2026-08-13T10:00:00+00:00", duracao_ms=1_000),
            _linha("2026-08-12T10:00:00+00:00", duracao_ms=1_000),
            _linha("2026-08-11T10:00:00+00:00", duracao_ms=1_000),
        ],
    }
    monkeypatch.setattr(watchdog.supabase_client, "buscar_ultimas_execucoes", lambda limite_por_etapa: execucoes)

    resultado = watchdog.avaliar_watchdog(agora=agora)

    assert resultado["execucao_travada"] is not None  # 15min > limiar custom de 10min
    assert resultado["etapas_lentas"] == []  # 10x não é > fator custom de 10
