import json
from pathlib import Path

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

    def range(self, inicio, fim):
        self.filtros.append(("range", inicio, fim))
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

    def upsert(self, payload, on_conflict=None):
        self._payload = payload
        self.cliente.chamadas.append(("upsert", self.tabela, payload, on_conflict))
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


def _chamadas_upsert(cliente, tabela):
    return [c[2] for c in cliente.chamadas if c[0] == "upsert" and c[1] == tabela]


# --------------------------------------------------------------------------
# upsert_tratativas_em_lote (achado 2026-08-20: substitui upsert_tratativa,
# até 3 idas ao Supabase POR linha, pelo mesmo padrão em lote já usado em
# upsert_situacoes_veiculo_sga_em_lote) — genesis do histórico só pras
# tratativas novas, status preservado explicitamente pras existentes.
# --------------------------------------------------------------------------

def test_upsert_tratativas_em_lote_faz_3_chamadas_no_total_nao_por_linha(monkeypatch):
    cliente = _ClienteFalso(retornos={
        "tratativas": [
            [{"chave_unica": "chave-existente", "status": "aguardando_resposta"}],
            [
                {"id": "id-existente", "chave_unica": "chave-existente", "status": "aguardando_resposta"},
                {"id": "id-nova", "chave_unica": "chave-nova", "status": sc.STATUS_PENDENTE},
            ],
        ],
    })
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_tratativas_em_lote([
        {"chave_unica": "chave-existente", "origem": "manutencao"},
        {"chave_unica": "chave-nova", "origem": "manutencao"},
    ])

    selects = [c for c in cliente.chamadas if c[0] == "select"]
    upserts = _chamadas_upsert(cliente, "tratativas")
    genesis = _chamadas_insert(cliente, "historico_status_tratativa")
    assert len(selects) == 1
    assert len(upserts) == 1
    assert len(genesis) == 1


def test_upsert_tratativas_em_lote_linha_nova_recebe_status_pendente_e_genesis(monkeypatch):
    cliente = _ClienteFalso(retornos={
        "tratativas": [[], [{"id": "id-nova", "chave_unica": "chave-nova", "status": sc.STATUS_PENDENTE}]],
    })
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_tratativas_em_lote([{"chave_unica": "chave-nova", "origem": "manutencao"}])

    upserts = _chamadas_upsert(cliente, "tratativas")
    assert upserts[0][0]["status"] == sc.STATUS_PENDENTE
    genesis = _chamadas_insert(cliente, "historico_status_tratativa")
    assert genesis == [[{"tratativa_id": "id-nova", "status_novo": sc.STATUS_PENDENTE}]]


def test_upsert_tratativas_em_lote_linha_existente_preserva_status_sem_genesis(monkeypatch):
    cliente = _ClienteFalso(retornos={
        "tratativas": [
            [{"chave_unica": "chave-1", "status": "aguardando_resposta"}],
            [{"id": "id-1", "chave_unica": "chave-1", "status": "aguardando_resposta"}],
        ],
    })
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_tratativas_em_lote([{"chave_unica": "chave-1", "origem": "manutencao"}])

    upserts = _chamadas_upsert(cliente, "tratativas")
    assert upserts[0][0]["status"] == "aguardando_resposta"
    assert _chamadas_insert(cliente, "historico_status_tratativa") == []


def test_upsert_tratativas_em_lote_misto_cada_linha_com_status_certo(monkeypatch):
    """Prova a correção do achado de heterogeneidade: um upsert em lote
    do PostgREST aplicaria a coluna `status` (presente só na linha nova)
    a TODAS as linhas do lote se ela fosse omitida na existente -- aqui
    ambas as linhas do payload de upsert precisam trazer `status`
    explícito, cada uma com o valor certo."""
    cliente = _ClienteFalso(retornos={
        "tratativas": [
            [{"chave_unica": "chave-existente", "status": "respondido"}],
            [
                {"id": "id-existente", "chave_unica": "chave-existente", "status": "respondido"},
                {"id": "id-nova", "chave_unica": "chave-nova", "status": sc.STATUS_PENDENTE},
            ],
        ],
    })
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_tratativas_em_lote([
        {"chave_unica": "chave-existente", "origem": "manutencao"},
        {"chave_unica": "chave-nova", "origem": "manutencao"},
    ])

    payload_por_chave = {p["chave_unica"]: p for p in _chamadas_upsert(cliente, "tratativas")[0]}
    assert payload_por_chave["chave-existente"]["status"] == "respondido"
    assert payload_por_chave["chave-nova"]["status"] == sc.STATUS_PENDENTE


def test_upsert_tratativas_em_lote_divide_em_mini_lotes(monkeypatch):
    """Achado 2026-08-20 (mesmo dia, depois da fatia anterior): mandar a
    fila INTEIRA numa requisição só devolveu um 400 cru do gateway
    ("JSON could not be generated") -- corrigido dividindo em mini-lotes
    de tamanho fixo. Aqui o tamanho é reduzido pra 2 (via monkeypatch) pra
    testar o chunking com poucos itens: 5 linhas / lote de 2 -> 3 chamadas
    de select e 3 de upsert (2+2+1), não 1 nem 5."""
    monkeypatch.setattr(sc, "_TAMANHO_LOTE_TRATATIVAS", 2)
    chamadas_sleep = []
    monkeypatch.setattr(sc, "sleep", lambda segundos: chamadas_sleep.append(segundos))
    cliente = _ClienteFalso(retornos={
        "tratativas": [
            [], [], [],  # 3 selects de "existentes" (nenhuma existe)
            [
                {"id": "id-1", "chave_unica": "chave-1", "status": sc.STATUS_PENDENTE},
                {"id": "id-2", "chave_unica": "chave-2", "status": sc.STATUS_PENDENTE},
            ],
            [
                {"id": "id-3", "chave_unica": "chave-3", "status": sc.STATUS_PENDENTE},
                {"id": "id-4", "chave_unica": "chave-4", "status": sc.STATUS_PENDENTE},
            ],
            [{"id": "id-5", "chave_unica": "chave-5", "status": sc.STATUS_PENDENTE}],
        ],
    })
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_tratativas_em_lote([
        {"chave_unica": f"chave-{i}", "origem": "manutencao"} for i in range(1, 6)
    ])

    selects = [c for c in cliente.chamadas if c[0] == "select"]
    upserts = _chamadas_upsert(cliente, "tratativas")
    genesis = _chamadas_insert(cliente, "historico_status_tratativa")
    assert len(selects) == 3
    assert [len(u) for u in upserts] == [2, 2, 1]
    assert len(genesis) == 3
    assert sum(len(g) for g in genesis) == 5
    # achado 2026-08-21: 1 sleep por chamada de rede (3 selects + 3 upserts
    # + 3 genesis) -- espaça as chamadas sequenciais pra não parecer rajada
    # pro anti-bot do Cloudflare.
    assert len(chamadas_sleep) == 9
    assert all(s == sc.ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS for s in chamadas_sleep)


def test_upsert_tratativas_em_lote_lista_vazia_nao_chama_supabase(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_tratativas_em_lote([])

    assert cliente.chamadas == []


def test_upsert_tratativas_em_lote_exige_chave_unica_em_cada_item(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    with pytest.raises(ValueError):
        sc.upsert_tratativas_em_lote([{"origem": "manutencao"}])


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


def test_buscar_situacao_manual_atual_por_chaves_divide_em_mini_lotes(monkeypatch):
    """Achado 2026-08-21: `chaves` aqui é toda a aba Tratativas atual — sem
    chunking, um `.in_()` via GET com muitas chaves pode gerar uma URL
    grande o bastante pro Cloudflare rejeitar antes do Postgres (mesma
    causa raiz de `buscar_estado_disparo_por_chaves`)."""
    monkeypatch.setattr(sc, "_TAMANHO_LOTE_TRATATIVAS", 2)
    chamadas_sleep = []
    monkeypatch.setattr(sc, "sleep", lambda s: chamadas_sleep.append(s))
    cliente = _ClienteFalso(retornos={
        "tratativas": [
            [{"chave_unica": "chave-1", "situacao_manual": "Agendado"}, {"chave_unica": "chave-2", "situacao_manual": None}],
            [{"chave_unica": "chave-3", "situacao_manual": "Concluído"}],
        ],
    })
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    resultado = sc.buscar_situacao_manual_atual_por_chaves(["chave-1", "chave-2", "chave-3"])

    selects = [c for c in cliente.chamadas if c[0] == "select"]
    assert len(selects) == 2
    assert resultado == {"chave-1": "Agendado", "chave-2": "", "chave-3": "Concluído"}
    assert len(chamadas_sleep) == 2


# --------------------------------------------------------------------------
# buscar_estado_disparo_por_chaves
# --------------------------------------------------------------------------

def test_buscar_estado_disparo_por_chaves_vazio_nao_consulta(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    assert sc.buscar_estado_disparo_por_chaves([]) == {}
    assert cliente.chamadas == []


def test_buscar_estado_disparo_por_chaves_devolve_mapa(monkeypatch):
    cliente = _ClienteFalso(retornos={
        "tratativas": [[{"chave_unica": "chave-1", "status": sc.STATUS_PENDENTE}]],
    })
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    resultado = sc.buscar_estado_disparo_por_chaves(["chave-1"])

    assert resultado == {"chave-1": {"chave_unica": "chave-1", "status": sc.STATUS_PENDENTE}}


def test_buscar_estado_disparo_por_chaves_divide_em_mini_lotes(monkeypatch):
    """Achado 2026-08-21, causa raiz CONFIRMADA em produção pelo log de
    diagnóstico HTTP (server: cloudflare, URL de 35KB): esta era a única
    chamada de rede da Fase E sem chunking -- com a fila real (~1.900
    itens), a URL do `.in_("chave_unica", chaves)` estourava o limite do
    gateway Cloudflare, e nenhuma quantidade de retry resolvia (a URL
    nunca mudava de tamanho entre tentativas)."""
    monkeypatch.setattr(sc, "_TAMANHO_LOTE_TRATATIVAS", 2)
    chamadas_sleep = []
    monkeypatch.setattr(sc, "sleep", lambda s: chamadas_sleep.append(s))
    cliente = _ClienteFalso(retornos={
        "tratativas": [
            [{"chave_unica": "chave-1", "status": sc.STATUS_PENDENTE}, {"chave_unica": "chave-2", "status": sc.STATUS_RESPONDIDO}],
            [{"chave_unica": "chave-3", "status": sc.STATUS_FINALIZADO}],
        ],
    })
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    resultado = sc.buscar_estado_disparo_por_chaves(["chave-1", "chave-2", "chave-3"])

    selects = [c for c in cliente.chamadas if c[0] == "select"]
    assert len(selects) == 2
    assert set(resultado.keys()) == {"chave-1", "chave-2", "chave-3"}
    assert len(chamadas_sleep) == 2


def test_buscar_estado_disparo_por_chaves_anota_lote_no_erro(monkeypatch):
    class _ClienteQueExplode:
        def table(self, nome):
            raise RuntimeError("falha de rede simulada")

    monkeypatch.setattr(sc, "get_client", lambda: _ClienteQueExplode())

    with pytest.raises(RuntimeError) as excinfo:
        sc.buscar_estado_disparo_por_chaves(["chave-1"])

    assert any("buscar_estado_disparo_por_chaves" in nota for nota in excinfo.value.__notes__)


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
    assert selects == [[("not_in", "status", sc._STATUS_FORA_DE_PENDENCIA), ("range", 0, 999)]]


def test_contar_pendencias_por_origem_pagina_alem_de_1000_linhas(monkeypatch):
    """Achado 2026-08-21 (mesma causa raiz do dashboard do cliente):
    `select()` sem `.range()` trunca em 1.000 linhas silenciosamente —
    confere que uma 1ª página cheia (1.000) busca a página seguinte."""
    pagina_1 = [{"origem": "manutencao"} for _ in range(1000)]
    pagina_2 = [{"origem": "instalacao"}, {"origem": "instalacao"}]
    cliente = _ClienteFalso(retornos={"tratativas": [pagina_1, pagina_2]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    contagem = sc.contar_pendencias_por_origem()

    assert contagem == {"manutencao": 1000, "instalacao": 2, "remocao": 0}
    selects = [c[2] for c in cliente.chamadas if c[0] == "select" and c[1] == "tratativas"]
    assert [f[-1] for f in selects] == [("range", 0, 999), ("range", 1000, 1999)]


# --------------------------------------------------------------------------
# buscar_tratativas_abertas_no_motor (Bloco H)
# --------------------------------------------------------------------------

def test_buscar_tratativas_abertas_no_motor_filtra_status_e_pagina(monkeypatch):
    linhas_esperadas = [{"chave_unica": "chave-1", "status": "pendente", "rodadas_ausente_fila": 0}]
    cliente = _ClienteFalso(retornos={"tratativas": [linhas_esperadas]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    resultado = sc.buscar_tratativas_abertas_no_motor()

    assert resultado == linhas_esperadas
    selects = [c[2] for c in cliente.chamadas if c[0] == "select" and c[1] == "tratativas"]
    assert selects == [[("not_in", "status", sc._STATUS_FORA_DA_FILA_MOTOR), ("range", 0, 999)]]


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
    assert selects == [[("not_in", "status", sc._STATUS_FORA_DE_PENDENCIA), ("range", 0, 999)]]


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


# --------------------------------------------------------------------------
# buscar_situacoes_veiculo_sga_em_lote / upsert_situacoes_veiculo_sga_em_lote
# (achado 2026-08-17: substituem as versões 1-a-1 -- 1 leitura/gravação em
# lote em vez de N idas ao Supabase, o maior motivo das execuções de horas
# na Fase D em escala real)
# --------------------------------------------------------------------------

def test_buscar_situacoes_veiculo_sga_em_lote_indexa_por_chassi(monkeypatch):
    linhas = [
        {"chassi": "CHASSI-001", "status": "ATIVO"},
        {"chassi": "CHASSI-002", "status": "INATIVO"},
    ]
    cliente = _ClienteFalso(retornos={"situacao_veiculo_sga": [linhas]})
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    resultado = sc.buscar_situacoes_veiculo_sga_em_lote(["CHASSI-001", "CHASSI-002", "CHASSI-003"])

    assert resultado == {"CHASSI-001": linhas[0], "CHASSI-002": linhas[1]}
    chamadas_select = [c for c in cliente.chamadas if c[0] == "select"]
    assert chamadas_select[0][2] == [("in", "chassi", ["CHASSI-001", "CHASSI-002", "CHASSI-003"])]


def test_buscar_situacoes_veiculo_sga_em_lote_lista_vazia_nao_chama_supabase(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    assert sc.buscar_situacoes_veiculo_sga_em_lote([]) == {}
    assert cliente.chamadas == []


def test_buscar_situacoes_veiculo_sga_em_lote_divide_em_mini_lotes(monkeypatch):
    """Achado 2026-08-21: mesma causa raiz de `buscar_estado_disparo_por_
    chaves` -- na escala real (milhares de veículos), o `.in_(...)` via GET
    sem chunking pode gerar uma URL grande o bastante pro Cloudflare
    rejeitar. Nunca bateu aqui na prática porque o checkpoint de Fase D
    reduz a lista antes, mas o risco é o mesmo."""
    monkeypatch.setattr(sc, "_TAMANHO_LOTE_SGA", 2)
    chamadas_sleep = []
    monkeypatch.setattr(sc, "sleep", lambda s: chamadas_sleep.append(s))
    cliente = _ClienteFalso(retornos={
        "situacao_veiculo_sga": [
            [{"chassi": "CHASSI-001", "status": "ATIVO"}, {"chassi": "CHASSI-002", "status": "INATIVO"}],
            [{"chassi": "CHASSI-003", "status": "ATIVO"}],
        ],
    })
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    resultado = sc.buscar_situacoes_veiculo_sga_em_lote(["CHASSI-001", "CHASSI-002", "CHASSI-003"])

    selects = [c for c in cliente.chamadas if c[0] == "select"]
    assert len(selects) == 2
    assert set(resultado.keys()) == {"CHASSI-001", "CHASSI-002", "CHASSI-003"}
    assert len(chamadas_sleep) == 2


def test_upsert_situacoes_veiculo_sga_em_lote_faz_1_chamada_so(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_situacoes_veiculo_sga_em_lote([
        {"chassi": "CHASSI-001", "status": "ATIVO", "desde": None, "atualizado_em": None, "encontrado_via": "chassi"},
        {"chassi": "CHASSI-002", "status": "INATIVO", "desde": None, "atualizado_em": None, "encontrado_via": "placa"},
    ])

    upserts = _chamadas_upsert(cliente, "situacao_veiculo_sga")
    assert len(upserts) == 1
    assert [r["chassi"] for r in upserts[0]] == ["CHASSI-001", "CHASSI-002"]


def test_upsert_situacoes_veiculo_sga_em_lote_converte_datetime_pra_iso(monkeypatch):
    from datetime import datetime

    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)
    momento = datetime(2026, 8, 17, 9, 0, 0)

    sc.upsert_situacoes_veiculo_sga_em_lote([
        {"chassi": "CHASSI-001", "status": "ATIVO", "desde": momento, "atualizado_em": momento},
    ])

    upserts = _chamadas_upsert(cliente, "situacao_veiculo_sga")
    assert upserts[0][0]["desde"] == momento.isoformat()
    assert upserts[0][0]["atualizado_em"] == momento.isoformat()


def test_upsert_situacoes_veiculo_sga_em_lote_lista_vazia_nao_chama_supabase(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_situacoes_veiculo_sga_em_lote([])

    assert cliente.chamadas == []


def test_upsert_situacoes_veiculo_sga_em_lote_divide_em_mini_lotes(monkeypatch):
    """Achado 2026-08-21: mesmo motivo do chunking já aplicado a
    `upsert_tratativas_em_lote` -- mandar milhares de registros num único
    POST pode gerar um corpo grande o bastante pro Cloudflare rejeitar."""
    monkeypatch.setattr(sc, "_TAMANHO_LOTE_SGA", 2)
    chamadas_sleep = []
    monkeypatch.setattr(sc, "sleep", lambda s: chamadas_sleep.append(s))
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    sc.upsert_situacoes_veiculo_sga_em_lote([
        {"chassi": f"CHASSI-{i:03d}", "status": "ATIVO", "desde": None, "atualizado_em": None} for i in range(5)
    ])

    upserts = _chamadas_upsert(cliente, "situacao_veiculo_sga")
    assert [len(u) for u in upserts] == [2, 2, 1]
    assert len(chamadas_sleep) == 3


def test_upsert_situacoes_veiculo_sga_em_lote_exige_chassi_em_cada_registro(monkeypatch):
    cliente = _ClienteFalso()
    monkeypatch.setattr(sc, "get_client", lambda: cliente)

    with pytest.raises(ValueError):
        sc.upsert_situacoes_veiculo_sga_em_lote([{"status": "ATIVO"}])


def test_get_client_desabilita_http2(monkeypatch):
    """Achado 2026-08-21: HTTP/2 tem um bug documentado do httpcore onde o
    pool de conexões pode reusar uma conexão já terminada por GOAWAY do
    servidor -- get_client() força HTTP/1.1 pra eliminar essa classe de
    erro, não só retentar o sintoma."""
    monkeypatch.setattr(
        sc.manager,
        "carregar_config",
        lambda: {"supabase": {"url": "https://exemplo.supabase.co", "service_role_key": "chave-fake"}},
    )
    sc.get_client.cache_clear()
    try:
        cliente = sc.get_client()
        sessao = cliente.postgrest.session
        assert sessao._transport._pool._http2 is False
        assert sessao.timeout.read == 30
    finally:
        sc.get_client.cache_clear()


def _resposta_http_fake(status_code, headers=None, corpo=b"", metodo="POST", url="https://exemplo.supabase.co/rest/v1/tratativas"):
    import httpx
    requisicao = httpx.Request(metodo, url)
    return httpx.Response(status_code, headers=headers or {}, content=corpo, request=requisicao)


def test_registrar_resposta_de_erro_grava_diagnostico_completo(monkeypatch, tmp_path):
    """Achado 2026-08-21: postgrest-py descarta os headers da resposta HTTP
    ao montar APIError -- sem eles, uma rejeição do gateway (Cloudflare,
    identificável por cf-ray) e um erro real do Postgres ficam
    indistinguíveis na tela. O hook grava o necessário em arquivo local pra
    diagnóstico definitivo de QUALQUER chamada futura."""
    caminho = tmp_path / "diagnostico_http_supabase.log"
    monkeypatch.setattr(sc, "caminho_log_diagnostico_http", lambda: caminho)

    resposta = _resposta_http_fake(
        400,
        headers={"cf-ray": "abc123-GRU", "server": "cloudflare", "date": "Fri, 21 Aug 2026 12:00:00 GMT"},
        corpo=b"Bad Request",
    )
    sc._registrar_resposta_de_erro(resposta)

    linhas = caminho.read_text(encoding="utf-8").strip().splitlines()
    assert len(linhas) == 1
    registro = json.loads(linhas[0])
    assert registro["status_code"] == 400
    assert registro["method"] == "POST"
    assert registro["cf_ray"] == "abc123-GRU"
    assert registro["server"] == "cloudflare"
    assert registro["corpo"] == "Bad Request"


def test_registrar_resposta_de_erro_ignora_sucesso(monkeypatch, tmp_path):
    caminho = tmp_path / "diagnostico_http_supabase.log"
    monkeypatch.setattr(sc, "caminho_log_diagnostico_http", lambda: caminho)

    sc._registrar_resposta_de_erro(_resposta_http_fake(200, corpo=b"{}"))

    assert not caminho.exists()


def test_registrar_resposta_de_erro_nunca_derruba_a_chamada_real(monkeypatch, tmp_path):
    """Achado 2026-08-21: uma falha ao gravar o log de diagnóstico (disco
    cheio, pasta sem permissão) nunca pode virar uma exceção nova que
    mascara o erro HTTP real."""
    monkeypatch.setattr(sc, "caminho_log_diagnostico_http", lambda: tmp_path / "sem" / "pasta" / "valida" / "x.log")
    monkeypatch.setattr(Path, "mkdir", lambda self, *a, **k: (_ for _ in ()).throw(OSError("disco cheio")))

    sc._registrar_resposta_de_erro(_resposta_http_fake(400, corpo=b"Bad Request"))
