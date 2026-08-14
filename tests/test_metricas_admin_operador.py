from datetime import datetime, timezone

from orchestrator import metricas_admin_operador as mao


class _Resposta:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, tabela, cliente):
        self.tabela = tabela
        self.cliente = cliente

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def neq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def is_(self, *a, **k):
        return self

    @property
    def not_(self):
        return self

    def execute(self):
        fila = self.cliente.retornos.get(self.tabela)
        if fila:
            return _Resposta(fila.pop(0))
        return _Resposta([])


class _RpcFalso:
    def __init__(self, retorno):
        self.retorno = retorno

    def execute(self):
        return _Resposta(self.retorno)


class _ClienteFalso:
    def __init__(self, rpc_retorno=None, retornos=None):
        self.rpc_retorno = rpc_retorno or []
        self.retornos = {tabela: list(dados) for tabela, dados in (retornos or {}).items()}
        self.chamadas_rpc = []

    def rpc(self, nome, params):
        self.chamadas_rpc.append((nome, params))
        return _RpcFalso(self.rpc_retorno)

    def table(self, nome):
        return _Query(nome, self)


# --------------------------------------------------------------------------
# _pendencias_em_aberto_agora
# --------------------------------------------------------------------------

def test_pendencias_em_aberto_agora_soma_pendente_e_em_andamento(monkeypatch):
    cliente = _ClienteFalso(
        rpc_retorno=[
            {"origem": "manutencao", "bucket": "pendente", "quantidade": 3},
            {"origem": "instalacao", "bucket": "em_andamento", "quantidade": 2},
            {"origem": "remocao", "bucket": "concluido", "quantidade": 10},
        ]
    )
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    assert mao._pendencias_em_aberto_agora() == 5
    assert cliente.chamadas_rpc[0][0] == "dashboard_estado_em"


# --------------------------------------------------------------------------
# _encaminhadas_para_puma_agora
# --------------------------------------------------------------------------

def test_encaminhadas_para_puma_agora_exclui_concluidas_pela_puma(monkeypatch):
    cliente = _ClienteFalso(
        retornos={
            "tratativas": [[{"id": "a"}, {"id": "b"}]],
            "puma_encaminhamentos": [[{"tratativa_id": "a"}]],
        }
    )
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    assert mao._encaminhadas_para_puma_agora() == 1


def test_encaminhadas_para_puma_agora_sem_nenhuma_encaminhada(monkeypatch):
    cliente = _ClienteFalso(retornos={"tratativas": [[]]})
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    assert mao._encaminhadas_para_puma_agora() == 0


# --------------------------------------------------------------------------
# _pendentes_por_cidade
# --------------------------------------------------------------------------

def test_pendentes_por_cidade_agrupa_e_ordena_alfabetico(monkeypatch):
    cliente = _ClienteFalso(
        retornos={"tratativas": [[{"cidade": "Recife"}, {"cidade": ""}, {"cidade": "Recife"}, {"cidade": None}]]}
    )
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    assert mao._pendentes_por_cidade() == [
        {"cidade": "Recife", "quantidade": 2},
        {"cidade": "Sem cidade cadastrada", "quantidade": 2},
    ]


# --------------------------------------------------------------------------
# _tendencia_diaria_30_dias
# --------------------------------------------------------------------------

def test_tendencia_diaria_30_dias_usa_janela_fixa_de_30_dias(monkeypatch):
    cliente = _ClienteFalso(rpc_retorno=[{"dia": "2026-08-01", "disparos": 1, "retornados": 0}])
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    resultado = mao._tendencia_diaria_30_dias()

    assert resultado == [{"dia": "2026-08-01", "disparos": 1, "retornados": 0}]
    nome_rpc, params = cliente.chamadas_rpc[0]
    assert nome_rpc == "dashboard_serie_diaria"
    desde = datetime.fromisoformat(params["p_desde"].replace("Z", "+00:00"))
    ate = datetime.fromisoformat(params["p_ate"].replace("Z", "+00:00"))
    assert (ate.date() - desde.date()).days == 29


# --------------------------------------------------------------------------
# _estado_por_origem_agora
# --------------------------------------------------------------------------

def test_estado_por_origem_agora_agrupa_por_origem_e_bucket(monkeypatch):
    cliente = _ClienteFalso(
        rpc_retorno=[
            {"origem": "instalacao", "bucket": "pendente", "quantidade": 3},
            {"origem": "instalacao", "bucket": "concluido", "quantidade": 5},
            {"origem": "manutencao", "bucket": "em_andamento", "quantidade": 2},
        ]
    )
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    resultado = mao._estado_por_origem_agora()

    assert resultado["instalacao"] == {"pendente": 3, "em_andamento": 0, "concluido": 5}
    assert resultado["manutencao"] == {"pendente": 0, "em_andamento": 2, "concluido": 0}
    assert resultado["remocao"] == {"pendente": 0, "em_andamento": 0, "concluido": 0}
    assert cliente.chamadas_rpc[0][0] == "dashboard_estado_em"


# --------------------------------------------------------------------------
# _distribuicao_urgencia_agora
# --------------------------------------------------------------------------

def test_distribuicao_urgencia_agora_conta_por_nivel_ignorando_sem_nivel(monkeypatch):
    cliente = _ClienteFalso(
        retornos={
            "tratativas": [[
                {"id": "a", "codigo_regra": "REGRA_1"},
                {"id": "b", "codigo_regra": "REGRA_2"},
                {"id": "c", "codigo_regra": "REGRA_SEM_NIVEL"},
            ]],
            "puma_encaminhamentos": [[]],
            "rule_templates": [[
                {"codigo_regra": "REGRA_1", "nivel_urgencia": 5},
                {"codigo_regra": "REGRA_2", "nivel_urgencia": 5},
                {"codigo_regra": "REGRA_SEM_NIVEL", "nivel_urgencia": None},
            ]],
        }
    )
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    assert mao._distribuicao_urgencia_agora() == {5: 2}


def test_distribuicao_urgencia_agora_exclui_concluidas_pela_puma(monkeypatch):
    cliente = _ClienteFalso(
        retornos={
            "tratativas": [[{"id": "a", "codigo_regra": "REGRA_1"}]],
            "puma_encaminhamentos": [[{"tratativa_id": "a"}]],
            "rule_templates": [[{"codigo_regra": "REGRA_1", "nivel_urgencia": 5}]],
        }
    )
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    assert mao._distribuicao_urgencia_agora() == {}


# --------------------------------------------------------------------------
# _evolucao_backlog_acumulado
# --------------------------------------------------------------------------

def test_evolucao_backlog_acumulado_calcula_saldo_e_total_acumulado(monkeypatch):
    cliente = _ClienteFalso(
        retornos={
            "tratativas": [
                [{"created_at": "2026-08-01T10:00:00"}],  # 1ª query: primeira linha (order+limit)
                [
                    {"created_at": "2026-08-01T10:00:00"},
                    {"created_at": "2026-08-01T11:00:00"},
                    {"created_at": "2026-08-02T09:00:00"},
                ],  # 2ª query: todas as criadas
                [{"finalizado_em": "2026-08-02T12:00:00"}],  # 3ª query: finalizadas
            ],
            "puma_encaminhamentos": [[]],  # concluídas pela Puma (nenhuma)
        }
    )
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    resultado = mao._evolucao_backlog_acumulado()
    por_dia = {ponto["dia"]: ponto for ponto in resultado}

    assert resultado[0] == {"dia": "2026-08-01", "abertas": 2, "concluidas": 0}
    assert por_dia["2026-08-02"] == {"dia": "2026-08-02", "abertas": 2, "concluidas": 1}
    hoje = datetime.now(timezone.utc).date().isoformat()
    assert resultado[-1]["dia"] == hoje


def test_evolucao_backlog_acumulado_sem_tratativas_devolve_lista_vazia(monkeypatch):
    cliente = _ClienteFalso(retornos={"tratativas": [[]]})
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    assert mao._evolucao_backlog_acumulado() == []


# --------------------------------------------------------------------------
# _pendentes_por_tipo_agora
# --------------------------------------------------------------------------

def test_pendentes_por_tipo_agora_reaproveita_estado_por_origem(monkeypatch):
    cliente = _ClienteFalso(
        rpc_retorno=[
            {"origem": "instalacao", "bucket": "pendente", "quantidade": 4},
            {"origem": "remocao", "bucket": "pendente", "quantidade": 1},
        ]
    )
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    assert mao._pendentes_por_tipo_agora() == {"instalacao": 4, "remocao": 1, "manutencao": 0}


# --------------------------------------------------------------------------
# montar_metricas_admin_operador — só inclui o que está visivel_operador=true
# --------------------------------------------------------------------------

def test_montar_metricas_admin_operador_so_inclui_visiveis(monkeypatch):
    cliente = _ClienteFalso(
        rpc_retorno=[{"origem": "manutencao", "bucket": "pendente", "quantidade": 7}],
        retornos={
            "dashboard_metricas_cliente": [
                [
                    {"chave": "pendencias_em_aberto", "visivel_operador": True},
                    {"chave": "encaminhadas_puma", "visivel_operador": False},
                    {"chave": "pendentes_por_cidade", "visivel_operador": True},
                ]
            ],
            "tratativas": [[{"cidade": "Olinda"}]],
        },
    )
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    resultado = mao.montar_metricas_admin_operador()

    assert resultado == {
        "metricas_simples": {"pendencias_em_aberto": 7},
        "metricas_lista": {"pendentes_por_cidade": [{"cidade": "Olinda", "quantidade": 1}]},
        "metricas_grafico": {},
    }


def test_montar_metricas_admin_operador_nenhuma_visivel(monkeypatch):
    cliente = _ClienteFalso(retornos={"dashboard_metricas_cliente": [[]]})
    monkeypatch.setattr(mao, "get_client", lambda: cliente)

    assert mao.montar_metricas_admin_operador() == {
        "metricas_simples": {}, "metricas_lista": {}, "metricas_grafico": {},
    }
