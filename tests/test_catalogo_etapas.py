import socket

import pytest

from orchestrator import catalogo_etapas as cat
from orchestrator import pipeline


@pytest.fixture(autouse=True)
def _sem_log_execucoes_real(monkeypatch, tmp_path):
    """Toda chamada real ao Supabase precisa estar mockada em teste
    unitário (mesmo princípio já usado pra `adquirir_execucao_lock`/
    `liberar_execucao_lock`) — sem isso, qualquer teste que rode uma
    etapa de verdade escreveria uma linha real em `log_execucoes` na
    produção. Autouse: cobre todo teste do arquivo, mesmo os que não
    mencionam isso explicitamente. Testes que querem inspecionar o que
    foi gravado sobrescrevem com seu próprio `monkeypatch.setattr` no
    corpo do teste.

    `_diretorio_logs` também aponta pra uma pasta temporária por padrão
    (mesmo motivo): sem isso, todo teste que passa por
    `_registrar_execucao_segura` escreveria em `logs/execucoes.log` de
    verdade, na pasta do projeto."""
    monkeypatch.setattr(cat.supabase_client, "registrar_log_execucao", lambda **kwargs: None)
    monkeypatch.setattr(cat, "_diretorio_logs", lambda: tmp_path)


def _resultado(etapa, sucesso=True, dados=None, mensagem="", aguardando_reconexao=None, cancelado=None):
    return pipeline.ResultadoEtapa(
        etapa, sucesso=sucesso, mensagem=mensagem, dados=dados or {},
        aguardando_reconexao=aguardando_reconexao, cancelado=cancelado,
    )


def _etapa_fake(id_, nome_funcao, **kwargs):
    return cat.EtapaCatalogo(id_, "X", id_, nome_funcao, **kwargs)


# --- estrutura do catálogo ---------------------------------------------------

def test_catalogo_tem_14_etapas_ids_unicos_e_so_enriquecimento_sga_e_manual():
    assert len(cat.CATALOGO) == 14
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
async def test_executar_etapas_com_contexto_excecao_nao_tratada_vira_falha(monkeypatch):
    """Achado 2026-08-25: `etapa_enriquecimento_sga` tem código de
    preparação sem try/except próprio -- uma exceção real ali (ex: falha
    ao ler a aba Instalação-Remoção) não pode escapar até `ui.app.
    _rodar_cadeia` sem nunca logar nem liberar a trava. Confirma que a
    exceção vira um `ResultadoEtapa(sucesso=False)` normal, com a cadeia
    parando do jeito de sempre (não repropaga)."""
    chamou_b = False

    async def _etapa_a():
        raise RuntimeError("falha de preparação, sem try/except próprio")

    async def _etapa_b():
        nonlocal chamou_b
        chamou_b = True
        return _resultado("b")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)
    monkeypatch.setattr(pipeline, "etapa_fake_b", _etapa_b, raising=False)

    etapas = [_etapa_fake("a", "etapa_fake_a"), _etapa_fake("b", "etapa_fake_b")]
    execucao = await cat.executar_etapas_com_contexto(etapas, {})

    assert execucao.motivo_parada == "falha"
    assert execucao.resultados[0].sucesso is False
    assert "RuntimeError" in execucao.resultados[0].mensagem
    assert "falha de preparação" in execucao.resultados[0].mensagem
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
            dados={
                "grupo_2_concluir": [], "fila_operacional": [], "divergencias_instalacao": [],
                "divergencias_remocao": [], "divergencias_manutencao": [],
            },
        )

    monkeypatch.setattr(pipeline, "etapa_motor_de_regras", _motor_de_regras_fake)
    monkeypatch.setattr(pipeline, "etapa_abrir_incidentes_automaticos", _abrir_incidentes_fake)
    monkeypatch.setattr(pipeline, "etapa_enriquecimento_sga", _enriquecimento_sga_fake)
    monkeypatch.setattr(pipeline, "etapa_consolidar_com_sga", _consolidar_com_sga_fake)

    ids = ["motor_de_regras", "abrir_incidentes_automaticos", "enriquecimento_sga", "consolidar_com_sga"]
    execucao = await cat.executar_etapas_com_contexto(cat.resolver_etapas("selecionadas", ids), {})

    assert execucao.motivo_parada is None
    assert chamadas_sga == 1


@pytest.mark.asyncio
async def test_cadeia_real_divergencias_remocao_e_manutencao_chegam_em_publicar_fila(monkeypatch):
    """Achado real 2026-08-25: `divergencias_remocao`/`divergencias_
    manutencao` foram implementadas em `orchestrator/pipeline.py` (Bloco
    B + Manutenção), mas nunca ganharam entrada em `EtapaCatalogo.saidas`
    (consolidar_com_sga) nem `entradas` (publicar_fila_operacional) --
    a lógica de classificação calculava certo, mas a cadeia real (só
    `kwargs_para`/`registrar_saidas`, nunca chamada direto pelos testes
    unitários de `etapa_publicar_fila_operacional`) nunca repassava esse
    dado -- as 2 abas ficavam sempre vazias em produção, só `divergencias_
    instalacao` (com wiring desde o Bloco B original) funcionava. Este
    teste roda a cadeia REAL (`resolver_etapas`, sem pular a etapa
    seguinte) pra pegar exatamente esse tipo de gap de novo."""
    async def _consolidar_com_sga_fake(dados_classificacao=None, dados_sga=None, equipamentos=None, instalacao_remocao=None):
        return pipeline.ResultadoEtapa(
            "consolidar_com_sga", sucesso=True,
            dados={
                "grupo_2_concluir": [], "fila_operacional": [],
                "divergencias_instalacao": ["div-instalacao"],
                "divergencias_remocao": ["div-remocao"],
                "divergencias_manutencao": ["div-manutencao"],
            },
        )

    recebido = {}

    async def _publicar_fila_fake(fila_operacional=None, divergencias_instalacao=None, divergencias_remocao=None, divergencias_manutencao=None):
        recebido["divergencias_instalacao"] = divergencias_instalacao
        recebido["divergencias_remocao"] = divergencias_remocao
        recebido["divergencias_manutencao"] = divergencias_manutencao
        return pipeline.ResultadoEtapa("publicar_fila_operacional", sucesso=True, dados={})

    monkeypatch.setattr(pipeline, "etapa_consolidar_com_sga", _consolidar_com_sga_fake)
    monkeypatch.setattr(pipeline, "etapa_publicar_fila_operacional", _publicar_fila_fake)

    ids = ["consolidar_com_sga", "publicar_fila_operacional"]
    execucao = await cat.executar_etapas_com_contexto(cat.resolver_etapas("selecionadas", ids), {})

    assert execucao.motivo_parada is None
    assert recebido["divergencias_instalacao"] == ["div-instalacao"]
    assert recebido["divergencias_remocao"] == ["div-remocao"]
    assert recebido["divergencias_manutencao"] == ["div-manutencao"]


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
async def test_executar_cadeia_libera_trava_em_excecao_nao_tratada_da_etapa(monkeypatch):
    """Mesmo achado 2026-08-25 do teste equivalente de `executar_etapas_
    com_contexto`, mas verificando de ponta a ponta que a trava é
    liberada -- é o sintoma real observado ao vivo (trava presa, sem
    log, sem jeito de cancelar pela UI)."""
    monkeypatch.setattr(cat.supabase_client, "adquirir_execucao_lock", lambda maquina: True)
    liberou = []
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: liberou.append(True))

    async def _baixar_excecao():
        raise RuntimeError("falha de preparação, sem try/except próprio")

    monkeypatch.setattr(pipeline, "etapa_baixar_relatorios", _baixar_excecao)

    execucao = await cat.executar_cadeia(["baixar_relatorios"], "selecionadas")

    assert execucao.motivo_parada == "falha"
    assert liberou == [True]


@pytest.mark.asyncio
async def test_executar_cadeia_libera_trava_e_repropaga_se_excecao_escapar_do_loop(monkeypatch):
    """Defesa em profundidade: mesmo que algo inesperado escape de
    `executar_etapas_com_contexto` inteiro (não só de 1 etapa -- esse já
    é protegido pelo teste acima), a trava não pode ficar presa. Esse
    caso repropaga (quem chamou precisa saber que quebrou de verdade,
    diferente de uma falha normal de etapa)."""
    monkeypatch.setattr(cat.supabase_client, "adquirir_execucao_lock", lambda maquina: True)
    liberou = []
    monkeypatch.setattr(cat.supabase_client, "liberar_execucao_lock", lambda: liberou.append(True))

    async def _executar_etapas_explode(*args, **kwargs):
        raise RuntimeError("algo quebrou fora da chamada de qualquer etapa")

    monkeypatch.setattr(cat, "executar_etapas_com_contexto", _executar_etapas_explode)

    with pytest.raises(RuntimeError, match="algo quebrou fora"):
        await cat.executar_cadeia(["baixar_relatorios"], "selecionadas")

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
async def test_retomar_etapa_enriquecimento_sga_repassa_alvos_override(monkeypatch):
    # Achado 2026-08-16: sem repassar os alvos de consulta (tipo+valor por
    # veículo) calculados na tentativa original, a retomada pós-queda de
    # sessão no meio da Fase D não saberia mais se cada pendente deve ser
    # buscado por Chassi ou por Placa.
    alvos_recebidos = {}

    async def _enriquecimento_sga_fake(chassis_override=None, alvos_override=None):
        alvos_recebidos.update(alvos_override or {})
        return pipeline.ResultadoEtapa(
            "enriquecimento_sga", sucesso=True,
            dados={"situacoes_sga": {"X2": {"status": "ATIVO"}}, "falhas": []},
        )

    monkeypatch.setattr(pipeline, "etapa_enriquecimento_sga", _enriquecimento_sga_fake)

    etapa = cat.etapa_por_id("enriquecimento_sga")
    alvos_originais = {
        "X1": (pipeline.TIPO_IDENTIFICADOR_CHASSI, "X1"), "X2": (pipeline.TIPO_IDENTIFICADOR_PLACA, "DEF5678"),
    }
    resultado_travado = pipeline.ResultadoEtapa(
        "enriquecimento_sga", sucesso=False,
        dados={"situacoes_sga": {"X1": {"status": "ATIVO"}}, "falhas": [], "alvos_consulta_sga": alvos_originais},
        aguardando_reconexao={"pendentes": ["X2"]},
    )

    resultado = await cat.retomar_etapa(etapa, resultado_travado)

    assert alvos_recebidos == alvos_originais
    assert resultado.dados["alvos_consulta_sga"] == alvos_originais
    assert resultado.dados["situacoes_sga"] == {"X1": {"status": "ATIVO"}, "X2": {"status": "ATIVO"}}


@pytest.mark.asyncio
async def test_retomar_etapa_enriquecimento_sga_excecao_nao_tratada_vira_falha(monkeypatch):
    """Mesmo achado 2026-08-25 do resto do arquivo, mas no caminho de
    retomada pós-reconexão -- `pipeline.etapa_enriquecimento_sga` é
    chamada direto aqui, fora do loop de `executar_etapas_com_contexto`
    (que já está protegido), então precisa do próprio try/except."""
    async def _enriquecimento_sga_excecao(chassis_override=None, alvos_override=None):
        raise RuntimeError("sessão do SGA caiu de um jeito inesperado")

    monkeypatch.setattr(pipeline, "etapa_enriquecimento_sga", _enriquecimento_sga_excecao)

    etapa = cat.etapa_por_id("enriquecimento_sga")
    resultado_travado = pipeline.ResultadoEtapa(
        "enriquecimento_sga", sucesso=False,
        dados={"situacoes_sga": {"X1": {"status": "ATIVO"}}, "falhas": [], "alvos_consulta_sga": {}},
        aguardando_reconexao={"pendentes": ["X2"]},
    )

    resultado = await cat.retomar_etapa(etapa, resultado_travado)

    assert resultado.sucesso is False
    assert "RuntimeError" in resultado.mensagem
    assert "sessão do SGA caiu" in resultado.mensagem
    # não perde o que já tinha sido persistido antes da queda
    assert resultado.dados["situacoes_sga"] == {"X1": {"status": "ATIVO"}}


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


# --- Log local em arquivo (achado 2026-08-20: listas grandes de erro) -------

def test_caminho_log_execucoes_usa_diretorio_logs():
    assert cat.caminho_log_execucoes() == cat._diretorio_logs() / "execucoes.log"


@pytest.mark.asyncio
async def test_registrar_log_arquivo_nao_grava_quando_sucesso_sem_falhas(monkeypatch, tmp_path):
    async def _etapa_a():
        return _resultado("a", sucesso=True, dados={})

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)

    await cat.executar_etapas_com_contexto([_etapa_fake("a", "etapa_fake_a")], {})

    assert not (tmp_path / "execucoes.log").exists()


@pytest.mark.asyncio
async def test_registrar_log_arquivo_grava_falhas_por_item(monkeypatch, tmp_path):
    async def _etapa_a():
        return _resultado(
            "abrir_incidentes_automaticos", sucesso=True,
            dados={"falhas": [{"descricao": "Placa OYX9B42", "erro": "incidente já aberto"}]},
        )

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)

    await cat.executar_etapas_com_contexto([_etapa_fake("abrir_incidentes_automaticos", "etapa_fake_a")], {})

    conteudo = (tmp_path / "execucoes.log").read_text(encoding="utf-8")
    assert "etapa=abrir_incidentes_automaticos" in conteudo
    assert "Placa OYX9B42 — incidente já aberto" in conteudo


@pytest.mark.asyncio
async def test_registrar_log_arquivo_grava_mensagem_de_falha_geral(monkeypatch, tmp_path):
    async def _etapa_a():
        return _resultado("publicar_fila_operacional", sucesso=False, mensagem="date/time field value out of range")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)

    await cat.executar_etapas_com_contexto([_etapa_fake("publicar_fila_operacional", "etapa_fake_a")], {})

    conteudo = (tmp_path / "execucoes.log").read_text(encoding="utf-8")
    assert "sucesso=False" in conteudo
    assert "date/time field value out of range" in conteudo


@pytest.mark.asyncio
async def test_registrar_log_arquivo_falha_de_disco_nao_derruba_etapa(monkeypatch, tmp_path):
    def _diretorio_com_erro():
        raise OSError("disco cheio")

    monkeypatch.setattr(cat, "_diretorio_logs", _diretorio_com_erro)

    async def _etapa_a():
        return _resultado("a", sucesso=False, mensagem="deu ruim")

    monkeypatch.setattr(pipeline, "etapa_fake_a", _etapa_a, raising=False)

    execucao = await cat.executar_etapas_com_contexto([_etapa_fake("a", "etapa_fake_a")], {})

    assert execucao.resultados[0].sucesso is False  # a etapa em si não é afetada pela falha do log


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
