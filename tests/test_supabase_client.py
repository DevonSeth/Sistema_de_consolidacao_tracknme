import pytest

from integrations import supabase_client as sc


class _Resposta:
    def __init__(self, data):
        self.data = data


class _QueryFalsa:
    """Substitui a query builder do supabase-py — só encadeia (retorna
    self) até `.execute()`, que resolve via a fila por tabela do
    `_ClienteFalso` (ver docstring dele)."""

    def __init__(self, tabela, cliente):
        self.tabela = tabela
        self.cliente = cliente
        self.filtros = []
        self._payload = None

    def select(self, *a, **k):
        return self

    def eq(self, campo, valor):
        self.filtros.append(("eq", campo, valor))
        return self

    def order(self, *a, **k):
        self.filtros.append(("order", a, k))
        return self

    def limit(self, *a, **k):
        self.filtros.append(("limit", a, k))
        return self

    def in_(self, campo, valores):
        self.filtros.append(("in", campo, list(valores)))
        return self

    def single(self):
        return self

    @property
    def not_(self):
        return _NotFalso(self)

    def insert(self, payload):
        self._payload = payload
        self.cliente.chamadas.append(("insert", self.tabela, payload))
        return self

    def update(self, payload):
        self._payload = payload
        self.cliente.chamadas.append(("update", self.tabela, payload, list(self.filtros)))
        return self

    def execute(self):
        if self._payload is None:
            self.cliente.chamadas.append(("select", self.tabela, list(self.filtros)))
        fila = self.cliente.retornos.get(self.tabela)
        if fila:
            return _Resposta(fila.pop(0))
        return _Resposta([])


class _NotFalso:
    """Fake de `.not_` — só cobre `.in_()`, único uso hoje
    (`contar_pendencias_por_origem`). Encadeia de volta pra `_QueryFalsa`."""

    def __init__(self, query):
        self.query = query

    def in_(self, campo, valores):
        self.query.filtros.append(("not_in", campo, list(valores)))
        return self.query


class _ClienteFalso:
    """Fake do `supabase.Client` — `retornos` é `{tabela: [dados1, dados2,
    ...]}`, consumido em FILA (1º `.execute()` naquela tabela recebe
    `dados1`, o 2º recebe `dados2`, etc; sem entrada = `[]`). `chamadas`
    registra toda `insert`/`update` pra assert nos testes."""

    def __init__(self, retornos=None):
        self.chamadas = []
        self.retornos = {tabela: list(dados) for tabela, dados in (retornos or {}).items()}

    def table(self, nome):
        return _QueryFalsa(nome, self)


def _chamadas_insert(cliente, tabela):
    return [c[2] for c in cliente.chamadas if c[0] == "insert" and c[1] == tabela]


def _chamadas_update(cliente, tabela):
    return [c[2] for c in cliente.chamadas if c[0] == "update" and c[1] == tabela]


# --------------------------------------------------------------------------
# upsert_tratativa — genesis do histórico só no caminho de INSERT
# --------------------------------------------------------------------------

def test_upsert_tratativa_insert_grava_historico_genesis(monkeypatch):
    cliente = _ClienteFalso(retornos={"tratativas": [[], [{"id": "nova-id"}]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_tratativa({"chave_unica": "chave-1", "origem": "manutencao"})

    historico = _chamadas_insert(cliente, "historico_status_tratativa")
    assert historico == [{"tratativa_id": "nova-id", "status_novo": sc.STATUS_PENDENTE}]


def test_upsert_tratativa_update_nao_grava_historico(monkeypatch):
    cliente = _ClienteFalso(retornos={"tratativas": [[{"id": "existente-id"}]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_tratativa({"chave_unica": "chave-1", "origem": "manutencao"})

    assert _chamadas_insert(cliente, "historico_status_tratativa") == []


# --------------------------------------------------------------------------
# atualizar_apos_envio
# --------------------------------------------------------------------------

def test_atualizar_apos_envio_grava_historico(monkeypatch):
    cliente = _ClienteFalso(retornos={"tratativas": [[{"tentativas": 0}]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.atualizar_apos_envio("trat-1", atendimento_id=1, mensagem_id=2, status=sc.STATUS_AGUARDANDO_RESPOSTA)

    historico = _chamadas_insert(cliente, "historico_status_tratativa")
    assert historico == [{"tratativa_id": "trat-1", "status_novo": sc.STATUS_AGUARDANDO_RESPOSTA}]


# --------------------------------------------------------------------------
# marcar_aguardando_ligacao
# --------------------------------------------------------------------------

def test_marcar_aguardando_ligacao_grava_historico(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.marcar_aguardando_ligacao("trat-1")

    historico = _chamadas_insert(cliente, "historico_status_tratativa")
    assert historico == [{"tratativa_id": "trat-1", "status_novo": sc.STATUS_AGUARDANDO_LIGACAO}]


# --------------------------------------------------------------------------
# registrar_ligacao
# --------------------------------------------------------------------------

def test_registrar_ligacao_conseguiu_agendar_finaliza_com_historico_e_finalizado_em(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.registrar_ligacao("trat-1", {"retornou": True, "conseguiu_agendar": True})

    updates = _chamadas_update(cliente, "tratativas")
    assert len(updates) == 1
    assert updates[0]["status"] == sc.STATUS_FINALIZADO
    assert "finalizado_em" in updates[0]
    assert updates[0]["finalizado_em"] == updates[0]["updated_at"]

    historico = _chamadas_insert(cliente, "historico_status_tratativa")
    assert historico == [{"tratativa_id": "trat-1", "status_novo": sc.STATUS_FINALIZADO}]


def test_registrar_ligacao_nao_conseguiu_agendar_nao_toca_status(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.registrar_ligacao("trat-1", {"retornou": True, "conseguiu_agendar": False})

    assert _chamadas_update(cliente, "tratativas") == []
    assert _chamadas_insert(cliente, "historico_status_tratativa") == []


# --------------------------------------------------------------------------
# encaminhar_puma
# --------------------------------------------------------------------------

def test_encaminhar_puma_grava_genesis_puma_e_historico_tratativa(monkeypatch):
    cliente = _ClienteFalso(retornos={"puma_encaminhamentos": [[{"id": "puma-1"}]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.encaminhar_puma("trat-1", motivo="sem sucesso em agendar")

    historico_puma = _chamadas_insert(cliente, "historico_status_puma")
    assert historico_puma == [
        {
            "puma_encaminhamento_id": "puma-1",
            "tratativa_id": "trat-1",
            "status_novo": sc.PUMA_STATUS_AGUARDANDO_ACAO,
        }
    ]

    historico_trat = _chamadas_insert(cliente, "historico_status_tratativa")
    assert historico_trat == [{"tratativa_id": "trat-1", "status_novo": sc.STATUS_ENCAMINHADO_PUMA}]


# --------------------------------------------------------------------------
# sincronizar_status_puma
# --------------------------------------------------------------------------

def test_sincronizar_status_puma_concluido_seta_concluido_em_e_historico(monkeypatch):
    cliente = _ClienteFalso(retornos={"puma_encaminhamentos": [[{"id": "puma-1"}]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.sincronizar_status_puma("trat-1", sc.PUMA_STATUS_CONCLUIDO)

    updates = _chamadas_update(cliente, "puma_encaminhamentos")
    assert len(updates) == 1
    assert updates[0]["status"] == sc.PUMA_STATUS_CONCLUIDO
    assert "concluido_em" in updates[0]

    historico = _chamadas_insert(cliente, "historico_status_puma")
    assert historico == [
        {"puma_encaminhamento_id": "puma-1", "tratativa_id": "trat-1", "status_novo": sc.PUMA_STATUS_CONCLUIDO}
    ]


def test_sincronizar_status_puma_em_andamento_nao_seta_concluido_em(monkeypatch):
    cliente = _ClienteFalso(retornos={"puma_encaminhamentos": [[{"id": "puma-1"}]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.sincronizar_status_puma("trat-1", sc.PUMA_STATUS_EM_ANDAMENTO)

    updates = _chamadas_update(cliente, "puma_encaminhamentos")
    assert "concluido_em" not in updates[0]


def test_sincronizar_status_puma_sem_encaminhamento_levanta_erro(monkeypatch):
    cliente = _ClienteFalso(retornos={"puma_encaminhamentos": [[]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    with pytest.raises(ValueError):
        sc.sincronizar_status_puma("trat-1", sc.PUMA_STATUS_CONCLUIDO)


# --------------------------------------------------------------------------
# sincronizar_observacao_puma
# --------------------------------------------------------------------------

def test_sincronizar_observacao_puma_atualiza_o_encaminhamento_mais_recente(monkeypatch):
    cliente = _ClienteFalso(retornos={"puma_encaminhamentos": [[{"id": "puma-1"}]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.sincronizar_observacao_puma("trat-1", "Resolvido no local.")

    updates = _chamadas_update(cliente, "puma_encaminhamentos")
    assert updates == [{"observacao_puma": "Resolvido no local."}]


def test_sincronizar_observacao_puma_aceita_string_vazia_pra_apagar_anotacao(monkeypatch):
    cliente = _ClienteFalso(retornos={"puma_encaminhamentos": [[{"id": "puma-1"}]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.sincronizar_observacao_puma("trat-1", "")

    updates = _chamadas_update(cliente, "puma_encaminhamentos")
    assert updates == [{"observacao_puma": ""}]


def test_sincronizar_observacao_puma_sem_encaminhamento_levanta_erro(monkeypatch):
    cliente = _ClienteFalso(retornos={"puma_encaminhamentos": [[]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    with pytest.raises(ValueError):
        sc.sincronizar_observacao_puma("trat-1", "texto")


# --------------------------------------------------------------------------
# sincronizar_campos_atendente
# --------------------------------------------------------------------------

def test_sincronizar_campos_atendente_sem_status_nao_grava_historico(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.sincronizar_campos_atendente("chave-1", {"Selecionado": True})

    assert _chamadas_insert(cliente, "historico_status_tratativa") == []


def test_sincronizar_campos_atendente_finalizado_seta_finalizado_em_e_historico(monkeypatch):
    # 2 entradas na fila de "tratativas": a 1ª é consumida pelo próprio
    # `.update()` (retorno não usado), a 2ª pelo `buscar_tratativa_por_chave`
    # (feito DEPOIS do update, ver docstring da função).
    cliente = _ClienteFalso(
        retornos={"tratativas": [[], [{"id": "trat-1", "chave_unica": "chave-1"}]]}
    )
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.sincronizar_campos_atendente("chave-1", {"status": sc.STATUS_FINALIZADO})

    updates = _chamadas_update(cliente, "tratativas")
    assert len(updates) == 1
    assert "finalizado_em" in updates[0]

    historico = _chamadas_insert(cliente, "historico_status_tratativa")
    assert historico == [{"tratativa_id": "trat-1", "status_novo": sc.STATUS_FINALIZADO}]


def test_sincronizar_campos_atendente_respeita_finalizado_em_ja_presente(monkeypatch):
    cliente = _ClienteFalso(retornos={"tratativas": [[{"id": "trat-1", "chave_unica": "chave-1"}]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.sincronizar_campos_atendente(
        "chave-1", {"status": sc.STATUS_FINALIZADO, "finalizado_em": "2026-01-01T00:00:00+00:00"}
    )

    updates = _chamadas_update(cliente, "tratativas")
    assert updates[0]["finalizado_em"] == "2026-01-01T00:00:00+00:00"


def test_sincronizar_campos_atendente_chave_inexistente_nao_grava_historico(monkeypatch):
    """A busca por `buscar_tratativa_por_chave` depois do update não acha
    nada (chave não existe) — mesma guarda de sempre, sem levantar erro."""
    cliente = _ClienteFalso(retornos={"tratativas": [[]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.sincronizar_campos_atendente("chave-fantasma", {"status": sc.STATUS_FINALIZADO})

    assert _chamadas_insert(cliente, "historico_status_tratativa") == []


# --------------------------------------------------------------------------
# buscar_situacao_manual_atual_por_chaves
# --------------------------------------------------------------------------

def test_buscar_situacao_manual_atual_por_chaves_vazio_nao_consulta(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    resultado = sc.buscar_situacao_manual_atual_por_chaves([])

    assert resultado == {}
    assert cliente.chamadas == []


def test_buscar_situacao_manual_atual_por_chaves_devolve_mapa(monkeypatch):
    cliente = _ClienteFalso(
        retornos={
            "tratativas": [
                [
                    {"chave_unica": "chave-1", "situacao_manual": "Agendado"},
                    {"chave_unica": "chave-2", "situacao_manual": None},
                ]
            ]
        }
    )
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    resultado = sc.buscar_situacao_manual_atual_por_chaves(["chave-1", "chave-2"])

    assert resultado == {"chave-1": "Agendado", "chave-2": ""}


# --------------------------------------------------------------------------
# contar_pendencias_por_origem
# --------------------------------------------------------------------------

def test_contar_pendencias_por_origem_agrupa_por_origem_conhecida(monkeypatch):
    cliente = _ClienteFalso(
        retornos={
            "tratativas": [
                [
                    {"origem": "manutencao"},
                    {"origem": "manutencao"},
                    {"origem": "instalacao"},
                    {"origem": "remocao"},
                ]
            ]
        }
    )
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    contagem = sc.contar_pendencias_por_origem()

    assert contagem == {"manutencao": 2, "instalacao": 1, "remocao": 1}


def test_contar_pendencias_por_origem_ignora_origem_desconhecida(monkeypatch):
    cliente = _ClienteFalso(
        retornos={"tratativas": [[{"origem": "manutencao"}, {"origem": "algo_novo"}]]}
    )
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    contagem = sc.contar_pendencias_por_origem()

    assert contagem == {"manutencao": 1, "instalacao": 0, "remocao": 0}


def test_contar_pendencias_por_origem_filtra_status_fora_de_pendencia(monkeypatch):
    cliente = _ClienteFalso(retornos={"tratativas": [[]]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.contar_pendencias_por_origem()

    selects = [c[2] for c in cliente.chamadas if c[0] == "select" and c[1] == "tratativas"]
    assert selects == [[("not_in", "status", sc._STATUS_FORA_DE_PENDENCIA)]]


# --------------------------------------------------------------------------
# buscar_tratativas_abertas_para_dashboard_operador
# --------------------------------------------------------------------------

def test_buscar_tratativas_abertas_para_dashboard_operador_devolve_linhas_e_filtra_status(monkeypatch):
    linhas_esperadas = [{"chave_unica": "chave-1", "origem": "manutencao", "status": "pendente"}]
    cliente = _ClienteFalso(retornos={"tratativas": [linhas_esperadas]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    resultado = sc.buscar_tratativas_abertas_para_dashboard_operador()

    assert resultado == linhas_esperadas
    selects = [c[2] for c in cliente.chamadas if c[0] == "select" and c[1] == "tratativas"]
    assert selects == [[("not_in", "status", sc._STATUS_FORA_DE_PENDENCIA)]]


# --------------------------------------------------------------------------
# Observabilidade (fatia 1) — registrar_log_execucao / buscar_execucao_lock_
# atual / buscar_ultimas_execucoes
# --------------------------------------------------------------------------

def test_registrar_log_execucao_grava_duracao_derivada(monkeypatch):
    from datetime import datetime, timezone

    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    iniciado_em = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)
    finalizado_em = datetime(2026, 8, 14, 10, 0, 2, 500000, tzinfo=timezone.utc)

    sc.registrar_log_execucao(
        execucao_id="exec-1",
        etapa_id="baixar_relatorios",
        maquina="PC-OPERADOR-1",
        iniciado_em=iniciado_em,
        finalizado_em=finalizado_em,
        sucesso=True,
        motivo_parada=None,
        mensagem="",
    )

    (payload,) = _chamadas_insert(cliente, "log_execucoes")
    assert payload["execucao_id"] == "exec-1"
    assert payload["etapa_id"] == "baixar_relatorios"
    assert payload["maquina"] == "PC-OPERADOR-1"
    assert payload["iniciado_em"] == iniciado_em.isoformat()
    assert payload["finalizado_em"] == finalizado_em.isoformat()
    assert payload["duracao_ms"] == 2500
    assert payload["sucesso"] is True
    assert payload["motivo_parada"] is None


def test_buscar_execucao_lock_atual_devolve_a_linha_seed(monkeypatch):
    linha = {"em_execucao": True, "maquina": "PC-OPERADOR-1", "iniciado_em": "2026-08-14T10:00:00+00:00"}
    cliente = _ClienteFalso(retornos={"execucao_lock": [linha]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    assert sc.buscar_execucao_lock_atual() == linha


def test_buscar_ultimas_execucoes_agrupa_por_etapa_e_respeita_limite(monkeypatch):
    linhas = [
        {"etapa_id": "baixar_relatorios", "iniciado_em": "2026-08-14T10:00:00+00:00", "finalizado_em": "", "duracao_ms": 100, "sucesso": True, "motivo_parada": None, "mensagem": ""},
        {"etapa_id": "enriquecimento_sga", "iniciado_em": "2026-08-14T10:05:00+00:00", "finalizado_em": "", "duracao_ms": 900000, "sucesso": True, "motivo_parada": None, "mensagem": ""},
        {"etapa_id": "baixar_relatorios", "iniciado_em": "2026-08-13T10:00:00+00:00", "finalizado_em": "", "duracao_ms": 110, "sucesso": True, "motivo_parada": None, "mensagem": ""},
        {"etapa_id": "baixar_relatorios", "iniciado_em": "2026-08-12T10:00:00+00:00", "finalizado_em": "", "duracao_ms": 120, "sucesso": True, "motivo_parada": None, "mensagem": ""},
    ]
    cliente = _ClienteFalso(retornos={"log_execucoes": [linhas]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    resultado = sc.buscar_ultimas_execucoes(limite_por_etapa=2)

    assert [l["iniciado_em"] for l in resultado["baixar_relatorios"]] == [
        "2026-08-14T10:00:00+00:00", "2026-08-13T10:00:00+00:00",
    ]
    assert len(resultado["enriquecimento_sga"]) == 1
