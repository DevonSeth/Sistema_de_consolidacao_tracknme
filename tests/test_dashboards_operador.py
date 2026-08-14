from datetime import datetime

import pytest

from orchestrator import dashboards_operador as do


def _linha(**extra):
    base = {
        "chave_unica": "chave-1",
        "origem": "manutencao",
        "identificador": "CHASSI-1",
        "chassi": "CHASSI-1",
        "cliente": "Cliente Teste",
        "telefone": "+5581988887777",
        "codigo_regra": "REGRA_1",
        "status": "pendente",
        "atendimento": "base",
        "status_contato": None,
        "situacao_manual": "",
        "situacao_manual_definida_em": None,
        "discrepancia_revisada": False,
        "tentativa_1": None,
        "tentativa_2": None,
        "tentativa_3": None,
        "created_at": "2026-08-01T10:00:00+00:00",
    }
    base.update(extra)
    return base


def _mockar(monkeypatch, linhas, templates=None):
    chamadas = []

    def _buscar_fake():
        chamadas.append("buscar")
        return linhas

    monkeypatch.setattr(do.supabase_client, "buscar_tratativas_abertas_para_dashboard_operador", _buscar_fake)
    monkeypatch.setattr(do.supabase_client, "buscar_rule_templates", lambda: templates or {})
    return chamadas


# --------------------------------------------------------------------------
# montar_dashboards_operador — estrutura geral + 1 busca só
# --------------------------------------------------------------------------

def test_montar_dashboards_operador_faz_uma_busca_so(monkeypatch):
    chamadas = _mockar(monkeypatch, [_linha()])

    resultado = do.montar_dashboards_operador(agora=datetime(2026, 8, 12))

    assert chamadas == ["buscar"]
    assert set(resultado.keys()) == {
        "fila_prioridade",
        "sem_atendimento",
        "funil_status",
        "situacao_manual_parada",
        "contato_invalido",
        "discrepancia_remocao_nao_revisada",
    }


# --------------------------------------------------------------------------
# Widget A — fila de prioridade
# --------------------------------------------------------------------------

def test_fila_prioridade_ordena_por_nivel_urgencia_desc(monkeypatch):
    templates = {
        "REGRA_1": {"nivel_urgencia": 3},
        "REGRA_5": {"nivel_urgencia": 5},
    }
    linhas = [
        _linha(chave_unica="baixa", codigo_regra="REGRA_1"),
        _linha(chave_unica="alta", codigo_regra="REGRA_5"),
    ]
    _mockar(monkeypatch, linhas, templates)

    resultado = do.montar_dashboards_operador(agora=datetime(2026, 8, 12))

    chaves_em_ordem = [l["chave_unica"] for l in resultado["fila_prioridade"]]
    assert chaves_em_ordem == ["alta", "baixa"]


def test_fila_prioridade_limita_a_50(monkeypatch):
    templates = {"REGRA_1": {"nivel_urgencia": 1}}
    linhas = [_linha(chave_unica=f"chave-{i}", codigo_regra="REGRA_1") for i in range(60)]
    _mockar(monkeypatch, linhas, templates)

    resultado = do.montar_dashboards_operador(agora=datetime(2026, 8, 12))

    assert len(resultado["fila_prioridade"]) == 50


# --------------------------------------------------------------------------
# Widget B — sem atendimento
# --------------------------------------------------------------------------

def test_sem_atendimento_pega_so_atendimento_vazio(monkeypatch):
    linhas = [
        _linha(chave_unica="tem", atendimento="base"),
        _linha(chave_unica="sem", atendimento=""),
        _linha(chave_unica="nulo", atendimento=None),
    ]
    _mockar(monkeypatch, linhas)

    resultado = do.montar_dashboards_operador(agora=datetime(2026, 8, 12))

    chaves = {l["chave_unica"] for l in resultado["sem_atendimento"]}
    assert chaves == {"sem", "nulo"}


# --------------------------------------------------------------------------
# Widget C — funil por status
# --------------------------------------------------------------------------

def test_funil_status_agrupa_contagem(monkeypatch):
    linhas = [
        _linha(status="pendente"),
        _linha(status="pendente"),
        _linha(status="aguardando_resposta"),
    ]
    _mockar(monkeypatch, linhas)

    resultado = do.montar_dashboards_operador(agora=datetime(2026, 8, 12))

    assert resultado["funil_status"] == {"pendente": 2, "aguardando_resposta": 1}


# --------------------------------------------------------------------------
# Widget D — situação manual parada
# --------------------------------------------------------------------------

def test_situacao_manual_parada_so_com_valor_e_timestamp(monkeypatch):
    linhas = [
        _linha(chave_unica="parada", situacao_manual="Agendado", situacao_manual_definida_em="2026-08-01T00:00:00+00:00"),
        _linha(chave_unica="sem_situacao", situacao_manual="", situacao_manual_definida_em=None),
        _linha(chave_unica="sem_timestamp", situacao_manual="Agendado", situacao_manual_definida_em=None),
    ]
    _mockar(monkeypatch, linhas)

    resultado = do.montar_dashboards_operador(agora=datetime(2026, 8, 12))

    chaves = {l["chave_unica"] for l in resultado["situacao_manual_parada"]}
    assert chaves == {"parada"}
    assert resultado["situacao_manual_parada"][0]["dias_situacao_manual"] > 0


def test_situacao_manual_parada_ordena_mais_antiga_primeiro(monkeypatch):
    linhas = [
        _linha(chave_unica="recente", situacao_manual="Agendado", situacao_manual_definida_em="2026-08-10T00:00:00+00:00"),
        _linha(chave_unica="antiga", situacao_manual="Agendado", situacao_manual_definida_em="2026-07-01T00:00:00+00:00"),
    ]
    _mockar(monkeypatch, linhas)

    resultado = do.montar_dashboards_operador(agora=datetime(2026, 8, 12))

    chaves_em_ordem = [l["chave_unica"] for l in resultado["situacao_manual_parada"]]
    assert chaves_em_ordem == ["antiga", "recente"]


# --------------------------------------------------------------------------
# Widget E — contato inválido bloqueado
# --------------------------------------------------------------------------

def test_contato_invalido_filtra_pelo_status_contato(monkeypatch):
    linhas = [
        _linha(chave_unica="invalido", status_contato="contato_invalido"),
        _linha(chave_unica="normal", status_contato=None),
    ]
    _mockar(monkeypatch, linhas)

    resultado = do.montar_dashboards_operador(agora=datetime(2026, 8, 12))

    chaves = {l["chave_unica"] for l in resultado["contato_invalido"]}
    assert chaves == {"invalido"}


# --------------------------------------------------------------------------
# Widget F — discrepância Remoção Ativa não revisada
# --------------------------------------------------------------------------

def test_discrepancia_remocao_nao_revisada_exige_codigo_e_flag_falsa(monkeypatch):
    linhas = [
        _linha(chave_unica="pendente_revisao", codigo_regra="REGRA_REMOCAO_ATIVA_URGENTE", discrepancia_revisada=False),
        _linha(chave_unica="ja_revisada", codigo_regra="REGRA_REMOCAO_ATIVA_URGENTE", discrepancia_revisada=True),
        _linha(chave_unica="outra_regra", codigo_regra="REGRA_1", discrepancia_revisada=False),
    ]
    _mockar(monkeypatch, linhas)

    resultado = do.montar_dashboards_operador(agora=datetime(2026, 8, 12))

    chaves = {l["chave_unica"] for l in resultado["discrepancia_remocao_nao_revisada"]}
    assert chaves == {"pendente_revisao"}
