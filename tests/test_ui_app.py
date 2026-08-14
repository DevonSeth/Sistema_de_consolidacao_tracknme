import threading
import time

import pytest

from orchestrator import catalogo_etapas, pipeline
from ui import app


def _esperar(condicao, timeout=2.0, intervalo=0.01):
    fim = time.time() + timeout
    while time.time() < fim:
        if condicao():
            return
        time.sleep(intervalo)
    raise AssertionError("condição não satisfeita a tempo (timeout do teste)")


@pytest.fixture(autouse=True)
def _reset_estado():
    app._estado = app._EstadoExecucao()
    yield


# --------------------------------------------------------------------------
# _LoopAssincronoPersistente
# --------------------------------------------------------------------------

def test_loop_persistente_roda_corrotina_de_outra_thread():
    loop = app._LoopAssincronoPersistente()

    async def _somar(a, b):
        return a + b

    futuro = loop.submeter(_somar(2, 3))
    assert futuro.result(timeout=2) == 5


# --------------------------------------------------------------------------
# _status_da_etapa — 6 estados
# --------------------------------------------------------------------------

def _etapa_fake(etapa_id="etapa-1"):
    return catalogo_etapas.EtapaCatalogo(etapa_id, "A", "Etapa Fake", "nome_funcao_fake")


def test_status_da_etapa_ocioso_por_padrao():
    etapa = _etapa_fake()
    assert app._status_da_etapa(etapa, app._estado) == app.STATUS_OCIOSO


def test_status_da_etapa_rodando():
    etapa = _etapa_fake()
    app._estado.rodando = True
    app._estado.etapa_atual_id = etapa.id
    assert app._status_da_etapa(etapa, app._estado) == app.STATUS_RODANDO


def test_status_da_etapa_sucesso():
    etapa = _etapa_fake()
    app._estado.resultados[etapa.id] = pipeline.ResultadoEtapa(etapa.id, sucesso=True)
    assert app._status_da_etapa(etapa, app._estado) == app.STATUS_SUCESSO


def test_status_da_etapa_erro():
    etapa = _etapa_fake()
    app._estado.resultados[etapa.id] = pipeline.ResultadoEtapa(etapa.id, sucesso=False, mensagem="falhou")
    assert app._status_da_etapa(etapa, app._estado) == app.STATUS_ERRO


def test_status_da_etapa_aguardando_reconexao():
    etapa = _etapa_fake()
    app._estado.motivo_parada = "aguardando_reconexao"
    app._estado.etapa_travada_id = etapa.id
    assert app._status_da_etapa(etapa, app._estado) == app.STATUS_AGUARDANDO_RECONEXAO


def test_status_da_etapa_cancelada():
    etapa = _etapa_fake()
    app._estado.resultados[etapa.id] = pipeline.ResultadoEtapa(
        etapa.id, sucesso=False, cancelado={"pendentes": ["x"]}
    )
    assert app._status_da_etapa(etapa, app._estado) == app.STATUS_CANCELADA


# --------------------------------------------------------------------------
# Api.executar_etapas — fluxo feliz, segunda chamada recusada, trava
# --------------------------------------------------------------------------

def test_executar_etapas_roda_e_grava_resultado(monkeypatch):
    resultado_etapa = pipeline.ResultadoEtapa("baixar_relatorios", sucesso=True, dados={"ok": True})

    async def _executar_cadeia_fake(ids, modo, cancelar_checker=None, on_progresso=None, on_progresso_item=None, on_resultado=None, contexto=None):
        on_progresso("baixar_relatorios")
        on_resultado("baixar_relatorios", resultado_etapa)
        return catalogo_etapas.ExecucaoCadeia(resultados=[resultado_etapa])

    monkeypatch.setattr(catalogo_etapas, "executar_cadeia", _executar_cadeia_fake)

    resultado = app.Api().executar_etapas(["baixar_relatorios"], "selecionadas")
    assert resultado == {"iniciado": True}

    _esperar(lambda: not app._estado.rodando)
    assert app._estado.resultados["baixar_relatorios"].sucesso is True
    assert app._estado.motivo_parada is None


def test_executar_etapas_recusa_segunda_chamada_enquanto_rodando(monkeypatch):
    async def _executar_cadeia_fake(*a, **k):
        return catalogo_etapas.ExecucaoCadeia(resultados=[])

    monkeypatch.setattr(catalogo_etapas, "executar_cadeia", _executar_cadeia_fake)

    api = app.Api()
    resultado1 = api.executar_etapas(["baixar_relatorios"], "selecionadas")
    resultado2 = api.executar_etapas(["ler_planilha_gestor"], "selecionadas")

    assert resultado1 == {"iniciado": True}
    assert resultado2 == {"iniciado": False, "motivo": "ja_rodando"}

    _esperar(lambda: not app._estado.rodando)


def test_executar_etapas_trava_concorrente(monkeypatch):
    async def _executar_cadeia_fake(*a, **k):
        return catalogo_etapas.ExecucaoCadeia(motivo_parada="travado")

    monkeypatch.setattr(catalogo_etapas, "executar_cadeia", _executar_cadeia_fake)

    app.Api().executar_etapas(["baixar_relatorios"], "selecionadas")
    _esperar(lambda: not app._estado.rodando)

    assert app._estado.motivo_parada == "travado"


# --------------------------------------------------------------------------
# Api.cancelar_execucao
# --------------------------------------------------------------------------

def test_cancelar_execucao_recusa_quando_nao_esta_rodando():
    assert app.Api().cancelar_execucao() == {"aceito": False, "motivo": "nao_esta_rodando"}


def test_cancelar_execucao_aceita_quando_rodando(monkeypatch):
    evento_liberar = threading.Event()

    async def _executar_cadeia_fake(ids, modo, cancelar_checker=None, on_progresso=None, on_progresso_item=None, on_resultado=None, contexto=None):
        import asyncio

        await asyncio.to_thread(evento_liberar.wait)
        cancelado = cancelar_checker()
        return catalogo_etapas.ExecucaoCadeia(motivo_parada="cancelada" if cancelado else None, resultados=[])

    monkeypatch.setattr(catalogo_etapas, "executar_cadeia", _executar_cadeia_fake)

    api = app.Api()
    api.executar_etapas(["baixar_relatorios"], "selecionadas")

    resultado = api.cancelar_execucao()
    assert resultado == {"aceito": True}

    evento_liberar.set()
    _esperar(lambda: not app._estado.rodando)
    assert app._estado.motivo_parada == "cancelada"


# --------------------------------------------------------------------------
# progresso_item — atualiza durante execução, some ao final
# --------------------------------------------------------------------------

def test_progresso_item_atualiza_durante_execucao_e_some_ao_final(monkeypatch):
    evento_liberar = threading.Event()

    async def _executar_cadeia_fake(ids, modo, cancelar_checker=None, on_progresso=None, on_progresso_item=None, on_resultado=None, contexto=None):
        import asyncio

        on_progresso("baixar_relatorios")
        on_progresso_item("baixar_relatorios", 2, 5)
        await asyncio.to_thread(evento_liberar.wait)
        resultado = pipeline.ResultadoEtapa("baixar_relatorios", sucesso=True)
        on_resultado("baixar_relatorios", resultado)
        return catalogo_etapas.ExecucaoCadeia(resultados=[resultado])

    monkeypatch.setattr(catalogo_etapas, "executar_cadeia", _executar_cadeia_fake)

    app.Api().executar_etapas(["baixar_relatorios"], "selecionadas")

    _esperar(lambda: app._estado.progresso_item is not None)
    assert app._estado.progresso_item == {"etapa_id": "baixar_relatorios", "concluidos": 2, "total": 5}

    evento_liberar.set()
    _esperar(lambda: not app._estado.rodando)
    assert app._estado.progresso_item is None


# --------------------------------------------------------------------------
# Fluxo fim-a-fim de reconexão manual
# --------------------------------------------------------------------------

def test_fluxo_completo_de_reconexao_manual(monkeypatch):
    etapa_sga = catalogo_etapas.etapa_por_id("enriquecimento_sga")
    etapa_seguinte = catalogo_etapas.etapa_por_id("consolidar_com_sga")
    resultado_travado = pipeline.ResultadoEtapa(
        "enriquecimento_sga", sucesso=False, aguardando_reconexao={"pendentes": ["CHASSI-1"], "processados": {}}
    )

    async def _executar_cadeia_fake(ids, modo, cancelar_checker=None, on_progresso=None, on_progresso_item=None, on_resultado=None, contexto=None):
        on_progresso("enriquecimento_sga")
        on_resultado("enriquecimento_sga", resultado_travado)
        return catalogo_etapas.ExecucaoCadeia(
            resultados=[resultado_travado],
            motivo_parada="aguardando_reconexao",
            etapa_travada_id="enriquecimento_sga",
            etapas_restantes=[etapa_seguinte],
            execucao_id="exec-original",
        )

    monkeypatch.setattr(catalogo_etapas, "executar_cadeia", _executar_cadeia_fake)

    api = app.Api()
    api.executar_etapas(["enriquecimento_sga", "consolidar_com_sga"], "selecionadas")
    _esperar(lambda: not app._estado.rodando)

    assert app._estado.motivo_parada == "aguardando_reconexao"
    assert app._estado.etapa_travada_id == "enriquecimento_sga"
    assert app._estado.execucao_id == "exec-original"
    assert app._status_da_etapa(etapa_sga, app._estado) == app.STATUS_AGUARDANDO_RECONEXAO

    # confirma reconexão manual — reaproveita o MESMO execucao_id da execução original
    resultado_final = pipeline.ResultadoEtapa("consolidar_com_sga", sucesso=True, dados={})
    execucao_id_recebido = []

    async def _continuar_apos_reconexao_fake(
        etapa_travada, contexto, resultado_travado_arg, etapas_restantes,
        execucao_id=None, cancelar_checker=None, on_progresso=None, on_progresso_item=None, on_resultado=None,
    ):
        assert etapa_travada.id == "enriquecimento_sga"
        assert etapas_restantes == [etapa_seguinte]
        execucao_id_recebido.append(execucao_id)
        on_resultado("consolidar_com_sga", resultado_final)
        return catalogo_etapas.ExecucaoCadeia(resultados=[resultado_final], execucao_id=execucao_id)

    monkeypatch.setattr(catalogo_etapas, "continuar_apos_reconexao", _continuar_apos_reconexao_fake)

    resposta = api.continuar_apos_reconexao_manual()
    assert resposta == {"iniciado": True}

    _esperar(lambda: not app._estado.rodando)
    assert app._estado.motivo_parada is None
    assert execucao_id_recebido == ["exec-original"]
    assert app._estado.execucao_id == "exec-original"
    assert app._estado.resultados["consolidar_com_sga"].sucesso is True


def test_continuar_apos_reconexao_manual_sem_nada_travado():
    assert app.Api().continuar_apos_reconexao_manual() == {"iniciado": False, "motivo": "nada_aguardando_reconexao"}


# --------------------------------------------------------------------------
# listar_etapas_com_status
# --------------------------------------------------------------------------

def test_listar_etapas_com_status_inclui_todas_as_etapas_e_contagem(monkeypatch):
    monkeypatch.setattr(
        app.supabase_client, "contar_pendencias_por_origem",
        lambda: {"manutencao": 3, "instalacao": 1, "remocao": 0},
    )

    resultado = app.Api().listar_etapas_com_status()

    assert resultado["contagem_por_origem"] == {"manutencao": 3, "instalacao": 1, "remocao": 0}
    assert len(resultado["etapas"]) == len(catalogo_etapas.CATALOGO)
    assert all(e["status"] == app.STATUS_OCIOSO for e in resultado["etapas"])
    ids = [e["id"] for e in resultado["etapas"]]
    assert ids == [etapa.id for etapa in catalogo_etapas.CATALOGO]


def test_listar_etapas_com_status_mensagem_erro_so_aparece_em_falha_real(monkeypatch):
    monkeypatch.setattr(app.supabase_client, "contar_pendencias_por_origem", lambda: {})
    etapa_erro, etapa_sucesso, etapa_cancelada = catalogo_etapas.CATALOGO[0:3]
    app._estado.resultados = {
        etapa_erro.id: pipeline.ResultadoEtapa(etapa_erro.id, sucesso=False, mensagem="Falha ao baixar relatório(s): [...]"),
        etapa_sucesso.id: pipeline.ResultadoEtapa(etapa_sucesso.id, sucesso=True, mensagem="ignorada"),
        etapa_cancelada.id: pipeline.ResultadoEtapa(etapa_cancelada.id, sucesso=False, mensagem="ignorada", cancelado={}),
    }

    por_id = {e["id"]: e["mensagem_erro"] for e in app.Api().listar_etapas_com_status()["etapas"]}

    assert por_id[etapa_erro.id] == "Falha ao baixar relatório(s): [...]"
    assert por_id[etapa_sucesso.id] is None
    assert por_id[etapa_cancelada.id] is None


# --------------------------------------------------------------------------
# obter_dashboards_operador / obter_progresso_atual
# --------------------------------------------------------------------------

def test_obter_dashboards_operador_devolve_shape_do_modulo(monkeypatch):
    from orchestrator import dashboards_operador

    fake = {
        "fila_prioridade": [],
        "sem_atendimento": [],
        "funil_status": {"pendente": 1},
        "situacao_manual_parada": [],
        "contato_invalido": [],
        "discrepancia_remocao_nao_revisada": [],
    }
    monkeypatch.setattr(dashboards_operador, "montar_dashboards_operador", lambda: fake)
    assert app.Api().obter_dashboards_operador() == fake


def test_obter_metricas_admin_operador_devolve_shape_do_modulo(monkeypatch):
    from orchestrator import metricas_admin_operador

    fake = {"metricas_simples": {"pendencias_em_aberto": 5}, "metricas_lista": {"pendentes_por_cidade": []}}
    monkeypatch.setattr(metricas_admin_operador, "montar_metricas_admin_operador", lambda desde, ate: fake)

    assert app.Api().obter_metricas_admin_operador() == fake


def test_obter_metricas_admin_operador_converte_desde_ate_pra_date(monkeypatch):
    from datetime import date

    from orchestrator import metricas_admin_operador

    capturado = {}

    def _fake(desde, ate):
        capturado["desde"] = desde
        capturado["ate"] = ate
        return {}

    monkeypatch.setattr(metricas_admin_operador, "montar_metricas_admin_operador", _fake)

    app.Api().obter_metricas_admin_operador("2026-07-01", "2026-08-14")

    assert capturado == {"desde": date(2026, 7, 1), "ate": date(2026, 8, 14)}


def test_obter_metricas_admin_operador_sem_argumentos_passa_none(monkeypatch):
    from orchestrator import metricas_admin_operador

    capturado = {}

    def _fake(desde, ate):
        capturado["desde"] = desde
        capturado["ate"] = ate
        return {}

    monkeypatch.setattr(metricas_admin_operador, "montar_metricas_admin_operador", _fake)

    app.Api().obter_metricas_admin_operador()

    assert capturado == {"desde": None, "ate": None}


def test_obter_status_watchdog_devolve_shape_do_modulo(monkeypatch):
    fake = {"execucao_travada": None, "etapas_com_falha": [], "etapas_lentas": []}
    monkeypatch.setattr(app.watchdog, "avaliar_watchdog", lambda: fake)

    assert app.Api().obter_status_watchdog() == fake


def test_obter_progresso_atual_sempre_serializavel():
    resultado = app.Api().obter_progresso_atual()

    assert resultado == {
        "rodando": False,
        "etapa_atual_id": None,
        "motivo_parada": None,
        "etapa_travada_id": None,
        "etapas_restantes": [],
        "progresso_item": None,
        "erro_geral": None,
        "mensagem_etapa_travada": None,
    }


def test_obter_progresso_atual_expoe_mensagem_da_etapa_travada():
    app._estado.resultado_travado = pipeline.ResultadoEtapa(
        "enriquecimento_sga", sucesso=False,
        mensagem="Sessão caída — aguardando reconexão manual (2 pendente(s)).",
        aguardando_reconexao={"pendentes": ["CHASSI-1", "CHASSI-2"]},
    )

    resultado = app.Api().obter_progresso_atual()

    assert resultado["mensagem_etapa_travada"] == "Sessão caída — aguardando reconexão manual (2 pendente(s))."


# --------------------------------------------------------------------------
# Bloqueio de fechamento da janela durante execução
# --------------------------------------------------------------------------

def test_execucao_em_andamento_falso_quando_ocioso():
    assert app._execucao_em_andamento() is False


def test_execucao_em_andamento_verdadeiro_quando_rodando():
    app._estado.rodando = True
    assert app._execucao_em_andamento() is True


def test_execucao_em_andamento_verdadeiro_quando_aguardando_reconexao():
    app._estado.rodando = False
    app._estado.motivo_parada = "aguardando_reconexao"
    assert app._execucao_em_andamento() is True


def test_ao_fechar_permite_fechar_quando_ocioso(monkeypatch):
    chamou_aviso = []
    monkeypatch.setattr(app, "_avisar_bloqueio_fechamento", lambda: chamou_aviso.append(True))

    assert app._ao_fechar() is True
    assert chamou_aviso == []


def test_ao_fechar_bloqueia_e_avisa_quando_rodando(monkeypatch):
    chamou_aviso = []
    monkeypatch.setattr(app, "_avisar_bloqueio_fechamento", lambda: chamou_aviso.append(True))
    app._estado.rodando = True

    assert app._ao_fechar() is False
    assert chamou_aviso == [True]


def test_ao_fechar_bloqueia_e_avisa_quando_aguardando_reconexao(monkeypatch):
    chamou_aviso = []
    monkeypatch.setattr(app, "_avisar_bloqueio_fechamento", lambda: chamou_aviso.append(True))
    app._estado.motivo_parada = "aguardando_reconexao"

    assert app._ao_fechar() is False
    assert chamou_aviso == [True]


def test_main_registra_ao_fechar_na_janela(monkeypatch):
    import sys
    import types

    class _EventoFalso:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    class _EventosFalsos:
        def __init__(self):
            self.closing = _EventoFalso()

    janelas_criadas = []

    class _JanelaFalsa:
        def __init__(self):
            self.events = _EventosFalsos()
            janelas_criadas.append(self)

    chamadas = {}

    def _create_window(*args, **kwargs):
        chamadas["create_window"] = (args, kwargs)
        return _JanelaFalsa()

    def _start(*args, **kwargs):
        chamadas["start"] = True

    webview_falso = types.SimpleNamespace(create_window=_create_window, start=_start)
    monkeypatch.setitem(sys.modules, "webview", webview_falso)

    app.main()

    assert chamadas["start"] is True
    _args, kwargs = chamadas["create_window"]
    assert kwargs["js_api"].__class__ is app.Api
    assert janelas_criadas[0].events.closing.handlers == [app._ao_fechar]
