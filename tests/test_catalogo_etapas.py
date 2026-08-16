import socket

import pytest

from orchestrator import catalogo_etapas as cat
from orchestrator import pipeline


@pytest.fixture(autouse=True)
def _sem_log_execucoes_real(monkeypatch):
    """Toda chamada real ao Supabase precisa estar mockada em teste
    unitário (mesmo princípio já usado pra `adquirir_execucao_lock`/
    `liberar_execucao_lock`) — sem isso, qualquer teste que rode uma
    etapa de verdade escreveria uma linha real em `log_execucoes` na
    produção. Autouse: cobre todo teste do arquivo, mesmo os que não
    mencionam isso explicitamente. Testes que querem inspecionar o que
    foi gravado sobrescrevem com seu próprio `monkeypatch.setattr` no
    corpo do teste."""
    monkeypatch.setattr(cat.supabase_client, "registrar_log_execucao", lambda **kwargs: None)


def _resultado(etapa, sucesso=True, dados=None, mensagem="", aguardando_reconexao=None, cancelado=None):
    return pipeline.ResultadoEtapa(
        etapa, sucesso=sucesso, mensagem=mensagem, dados=dados or {},
        aguardando_reconexao=aguardando_reconexao, cancelado=cancelado,
    )


def _etapa_fake(id_, nome_funcao, **kwargs):
    return cat.EtapaCatalogo(id_, "X", id_, nome_funcao, **kwargs)


# --- estrutura do catálogo ---------------------------------------------------

def test_catalogo_tem_13_etapas_ids_unicos_e_so_enriquecimento_sga_e_manual():
    assert len(cat.CATALOGO) == 13
    ids = [etapa.id for etapa in cat.CATALOGO]
    assert len(ids) == len(set(ids))
    assert [etapa.id for etapa in cat.CATALOGO if etapa.manual] == ["enriquecimento_sga"]


def test_etapa_por_id_desconhecida_levanta_erro():
    with pytest.raises(ValueError):
        cat.etapa_por_id("nao_existe")


def test_processar_resultado_ligacao_recebe_equipamentos_e_incidentes_do_contexto():
    """Wiring da detecção automática de conclusão em "Encaminhar pra
    Puma" (decisão de negócio 2026-08-14): `equipamentos`/`incidentes`
    já produzidos por `ler_planilha_gestor` devem chegar em
    `processar_resultado_ligacao` sem reler Sheets."""
    etapa_leitura = cat.etapa_por_id("ler_planilha_gestor")
    assert etapa_leitura.saidas["incidentes"] == "incidentes"

    etapa_ligacao = cat.etapa_por_id("processar_resultado_ligacao")
    contexto = {"equipamentos": ["equip-1"], "incidentes": ["incidente-1"], "outra_chave": "ignorada"}
    assert cat.kwargs_para(etapa_ligacao, contexto) == {
        "equipamentos": ["equip-1"], "incidentes": ["incidente-1"],
    }


# --- resolver_etapas ---------------------------------------------------------

def test_resolver_etapas_todas():
    assert cat.resolver_etapas("todas") == cat.CATALOGO


def test_resolver_etapas_selecionadas_preserva_ordem_do_catalogo():
    etapas = cat.resolver_etapas("selecionadas", ["publicar_fila_operacional", "baixar_relatorios"])
    assert [etapa.id for etapa in etapas] == ["baixar_relatorios", "publicar_fila_operacional"]


def test_resolver_etapas_a_partir_de():
    etapas = cat.resolver_etapas("a_partir_de:enriquecimento_sga")
    assert etapas[0].id == "enriquecimento_sga"
    assert etapas[-1].id == "processar_alertas"


def test_resolver_etapas_a_partir_de_id_desconhecido_levanta_erro():
    with pytest.raises(ValueError):
        cat.resolver_etapas("a_partir_de:nao_existe")


def test_resolver_etapas_modo_desconhecido_levanta_erro():
    with pytest.raises(ValueError):
        cat.resolver_etapas("modo_invalido")


# --- executar_etapas_com_contexto -------------------------------------------

@pytest.mark.asyncio
async def test_executar_etapas_com_contexto_encadeia_saida_para_entrada(monkeypatch):
    chamadas = []

    async def _etapa_a():
        return _resultado("a", dados={"valor": 42})

    async def _etapa_b(x):
        chamadas.append(x)
        return _resultado("b")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)
    monkeypatch.setattr(pipeline, "etapa_fake_b", _etapa_b, raising=False)

    etapas = [
        _etapa_fake("a", "etapa_fake_a", saidas={"saida_a": "valor"}),
        _etapa_fake("b", "etapa_fake_b", entradas={"saida_a": "x"}),
    ]
    execucao = await cat.executar_etapas_com_contexto(etapas, {})

    assert execucao.motivo_parada is None
    assert chamadas == [42]


@pytest.mark.asyncio
async def test_executar_etapas_com_contexto_para_na_primeira_falha(monkeypatch):
    chamou_b = False

    async def _etapa_a():
        return _resultado("a", sucesso=False, mensagem="deu erro")

    async def _etapa_b():
        nonlocal chamou_b
        chamou_b = True
        return _resultado("b")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)
    monkeypatch.setattr(pipeline, "etapa_fake_b", _etapa_b, raising=False)

    etapas = [_etapa_fake("a", "etapa_fake_a"), _etapa_fake("b", "etapa_fake_b")]
    execucao = await cat.executar_etapas_com_contexto(etapas, {})

    assert execucao.motivo_parada == "falha"
    assert execucao.etapas_restantes == [etapas[1]]
    assert chamou_b is False


@pytest.mark.asyncio
async def test_executar_etapas_com_contexto_para_em_aguardando_reconexao(monkeypatch):
    async def _etapa_a():
        return _resultado("a", sucesso=False, aguardando_reconexao={"pendentes": ["x"]})

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)
    monkeypatch.setattr(pipeline, "etapa_fake_b", lambda: None, raising=False)

    etapas = [_etapa_fake("a", "etapa_fake_a"), _etapa_fake("b", "etapa_fake_b")]
    execucao = await cat.executar_etapas_com_contexto(etapas, {})

    assert execucao.motivo_parada == "aguardando_reconexao"
    assert execucao.etapa_travada_id == "a"
    assert execucao.etapas_restantes == [etapas[1]]


@pytest.mark.asyncio
async def test_executar_etapas_com_contexto_cancelar_checker_para_entre_etapas(monkeypatch):
    chamou_b = False

    async def _etapa_a():
        return _resultado("a")

    async def _etapa_b():
        nonlocal chamou_b
        chamou_b = True
        return _resultado("b")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)
    monkeypatch.setattr(pipeline, "etapa_fake_b", _etapa_b, raising=False)

    fila_cancelar = [False, True]

    def _cancelar_checker():
        return fila_cancelar.pop(0)

    etapas = [_etapa_fake("a", "etapa_fake_a"), _etapa_fake("b", "etapa_fake_b")]
    execucao = await cat.executar_etapas_com_contexto(etapas, {}, cancelar_checker=_cancelar_checker)

    assert execucao.motivo_parada == "cancelada"
    assert chamou_b is False
    assert execucao.etapas_restantes == [etapas[1]]


@pytest.mark.asyncio
async def test_executar_etapas_com_contexto_para_quando_resultado_sinaliza_cancelado(monkeypatch):
    chamou_b = False

    async def _etapa_a():
        return _resultado("a", sucesso=False, cancelado={"pendentes": ["x"]})

    async def _etapa_b():
        nonlocal chamou_b
        chamou_b = True
        return _resultado("b")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)
    monkeypatch.setattr(pipeline, "etapa_fake_b", _etapa_b, raising=False)

    etapas = [_etapa_fake("a", "etapa_fake_a"), _etapa_fake("b", "etapa_fake_b")]
    execucao = await cat.executar_etapas_com_contexto(etapas, {})

    assert execucao.motivo_parada == "cancelada"
    assert chamou_b is False
    assert execucao.etapas_restantes == [etapas[1]]


@pytest.mark.asyncio
async def test_executar_etapas_com_contexto_repassa_cancelar_checker_pra_etapa_com_fila(monkeypatch):
    """Diferente do check "entre etapas" (que só lê a flag antes de
    começar a próxima), este confirma que o MESMO `cancelar_checker` é
    injetado como kwarg da etapa quando ela suporta (`suporta_progresso`)
    — é essa injeção que permite a etapa parar sozinha no meio da sua
    fila."""
    recebido = {}

    async def _etapa_a(cancelar_checker=None):
        recebido["cancelar_checker"] = cancelar_checker
        return _resultado("a")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)
    checker = lambda: False
    etapas = [_etapa_fake("a", "etapa_fake_a", suporta_progresso=True)]

    await cat.executar_etapas_com_contexto(etapas, {}, cancelar_checker=checker)

    assert recebido["cancelar_checker"] is checker


@pytest.mark.asyncio
async def test_on_progresso_item_so_chamado_para_etapas_que_suportam(monkeypatch):
    async def _etapa_a(on_progresso=None):
        if on_progresso is not None:
            on_progresso(1, 1)
        return _resultado("a")

    async def _etapa_b():
        return _resultado("b")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)
    monkeypatch.setattr(pipeline, "etapa_fake_b", _etapa_b, raising=False)

    chamadas = []
    etapas = [
        _etapa_fake("a", "etapa_fake_a", suporta_progresso=True),
        _etapa_fake("b", "etapa_fake_b", suporta_progresso=False),
    ]
    await cat.executar_etapas_com_contexto(
        etapas, {}, on_progresso_item=lambda etapa_id, concluidos, total: chamadas.append((etapa_id, concluidos, total))
    )

    assert chamadas == [("a", 1, 1)]


@pytest.mark.asyncio
async def test_cadeia_real_enriquecimento_sga_roda_uma_vez_so(monkeypatch):
    chamadas_sga = 0

    def _motor_de_regras_fake(dados=None):
        return pipeline.ResultadoEtapa(
            "motor_de_regras", sucesso=True,
            dados={"grupo_1_abrir": [], "grupo_2_concluir": [], "grupo_3_tratativa_humana": []},
        )

    async def _abrir_incidentes_fake(dados=None, on_progresso=None):
        return pipeline.ResultadoEtapa("abrir_incidentes_automaticos", sucesso=True, dados={"abertos": [], "falhas": []})

    async def _enriquecimento_sga_fake(
        dados_classificacao=None, instalacao_remocao=None, chassis_override=None, on_progresso=None
    ):
        nonlocal chamadas_sga
        chamadas_sga += 1
        return pipeline.ResultadoEtapa("enriquecimento_sga", sucesso=True, dados={"situacoes_sga": {}})

    async def _consolidar_com_sga_fake(dados_classificacao=None, dados_sga=None, equipamentos=None, instalacao_remocao=None):
        assert dados_sga == {"situacoes_sga": {}}
        return pipeline.ResultadoEtapa(
            "consolidar_com_sga", sucesso=True,
            dados={"grupo_2_concluir": [], "fila_operacional": [], "divergencias_instalacao": []},
        )

    monkeypatch.setattr(pipeline, "etapa_motor_de_regras", _motor_de_regras_fake)
    monkeypatch.setattr(pipeline, "etapa_abrir_incidentes_automaticos", _abrir_incidentes_fake)
    monkeypatch.setattr(pipeline, "etapa_enriquecimento_sga", _enriquecimento_sga_fake)
    monkeypatch.setattr(pipeline, "etapa_consolidar_com_sga", _consolidar_com_sga_fake)

    ids = ["motor_de_regras", "abrir_incidentes_automaticos", "enriquecimento_sga", "consolidar_com_sga"]
    execucao = await cat.executar_etapas_com_contexto(cat.resolver_etapas("selecionadas", ids), {})

    assert execucao.motivo_parada is None
    assert chamadas_sga == 1


# --- executar_cadeia / trava de execução concorrente -------------------------

@pytest.mark.asyncio
async def test_executar_cadeia_travado_nao_roda_nenhuma_etapa(monkeypatch):
    monkeypatch.setattr(cat.supabase_client, "adquirir_execucao_lock", lambda maquina: False)
    chamou_baixar = False

    async def _baixar_fake():
        nonlocal chamou_baixar
        chamou_baixar = True
        return pipeline.ResultadoEtapa("baixar_relatorios", sucesso=True)

    monkeypatch.setattr(pipeline, "etapa_baixar_relatorios", _baixar_fake)

    execucao = await cat.executar_cadeia(["baixar_relatorios"], "selecionadas")

    assert execucao.motivo_parada == "travado"
    assert chamou_baixar is False


@pytest.mark.asyncio
async def test_executar_cadeia_adquire_trava_com_hostname_da_maquina(monkeypatch):
    chamadas = []
    monkeypatch.setattr(cat.supabase_client, "adquirir_execucao_lock", lambda maquina: chamadas.append(maquina) or True)
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: None)

    async def _baixar_fake():
        return pipeline.ResultadoEtapa("baixar_relatorios", sucesso=True, dados={})

    monkeypatch.setattr(pipeline, "etapa_baixar_relatorios", _baixar_fake)

    await cat.executar_cadeia(["baixar_relatorios"], "selecionadas")

    assert chamadas == [socket.gethostname()]


@pytest.mark.asyncio
async def test_executar_cadeia_libera_trava_em_sucesso(monkeypatch):
    monkeypatch.setattr(cat.supabase_client, "adquirir_execucao_lock", lambda maquina: True)
    liberou = []
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: liberou.append(True))

    async def _baixar_fake():
        return pipeline.ResultadoEtapa("baixar_relatorios", sucesso=True, dados={})

    monkeypatch.setattr(pipeline, "etapa_baixar_relatorios", _baixar_fake)

    execucao = await cat.executar_cadeia(["baixar_relatorios"], "selecionadas")

    assert execucao.motivo_parada is None
    assert liberou == [True]


@pytest.mark.asyncio
async def test_executar_cadeia_libera_trava_em_falha(monkeypatch):
    monkeypatch.setattr(cat.supabase_client, "adquirir_execucao_lock", lambda maquina: True)
    liberou = []
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: liberou.append(True))

    async def _baixar_falha():
        return pipeline.ResultadoEtapa("baixar_relatorios", sucesso=False, mensagem="erro")

    monkeypatch.setattr(pipeline, "etapa_baixar_relatorios", _baixar_falha)

    execucao = await cat.executar_cadeia(["baixar_relatorios"], "selecionadas")

    assert execucao.motivo_parada == "falha"
    assert liberou == [True]


@pytest.mark.asyncio
async def test_executar_cadeia_libera_trava_em_cancelada(monkeypatch):
    monkeypatch.setattr(cat.supabase_client, "adquirir_execucao_lock", lambda maquina: True)
    liberou = []
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: liberou.append(True))

    execucao = await cat.executar_cadeia(["baixar_relatorios"], "selecionadas", cancelar_checker=lambda: True)

    assert execucao.motivo_parada == "cancelada"
    assert liberou == [True]


@pytest.mark.asyncio
async def test_executar_cadeia_nao_libera_trava_em_aguardando_reconexao(monkeypatch):
    monkeypatch.setattr(cat.supabase_client, "adquirir_execucao_lock", lambda maquina: True)
    liberou = []
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: liberou.append(True))

    async def _abrir_reconexao(dados=None, on_progresso=None):
        return pipeline.ResultadoEtapa(
            "abrir_incidentes_automaticos", sucesso=False,
            dados={"abertos": [], "falhas": []}, aguardando_reconexao={"pendentes": ["x"]},
        )

    monkeypatch.setattr(pipeline, "etapa_abrir_incidentes_automaticos", _abrir_reconexao)

    execucao = await cat.executar_cadeia(
        ["abrir_incidentes_automaticos"], "selecionadas", contexto={"classificacao": {"grupo_1_abrir": []}},
    )

    assert execucao.motivo_parada == "aguardando_reconexao"
    assert liberou == []


# --- retomar_etapa / continuar_apos_reconexao --------------------------------

@pytest.mark.asyncio
async def test_continuar_apos_reconexao_libera_trava_ao_finalizar_com_sucesso(monkeypatch):
    liberou = []
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: liberou.append(True))

    async def _abrir_incidentes_fake(dados=None, on_progresso=None):
        return pipeline.ResultadoEtapa(
            "abrir_incidentes_automaticos", sucesso=True,
            dados={"abertos": [dados["grupo_1_abrir"][0]], "falhas": []},
        )

    monkeypatch.setattr(pipeline, "etapa_abrir_incidentes_automaticos", _abrir_incidentes_fake)

    etapa = cat.etapa_por_id("abrir_incidentes_automaticos")
    resultado_travado = pipeline.ResultadoEtapa(
        "abrir_incidentes_automaticos", sucesso=False,
        dados={"abertos": [{"placa": "JA-PROCESSADA"}], "falhas": []},
        aguardando_reconexao={"pendentes": [{"placa": "PENDENTE"}]},
    )

    execucao = await cat.continuar_apos_reconexao(etapa, {}, resultado_travado, etapas_restantes=[])

    assert execucao.motivo_parada is None
    assert execucao.resultados[0].dados["abertos"] == [{"placa": "JA-PROCESSADA"}, {"placa": "PENDENTE"}]
    assert liberou == [True]


@pytest.mark.asyncio
async def test_continuar_apos_reconexao_caindo_de_novo_nao_duplica_e_mantem_trava(monkeypatch):
    liberou = []
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: liberou.append(True))

    async def _abrir_incidentes_cai_de_novo(dados=None, on_progresso=None):
        return pipeline.ResultadoEtapa(
            "abrir_incidentes_automaticos", sucesso=False,
            dados={"abertos": [], "falhas": []}, aguardando_reconexao={"pendentes": dados["grupo_1_abrir"]},
        )

    monkeypatch.setattr(pipeline, "etapa_abrir_incidentes_automaticos", _abrir_incidentes_cai_de_novo)

    etapa = cat.etapa_por_id("abrir_incidentes_automaticos")
    resultado_travado = pipeline.ResultadoEtapa(
        "abrir_incidentes_automaticos", sucesso=False,
        dados={"abertos": [{"placa": "JA-PROCESSADA"}], "falhas": []},
        aguardando_reconexao={"pendentes": [{"placa": "PENDENTE"}]},
    )

    execucao = await cat.continuar_apos_reconexao(etapa, {}, resultado_travado, etapas_restantes=[])

    assert execucao.motivo_parada == "aguardando_reconexao"
    assert execucao.resultados[0].dados["abertos"] == [{"placa": "JA-PROCESSADA"}]
    assert execucao.resultados[0].aguardando_reconexao == {"pendentes": [{"placa": "PENDENTE"}]}
    assert liberou == []


@pytest.mark.asyncio
async def test_continuar_apos_reconexao_cancelada_no_meio_da_retomada_libera_trava(monkeypatch):
    liberou = []
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: liberou.append(True))

    async def _abrir_incidentes_cancelado(dados=None, on_progresso=None, cancelar_checker=None):
        return pipeline.ResultadoEtapa(
            "abrir_incidentes_automaticos", sucesso=False,
            dados={"abertos": [], "falhas": []}, cancelado={"pendentes": dados["grupo_1_abrir"]},
        )

    monkeypatch.setattr(pipeline, "etapa_abrir_incidentes_automaticos", _abrir_incidentes_cancelado)

    etapa = cat.etapa_por_id("abrir_incidentes_automaticos")
    resultado_travado = pipeline.ResultadoEtapa(
        "abrir_incidentes_automaticos", sucesso=False,
        dados={"abertos": [{"placa": "JA-PROCESSADA"}], "falhas": []},
        aguardando_reconexao={"pendentes": [{"placa": "PENDENTE"}]},
    )

    execucao = await cat.continuar_apos_reconexao(
        etapa, {}, resultado_travado, etapas_restantes=[cat.etapa_por_id("consolidar_com_sga")],
        cancelar_checker=lambda: True,
    )

    assert execucao.motivo_parada == "cancelada"
    assert execucao.resultados[0].cancelado == {"pendentes": [{"placa": "PENDENTE"}]}
    # a mesclagem de sucesso/falha continua funcionando mesmo cancelado
    assert execucao.resultados[0].dados["abertos"] == [{"placa": "JA-PROCESSADA"}]
    assert liberou == [True]


@pytest.mark.asyncio
async def test_continuar_apos_reconexao_prossegue_com_etapas_restantes(monkeypatch):
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: None)

    async def _abrir_incidentes_fake(dados=None, on_progresso=None):
        return pipeline.ResultadoEtapa("abrir_incidentes_automaticos", sucesso=True, dados={"abertos": [], "falhas": []})

    chamou_proxima = False

    async def _proxima_fake(**kwargs):
        nonlocal chamou_proxima
        chamou_proxima = True
        return pipeline.ResultadoEtapa("enriquecimento_sga", sucesso=True, dados={"situacoes_sga": {}})

    monkeypatch.setattr(pipeline, "etapa_abrir_incidentes_automaticos", _abrir_incidentes_fake)
    monkeypatch.setattr(pipeline, "etapa_enriquecimento_sga", _proxima_fake)

    etapa = cat.etapa_por_id("abrir_incidentes_automaticos")
    proxima_etapa = cat.etapa_por_id("enriquecimento_sga")
    resultado_travado = pipeline.ResultadoEtapa(
        "abrir_incidentes_automaticos", sucesso=False, dados={"abertos": [], "falhas": []},
        aguardando_reconexao={"pendentes": []},
    )

    execucao = await cat.continuar_apos_reconexao(etapa, {}, resultado_travado, etapas_restantes=[proxima_etapa])

    assert execucao.motivo_parada is None
    assert chamou_proxima is True


@pytest.mark.asyncio
async def test_retomar_etapa_enriquecimento_sga_repassa_mapa_placas_override(monkeypatch):
    # Achado 2026-08-16: sem repassar o mapa placa-por-identificador
    # calculado na tentativa original, a retomada pós-queda de sessão no
    # meio da Fase D perderia o fallback de busca por Placa justamente
    # pros chassis que ainda faltavam.
    mapa_recebido = {}

    async def _enriquecimento_sga_fake(chassis_override=None, mapa_placas_override=None):
        mapa_recebido.update(mapa_placas_override or {})
        return pipeline.ResultadoEtapa(
            "enriquecimento_sga", sucesso=True,
            dados={"situacoes_sga": {"X2": {"status": "ATIVO"}}, "falhas": []},
        )

    monkeypatch.setattr(pipeline, "etapa_enriquecimento_sga", _enriquecimento_sga_fake)

    etapa = cat.etapa_por_id("enriquecimento_sga")
    resultado_travado = pipeline.ResultadoEtapa(
        "enriquecimento_sga", sucesso=False,
        dados={"situacoes_sga": {"X1": {"status": "ATIVO"}}, "falhas": [], "mapa_placas": {"X1": "ABC1234", "X2": "DEF5678"}},
        aguardando_reconexao={"pendentes": ["X2"]},
    )

    resultado = await cat.retomar_etapa(etapa, resultado_travado)

    assert mapa_recebido == {"X1": "ABC1234", "X2": "DEF5678"}
    assert resultado.dados["mapa_placas"] == {"X1": "ABC1234", "X2": "DEF5678"}
    assert resultado.dados["situacoes_sga"] == {"X1": {"status": "ATIVO"}, "X2": {"status": "ATIVO"}}


@pytest.mark.asyncio
async def test_retomar_etapa_id_nao_suportado_levanta_erro():
    etapa = cat.etapa_por_id("motor_de_regras")
    resultado_travado = pipeline.ResultadoEtapa("motor_de_regras", sucesso=False, aguardando_reconexao={"pendentes": []})

    with pytest.raises(ValueError):
        await cat.retomar_etapa(etapa, resultado_travado)


# --- Observabilidade (fatia 1) — execucao_id / duração / watchdog-safe ------

@pytest.mark.asyncio
async def test_executar_etapas_com_contexto_gera_execucao_id_e_reaproveita_em_todas_as_etapas(monkeypatch):
    chamadas = []
    monkeypatch.setattr(cat.supabase_client, "registrar_log_execucao", lambda **kwargs: chamadas.append(kwargs))

    async def _etapa_a():
        return _resultado("a")

    async def _etapa_b():
        return _resultado("b")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)
    monkeypatch.setattr(pipeline, "etapa_fake_b", _etapa_b, raising=False)

    etapas = [_etapa_fake("a", "etapa_fake_a"), _etapa_fake("b", "etapa_fake_b")]
    execucao = await cat.executar_etapas_com_contexto(etapas, {})

    assert execucao.execucao_id
    assert len(chamadas) == 2
    assert chamadas[0]["execucao_id"] == chamadas[1]["execucao_id"] == execucao.execucao_id
    assert chamadas[0]["etapa_id"] == "a"
    assert chamadas[1]["etapa_id"] == "b"
    assert chamadas[0]["sucesso"] is True
    assert chamadas[0]["motivo_parada"] is None
    assert chamadas[0]["iniciado_em"] <= chamadas[0]["finalizado_em"]


@pytest.mark.asyncio
async def test_executar_etapas_com_contexto_execucao_id_explicito_e_repassado(monkeypatch):
    chamadas = []
    monkeypatch.setattr(cat.supabase_client, "registrar_log_execucao", lambda **kwargs: chamadas.append(kwargs))

    async def _etapa_a():
        return _resultado("a")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)

    execucao = await cat.executar_etapas_com_contexto(
        [_etapa_fake("a", "etapa_fake_a")], {}, execucao_id="exec-fixo",
    )

    assert execucao.execucao_id == "exec-fixo"
    assert chamadas[0]["execucao_id"] == "exec-fixo"


@pytest.mark.asyncio
async def test_executar_etapas_com_contexto_registra_motivo_parada_em_falha(monkeypatch):
    chamadas = []
    monkeypatch.setattr(cat.supabase_client, "registrar_log_execucao", lambda **kwargs: chamadas.append(kwargs))

    async def _etapa_a():
        return _resultado("a", sucesso=False, mensagem="deu ruim")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)

    await cat.executar_etapas_com_contexto([_etapa_fake("a", "etapa_fake_a")], {})

    assert chamadas[0]["sucesso"] is False
    assert chamadas[0]["motivo_parada"] == "falha"
    assert chamadas[0]["mensagem"] == "deu ruim"


@pytest.mark.asyncio
async def test_falha_ao_registrar_log_execucao_nao_derruba_etapa_nem_impede_liberar_trava(monkeypatch):
    monkeypatch.setattr(cat.supabase_client, "adquirir_execucao_lock", lambda maquina: True)
    liberou = []
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: liberou.append(True))

    def _registrar_com_falha(**kwargs):
        raise RuntimeError("Supabase indisponível")

    monkeypatch.setattr(cat.supabase_client, "registrar_log_execucao", _registrar_com_falha)

    async def _baixar_fake():
        return pipeline.ResultadoEtapa("baixar_relatorios", sucesso=True, dados={})

    monkeypatch.setattr(pipeline, "etapa_baixar_relatorios", _baixar_fake)

    execucao = await cat.executar_cadeia(["baixar_relatorios"], "selecionadas")

    assert execucao.motivo_parada is None
    assert execucao.resultados[0].sucesso is True
    assert liberou == [True]


@pytest.mark.asyncio
async def test_continuar_apos_reconexao_reaproveita_execucao_id_da_execucao_original(monkeypatch):
    chamadas = []
    monkeypatch.setattr(cat.supabase_client, "registrar_log_execucao", lambda **kwargs: chamadas.append(kwargs))
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: None)

    async def _abrir_incidentes_fake(dados=None, on_progresso=None):
        return pipeline.ResultadoEtapa(
            "abrir_incidentes_automaticos", sucesso=True,
            dados={"abertos": [dados["grupo_1_abrir"][0]], "falhas": []},
        )

    monkeypatch.setattr(pipeline, "etapa_abrir_incidentes_automaticos", _abrir_incidentes_fake)

    etapa = cat.etapa_por_id("abrir_incidentes_automaticos")
    resultado_travado = pipeline.ResultadoEtapa(
        "abrir_incidentes_automaticos", sucesso=False,
        dados={"abertos": [{"placa": "JA-PROCESSADA"}], "falhas": []},
        aguardando_reconexao={"pendentes": [{"placa": "PENDENTE"}]},
    )

    execucao = await cat.continuar_apos_reconexao(
        etapa, {}, resultado_travado, etapas_restantes=[], execucao_id="exec-original",
    )

    assert execucao.execucao_id == "exec-original"
    assert len(chamadas) == 1
    assert chamadas[0]["execucao_id"] == "exec-original"
    assert chamadas[0]["etapa_id"] == "abrir_incidentes_automaticos"
