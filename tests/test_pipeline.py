from datetime import datetime
from pathlib import Path

import pytest

from core.constants import COL_RASTREADORES_CHASSI
from core.dedup import gerar_chave_unica
from orchestrator import pipeline as orch


# --- etapa_baixar_relatorios ------------------------------------------------

@pytest.mark.asyncio
async def test_etapa_baixar_relatorios_sucesso(monkeypatch):
    async def _baixar_relatorios_fake():
        return {"incidentes": "downloads/incidentes.xlsx", "rastreadores_ativos": "downloads/rastreadores_ativos.xlsx"}

    monkeypatch.setattr(orch.tracknme_bot, "baixar_relatorios", _baixar_relatorios_fake)

    resultado = await orch.etapa_baixar_relatorios()

    assert resultado.etapa == "baixar_relatorios"
    assert resultado.sucesso is True
    assert resultado.dados == {
        "incidentes": "downloads/incidentes.xlsx",
        "rastreadores_ativos": "downloads/rastreadores_ativos.xlsx",
    }


@pytest.mark.asyncio
async def test_etapa_baixar_relatorios_falha_nao_propaga_excecao(monkeypatch):
    async def _baixar_relatorios_fake():
        raise RuntimeError("Falha ao baixar relatório(s)")

    monkeypatch.setattr(orch.tracknme_bot, "baixar_relatorios", _baixar_relatorios_fake)

    resultado = await orch.etapa_baixar_relatorios()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Falha ao baixar relatório(s)"
    assert resultado.dados == {}


# --- etapa_ler_planilha_gestor ----------------------------------------------

def _ler_linhas_xlsx_fake(caminho):
    nome = Path(caminho).name
    if nome == "incidentes.xlsx":
        return [
            ["ID", "Placa", "Linha"],
            ["1", "ABC1234", 12345],
            ["2", None, "9"],
        ]
    if nome == "rastreadores_ativos.xlsx":
        return [
            ["ID", "Modelo", "Imei"],
            ["10", "J16", None],
        ]
    raise AssertionError(f"caminho inesperado: {caminho}")


def _preparar_mocks_sucesso(monkeypatch):
    chamadas_carregar = []

    def _carregar_administrador_fake(aba, linhas):
        chamadas_carregar.append((aba, linhas))

    def _ler_aba_fake(planilha, aba):
        assert planilha == orch.google_sheets_client.NOME_PLANILHA_ADMINISTRADOR
        assert aba == "Instalação-Remoção"
        return [{"Chassi": "XYZ0000000000001", "Serviço": "Retirada"}]

    monkeypatch.setattr(orch.tracknme_bot, "ler_linhas_xlsx", _ler_linhas_xlsx_fake)
    monkeypatch.setattr(orch.google_sheets_client, "carregar_administrador", _carregar_administrador_fake)
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_fake)
    return chamadas_carregar


def test_etapa_ler_planilha_gestor_sucesso_com_caminhos_explicitos(monkeypatch):
    chamadas_carregar = _preparar_mocks_sucesso(monkeypatch)

    resultado = orch.etapa_ler_planilha_gestor(
        {"incidentes": "downloads/incidentes.xlsx", "rastreadores_ativos": "downloads/rastreadores_ativos.xlsx"}
    )

    assert resultado.sucesso is True
    assert resultado.dados["incidentes"] == [
        {"ID": "1", "Placa": "ABC1234", "Linha": "12345"},
        {"ID": "2", "Placa": "", "Linha": "9"},
    ]
    assert resultado.dados["equipamentos"] == [
        {"col_1": "10", "col_2": "J16", "col_3": ""},
    ]
    assert resultado.dados["instalacao_remocao"] == [{"Chassi": "XYZ0000000000001", "Serviço": "Retirada"}]

    assert chamadas_carregar == [
        ("Incidentes", [["1", "ABC1234", "12345"], ["2", "", "9"]]),
        ("Rastreadores Ativos", [["10", "J16", ""]]),
    ]


def test_etapa_ler_planilha_gestor_normaliza_int_e_none(monkeypatch):
    """Achado com os xlsx reais: 'Linha' de Incidentes vem como int, células
    vazias como None — nunca deveriam sobrar como não-string no resultado."""
    _preparar_mocks_sucesso(monkeypatch)

    resultado = orch.etapa_ler_planilha_gestor(
        {"incidentes": "downloads/incidentes.xlsx", "rastreadores_ativos": "downloads/rastreadores_ativos.xlsx"}
    )

    valores = [v for linha in resultado.dados["incidentes"] for v in linha.values()]
    valores += [v for linha in resultado.dados["equipamentos"] for v in linha.values()]
    assert all(isinstance(v, str) for v in valores)


def test_etapa_ler_planilha_gestor_usa_caminhos_padrao_quando_none(monkeypatch):
    caminhos_recebidos = []

    def _ler_linhas_xlsx_fake_com_registro(caminho):
        caminhos_recebidos.append(Path(caminho))
        return _ler_linhas_xlsx_fake(caminho)

    monkeypatch.setattr(orch.tracknme_bot, "ler_linhas_xlsx", _ler_linhas_xlsx_fake_com_registro)
    monkeypatch.setattr(orch.google_sheets_client, "carregar_administrador", lambda aba, linhas: None)
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", lambda planilha, aba: [])

    resultado = orch.etapa_ler_planilha_gestor()

    assert resultado.sucesso is True
    esperado = orch._caminhos_padrao()
    assert caminhos_recebidos == [Path(esperado["incidentes"]), Path(esperado["rastreadores_ativos"])]


def test_etapa_ler_planilha_gestor_falha_em_ler_linhas_xlsx(monkeypatch):
    def _ler_linhas_xlsx_fake_falha(caminho):
        raise RuntimeError("xlsx corrompido")

    monkeypatch.setattr(orch.tracknme_bot, "ler_linhas_xlsx", _ler_linhas_xlsx_fake_falha)

    resultado = orch.etapa_ler_planilha_gestor(
        {"incidentes": "downloads/incidentes.xlsx", "rastreadores_ativos": "downloads/rastreadores_ativos.xlsx"}
    )

    assert resultado.sucesso is False
    assert resultado.mensagem == "xlsx corrompido"


def test_etapa_ler_planilha_gestor_falha_em_carregar_administrador(monkeypatch):
    monkeypatch.setattr(orch.tracknme_bot, "ler_linhas_xlsx", _ler_linhas_xlsx_fake)

    def _carregar_administrador_falha(aba, linhas):
        raise RuntimeError("Sheets indisponível")

    monkeypatch.setattr(orch.google_sheets_client, "carregar_administrador", _carregar_administrador_falha)

    resultado = orch.etapa_ler_planilha_gestor(
        {"incidentes": "downloads/incidentes.xlsx", "rastreadores_ativos": "downloads/rastreadores_ativos.xlsx"}
    )

    assert resultado.sucesso is False
    assert resultado.mensagem == "Sheets indisponível"


def test_etapa_ler_planilha_gestor_falha_em_ler_aba(monkeypatch):
    monkeypatch.setattr(orch.tracknme_bot, "ler_linhas_xlsx", _ler_linhas_xlsx_fake)
    monkeypatch.setattr(orch.google_sheets_client, "carregar_administrador", lambda aba, linhas: None)

    def _ler_aba_falha(planilha, aba):
        raise RuntimeError("Instalação-Remoção inacessível")

    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_falha)

    resultado = orch.etapa_ler_planilha_gestor(
        {"incidentes": "downloads/incidentes.xlsx", "rastreadores_ativos": "downloads/rastreadores_ativos.xlsx"}
    )

    assert resultado.sucesso is False
    assert resultado.mensagem == "Instalação-Remoção inacessível"


# --- etapa_motor_de_regras ---------------------------------------------------

_DADOS_FAKE = {
    "incidentes": [{"ID": "1"}, {"ID": "2"}],
    "equipamentos": [{"col_1": "10"}],
    "instalacao_remocao": [{"Chassi": "XYZ"}],
}

_GRUPOS_FAKE = {
    "grupo_1_abrir": [{"placa": "ABC1234"}],
    "grupo_2_concluir": [],
    "grupo_3_tratativa_humana": [{"id": "1", "codigo_regra": "REGRA_1"}],
}


def _preparar_mocks_motor_de_regras(monkeypatch):
    chamadas = {"parametros": 0, "templates": 0, "classificar": []}

    def _buscar_parametros_fake():
        chamadas["parametros"] += 1
        return {"tempo_limiar_inatividade_horas": 48}

    def _buscar_rule_templates_fake():
        chamadas["templates"] += 1
        return {"REGRA_1": {"nivel_urgencia": 5}}

    def _classificar_incidentes_fake(incidentes, equipamentos, instalacao_remocao, parametros, templates, agora=None):
        chamadas["classificar"].append((incidentes, equipamentos, instalacao_remocao, parametros, templates))
        return _GRUPOS_FAKE

    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", _buscar_parametros_fake)
    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", _buscar_rule_templates_fake)
    monkeypatch.setattr(orch.motor_regras, "classificar_incidentes", _classificar_incidentes_fake)
    return chamadas


def _ler_aba_nao_deveria_ser_chamado(planilha, aba):
    raise AssertionError("não deveria ler Sheets quando 'dados' é explícito")


def test_etapa_motor_de_regras_sucesso_com_dados_explicitos_busca_config_uma_vez(monkeypatch):
    chamadas = _preparar_mocks_motor_de_regras(monkeypatch)
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_nao_deveria_ser_chamado)

    resultado = orch.etapa_motor_de_regras(_DADOS_FAKE)

    assert resultado.sucesso is True
    assert resultado.dados == _GRUPOS_FAKE
    # parametros/templates buscados 1x só, mesmo com 2 incidentes no fixture
    # (decisão: classificar_incidentes já processa a lista inteira numa só
    # chamada, não existe "por item" no orchestrator).
    assert chamadas["parametros"] == 1
    assert chamadas["templates"] == 1
    assert len(chamadas["classificar"]) == 1
    incidentes, equipamentos, instalacao_remocao, _parametros, _templates = chamadas["classificar"][0]
    assert incidentes == _DADOS_FAKE["incidentes"]
    assert equipamentos == _DADOS_FAKE["equipamentos"]
    assert instalacao_remocao == _DADOS_FAKE["instalacao_remocao"]


def test_etapa_motor_de_regras_le_administrador_quando_dados_none(monkeypatch):
    _preparar_mocks_motor_de_regras(monkeypatch)
    abas_lidas = []
    dados_por_aba = {
        "Incidentes": _DADOS_FAKE["incidentes"],
        "Rastreadores Ativos": _DADOS_FAKE["equipamentos"],
        "Instalação-Remoção": _DADOS_FAKE["instalacao_remocao"],
    }

    def _ler_aba_fake(planilha, aba):
        abas_lidas.append((planilha, aba))
        return dados_por_aba[aba]

    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_fake)

    resultado = orch.etapa_motor_de_regras()

    assert resultado.sucesso is True
    assert resultado.dados == _GRUPOS_FAKE
    assert abas_lidas == [
        (orch.google_sheets_client.NOME_PLANILHA_ADMINISTRADOR, "Incidentes"),
        (orch.google_sheets_client.NOME_PLANILHA_ADMINISTRADOR, "Rastreadores Ativos"),
        (orch.google_sheets_client.NOME_PLANILHA_ADMINISTRADOR, "Instalação-Remoção"),
    ]


def test_etapa_motor_de_regras_falha_ao_ler_administrador(monkeypatch):
    def _ler_aba_falha(planilha, aba):
        raise RuntimeError("Sheets indisponível")

    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_falha)

    resultado = orch.etapa_motor_de_regras()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Sheets indisponível"


def test_etapa_motor_de_regras_falha_ao_buscar_parametros(monkeypatch):
    def _buscar_parametros_falha():
        raise RuntimeError("Supabase indisponível")

    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", _buscar_parametros_falha)

    resultado = orch.etapa_motor_de_regras(_DADOS_FAKE)

    assert resultado.sucesso is False
    assert resultado.mensagem == "Supabase indisponível"


def test_etapa_motor_de_regras_falha_ao_buscar_templates(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})

    def _buscar_templates_falha():
        raise RuntimeError("rule_templates indisponível")

    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", _buscar_templates_falha)

    resultado = orch.etapa_motor_de_regras(_DADOS_FAKE)

    assert resultado.sucesso is False
    assert resultado.mensagem == "rule_templates indisponível"


def test_etapa_motor_de_regras_falha_ao_classificar(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: {})

    def _classificar_falha(*args, **kwargs):
        raise RuntimeError("regra inválida")

    monkeypatch.setattr(orch.motor_regras, "classificar_incidentes", _classificar_falha)

    resultado = orch.etapa_motor_de_regras(_DADOS_FAKE)

    assert resultado.sucesso is False
    assert resultado.mensagem == "regra inválida"


# --- etapa_abrir_incidentes_automaticos / etapa_fechar_incidentes_automaticos ---
# (2026-08-07: dividida de `etapa_reconciliacao_automatica`, pra caber a
# consulta ao SGA entre abrir e fechar — ver core/motor_regras.aplicar_situacoes_sga)

_GRUPO_1_ABRIR_FAKE = [
    {"placa": "ABC1234", "chassi": "X1", "chassi_sga": "X1", "imei": "111", "cliente": "Fulano"}
]
_GRUPO_2_CONCLUIR_FAKE = [{
    "id": "555", "placa": "DEF5678", "chassi": "X2", "chassi_sga": "X2", "imei": "222", "cliente": "Ciclano",
    "codigo_regra": "REGRA_2", "acao_sugerida": "Conclui incidente automaticamente.",
    "observacao_sistema": "Voltou a comunicar dentro de 48h.", "nivel_urgencia": 1,
}]
_DADOS_GRUPOS_FAKE = {
    "grupo_1_abrir": _GRUPO_1_ABRIR_FAKE,
    "grupo_2_concluir": _GRUPO_2_CONCLUIR_FAKE,
    "grupo_3_tratativa_humana": [],
}


class _PageFalsaReconciliacao:
    async def close(self):
        pass


class _ContextoFalsoReconciliacao:
    def __init__(self):
        self.fechado = False

    async def new_page(self):
        return _PageFalsaReconciliacao()

    async def close(self):
        self.fechado = True


class _BrowserFalsoReconciliacao:
    def __init__(self):
        self.fechado = False

    async def close(self):
        self.fechado = True


class _PlaywrightCtxFalso:
    async def __aenter__(self):
        return "playwright-fake"

    async def __aexit__(self, *args):
        return False


async def _sempre_sucesso(*args, **kwargs):
    return "ok"


def _preparar_mocks_playwright(monkeypatch):
    contexto = _ContextoFalsoReconciliacao()
    browser = _BrowserFalsoReconciliacao()

    async def _abrir_navegador_autenticado_fake(playwright):
        return browser, contexto

    monkeypatch.setattr(orch, "async_playwright", lambda: _PlaywrightCtxFalso())
    monkeypatch.setattr(orch.tracknme_bot, "abrir_navegador_autenticado", _abrir_navegador_autenticado_fake)
    # `tracknme_http_habilitado` ausente = kill switch desligado (default) --
    # `_etapa_incidente_2_estagios` sempre lê `buscar_parametros()`, mesmo
    # com o kill switch desligado, então precisa de um mock aqui pra não
    # bater na rede de verdade (mesmo padrão de `_preparar_mocks_sga`).
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    return contexto, browser


@pytest.mark.asyncio
async def test_processar_fila_com_navegador_propaga_aguardando_reconexao(monkeypatch):
    contexto, browser = _preparar_mocks_playwright(monkeypatch)

    async def _processar_fila_levanta_reconexao(contexto_arg, itens, acao, on_progresso=None, cancelar_checker=None, on_item_iniciado=None):
        raise orch.playwright_utils.AguardandoReconexao(
            pendentes=["item-pendente"],
            processados=[
                orch.playwright_utils.ResultadoItem(item="item-ok", sucesso=True, resultado={"linha": "ok"})
            ],
        )

    monkeypatch.setattr(orch.playwright_utils, "processar_fila", _processar_fila_levanta_reconexao)

    resultados, erro, reconexao, cancelamento = await orch._processar_fila_com_navegador(
        ["item-ok", "item-pendente"], _sempre_sucesso
    )

    assert resultados == []
    assert erro is None
    assert reconexao is not None
    assert reconexao.pendentes == ["item-pendente"]
    assert cancelamento is None
    assert contexto.fechado is True
    assert browser.fechado is True


@pytest.mark.asyncio
async def test_processar_fila_com_navegador_propaga_cancelamento(monkeypatch):
    contexto, browser = _preparar_mocks_playwright(monkeypatch)

    async def _processar_fila_levanta_cancelamento(contexto_arg, itens, acao, on_progresso=None, cancelar_checker=None, on_item_iniciado=None):
        raise orch.playwright_utils.CancelamentoSolicitado(
            pendentes=["item-pendente"],
            processados=[
                orch.playwright_utils.ResultadoItem(item="item-ok", sucesso=True, resultado={"linha": "ok"})
            ],
        )

    monkeypatch.setattr(orch.playwright_utils, "processar_fila", _processar_fila_levanta_cancelamento)

    resultados, erro, reconexao, cancelamento = await orch._processar_fila_com_navegador(
        ["item-ok", "item-pendente"], _sempre_sucesso
    )

    assert resultados == []
    assert erro is None
    assert reconexao is None
    assert cancelamento is not None
    assert cancelamento.pendentes == ["item-pendente"]
    assert contexto.fechado is True
    assert browser.fechado is True


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_sucesso(monkeypatch):
    contexto, browser = _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente", _sempre_sucesso)

    resultado = await orch.etapa_abrir_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.sucesso is True
    assert resultado.dados["abertos"] == _GRUPO_1_ABRIR_FAKE
    assert resultado.dados["falhas"] == []
    assert contexto.fechado is True
    assert browser.fechado is True


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_falha_de_item_nao_derruba_a_etapa(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)

    async def _abrir_incidente_falha(page, placa, cliente):
        raise RuntimeError("Veículo não encontrado ou contrato inativo")

    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente", _abrir_incidente_falha)

    resultado = await orch.etapa_abrir_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.sucesso is True
    assert resultado.dados["abertos"] == []
    assert len(resultado.dados["falhas"]) == 1
    assert resultado.dados["falhas"][0]["linha"] == _GRUPO_1_ABRIR_FAKE[0]
    assert "Veículo não encontrado" in resultado.dados["falhas"][0]["erro"]


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_falha_inclui_descricao(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)

    async def _abrir_incidente_falha(page, placa, cliente):
        raise RuntimeError("Veículo não encontrado ou contrato inativo")

    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente", _abrir_incidente_falha)

    resultado = await orch.etapa_abrir_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.dados["falhas"][0]["descricao"] == "Placa ABC1234 — Fulano"


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_repassa_on_worker_status(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente", _sempre_sucesso)
    chamadas = []

    resultado = await orch.etapa_abrir_incidentes_automaticos(
        _DADOS_GRUPOS_FAKE,
        on_worker_status=lambda worker_id, descricao: chamadas.append((worker_id, descricao)),
    )

    assert resultado.sucesso is True
    assert chamadas == [(0, "Placa ABC1234 — Fulano")]


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_repassa_on_progresso(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente", _sempre_sucesso)
    chamadas_progresso = []

    resultado = await orch.etapa_abrir_incidentes_automaticos(
        _DADOS_GRUPOS_FAKE,
        on_progresso=lambda concluidos, total: chamadas_progresso.append((concluidos, total)),
    )

    assert resultado.sucesso is True
    assert chamadas_progresso == [(1, 1)]


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_dados_none_usa_motor_de_regras(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente", _sempre_sucesso)

    def _etapa_motor_de_regras_fake(dados=None):
        assert dados is None
        return orch.ResultadoEtapa("motor_de_regras", sucesso=True, dados=_DADOS_GRUPOS_FAKE)

    monkeypatch.setattr(orch, "etapa_motor_de_regras", _etapa_motor_de_regras_fake)

    resultado = await orch.etapa_abrir_incidentes_automaticos()

    assert resultado.sucesso is True
    assert resultado.dados["abertos"] == _GRUPO_1_ABRIR_FAKE


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_propaga_falha_do_motor_sem_abrir_navegador(monkeypatch):
    chamou_navegador = False

    async def _abrir_navegador_autenticado_nao_deveria_ser_chamado(playwright):
        nonlocal chamou_navegador
        chamou_navegador = True
        raise AssertionError("não deveria abrir navegador")

    monkeypatch.setattr(
        orch.tracknme_bot, "abrir_navegador_autenticado", _abrir_navegador_autenticado_nao_deveria_ser_chamado
    )

    def _etapa_motor_de_regras_falha(dados=None):
        return orch.ResultadoEtapa("motor_de_regras", sucesso=False, mensagem="Supabase indisponível")

    monkeypatch.setattr(orch, "etapa_motor_de_regras", _etapa_motor_de_regras_falha)

    resultado = await orch.etapa_abrir_incidentes_automaticos()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Supabase indisponível"
    assert chamou_navegador is False


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_falha_ao_abrir_navegador(monkeypatch):
    monkeypatch.setattr(orch, "async_playwright", lambda: _PlaywrightCtxFalso())
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})

    async def _abrir_navegador_autenticado_falha(playwright):
        raise RuntimeError("Login automático falhou")

    monkeypatch.setattr(orch.tracknme_bot, "abrir_navegador_autenticado", _abrir_navegador_autenticado_falha)

    resultado = await orch.etapa_abrir_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.sucesso is False
    assert resultado.mensagem == "Login automático falhou"


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_sessao_caida_preserva_sucesso_anterior(monkeypatch):
    contexto, browser = _preparar_mocks_playwright(monkeypatch)
    grupo_1_dois_itens = [
        {"placa": "SUCESSO1", "chassi": "X1", "imei": "111", "cliente": "Fulano"},
        {"placa": "SESSAOCAI", "chassi": "X9", "imei": "999", "cliente": "Beltrano"},
    ]
    dados = {**_DADOS_GRUPOS_FAKE, "grupo_1_abrir": grupo_1_dois_itens}

    async def _abrir_incidente_sessao_caida(page, placa, cliente):
        if placa == "SESSAOCAI":
            raise orch.playwright_utils.SessaoCaidaError("sessão caiu")
        return f"Incidente aberto para {placa}"

    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente", _abrir_incidente_sessao_caida)

    resultado = await orch.etapa_abrir_incidentes_automaticos(dados)

    assert resultado.sucesso is False
    assert resultado.aguardando_reconexao == {"pendentes": [grupo_1_dois_itens[1]]}
    assert resultado.dados["abertos"] == [grupo_1_dois_itens[0]]
    assert contexto.fechado is True
    assert browser.fechado is True


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_cancelado_preserva_sucesso_anterior(monkeypatch):
    contexto, browser = _preparar_mocks_playwright(monkeypatch)

    async def _processar_fila_levanta_cancelamento(contexto_arg, itens, acao, on_progresso=None, cancelar_checker=None, on_item_iniciado=None):
        raise orch.playwright_utils.CancelamentoSolicitado(
            pendentes=[{"placa": "PENDENTE"}],
            processados=[
                orch.playwright_utils.ResultadoItem(
                    item={"placa": "FEITO"}, sucesso=True, resultado={"linha": {"placa": "FEITO"}}
                )
            ],
        )

    monkeypatch.setattr(orch.playwright_utils, "processar_fila", _processar_fila_levanta_cancelamento)

    resultado = await orch.etapa_abrir_incidentes_automaticos(
        _DADOS_GRUPOS_FAKE, cancelar_checker=lambda: True
    )

    assert resultado.sucesso is False
    assert resultado.cancelado == {"pendentes": [{"placa": "PENDENTE"}]}
    assert resultado.dados["abertos"] == [{"placa": "FEITO"}]
    assert contexto.fechado is True
    assert browser.fechado is True


@pytest.mark.asyncio
async def test_etapa_fechar_incidentes_automaticos_sucesso(monkeypatch):
    contexto, browser = _preparar_mocks_playwright(monkeypatch)
    chamadas_concluir = []

    async def _concluir_incidente_fake(page, placa, motivo, numero_incidente=None):
        chamadas_concluir.append({"placa": placa, "motivo": motivo, "numero_incidente": numero_incidente})
        return f"Incidente {numero_incidente} concluído"

    monkeypatch.setattr(orch.tracknme_bot, "concluir_incidente", _concluir_incidente_fake)

    resultado = await orch.etapa_fechar_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.sucesso is True
    assert resultado.dados["concluidos"] == _GRUPO_2_CONCLUIR_FAKE
    assert resultado.dados["falhas"] == []
    # motivo = observacao_sistema, numero_incidente = id — decisões de negócio fechadas na conversa.
    assert chamadas_concluir == [
        {"placa": "DEF5678", "motivo": "Voltou a comunicar dentro de 48h.", "numero_incidente": "555"}
    ]
    assert contexto.fechado is True
    assert browser.fechado is True


@pytest.mark.asyncio
async def test_etapa_fechar_incidentes_automaticos_sessao_caida_preserva_sucesso_anterior(monkeypatch):
    contexto, browser = _preparar_mocks_playwright(monkeypatch)
    grupo_2_dois_itens = [
        {
            "id": "1", "placa": "SUCESSO1", "chassi": "X1", "imei": "111", "cliente": "Fulano",
            "codigo_regra": "REGRA_2", "acao_sugerida": "Conclui incidente automaticamente.",
            "observacao_sistema": "motivo-1", "nivel_urgencia": 1,
        },
        {
            "id": "2", "placa": "SESSAOCAI", "chassi": "X9", "imei": "999", "cliente": "Beltrano",
            "codigo_regra": "REGRA_2", "acao_sugerida": "Conclui incidente automaticamente.",
            "observacao_sistema": "motivo-2", "nivel_urgencia": 1,
        },
    ]
    dados = {**_DADOS_GRUPOS_FAKE, "grupo_2_concluir": grupo_2_dois_itens}

    async def _concluir_incidente_sessao_caida(page, placa, motivo, numero_incidente=None):
        if placa == "SESSAOCAI":
            raise orch.playwright_utils.SessaoCaidaError("sessão caiu")
        return f"Incidente {numero_incidente} concluído"

    monkeypatch.setattr(orch.tracknme_bot, "concluir_incidente", _concluir_incidente_sessao_caida)

    resultado = await orch.etapa_fechar_incidentes_automaticos(dados)

    assert resultado.sucesso is False
    assert resultado.aguardando_reconexao == {"pendentes": [grupo_2_dois_itens[1]]}
    assert resultado.dados["concluidos"] == [grupo_2_dois_itens[0]]
    assert contexto.fechado is True
    assert browser.fechado is True


@pytest.mark.asyncio
async def test_etapa_fechar_incidentes_automaticos_cancelado_preserva_sucesso_anterior(monkeypatch):
    contexto, browser = _preparar_mocks_playwright(monkeypatch)

    async def _processar_fila_levanta_cancelamento(contexto_arg, itens, acao, on_progresso=None, cancelar_checker=None, on_item_iniciado=None):
        raise orch.playwright_utils.CancelamentoSolicitado(
            pendentes=[{"placa": "PENDENTE"}],
            processados=[
                orch.playwright_utils.ResultadoItem(
                    item={"placa": "FEITO"}, sucesso=True, resultado={"linha": {"placa": "FEITO"}}
                )
            ],
        )

    monkeypatch.setattr(orch.playwright_utils, "processar_fila", _processar_fila_levanta_cancelamento)

    resultado = await orch.etapa_fechar_incidentes_automaticos(
        _DADOS_GRUPOS_FAKE, cancelar_checker=lambda: True
    )

    assert resultado.sucesso is False
    assert resultado.cancelado == {"pendentes": [{"placa": "PENDENTE"}]}
    assert resultado.dados["concluidos"] == [{"placa": "FEITO"}]
    assert contexto.fechado is True
    assert browser.fechado is True


@pytest.mark.asyncio
async def test_etapa_fechar_incidentes_automaticos_repassa_on_progresso(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(orch.tracknme_bot, "concluir_incidente", _sempre_sucesso)
    chamadas_progresso = []

    resultado = await orch.etapa_fechar_incidentes_automaticos(
        _DADOS_GRUPOS_FAKE,
        on_progresso=lambda concluidos, total: chamadas_progresso.append((concluidos, total)),
    )

    assert resultado.sucesso is True
    assert chamadas_progresso == [(1, 1)]


@pytest.mark.asyncio
async def test_etapa_fechar_incidentes_automaticos_falha_de_item_nao_derruba_a_etapa(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)

    async def _concluir_incidente_falha(page, placa, motivo, numero_incidente=None):
        raise RuntimeError("Nenhum incidente aberto encontrado")

    monkeypatch.setattr(orch.tracknme_bot, "concluir_incidente", _concluir_incidente_falha)

    resultado = await orch.etapa_fechar_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.sucesso is True
    assert resultado.dados["concluidos"] == []
    assert len(resultado.dados["falhas"]) == 1
    assert resultado.dados["falhas"][0]["linha"] == _GRUPO_2_CONCLUIR_FAKE[0]
    assert resultado.dados["falhas"][0]["descricao"] == "Placa DEF5678 — Ciclano"


@pytest.mark.asyncio
async def test_etapa_fechar_incidentes_automaticos_repassa_on_worker_status(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(orch.tracknme_bot, "concluir_incidente", _sempre_sucesso)
    chamadas = []

    resultado = await orch.etapa_fechar_incidentes_automaticos(
        _DADOS_GRUPOS_FAKE,
        on_worker_status=lambda worker_id, descricao: chamadas.append((worker_id, descricao)),
    )

    assert resultado.sucesso is True
    assert chamadas == [(0, "Placa DEF5678 — Ciclano")]


@pytest.mark.asyncio
async def test_etapa_fechar_incidentes_automaticos_dados_none_usa_consolidar_com_sga(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(orch.tracknme_bot, "concluir_incidente", _sempre_sucesso)

    async def _etapa_consolidar_com_sga_fake(*args, **kwargs):
        return orch.ResultadoEtapa(
            "consolidar_com_sga", sucesso=True,
            dados={"grupo_2_concluir": _GRUPO_2_CONCLUIR_FAKE, "fila_operacional": []},
        )

    monkeypatch.setattr(orch, "etapa_consolidar_com_sga", _etapa_consolidar_com_sga_fake)

    resultado = await orch.etapa_fechar_incidentes_automaticos()

    assert resultado.sucesso is True
    assert resultado.dados["concluidos"] == _GRUPO_2_CONCLUIR_FAKE


@pytest.mark.asyncio
async def test_etapa_fechar_incidentes_automaticos_propaga_falha_da_consolidacao_sem_abrir_navegador(monkeypatch):
    chamou_navegador = False

    async def _abrir_navegador_autenticado_nao_deveria_ser_chamado(playwright):
        nonlocal chamou_navegador
        chamou_navegador = True
        raise AssertionError("não deveria abrir navegador")

    monkeypatch.setattr(
        orch.tracknme_bot, "abrir_navegador_autenticado", _abrir_navegador_autenticado_nao_deveria_ser_chamado
    )

    async def _etapa_consolidar_com_sga_falha(*args, **kwargs):
        return orch.ResultadoEtapa("consolidar_com_sga", sucesso=False, mensagem="SGA indisponível")

    monkeypatch.setattr(orch, "etapa_consolidar_com_sga", _etapa_consolidar_com_sga_falha)

    resultado = await orch.etapa_fechar_incidentes_automaticos()

    assert resultado.sucesso is False
    assert resultado.mensagem == "SGA indisponível"
    assert chamou_navegador is False


@pytest.mark.asyncio
async def test_etapa_fechar_incidentes_automaticos_falha_ao_abrir_navegador(monkeypatch):
    monkeypatch.setattr(orch, "async_playwright", lambda: _PlaywrightCtxFalso())
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})

    async def _abrir_navegador_autenticado_falha(playwright):
        raise RuntimeError("Login automático falhou")

    monkeypatch.setattr(orch.tracknme_bot, "abrir_navegador_autenticado", _abrir_navegador_autenticado_falha)

    resultado = await orch.etapa_fechar_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.sucesso is False
    assert resultado.mensagem == "Login automático falhou"


# --- Estágio HTTP de abrir/fechar_incidentes_automaticos (achado 2026-08-19) -


class _ClienteHttpFalso:
    async def aclose(self):
        pass


async def _preparar_contexto_http_falso():
    return orch.tracknme_bot.ContextoHttp(cliente=_ClienteHttpFalso(), brand_id=1, user_id=1)


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_kill_switch_desligado_nunca_usa_http(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)  # buscar_parametros -> {} (kill switch ausente = desligado)
    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente", _sempre_sucesso)

    async def _abrir_incidente_http_nao_deveria_ser_chamado(contexto_http, placa, cliente):
        raise AssertionError("não deveria chamar abrir_incidente_http com o kill switch desligado")

    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente_http", _abrir_incidente_http_nao_deveria_ser_chamado)

    resultado = await orch.etapa_abrir_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.sucesso is True
    assert "tracknme_http_abortado" not in resultado.dados


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_repassa_timeout_configurado(monkeypatch):
    """Mesmo achado 2026-08-20 do SGA (ver teste equivalente em
    etapa_enriquecimento_sga) — tracknme_http_timeout_base_ms precisa
    chegar em segundos até processar_fila_http."""
    _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_parametros",
        lambda: {"tracknme_http_habilitado": True, "tracknme_http_timeout_base_ms": 5000},
    )
    monkeypatch.setattr(orch.tracknme_bot, "preparar_contexto_http", _preparar_contexto_http_falso)

    timeouts_recebidos = []

    async def _processar_fila_http_fake(
        contexto_http, itens, acao, concorrencia=10, max_tentativas=3,
        on_progresso=None, cancelar_checker=None, on_item_iniciado=None, timeout_segundos=None,
    ):
        timeouts_recebidos.append(timeout_segundos)
        return []

    monkeypatch.setattr(orch.playwright_utils, "processar_fila_http", _processar_fila_http_fake)

    resultado = await orch.etapa_abrir_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.sucesso is True
    assert timeouts_recebidos
    assert all(t == 5.0 for t in timeouts_recebidos)


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_timeout_usa_default_quando_parametro_ausente(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_parametros", lambda: {"tracknme_http_habilitado": True},
    )
    monkeypatch.setattr(orch.tracknme_bot, "preparar_contexto_http", _preparar_contexto_http_falso)

    timeouts_recebidos = []

    async def _processar_fila_http_fake(
        contexto_http, itens, acao, concorrencia=10, max_tentativas=3,
        on_progresso=None, cancelar_checker=None, on_item_iniciado=None, timeout_segundos=None,
    ):
        timeouts_recebidos.append(timeout_segundos)
        return []

    monkeypatch.setattr(orch.playwright_utils, "processar_fila_http", _processar_fila_http_fake)

    resultado = await orch.etapa_abrir_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.sucesso is True
    assert timeouts_recebidos
    assert all(t == 30.0 for t in timeouts_recebidos)


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_circuit_breaker_aborta_resto_pro_playwright(monkeypatch):
    contexto, browser = _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_parametros",
        lambda: {
            "tracknme_http_habilitado": True, "tracknme_http_tamanho_canario": 1,
            "tracknme_http_limiar_falha_tecnica": 0.1,
        },
    )
    monkeypatch.setattr(orch.tracknme_bot, "preparar_contexto_http", _preparar_contexto_http_falso)

    chamadas_http, chamadas_playwright = [], []

    async def _abrir_incidente_http_falha(contexto_http, placa, cliente):
        chamadas_http.append(placa)
        raise RuntimeError("falha técnica simulada")

    async def _abrir_incidente_sucesso(page, placa, cliente):
        chamadas_playwright.append(placa)
        return f"Incidente aberto para {placa}"

    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente_http", _abrir_incidente_http_falha)
    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente", _abrir_incidente_sucesso)

    grupo_1_dois_itens = [
        {"placa": "CANARIO", "chassi": "X1", "imei": "111", "cliente": "Fulano"},
        {"placa": "RESTO", "chassi": "X2", "imei": "222", "cliente": "Beltrano"},
    ]
    dados = {**_DADOS_GRUPOS_FAKE, "grupo_1_abrir": grupo_1_dois_itens}

    resultado = await orch.etapa_abrir_incidentes_automaticos(dados)

    assert resultado.sucesso is True
    assert resultado.dados["tracknme_http_abortado"]["motivo"] == "taxa_falha_tecnica"
    # processar_fila_http tenta 2 rounds x 3 tentativas cada pro item do
    # canário (que falha sempre) antes de desistir -- só importa que
    # "RESTO" nunca passou por HTTP, indo direto pro Playwright.
    assert set(chamadas_http) == {"CANARIO"}
    assert chamadas_playwright == ["RESTO"]
    assert resultado.dados["abertos"] == [grupo_1_dois_itens[1]]
    assert resultado.dados["falhas"][0]["linha"] == grupo_1_dois_itens[0]
    assert contexto.fechado is True
    assert browser.fechado is True


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_duplicado_nao_dispara_circuit_breaker(monkeypatch):
    # Achado ao vivo 2026-08-19 (teste de escala, 100 candidatos reais):
    # 8% de IncidenteDuplicadoError, 0% de falha técnica de verdade -- o
    # circuit breaker não deve contar duplicado como falha técnica.
    contexto, browser = _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_parametros",
        lambda: {"tracknme_http_habilitado": True, "tracknme_http_limiar_falha_tecnica": 0.05},
    )
    monkeypatch.setattr(orch.tracknme_bot, "preparar_contexto_http", _preparar_contexto_http_falso)

    async def _abrir_incidente_http_duplicado(contexto_http, placa, cliente):
        raise orch.tracknme_bot.IncidenteDuplicadoError(f"incidente já aberto (placa={placa})")

    monkeypatch.setattr(orch.tracknme_bot, "abrir_incidente_http", _abrir_incidente_http_duplicado)

    # 10 itens, todos "duplicado" (100% > limiar de 5%) -- não deve abortar.
    grupo_1_dez_itens = [
        {"placa": f"DUP{i}", "chassi": f"X{i}", "imei": str(i), "cliente": "Fulano"} for i in range(10)
    ]
    dados = {**_DADOS_GRUPOS_FAKE, "grupo_1_abrir": grupo_1_dez_itens}

    resultado = await orch.etapa_abrir_incidentes_automaticos(dados)

    assert resultado.sucesso is True
    assert "tracknme_http_abortado" not in resultado.dados
    assert resultado.dados["abertos"] == []
    assert len(resultado.dados["falhas"]) == 10


@pytest.mark.asyncio
async def test_etapa_abrir_incidentes_automaticos_reconexao_no_estagio_http_inclui_resto_pendente(monkeypatch):
    # Achado 2026-08-19 (mesmo espírito do teste equivalente do SGA): se o
    # Estágio HTTP cair durante o canário, o "resto" (que nunca chegou a
    # ser tentado, nem por HTTP nem por Playwright) também precisa voltar
    # em "pendentes" -- senão a retomada os perde.
    _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_parametros",
        lambda: {"tracknme_http_habilitado": True, "tracknme_http_tamanho_canario": 1},
    )
    monkeypatch.setattr(orch.tracknme_bot, "preparar_contexto_http", _preparar_contexto_http_falso)

    async def _processar_fila_http_levanta_reconexao(
        contexto_http, itens, acao, concorrencia=10, max_tentativas=3,
        on_progresso=None, cancelar_checker=None, on_item_iniciado=None, timeout_segundos=None,
    ):
        raise orch.playwright_utils.AguardandoReconexao(pendentes=list(itens), processados=[])

    monkeypatch.setattr(orch.playwright_utils, "processar_fila_http", _processar_fila_http_levanta_reconexao)

    grupo_1_dois_itens = [
        {"placa": "CANARIO", "chassi": "X1", "imei": "111", "cliente": "Fulano"},
        {"placa": "RESTO", "chassi": "X2", "imei": "222", "cliente": "Beltrano"},
    ]
    dados = {**_DADOS_GRUPOS_FAKE, "grupo_1_abrir": grupo_1_dois_itens}

    resultado = await orch.etapa_abrir_incidentes_automaticos(dados)

    assert resultado.sucesso is False
    assert {p["placa"] for p in resultado.aguardando_reconexao["pendentes"]} == {"CANARIO", "RESTO"}


@pytest.mark.asyncio
async def test_etapa_fechar_incidentes_automaticos_kill_switch_desligado_nunca_usa_http(monkeypatch):
    _preparar_mocks_playwright(monkeypatch)
    monkeypatch.setattr(orch.tracknme_bot, "concluir_incidente", _sempre_sucesso)

    async def _concluir_incidente_http_nao_deveria_ser_chamada(contexto_http, placa, motivo, numero_incidente=None):
        raise AssertionError("não deveria chamar concluir_incidente_http com o kill switch desligado")

    monkeypatch.setattr(orch.tracknme_bot, "concluir_incidente_http", _concluir_incidente_http_nao_deveria_ser_chamada)

    resultado = await orch.etapa_fechar_incidentes_automaticos(_DADOS_GRUPOS_FAKE)

    assert resultado.sucesso is True
    assert "tracknme_http_abortado" not in resultado.dados


# --- _persistir_situacoes_sga / _formatar_situacoes_recentes (Bloco C1, ----
# 2026-08-24: cidade/bairro do SGA persistidos no checkpoint) --------------

def test_persistir_situacoes_sga_inclui_cidade_bairro_no_payload_de_upsert(monkeypatch):
    """Antes deste fix, o payload de `upsert_situacoes_veiculo_sga_em_lote`
    não levava cidade/bairro (só o dict de uso imediato desta execução
    tinha) — uma tratativa que reaproveitasse o checkpoint ficava sem
    esse dado."""
    monkeypatch.setattr(orch.supabase_client, "buscar_situacoes_veiculo_sga_em_lote", lambda chassis: {})
    persistido = []
    monkeypatch.setattr(
        orch.supabase_client, "upsert_situacoes_veiculo_sga_em_lote",
        lambda registros: persistido.extend(registros),
    )
    resultados = [
        orch.playwright_utils.ResultadoItem(
            item="CHASSI-1", sucesso=True,
            resultado={"status": "ATIVO", "cidade": "Recife", "bairro": "Boa Vista", "encontrado_via": "chassi"},
        )
    ]

    situacoes_sga, falhas = orch._persistir_situacoes_sga(resultados, agora=datetime(2026, 8, 24, 10, 0, 0))

    assert falhas == []
    assert persistido == [{
        "chassi": "CHASSI-1", "status": "ATIVO", "desde": datetime(2026, 8, 24, 10, 0, 0),
        "atualizado_em": datetime(2026, 8, 24, 10, 0, 0), "encontrado_via": "chassi",
        "cidade": "Recife", "bairro": "Boa Vista",
    }]
    assert situacoes_sga["CHASSI-1"]["cidade"] == "Recife"
    assert situacoes_sga["CHASSI-1"]["bairro"] == "Boa Vista"


def test_formatar_situacoes_recentes_devolve_cidade_bairro_do_checkpoint():
    recentes = {
        "CHASSI-1": {"status": "ATIVO", "desde": "2026-08-20", "cidade": "Recife", "bairro": "Boa Vista", "encontrado_via": "chassi"},
    }

    formatado = orch._formatar_situacoes_recentes(recentes)

    assert formatado["CHASSI-1"]["cidade"] == "Recife"
    assert formatado["CHASSI-1"]["bairro"] == "Boa Vista"


def test_formatar_situacoes_recentes_registro_antigo_sem_cidade_bairro_cai_em_vazio():
    """Linha gravada antes do Bloco C1 (sem as colunas ainda preenchidas)
    não pode quebrar — cai em string vazia, mesmo comportamento de antes."""
    recentes = {"CHASSI-1": {"status": "ATIVO", "desde": "2026-08-20", "encontrado_via": "chassi"}}

    formatado = orch._formatar_situacoes_recentes(recentes)

    assert formatado["CHASSI-1"]["cidade"] == ""
    assert formatado["CHASSI-1"]["bairro"] == ""


# --- etapa_enriquecimento_sga ------------------------------------------------

_INSTALACAO_REMOCAO_FAKE = [{"Chassi": "chassi-ir-1", "Serviço": "Instalação"}]


def _atualizar_situacao_sga_fake(chassi, status_novo, anterior, agora, encontrado_via=None, cidade="", bairro=""):
    return {
        "chassi": chassi, "status": status_novo, "desde": agora, "atualizado_em": agora,
        "encontrado_via": encontrado_via, "cidade": cidade, "bairro": bairro,
    }


def _preparar_mocks_sga(monkeypatch):
    contexto = _ContextoFalsoReconciliacao()
    browser = _BrowserFalsoReconciliacao()

    async def _aguardar_login_manual_fake(playwright):
        return browser, contexto

    monkeypatch.setattr(orch, "async_playwright", lambda: _PlaywrightCtxFalso())
    monkeypatch.setattr(orch.sga_bot, "aguardar_login_manual", _aguardar_login_manual_fake)
    monkeypatch.setattr(orch.supabase_client, "buscar_situacoes_veiculo_sga_em_lote", lambda chassis: {})
    monkeypatch.setattr(orch.supabase_client, "upsert_situacoes_veiculo_sga_em_lote", lambda registros: None)
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    monkeypatch.setattr(
        orch.motor_regras_instalacao_remocao, "atualizar_situacao_sga", _atualizar_situacao_sga_fake
    )
    return contexto, browser


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_sucesso_consulta_uniao_de_chassis(monkeypatch):
    contexto, browser = _preparar_mocks_sga(monkeypatch)
    chassis_consultados = []

    async def _consultar_situacao_fake(page, tipo, valor):
        chassis_consultados.append(valor)
        return {"status": "ATIVO", "cidade": "Recife", "bairro": "Boa Vista", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, _INSTALACAO_REMOCAO_FAKE)

    assert resultado.sucesso is True
    # X1 (grupo_1), X2 (grupo_2), CHASSI-IR-1 (instalação-remoção) — maiúsculo, sem duplicar.
    assert sorted(chassis_consultados) == ["CHASSI-IR-1", "X1", "X2"]
    situacoes = resultado.dados["situacoes_sga"]
    assert {chassi: info["status"] for chassi, info in situacoes.items()} == {
        "CHASSI-IR-1": "ATIVO", "X1": "ATIVO", "X2": "ATIVO",
    }
    assert situacoes["X1"]["cidade"] == "Recife"
    assert situacoes["X1"]["bairro"] == "Boa Vista"
    assert contexto.fechado is True
    assert browser.fechado is True


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_ignora_chassi_com_falha_de_consulta(monkeypatch):
    _preparar_mocks_sga(monkeypatch)

    async def _consultar_situacao_fake(page, tipo, valor):
        if valor == "X1":
            raise RuntimeError("Status do veículo não carregou")
        return {"status": "INATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [])

    assert resultado.sucesso is True
    assert "X1" not in resultado.dados["situacoes_sga"]
    assert resultado.dados["situacoes_sga"]["X2"]["status"] == "INATIVO"
    # X1 não vira mais um chassi descartado em silêncio — aparece em "falhas"
    # com o erro real, pro atendente poder agir.
    assert len(resultado.dados["falhas"]) == 1
    assert resultado.dados["falhas"][0]["item"] == "X1"
    assert resultado.dados["falhas"][0]["descricao"] == "Chassi X1"
    assert "Status do veículo não carregou" in resultado.dados["falhas"][0]["erro"]


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_repassa_on_worker_status(monkeypatch):
    _preparar_mocks_sga(monkeypatch)

    async def _consultar_situacao_fake(page, tipo, valor):
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)
    chamadas = []

    resultado = await orch.etapa_enriquecimento_sga(
        _DADOS_GRUPOS_FAKE, [],
        on_worker_status=lambda worker_id, descricao: chamadas.append((worker_id, descricao)),
    )

    assert resultado.sucesso is True
    # Sem um yield real na acao fake, não dá pra garantir qual worker pega
    # qual chassi (poderia até ser só o worker 0 processando os 2, se
    # nenhum outro tiver chance de rodar antes da fila esvaziar) — só
    # confere que os 2 chassis foram reportados, com descrição certa.
    assert sorted(descricao for _, descricao in chamadas) == ["Chassi X1", "Chassi X2"]


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_repassa_on_progresso(monkeypatch):
    _preparar_mocks_sga(monkeypatch)

    async def _consultar_situacao_fake(page, tipo, valor):
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)
    chamadas_progresso = []

    resultado = await orch.etapa_enriquecimento_sga(
        _DADOS_GRUPOS_FAKE, [],
        on_progresso=lambda concluidos, total: chamadas_progresso.append((concluidos, total)),
    )

    assert resultado.sucesso is True
    assert chamadas_progresso == [(1, 2), (2, 2)]


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_dados_none_usa_motor_de_regras_e_le_instalacao_remocao(monkeypatch):
    _preparar_mocks_sga(monkeypatch)

    async def _consultar_situacao_fake(page, tipo, valor):
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    def _etapa_motor_de_regras_fake(dados=None):
        assert dados is None
        return orch.ResultadoEtapa("motor_de_regras", sucesso=True, dados=_DADOS_GRUPOS_FAKE)

    abas_lidas = []

    def _ler_aba_fake(planilha, aba):
        abas_lidas.append(aba)
        return _INSTALACAO_REMOCAO_FAKE

    monkeypatch.setattr(orch, "etapa_motor_de_regras", _etapa_motor_de_regras_fake)
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_fake)

    resultado = await orch.etapa_enriquecimento_sga()

    assert resultado.sucesso is True
    assert abas_lidas == ["Instalação-Remoção"]


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_propaga_falha_do_motor_sem_abrir_navegador(monkeypatch):
    chamou_navegador = False

    async def _aguardar_login_manual_nao_deveria_ser_chamado(playwright):
        nonlocal chamou_navegador
        chamou_navegador = True
        raise AssertionError("não deveria abrir navegador")

    monkeypatch.setattr(orch.sga_bot, "aguardar_login_manual", _aguardar_login_manual_nao_deveria_ser_chamado)

    def _etapa_motor_de_regras_falha(dados=None):
        return orch.ResultadoEtapa("motor_de_regras", sucesso=False, mensagem="Supabase indisponível")

    monkeypatch.setattr(orch, "etapa_motor_de_regras", _etapa_motor_de_regras_falha)

    resultado = await orch.etapa_enriquecimento_sga(instalacao_remocao=[])

    assert resultado.sucesso is False
    assert resultado.mensagem == "Supabase indisponível"
    assert chamou_navegador is False


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_sessao_caida_persiste_sucessos_antes_da_queda(monkeypatch):
    _preparar_mocks_sga(monkeypatch)
    persistidos = []
    monkeypatch.setattr(
        orch.supabase_client, "upsert_situacoes_veiculo_sga_em_lote",
        lambda registros: persistidos.extend(r["chassi"] for r in registros),
    )

    async def _processar_fila_levanta_reconexao(contexto_arg, chassis, acao, on_progresso=None, cancelar_checker=None, on_item_iniciado=None):
        raise orch.playwright_utils.AguardandoReconexao(
            pendentes=["X2"],
            processados=[
                orch.playwright_utils.ResultadoItem(
                    item="X1", sucesso=True,
                    resultado={"status": "ATIVO", "cidade": "Recife", "bairro": "Boa Vista"},
                )
            ],
        )

    monkeypatch.setattr(orch.playwright_utils, "processar_fila", _processar_fila_levanta_reconexao)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [])

    assert resultado.sucesso is False
    assert resultado.aguardando_reconexao == {"pendentes": ["X2"]}
    assert persistidos == ["X1"]
    assert resultado.dados["situacoes_sga"]["X1"]["status"] == "ATIVO"
    assert "X2" not in resultado.dados["situacoes_sga"]


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_cancelado_persiste_sucessos_antes_do_cancelamento(monkeypatch):
    _preparar_mocks_sga(monkeypatch)
    persistidos = []
    monkeypatch.setattr(
        orch.supabase_client, "upsert_situacoes_veiculo_sga_em_lote",
        lambda registros: persistidos.extend(r["chassi"] for r in registros),
    )

    async def _processar_fila_levanta_cancelamento(contexto_arg, chassis, acao, on_progresso=None, cancelar_checker=None, on_item_iniciado=None):
        raise orch.playwright_utils.CancelamentoSolicitado(
            pendentes=["X2"],
            processados=[
                orch.playwright_utils.ResultadoItem(
                    item="X1", sucesso=True,
                    resultado={"status": "ATIVO", "cidade": "Recife", "bairro": "Boa Vista"},
                )
            ],
        )

    monkeypatch.setattr(orch.playwright_utils, "processar_fila", _processar_fila_levanta_cancelamento)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [], cancelar_checker=lambda: True)

    assert resultado.sucesso is False
    assert resultado.cancelado == {"pendentes": ["X2"]}
    assert persistidos == ["X1"]
    assert resultado.dados["situacoes_sga"]["X1"]["status"] == "ATIVO"
    assert "X2" not in resultado.dados["situacoes_sga"]


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_chassis_override_ignora_defaults(monkeypatch):
    _preparar_mocks_sga(monkeypatch)

    def _etapa_motor_de_regras_nao_deveria_ser_chamada(dados=None):
        raise AssertionError("não deveria chamar etapa_motor_de_regras")

    def _ler_aba_nao_deveria_ser_chamada(planilha, aba):
        raise AssertionError("não deveria ler a aba")

    monkeypatch.setattr(orch, "etapa_motor_de_regras", _etapa_motor_de_regras_nao_deveria_ser_chamada)
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_nao_deveria_ser_chamada)

    chamadas = []

    async def _consultar_situacao_fake(page, tipo, valor):
        chamadas.append((tipo, valor))
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    resultado = await orch.etapa_enriquecimento_sga(
        chassis_override=["X9"], alvos_override={"X9": (orch.TIPO_IDENTIFICADOR_PLACA, "XYZ0001")}
    )

    assert resultado.sucesso is True
    assert chamadas == [(orch.TIPO_IDENTIFICADOR_PLACA, "XYZ0001")]


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_falha_ao_abrir_navegador(monkeypatch):
    monkeypatch.setattr(orch, "async_playwright", lambda: _PlaywrightCtxFalso())
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})

    async def _aguardar_login_manual_falha(playwright):
        raise RuntimeError("Login manual não confirmado a tempo")

    monkeypatch.setattr(orch.sga_bot, "aguardar_login_manual", _aguardar_login_manual_falha)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [])

    assert resultado.sucesso is False
    assert resultado.mensagem == "Login manual não confirmado a tempo"


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_usa_chassi_confirmado_quando_disponivel(monkeypatch):
    # Achado 2026-08-16: só busca por Chassi quando há chassi CONFIRMADO
    # via cadastro (`chassi_sga`) — X1/X2 têm equipamento encontrado
    # (_GRUPO_1_ABRIR_FAKE/_GRUPO_2_CONCLUIR_FAKE já trazem `chassi_sga`).
    _preparar_mocks_sga(monkeypatch)
    chamadas = {}

    async def _consultar_situacao_fake(page, tipo, valor):
        chamadas[valor] = tipo
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    instalacao_remocao = [{"Chassi": "chassi-ir-1", "Serviço": "Instalação", "Placa": "ghi9012"}]
    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, instalacao_remocao)

    assert resultado.sucesso is True
    assert chamadas == {
        "X1": orch.TIPO_IDENTIFICADOR_CHASSI,
        "X2": orch.TIPO_IDENTIFICADOR_CHASSI,
        "CHASSI-IR-1": orch.TIPO_IDENTIFICADOR_CHASSI,
    }


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_sem_chassi_confirmado_usa_placa_valida(monkeypatch):
    # Sem `chassi_sga` (equipamento não encontrado no cadastro), busca
    # pela placa do incidente — nunca pelo identificador de dedup cru
    # (que poderia ser um IMEI).
    _preparar_mocks_sga(monkeypatch)
    chamadas = {}

    async def _consultar_situacao_fake(page, tipo, valor):
        chamadas[valor] = tipo
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    grupo_1 = [{"placa": "GHI9012", "chassi": "862667082144174", "imei": "862667082144174", "cliente": "Fulano"}]
    dados = {**_DADOS_GRUPOS_FAKE, "grupo_1_abrir": grupo_1, "grupo_2_concluir": []}
    resultado = await orch.etapa_enriquecimento_sga(dados, [])

    assert resultado.sucesso is True
    assert chamadas == {"GHI9012": orch.TIPO_IDENTIFICADOR_PLACA}
    # a chave em situacoes_sga continua sendo o identificador de dedup, não a placa buscada.
    assert resultado.dados["situacoes_sga"]["862667082144174"]["status"] == "ATIVO"


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_sem_chassi_confirmado_e_sem_placa_valida_nao_consulta(monkeypatch):
    # Nem chassi confirmado nem placa válida (genérica) -- nada confiável
    # pra digitar em nenhum campo do SGA, então o veículo não é consultado.
    _preparar_mocks_sga(monkeypatch)
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {"placas_genericas": "SEM PLACA"})
    chamadas = []

    async def _consultar_situacao_fake(page, tipo, valor):
        chamadas.append((tipo, valor))
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    grupo_1 = [{"placa": "SEM PLACA", "chassi": "111", "imei": "111", "cliente": "Fulano"}]
    dados = {**_DADOS_GRUPOS_FAKE, "grupo_1_abrir": grupo_1, "grupo_2_concluir": []}
    resultado = await orch.etapa_enriquecimento_sga(dados, [])

    assert resultado.sucesso is True
    assert chamadas == []
    assert resultado.dados["situacoes_sga"] == {}
    assert resultado.dados["alvos_consulta_sga"] == {}


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_inclui_alvos_consulta_sga_no_resultado(monkeypatch):
    _preparar_mocks_sga(monkeypatch)

    async def _consultar_situacao_fake(page, tipo, valor):
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [])

    assert resultado.sucesso is True
    # dados["alvos_consulta_sga"] é repassado só pra retomar_etapa
    # reaproveitar na retomada pós-reconexão, sem recalcular
    # dados_classificacao do zero.
    assert resultado.dados["alvos_consulta_sga"] == {
        "X1": (orch.TIPO_IDENTIFICADOR_CHASSI, "X1"),
        "X2": (orch.TIPO_IDENTIFICADOR_CHASSI, "X2"),
    }


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_checkpoint_pula_quem_foi_atualizado_recentemente(monkeypatch):
    # Achado 2026-08-17: sem esse checkpoint, toda execução reconsultava
    # TODOS os veículos conhecidos, mesmo os checados minutos atrás -- na
    # escala real (milhares de registros), isso sozinho esticava a Fase D
    # por horas.
    _preparar_mocks_sga(monkeypatch)
    agora = datetime.now(orch.timezone.utc)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_situacoes_veiculo_sga_em_lote",
        lambda chassis: {
            "X1": {"chassi": "X1", "status": "ATIVO", "desde": "2026-08-01T00:00:00+00:00",
                    "atualizado_em": agora.isoformat(), "encontrado_via": "chassi"},
        },
    )
    chamadas = []

    async def _consultar_situacao_fake(page, tipo, valor):
        chamadas.append(valor)
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [])

    assert resultado.sucesso is True
    # X1 foi atualizado agora mesmo -- pulado; X2 nunca foi checado -- consultado.
    assert chamadas == ["X2"]
    assert resultado.dados["alvos_consulta_sga"] == {"X2": (orch.TIPO_IDENTIFICADOR_CHASSI, "X2")}
    # X1 continua aparecendo em situacoes_sga (reusa o último status conhecido).
    assert resultado.dados["situacoes_sga"]["X1"]["status"] == "ATIVO"
    assert resultado.dados["situacoes_sga"]["X1"]["encontrado_via"] == "chassi"


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_checkpoint_nao_pula_quem_esta_desatualizado(monkeypatch):
    _preparar_mocks_sga(monkeypatch)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_situacoes_veiculo_sga_em_lote",
        lambda chassis: {
            "X1": {"chassi": "X1", "status": "ATIVO", "desde": "2020-01-01T00:00:00+00:00",
                    "atualizado_em": "2020-01-01T00:00:00+00:00", "encontrado_via": "chassi"},
        },
    )
    chamadas = []

    async def _consultar_situacao_fake(page, tipo, valor):
        chamadas.append(valor)
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [])

    assert resultado.sucesso is True
    assert sorted(chamadas) == ["X1", "X2"]


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_checkpoint_com_erro_de_leitura_nao_pula_ninguem(monkeypatch):
    _preparar_mocks_sga(monkeypatch)

    def _buscar_falha(chassis):
        raise RuntimeError("Supabase indisponível")

    monkeypatch.setattr(orch.supabase_client, "buscar_situacoes_veiculo_sga_em_lote", _buscar_falha)
    chamadas = []

    async def _consultar_situacao_fake(page, tipo, valor):
        chamadas.append(valor)
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [])

    assert resultado.sucesso is True
    assert sorted(chamadas) == ["X1", "X2"]


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_falha_ao_gravar_nao_perde_resultado_nem_derruba_etapa(monkeypatch):
    # Achado 2026-08-17: antes, uma falha de rede na gravação (fora do
    # try/except da etapa) escapava como exceção não tratada -- deixava a
    # trava de execução presa pra sempre (nunca era liberada).
    _preparar_mocks_sga(monkeypatch)

    def _upsert_falha(registros):
        raise RuntimeError("Timeout na gravação")

    monkeypatch.setattr(orch.supabase_client, "upsert_situacoes_veiculo_sga_em_lote", _upsert_falha)

    async def _consultar_situacao_fake(page, tipo, valor):
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [])

    assert resultado.sucesso is True
    assert resultado.dados["situacoes_sga"]["X1"]["status"] == "ATIVO"
    assert resultado.dados["situacoes_sga"]["X2"]["status"] == "ATIVO"
    falhas_persistencia = [f for f in resultado.dados["falhas"] if "Timeout na gravação" in f["erro"]]
    assert len(falhas_persistencia) == 2


# --- etapa_enriquecimento_sga: Estágio HTTP (achado 2026-08-19) -------------


class _RequestContextHttpFake:
    """`request_context` mínimo pros testes do Estágio HTTP -- só precisa
    saber fechar (`.dispose()`), a consulta em si é sempre mockada em
    `orch.sga_bot.consultar_situacao_http`, nunca chama nada real."""

    async def dispose(self):
        pass


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_http_habilitado_mistura_chassi_e_placa(monkeypatch):
    # Com sga_http_habilitado, chassi confirmado vai pro Estágio HTTP e
    # placa continua pelo Estágio Playwright, na MESMA execução -- sem
    # pedir captcha 2x (abrir_contexto_com_sessao reusa a sessão do
    # Estágio HTTP em vez de aguardar_login_manual de novo).
    contexto, browser = _preparar_mocks_sga(monkeypatch)
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {"sga_http_habilitado": True})

    class _RequestContextFake:
        def __init__(self):
            self.dispose_chamado = False

        async def dispose(self):
            self.dispose_chamado = True

    request_context_fake = _RequestContextFake()
    estado_fake = {"cookies": ["fake"]}

    async def _preparar_contexto_http_fake(playwright, browser_arg, context_arg):
        return request_context_fake, estado_fake

    monkeypatch.setattr(orch.sga_bot, "preparar_contexto_http", _preparar_contexto_http_fake)

    chamadas_abrir_contexto_com_sessao = []

    async def _abrir_contexto_com_sessao_fake(playwright, estado):
        chamadas_abrir_contexto_com_sessao.append(estado)
        return browser, contexto

    monkeypatch.setattr(orch.sga_bot, "abrir_contexto_com_sessao", _abrir_contexto_com_sessao_fake)

    chamadas_http, chamadas_playwright = [], []

    async def _consultar_situacao_http_fake(request_context, tipo, valor):
        chamadas_http.append(valor)
        return {"status": "ATIVO", "cidade": "Recife", "bairro": "Boa Vista", "encontrado_via": tipo}

    async def _consultar_situacao_fake(page, tipo, valor):
        chamadas_playwright.append(valor)
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao_http", _consultar_situacao_http_fake)
    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    # X2 (grupo_2, chassi_sga confirmado) vai pro HTTP; GHI9012 (grupo_1
    # sem chassi_sga, só placa válida) vai pro Playwright.
    grupo_1 = [{"placa": "GHI9012", "chassi": "862667082144174", "imei": "862667082144174", "cliente": "Fulano"}]
    dados = {**_DADOS_GRUPOS_FAKE, "grupo_1_abrir": grupo_1}

    resultado = await orch.etapa_enriquecimento_sga(dados, [])

    assert resultado.sucesso is True
    assert chamadas_http == ["X2"]
    assert chamadas_playwright == ["862667082144174"] or chamadas_playwright == ["GHI9012"]
    assert chamadas_abrir_contexto_com_sessao == [estado_fake]
    assert request_context_fake.dispose_chamado is True
    assert resultado.dados["situacoes_sga"]["X2"]["status"] == "ATIVO"
    assert resultado.dados["situacoes_sga"]["862667082144174"]["status"] == "ATIVO"


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_repassa_timeout_configurado_pro_processar_fila_http(monkeypatch):
    """Achado 2026-08-20: sga_http_timeout_base_ms (system_parameters)
    precisa chegar em segundos até processar_fila_http -- sem isso, o
    cinto de segurança contra trava indefinida (ver test_playwright_utils)
    nunca é ativado de verdade em produção."""
    _preparar_mocks_sga(monkeypatch)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_parametros",
        lambda: {"sga_http_habilitado": True, "sga_http_timeout_base_ms": 5000},
    )

    async def _preparar_contexto_http_fake(playwright, browser_arg, context_arg):
        return _RequestContextHttpFake(), {}

    monkeypatch.setattr(orch.sga_bot, "preparar_contexto_http", _preparar_contexto_http_fake)

    timeouts_recebidos = []

    async def _processar_fila_http_fake(
        request_context, itens, acao, concorrencia=80, max_tentativas=3,
        on_progresso=None, cancelar_checker=None, on_item_iniciado=None, timeout_segundos=None,
    ):
        timeouts_recebidos.append(timeout_segundos)
        return []

    monkeypatch.setattr(orch.playwright_utils, "processar_fila_http", _processar_fila_http_fake)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, _INSTALACAO_REMOCAO_FAKE)

    assert resultado.sucesso is True
    assert timeouts_recebidos  # confirma que o Estágio HTTP rodou de verdade (X2 é chassi confirmado)
    assert all(t == 5.0 for t in timeouts_recebidos)  # 5000ms -> 5.0s


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_timeout_usa_default_quando_parametro_ausente(monkeypatch):
    _preparar_mocks_sga(monkeypatch)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_parametros", lambda: {"sga_http_habilitado": True},
    )

    async def _preparar_contexto_http_fake(playwright, browser_arg, context_arg):
        return _RequestContextHttpFake(), {}

    monkeypatch.setattr(orch.sga_bot, "preparar_contexto_http", _preparar_contexto_http_fake)

    timeouts_recebidos = []

    async def _processar_fila_http_fake(
        request_context, itens, acao, concorrencia=80, max_tentativas=3,
        on_progresso=None, cancelar_checker=None, on_item_iniciado=None, timeout_segundos=None,
    ):
        timeouts_recebidos.append(timeout_segundos)
        return []

    monkeypatch.setattr(orch.playwright_utils, "processar_fila_http", _processar_fila_http_fake)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, _INSTALACAO_REMOCAO_FAKE)

    assert resultado.sucesso is True
    assert timeouts_recebidos
    assert all(t == 30.0 for t in timeouts_recebidos)  # default 30000ms -> 30.0s


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_kill_switch_desligado_nunca_usa_http(monkeypatch):
    _preparar_mocks_sga(monkeypatch)  # buscar_parametros -> {} (sga_http_habilitado ausente = desligado)

    async def _consultar_situacao_http_nao_deveria_ser_chamada(request_context, tipo, valor):
        raise AssertionError("não deveria chamar consultar_situacao_http com o kill switch desligado")

    async def _consultar_situacao_fake(page, tipo, valor):
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao_http", _consultar_situacao_http_nao_deveria_ser_chamada)
    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [])

    assert resultado.sucesso is True
    assert "sga_http_abortado" not in resultado.dados


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_circuit_breaker_aborta_resto_pro_playwright(monkeypatch):
    _preparar_mocks_sga(monkeypatch)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_parametros",
        lambda: {"sga_http_habilitado": True, "sga_http_tamanho_canario": 1, "sga_http_limiar_nao_encontrado": 0.1},
    )

    async def _preparar_contexto_http_fake(playwright, browser_arg, context_arg):
        return _RequestContextHttpFake(), {"cookies": ["fake"]}

    monkeypatch.setattr(orch.sga_bot, "preparar_contexto_http", _preparar_contexto_http_fake)

    async def _abrir_contexto_com_sessao_fake(playwright, estado):
        return _BrowserFalsoReconciliacao(), _ContextoFalsoReconciliacao()

    monkeypatch.setattr(orch.sga_bot, "abrir_contexto_com_sessao", _abrir_contexto_com_sessao_fake)

    chamadas_http, chamadas_playwright = [], []

    async def _consultar_situacao_http_fake(request_context, tipo, valor):
        chamadas_http.append(valor)
        return {"status": orch.sga_bot.STATUS_NAO_ENCONTRADO, "cidade": "", "bairro": "", "encontrado_via": tipo}

    async def _consultar_situacao_fake(page, tipo, valor):
        chamadas_playwright.append(valor)
        return {"status": "ATIVO", "cidade": "", "bairro": "", "encontrado_via": tipo}

    monkeypatch.setattr(orch.sga_bot, "consultar_situacao_http", _consultar_situacao_http_fake)
    monkeypatch.setattr(orch.sga_bot, "consultar_situacao", _consultar_situacao_fake)

    # X1 e X2 (ambos chassi confirmado): tamanho_canario=1 -> só X1 entra
    # no canário, 100% "não encontrado" ultrapassa o limiar de 10% ->
    # circuit breaker aborta, X2 (o "resto") é redirecionado pro Playwright.
    resultado = await orch.etapa_enriquecimento_sga(_DADOS_GRUPOS_FAKE, [])

    assert resultado.sucesso is True
    assert resultado.dados["sga_http_abortado"]["motivo"] == "taxa_nao_encontrado"
    assert chamadas_http == ["X1"]
    assert chamadas_playwright == ["X2"]
    assert resultado.dados["situacoes_sga"]["X1"]["status"] == orch.sga_bot.STATUS_NAO_ENCONTRADO
    assert resultado.dados["situacoes_sga"]["X2"]["status"] == "ATIVO"


@pytest.mark.asyncio
async def test_etapa_enriquecimento_sga_reconexao_no_estagio_http_inclui_placa_pendente(monkeypatch):
    # Achado 2026-08-19 (Decisão 3 do plano): se o Estágio HTTP cair, os
    # alvos de Placa (Estágio Playwright) que NUNCA chegaram a rodar
    # também precisam voltar em "pendentes" -- senão a retomada os perde.
    _preparar_mocks_sga(monkeypatch)
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {"sga_http_habilitado": True})

    async def _preparar_contexto_http_fake(playwright, browser_arg, context_arg):
        return _RequestContextHttpFake(), {"cookies": ["fake"]}

    monkeypatch.setattr(orch.sga_bot, "preparar_contexto_http", _preparar_contexto_http_fake)

    async def _processar_fila_http_levanta_reconexao(
        request_context, chassis, acao, concorrencia=80, max_tentativas=3,
        on_progresso=None, cancelar_checker=None, on_item_iniciado=None, timeout_segundos=None,
    ):
        raise orch.playwright_utils.AguardandoReconexao(pendentes=list(chassis), processados=[])

    monkeypatch.setattr(orch.playwright_utils, "processar_fila_http", _processar_fila_http_levanta_reconexao)

    # X2 (grupo_2, chassi confirmado) vai pro Estágio HTTP (que cai);
    # 862667082144174 (grupo_1, só placa válida) fica no Estágio
    # Playwright, que nunca chega a rodar.
    grupo_1 = [{"placa": "GHI9012", "chassi": "862667082144174", "imei": "862667082144174", "cliente": "Fulano"}]
    dados = {**_DADOS_GRUPOS_FAKE, "grupo_1_abrir": grupo_1}

    resultado = await orch.etapa_enriquecimento_sga(dados, [])

    assert resultado.sucesso is False
    assert set(resultado.aguardando_reconexao["pendentes"]) == {"X2", "862667082144174"}


# --- etapa_consolidar_com_sga -----------------------------------------------

_DADOS_SGA_FAKE = {
    "situacoes_sga": {
        "X2": {"status": "INATIVO", "desde": None, "cidade": "", "bairro": ""},
        "CHASSI-IR-1": {"status": "INATIVO", "desde": None, "cidade": "", "bairro": ""},
    },
}
_TRATATIVA_IR_FAKE = {"chassi": "CHASSI-IR-1", "origem": "instalacao", "codigo_regra": "REGRA_PRAZO_NORMAL"}
_LINHA_GRUPO_3_APOS_SGA_FAKE = {"id": "9", "codigo_regra": "REGRA_1", "chassi": "X9"}


def _preparar_mocks_consolidar(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {"tempo_limiar_inatividade_horas": 48})
    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: {})

    def _aplicar_situacoes_sga_fake(dados_classificacao, situacoes_sga, templates):
        return {
            "grupo_1_abrir": dados_classificacao["grupo_1_abrir"],
            "grupo_2_concluir": _GRUPO_2_CONCLUIR_FAKE,
            "grupo_3_tratativa_humana": [_LINHA_GRUPO_3_APOS_SGA_FAKE],
        }

    def _classificar_instalacao_remocao_fake(registros, equipamentos, situacoes_sga, parametros, templates, agora=None):
        return [_TRATATIVA_IR_FAKE], []

    monkeypatch.setattr(orch.motor_regras, "aplicar_situacoes_sga", _aplicar_situacoes_sga_fake)
    monkeypatch.setattr(
        orch.motor_regras_instalacao_remocao, "classificar_instalacao_remocao", _classificar_instalacao_remocao_fake
    )


@pytest.mark.asyncio
async def test_etapa_consolidar_com_sga_sucesso_com_tudo_explicito(monkeypatch):
    _preparar_mocks_consolidar(monkeypatch)

    resultado = await orch.etapa_consolidar_com_sga(
        _DADOS_GRUPOS_FAKE, _DADOS_SGA_FAKE, [{"col_1": "equipamento"}], _INSTALACAO_REMOCAO_FAKE
    )

    assert resultado.sucesso is True
    assert resultado.dados["grupo_2_concluir"] == _GRUPO_2_CONCLUIR_FAKE
    fila = resultado.dados["fila_operacional"]
    assert len(fila) == 2
    assert {**_LINHA_GRUPO_3_APOS_SGA_FAKE, "origem": "manutencao"} in fila
    assert _TRATATIVA_IR_FAKE in fila
    assert resultado.dados["divergencias_instalacao"] == []


@pytest.mark.asyncio
async def test_etapa_consolidar_com_sga_usa_defaults_quando_tudo_none(monkeypatch):
    _preparar_mocks_consolidar(monkeypatch)

    def _etapa_motor_de_regras_fake(dados=None):
        assert dados is None
        return orch.ResultadoEtapa("motor_de_regras", sucesso=True, dados=_DADOS_GRUPOS_FAKE)

    dados_por_aba = {
        "Incidentes": [],
        "Rastreadores Ativos": [{"col_1": "equipamento"}],
        "Instalação-Remoção": _INSTALACAO_REMOCAO_FAKE,
    }

    def _ler_aba_fake(planilha, aba):
        return dados_por_aba[aba]

    chamadas_sga = []

    async def _etapa_enriquecimento_sga_fake(dados_classificacao, instalacao_remocao):
        chamadas_sga.append((dados_classificacao, instalacao_remocao))
        return orch.ResultadoEtapa("enriquecimento_sga", sucesso=True, dados=_DADOS_SGA_FAKE)

    monkeypatch.setattr(orch, "etapa_motor_de_regras", _etapa_motor_de_regras_fake)
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_fake)
    monkeypatch.setattr(orch, "etapa_enriquecimento_sga", _etapa_enriquecimento_sga_fake)

    resultado = await orch.etapa_consolidar_com_sga()

    assert resultado.sucesso is True
    assert len(chamadas_sga) == 1
    assert chamadas_sga[0] == (_DADOS_GRUPOS_FAKE, _INSTALACAO_REMOCAO_FAKE)


@pytest.mark.asyncio
async def test_etapa_consolidar_com_sga_falha_ao_classificar_motor(monkeypatch):
    def _etapa_motor_de_regras_falha(dados=None):
        return orch.ResultadoEtapa("motor_de_regras", sucesso=False, mensagem="Supabase indisponível")

    monkeypatch.setattr(orch, "etapa_motor_de_regras", _etapa_motor_de_regras_falha)

    resultado = await orch.etapa_consolidar_com_sga()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Supabase indisponível"


@pytest.mark.asyncio
async def test_etapa_consolidar_com_sga_propaga_falha_do_sga(monkeypatch):
    async def _etapa_enriquecimento_sga_falha(dados_classificacao, instalacao_remocao):
        return orch.ResultadoEtapa("enriquecimento_sga", sucesso=False, mensagem="SGA indisponível")

    monkeypatch.setattr(orch, "etapa_enriquecimento_sga", _etapa_enriquecimento_sga_falha)

    resultado = await orch.etapa_consolidar_com_sga(
        _DADOS_GRUPOS_FAKE, None, [{"col_1": "equipamento"}], _INSTALACAO_REMOCAO_FAKE
    )

    assert resultado.sucesso is False
    assert resultado.mensagem == "SGA indisponível"


@pytest.mark.asyncio
async def test_etapa_consolidar_com_sga_falha_ao_classificar_instalacao_remocao(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: {})
    monkeypatch.setattr(
        orch.motor_regras, "aplicar_situacoes_sga",
        lambda dados_classificacao, situacoes_sga, templates: dados_classificacao,
    )

    def _classificar_instalacao_remocao_falha(*args, **kwargs):
        raise RuntimeError("regra de instalação/remoção inválida")

    monkeypatch.setattr(
        orch.motor_regras_instalacao_remocao, "classificar_instalacao_remocao", _classificar_instalacao_remocao_falha
    )

    resultado = await orch.etapa_consolidar_com_sga(
        _DADOS_GRUPOS_FAKE, _DADOS_SGA_FAKE, [{"col_1": "equipamento"}], _INSTALACAO_REMOCAO_FAKE
    )

    assert resultado.sucesso is False
    assert resultado.mensagem == "regra de instalação/remoção inválida"


# --- _anotar_erro / _mensagem_com_notas (achado 2026-08-21: diagnóstico -----
# definitivo de sub-etapa, sem trocar o tipo da exceção original) -----------

def test_mensagem_com_notas_sem_notas_cai_pra_str_puro():
    assert orch._mensagem_com_notas(RuntimeError("erro puro")) == "erro puro"


def test_mensagem_com_notas_inclui_contexto_das_notas():
    erro = RuntimeError("erro de rede")
    erro.add_note("[etapa_a]")
    erro.add_note("[sub_passo_b]")

    assert orch._mensagem_com_notas(erro) == "erro de rede | contexto: [etapa_a] > [sub_passo_b]"


def test_anotar_erro_anota_e_relanca_sem_trocar_tipo():
    with pytest.raises(ValueError) as excinfo:
        with orch._anotar_erro("contexto_teste"):
            raise ValueError("falha original")

    assert excinfo.value.__notes__ == ["[contexto_teste]"]


def test_anotar_erro_nao_interfere_com_retry_por_tipo():
    """Guarda de não-regressão: `retry_erro_transitorio_windows` decide se
    retenta com base em `isinstance(e, ...)` -- `add_note` não pode trocar
    o tipo nem quebrar essa checagem."""
    import httpx

    tentativas = []

    @orch.supabase_client.retry_erro_transitorio_windows(tentativas=2, espera_segundos=0)
    def _funcao_com_contexto():
        with orch._anotar_erro("contexto_teste"):
            tentativas.append(1)
            if len(tentativas) < 2:
                raise httpx.ConnectError("conexão recusada")
            return "ok"

    assert _funcao_com_contexto() == "ok"
    assert len(tentativas) == 2


# --- etapa_publicar_fila_operacional ----------------------------------------

def _linha_manutencao(placa="ABC1234", data_incidente="01/08/2026 10:00:00", evento="Sem comunicação", **extra):
    base = {
        "origem": "manutencao", "placa": placa, "chassi": "CHASSI-M1", "cliente": "Cliente M",
        "codigo_regra": "REGRA_1", "acao_sugerida": "Seguir esteira.", "observacao_sistema": "Sem comunicação.",
        "nivel_urgencia": 5, "data_incidente": data_incidente, "evento": evento,
        "telefone": "+5581988887777", "tipo_identificador": "chassi", "identificador": "CHASSI-M1",
        "sga": "ATIVO", "cidade": "Recife", "bairro": "Boa Vista",
    }
    base.update(extra)
    return base


def _linha_instalacao(chassi="CHASSI-I1", **extra):
    base = {
        "origem": "instalacao", "chassi": chassi, "placa": "XYZ1111", "cliente": "Cliente I",
        "codigo_regra": "REGRA_PRAZO_NORMAL", "acao_sugerida": "Aguardar.", "observacao_sistema": "Pendente.",
        "nivel_urgencia": 1, "cpf": "12345678900", "situacao": "Ativo", "data_contrato": "01/08/2026",
        "telefone": "+5581988880000", "cidade": "Olinda", "bairro": "Rio Doce",
        "tipo_identificador": "chassi", "identificador": chassi, "sga": "",
    }
    base.update(extra)
    return base


def _linha_remocao(chassi="CHASSI-R1", **extra):
    base = {
        "origem": "remocao", "chassi": chassi, "placa": "QWE2222", "cliente": "Cliente R",
        "codigo_regra": "REGRA_REMOCAO_PRAZO_URGENTE", "acao_sugerida": "Agendar retirada.",
        "observacao_sistema": "INATIVO há 25 dias.", "nivel_urgencia": 5,
        "cpf": "98765432100", "situacao": "Inativo", "data_contrato": "01/01/2026",
        "telefone": "+5581988881111", "cidade": "Jaboatão", "bairro": "Centro",
        "tipo_identificador": "chassi", "identificador": chassi, "sga": "INATIVO",
    }
    base.update(extra)
    return base


def _chave(linha):
    if linha["origem"] == "manutencao":
        dados = {"placa": linha["placa"], "data_incidente": linha["data_incidente"], "evento": linha["evento"]}
    else:
        dados = {
            "cpf": linha["cpf"], "chassi": linha["chassi"],
            "situacao": linha["situacao"], "data_contrato": linha["data_contrato"],
        }
    return gerar_chave_unica(linha["origem"], dados)


def _preparar_mocks_publicar(
    monkeypatch, linhas_sheet_antiga, estado_disparo_por_chave=None, situacao_manual_atual_por_chave=None,
    tratativas_abertas_no_motor=None,
):
    estado_disparo_por_chave = estado_disparo_por_chave or {}
    situacao_manual_atual_por_chave = situacao_manual_atual_por_chave or {}
    tratativas_abertas_no_motor = tratativas_abertas_no_motor or []

    def _ler_aba_fake(planilha, aba):
        return linhas_sheet_antiga

    reescritas = []

    def _reescrever_aba_fake(planilha, aba, linhas):
        reescritas.append((planilha, aba, linhas))

    upserts = []

    def _upsert_tratativas_em_lote_fake(lista_dados):
        upserts.extend(lista_dados)

    syncs = []

    def _sincronizar_fake(chave_unica, campos):
        syncs.append((chave_unica, campos))

    chamadas_estado_disparo = []

    def _buscar_estado_disparo_fake(chaves):
        chamadas_estado_disparo.append(chaves)
        return {chave: estado_disparo_por_chave[chave] for chave in chaves if chave in estado_disparo_por_chave}

    monkeypatch.setattr(orch, "sleep", lambda segundos: None)
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_fake)
    monkeypatch.setattr(orch.google_sheets_client, "reescrever_aba", _reescrever_aba_fake)
    monkeypatch.setattr(orch.supabase_client, "upsert_tratativas_em_lote", _upsert_tratativas_em_lote_fake)
    monkeypatch.setattr(orch.supabase_client, "sincronizar_campos_atendente", _sincronizar_fake)
    monkeypatch.setattr(orch.supabase_client, "buscar_estado_disparo_por_chaves", _buscar_estado_disparo_fake)
    monkeypatch.setattr(
        orch.supabase_client, "buscar_tratativas_abertas_no_motor", lambda: tratativas_abertas_no_motor
    )
    monkeypatch.setattr(
        orch.supabase_client,
        "buscar_situacao_manual_atual_por_chaves",
        lambda chaves: {c: situacao_manual_atual_por_chave[c] for c in chaves if c in situacao_manual_atual_por_chave},
    )
    monkeypatch.setattr(orch.supabase_client, "buscar_bases_ativas", lambda: [{"id": "base-uuid-1", "nome": "Base Teste", "endereco": "Rua Exemplo, 123"}])
    monkeypatch.setattr(orch.supabase_client, "buscar_pontos_acao_ativos", lambda: [{"id": "ponto-uuid-1", "nome_local": "Ponto Teste", "endereco": "Praça Exemplo, s/n", "data": "2026-08-20"}])
    return reescritas, upserts, syncs, chamadas_estado_disparo


def _linha_atendente_sheet(chave, **extra):
    base = {
        "ID (hash)": chave, "Selecionado": False, "Técnico": "", "Situação Manual": "",
        "Data Agendada": "", "Observação Manual": "", "Discrepância revisada": False, "Finalizado": False,
        "Atendimento": "", "Base": "", "Ponto de Ação": "", "Telefone corrigido": False,
    }
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_preserva_edicao_do_atendente(monkeypatch):
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(
        chave, Selecionado=True, **{"Técnico": "João", "Situação Manual": "Agendado",
                                     "Data Agendada": "10/08/2026", "Observação Manual": "Cliente confirmou"}
    )]
    reescritas, upserts, syncs, _ = _preparar_mocks_publicar(monkeypatch, sheet_antiga)
    agora = datetime(2026, 8, 10, 9, 0, 0)

    resultado = await orch.etapa_publicar_fila_operacional([linha], agora=agora)

    assert resultado.sucesso is True
    assert resultado.dados == {"publicadas": 1, "total_fila": 1}

    assert len(upserts) == 1
    assert upserts[0]["chave_unica"] == chave
    assert "nivel_urgencia" not in upserts[0]
    assert "selecionado" not in upserts[0]  # campo de atendente não vai no upsert do motor

    assert len(syncs) == 1
    assert syncs[0] == (chave, {
        "selecionado": True, "tecnico": "João", "situacao_manual": "Agendado",
        "observacao_manual": "Cliente confirmou", "discrepancia_revisada": False,
        "atendimento": "", "base_id": None, "ponto_acao_id": None,
        "data_agendada": "2026-08-10", "situacao_manual_definida_em": agora.isoformat(),
    })

    _, _, linhas_escritas = reescritas[0]
    assert len(linhas_escritas) == 1
    escrita = linhas_escritas[0]
    assert escrita["ID (hash)"] == chave
    assert escrita["Tipo Serviço"] == "Manutenção"
    assert escrita["Selecionado"] is True
    assert escrita["Técnico"] == "João"
    assert escrita["Situação Manual"] == "Agendado"
    assert escrita["Nível de Urgência"] == 5


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_sincroniza_atendimento_base_ponto_acao(monkeypatch):
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave, Atendimento="Base", Base="Base Teste")]
    reescritas, upserts, syncs, _ = _preparar_mocks_publicar(monkeypatch, sheet_antiga)

    resultado = await orch.etapa_publicar_fila_operacional([linha])

    assert resultado.sucesso is True
    assert syncs[0][1]["atendimento"] == "base"
    assert syncs[0][1]["base_id"] == "base-uuid-1"
    assert syncs[0][1]["ponto_acao_id"] is None

    _, _, linhas_escritas = reescritas[0]
    escrita = linhas_escritas[0]
    assert escrita["Atendimento"] == "Base"
    assert escrita["Base"] == "Base Teste"
    assert escrita["Ponto de Ação"] == ""


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_atendimento_acao_resolve_ponto_acao(monkeypatch):
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave, Atendimento="Ação", **{"Ponto de Ação": "Ponto Teste"})]
    _, _, syncs, _ = _preparar_mocks_publicar(monkeypatch, sheet_antiga)

    await orch.etapa_publicar_fila_operacional([linha])

    assert syncs[0][1]["atendimento"] == "acao"
    assert syncs[0][1]["ponto_acao_id"] == "ponto-uuid-1"
    assert syncs[0][1]["base_id"] is None


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_atendimento_em_rota(monkeypatch):
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave, Atendimento="Em rota")]
    _, _, syncs, _ = _preparar_mocks_publicar(monkeypatch, sheet_antiga)

    await orch.etapa_publicar_fila_operacional([linha])

    assert syncs[0][1]["atendimento"] == "rota"


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_telefone_corrigido_limpa_status_contato(monkeypatch):
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave, **{"Telefone corrigido": True})]
    _, _, syncs, _ = _preparar_mocks_publicar(monkeypatch, sheet_antiga)

    await orch.etapa_publicar_fila_operacional([linha])

    assert syncs[0][1]["status_contato"] is None


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_sem_telefone_corrigido_nao_toca_status_contato(monkeypatch):
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave)]
    _, _, syncs, _ = _preparar_mocks_publicar(monkeypatch, sheet_antiga)

    await orch.etapa_publicar_fila_operacional([linha])

    assert "status_contato" not in syncs[0][1]


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_retorno_associado_vem_do_estado_disparo(monkeypatch):
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave)]
    estado_disparo = {chave: {"status": "aguardando_resposta", "retorno_associado": "Instalado — associado confirma, revisar"}}
    reescritas, _, _, _ = _preparar_mocks_publicar(monkeypatch, sheet_antiga, estado_disparo)

    await orch.etapa_publicar_fila_operacional([linha])

    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas[0]["Retorno do Associado"] == "Instalado — associado confirma, revisar"


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_exclui_linha_finalizada(monkeypatch):
    linha = _linha_instalacao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave, Finalizado=True)]
    reescritas, upserts, syncs, _ = _preparar_mocks_publicar(monkeypatch, sheet_antiga)

    resultado = await orch.etapa_publicar_fila_operacional([linha])

    assert resultado.sucesso is True
    assert resultado.dados == {"publicadas": 0, "total_fila": 1}
    assert len(upserts) == 1  # upsert do motor continua rodando, só a aba exclui
    assert syncs[0][1]["status"] == "finalizado"
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas == []


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_linha_nova_usa_defaults_do_atendente(monkeypatch):
    linha = _linha_remocao()
    reescritas, upserts, syncs, _ = _preparar_mocks_publicar(monkeypatch, [])

    resultado = await orch.etapa_publicar_fila_operacional([linha])

    assert resultado.sucesso is True
    _, _, linhas_escritas = reescritas[0]
    assert len(linhas_escritas) == 1
    escrita = linhas_escritas[0]
    assert escrita["Selecionado"] is False
    assert escrita["Técnico"] == ""
    assert escrita["Finalizado"] is False
    assert escrita["Tipo Serviço"] == "Remoção"


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_uma_linha_por_origem(monkeypatch):
    fila = [_linha_manutencao(), _linha_instalacao(), _linha_remocao()]
    reescritas, upserts, syncs, chamadas_estado_disparo = _preparar_mocks_publicar(monkeypatch, [])

    resultado = await orch.etapa_publicar_fila_operacional(fila)

    assert resultado.sucesso is True
    assert resultado.dados == {"publicadas": 3, "total_fila": 3}
    chaves_upsert = {u["chave_unica"] for u in upserts}
    assert len(chaves_upsert) == 3  # 3 chaves diferentes, sem colisão entre origens
    assert len(chamadas_estado_disparo) == 1  # busca em lote, não uma por linha
    assert set(chamadas_estado_disparo[0]) == chaves_upsert
    _, _, linhas_escritas = reescritas[0]
    tipos = {l["Tipo Serviço"] for l in linhas_escritas}
    assert tipos == {"Manutenção", "Instalação", "Remoção"}


def _linha_divergencia_instalacao(**extra):
    base = {
        "chassi": "CHASSI-JAFEITA", "placa": "ABC1234",
        "cliente_cadastro": "Fulano de Tal", "cliente_rastreadores": "Fulano de Tal",
        "data_contrato": "15/03/2026", "data_instalacao": "10/08/2026",
        "imei": "123456789012345",
        "observacao": "Chassi já consta em Rastreadores Ativos (instalado), mas ainda está em Instalação-Remoção.",
        "acao": "Remover ou atualizar a linha em Instalação-Remoção.",
        "cpf": "12345678900", "situacao": "Ativo",
    }
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_escreve_aba_de_divergencia_instalacao(monkeypatch):
    divergencia = _linha_divergencia_instalacao()
    reescritas, _upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, [])

    resultado = await orch.etapa_publicar_fila_operacional([], divergencias_instalacao=[divergencia])

    assert resultado.sucesso is True
    assert len(reescritas) == 2
    _, aba, linhas_escritas = reescritas[1]
    assert aba == "Análise de Divergência - Instalação"
    assert len(linhas_escritas) == 1
    escrita = linhas_escritas[0]
    assert escrita["Chassi"] == "CHASSI-JAFEITA"
    assert escrita["Placa"] == "ABC1234"
    assert escrita["Cliente (Instalação-Remoção cadastro)"] == "Fulano de Tal"
    assert escrita["Cliente (Rastreadores Ativos)"] == "Fulano de Tal"
    assert escrita["Data Contrato"] == "15/03/2026"
    assert escrita["Data de Instalação"] == "10/08/2026"
    assert escrita["IMEI"] == "123456789012345"
    assert escrita["Observação"] == divergencia["observacao"]
    assert escrita["Ação"] == divergencia["acao"]
    assert escrita["ID (hash)"] == gerar_chave_unica("instalacao", {
        "cpf": "12345678900", "chassi": "CHASSI-JAFEITA", "situacao": "Ativo", "data_contrato": "15/03/2026",
    })


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_divergencia_none_usa_consolidar_com_sga(monkeypatch):
    divergencia = _linha_divergencia_instalacao()

    async def _etapa_consolidar_com_sga_fake():
        return orch.ResultadoEtapa(
            "consolidar_com_sga", sucesso=True,
            dados={"fila_operacional": [], "divergencias_instalacao": [divergencia]},
        )

    monkeypatch.setattr(orch, "etapa_consolidar_com_sga", _etapa_consolidar_com_sga_fake)
    reescritas, _upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, [])

    resultado = await orch.etapa_publicar_fila_operacional()

    assert resultado.sucesso is True
    _, aba, linhas_escritas = reescritas[1]
    assert aba == "Análise de Divergência - Instalação"
    assert len(linhas_escritas) == 1


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_dados_none_usa_consolidar_com_sga(monkeypatch):
    linha = _linha_manutencao()

    async def _etapa_consolidar_com_sga_fake():
        return orch.ResultadoEtapa("consolidar_com_sga", sucesso=True, dados={"fila_operacional": [linha]})

    monkeypatch.setattr(orch, "etapa_consolidar_com_sga", _etapa_consolidar_com_sga_fake)
    _reescritas, upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, [])

    resultado = await orch.etapa_publicar_fila_operacional()

    assert resultado.sucesso is True
    assert len(upserts) == 1


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_propaga_falha_da_consolidacao(monkeypatch):
    async def _etapa_consolidar_com_sga_falha():
        return orch.ResultadoEtapa("consolidar_com_sga", sucesso=False, mensagem="SGA indisponível")

    monkeypatch.setattr(orch, "etapa_consolidar_com_sga", _etapa_consolidar_com_sga_falha)

    resultado = await orch.etapa_publicar_fila_operacional()

    assert resultado.sucesso is False
    assert resultado.mensagem == "SGA indisponível"


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_falha_ao_ler_aba(monkeypatch):
    def _ler_aba_falha(planilha, aba):
        raise RuntimeError("Sheets indisponível pra leitura")

    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_falha)

    resultado = await orch.etapa_publicar_fila_operacional([_linha_manutencao()])

    assert resultado.sucesso is False
    assert "Sheets indisponível pra leitura" in resultado.mensagem
    assert "sincronizar_atendente_da_aba" in resultado.mensagem


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_falha_ao_reescrever_aba(monkeypatch):
    def _reescrever_aba_falha(planilha, aba, linhas):
        raise RuntimeError("Sheets indisponível pra escrita")

    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", lambda planilha, aba: [])
    monkeypatch.setattr(orch.google_sheets_client, "reescrever_aba", _reescrever_aba_falha)
    monkeypatch.setattr(orch.supabase_client, "upsert_tratativas_em_lote", lambda lista_dados: None)
    monkeypatch.setattr(orch.supabase_client, "sincronizar_campos_atendente", lambda chave, campos: None)
    monkeypatch.setattr(orch.supabase_client, "buscar_estado_disparo_por_chaves", lambda chaves: {})
    monkeypatch.setattr(orch.supabase_client, "buscar_tratativas_abertas_no_motor", lambda: [])
    monkeypatch.setattr(orch.supabase_client, "buscar_bases_ativas", lambda: [])
    monkeypatch.setattr(orch.supabase_client, "buscar_pontos_acao_ativos", lambda: [])

    resultado = await orch.etapa_publicar_fila_operacional([_linha_manutencao()])

    assert resultado.sucesso is False
    assert "Sheets indisponível pra escrita" in resultado.mensagem
    assert "reescrever_aba:Tratativas" in resultado.mensagem


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_usa_estado_disparo_fresco_do_supabase(monkeypatch):
    """Diferente dos campos de atendente (lidos da aba), "Status
    Sistema"/"Status Contato"/tentativas/resposta vêm sempre frescos do
    Supabase — mesmo que a aba anterior tivesse esses campos com outro
    valor (ela nunca é a fonte pra isso)."""
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave)]  # sem nenhum campo de disparo (aba não tem essas colunas aqui)
    estado_disparo = {
        chave: {
            "status": "aguardando_resposta", "status_contato": None,
            "tentativa_1": "2026-08-05T10:00:00+00:00", "tentativa_2": None, "tentativa_3": None,
            "resposta": None, "data_resposta": None,
        }
    }
    reescritas, _upserts, _syncs, chamadas = _preparar_mocks_publicar(monkeypatch, sheet_antiga, estado_disparo)

    resultado = await orch.etapa_publicar_fila_operacional([linha])

    assert resultado.sucesso is True
    assert chamadas == [[chave]]
    _, _, linhas_escritas = reescritas[0]
    escrita = linhas_escritas[0]
    assert escrita["Status Sistema"] == "aguardando_resposta"
    assert escrita["Tentativa 1"] == "2026-08-05T10:00:00+00:00"
    assert escrita["Status Contato"] == ""
    assert escrita["Resposta"] == ""


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_dias_sem_contato_sem_tentativas_usa_created_at(monkeypatch):
    """Sem nenhum disparo ainda, o marco zero é `created_at` da
    tratativa (não `data_incidente`/`data_contrato`) — decisão fechada
    2026-08-07 (ver docs/planilha_operacional.md)."""
    linha = _linha_manutencao()
    chave = _chave(linha)
    estado_disparo = {
        chave: {
            "status": "pendente", "status_contato": None,
            "tentativa_1": None, "tentativa_2": None, "tentativa_3": None,
            "resposta": None, "data_resposta": None,
            "created_at": "2024-01-08T10:00:00+00:00",  # segunda-feira
        }
    }
    reescritas, *_ = _preparar_mocks_publicar(monkeypatch, [], estado_disparo)

    resultado = await orch.etapa_publicar_fila_operacional([linha], agora=datetime(2024, 1, 10, 9, 0, 0))

    assert resultado.sucesso is True
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas[0]["Dias sem contato"] == "2 dias sem contato"


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_dias_sem_contato_usa_tentativa_mais_recente(monkeypatch):
    """Com tentativas registradas, o anchor é a mais recente
    (`tentativa_3` > `tentativa_2` > `tentativa_1`), nunca `created_at`
    (que só serve de fallback antes do 1º disparo)."""
    linha = _linha_manutencao()
    chave = _chave(linha)
    estado_disparo = {
        chave: {
            "status": "aguardando_resposta", "status_contato": None,
            "tentativa_1": "2024-01-02T10:00:00+00:00", "tentativa_2": None,
            "tentativa_3": "2024-01-08T10:00:00+00:00",  # segunda-feira
            "resposta": None, "data_resposta": None,
            "created_at": "2023-12-01T10:00:00+00:00",
        }
    }
    reescritas, *_ = _preparar_mocks_publicar(monkeypatch, [], estado_disparo)

    resultado = await orch.etapa_publicar_fila_operacional([linha], agora=datetime(2024, 1, 10, 9, 0, 0))

    assert resultado.sucesso is True
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas[0]["Dias sem contato"] == "2 dias sem contato"


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_exclui_linha_com_status_aguardando_ligacao(monkeypatch):
    """Uma tratativa escalada pra ligação (Fase F.3) some de `Tratativas`
    no próximo ciclo, mesmo sem o checkbox `Finalizado` marcado — ela
    ainda é gerada pelo motor, mas o status fresco do Supabase já mudou."""
    linha = _linha_manutencao()
    chave = _chave(linha)
    estado_disparo = {chave: {"status": "aguardando_ligacao"}}
    reescritas, _upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, [], estado_disparo)

    resultado = await orch.etapa_publicar_fila_operacional([linha])

    assert resultado.sucesso is True
    assert resultado.dados == {"publicadas": 0, "total_fila": 1}
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas == []


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_exclui_linha_com_status_encaminhado_puma(monkeypatch):
    linha = _linha_manutencao()
    chave = _chave(linha)
    estado_disparo = {chave: {"status": "encaminhado_puma"}}
    reescritas, _upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, [], estado_disparo)

    resultado = await orch.etapa_publicar_fila_operacional([linha])

    assert resultado.sucesso is True
    assert resultado.dados == {"publicadas": 0, "total_fila": 1}
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas == []


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_exclui_discrepancia_revisada_de_remocao_ativa(monkeypatch):
    """`Discrepância revisada` marcado suprime a exibição só quando a
    regra é REGRA_REMOCAO_ATIVA_* — evita o alerta repetido todo dia
    enquanto o atendente está resolvendo a divergência."""
    linha = _linha_remocao(codigo_regra="REGRA_REMOCAO_ATIVA_URGENTE")
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave, **{"Discrepância revisada": True})]
    reescritas, _upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, sheet_antiga)

    resultado = await orch.etapa_publicar_fila_operacional([linha])

    assert resultado.sucesso is True
    assert resultado.dados == {"publicadas": 0, "total_fila": 1}
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas == []


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_discrepancia_revisada_nao_afeta_outras_regras(monkeypatch):
    """O checkbox só suprime REGRA_REMOCAO_ATIVA_* — outras regras (mesmo
    marcado) continuam aparecendo normalmente."""
    linha = _linha_remocao(codigo_regra="REGRA_REMOCAO_TITULARIDADE_URGENTE")
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave, **{"Discrepância revisada": True})]
    reescritas, _upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, sheet_antiga)

    resultado = await orch.etapa_publicar_fila_operacional([linha])

    assert resultado.sucesso is True
    assert resultado.dados == {"publicadas": 1, "total_fila": 1}
    _, _, linhas_escritas = reescritas[0]
    assert len(linhas_escritas) == 1


def test_data_referencia_iso_converte_dia_maior_que_12():
    """Achado 2026-08-20: `data_incidente`/`data_contrato` "DD/MM/AAAA
    HH:MM:SS" cru quebrava o upsert no Postgres quando o dia era >12 (não
    existe mês 20) — "20/08/2026 00:30:33" é o valor real que causou o
    erro `date/time field value out of range` na Fase E ao vivo."""
    assert orch._data_referencia_iso({"data_incidente": "20/08/2026 00:30:33"}) == "2026-08-20T00:30:33"


def test_data_referencia_iso_sem_hora():
    assert orch._data_referencia_iso({"data_contrato": "01/08/2026"}) == "2026-08-01T00:00:00"


def test_data_referencia_iso_ilegivel_ou_vazia_vira_none():
    assert orch._data_referencia_iso({"data_incidente": "não sei"}) is None
    assert orch._data_referencia_iso({"data_incidente": ""}) is None
    assert orch._data_referencia_iso({}) is None


def test_formatar_data_referencia_para_exibicao_com_hora():
    assert orch._formatar_data_referencia_para_exibicao("2026-08-20T00:30:33") == "20/08/2026 00:30:33"


def test_formatar_data_referencia_para_exibicao_meia_noite_vira_so_data():
    assert orch._formatar_data_referencia_para_exibicao("2026-08-01T00:00:00") == "01/08/2026"


def test_formatar_data_referencia_para_exibicao_vazia_ou_none():
    assert orch._formatar_data_referencia_para_exibicao("") == ""
    assert orch._formatar_data_referencia_para_exibicao(None) == ""


def test_formatar_data_referencia_para_exibicao_valor_legado_nao_iso_passa_direto():
    """Dado histórico gravado antes deste fix (formato BR cru, não ISO) —
    `fromisoformat` falha, cai no fallback que devolve o valor como veio
    em vez de estourar, pra não quebrar exibição de linhas antigas."""
    assert orch._formatar_data_referencia_para_exibicao("01/08/2026") == "01/08/2026"


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_upserta_data_referencia_com_dia_maior_que_12(monkeypatch):
    """Regressão de ponta a ponta do erro real de produção (2026-08-20):
    uma linha com `data_incidente` de dia>12 precisa upsertar com sucesso
    (não propagar a exceção do Postgres) e o payload capturado precisa
    carregar a data já em ISO."""
    linha = _linha_manutencao(data_incidente="20/08/2026 00:30:33")
    _reescritas, upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, [])

    resultado = await orch.etapa_publicar_fila_operacional([linha])

    assert resultado.sucesso is True
    assert upserts[0]["data_referencia"] == "2026-08-20T00:30:33"


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_aba_tratativas_data_sem_hora(monkeypatch):
    """A aba "Tratativas" recalcula a data direto do dado fresco desta
    execução (`_data_referencia`, não passa pelos helpers de conversão pro
    Supabase) — normalização pedida pelo usuário (2026-08-21): dd/mm/aaaa
    pro atendente, sem hora, mesmo quando a origem (Track N'Me) traz a data
    do incidente com hora embutida."""
    linha = _linha_manutencao(data_incidente="20/08/2026 00:30:33")
    reescritas, _upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, [])

    resultado = await orch.etapa_publicar_fila_operacional([linha])

    assert resultado.sucesso is True
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas[0]["Data Contrato / Data Incidente"] == "20/08/2026"


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_aba_tratativas_coluna_placa(monkeypatch):
    """Coluna "Placa" nova (pedido do usuário, 2026-08-21) — mesma regra de
    resolução já usada na mensagem WhatsApp (`resolver_placa_para_mensagem`),
    pra bater com o que o operador vê no template."""
    linha_valida = _linha_manutencao(placa="ABC1234")
    linha_ficticia = _linha_manutencao(placa="SGA0612", modelo="CG 160 FAN", chassi="CHASSI-M2")
    linha_vazia = _linha_manutencao(placa="", chassi="CHASSI-M3")
    reescritas, _upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, [])

    resultado = await orch.etapa_publicar_fila_operacional([linha_valida, linha_ficticia, linha_vazia])

    assert resultado.sucesso is True
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas[0]["Placa"] == "ABC1234"
    assert linhas_escritas[1]["Placa"] == "CG 160 FAN"
    assert linhas_escritas[2]["Placa"] == "placa não cadastrada"


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_chave_manutencao_estavel_entre_rodadas(monkeypatch):
    """Guarda de não-regressão do Bloco H (2026-08-24): o Track N'Me
    atualiza `data_incidente` periodicamente enquanto o incidente segue
    aberto — a chave de manutenção não pode mais depender desse campo,
    senão a mesma pendência real vira uma linha nova (órfã) a cada
    atualização. Mesma placa+evento em 2 rodadas com `data_incidente`
    diferente precisa upsertar a MESMA `chave_unica`."""
    upserts_por_rodada = []
    for data_incidente in ("20/08/2026 13:04:04", "21/08/2026 06:19:02"):
        linha = _linha_manutencao(data_incidente=data_incidente)
        _reescritas, upserts, _syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, [])
        resultado = await orch.etapa_publicar_fila_operacional([linha])
        assert resultado.sucesso is True
        upserts_por_rodada.append(upserts[0]["chave_unica"])

    assert upserts_por_rodada[0] == upserts_por_rodada[1]


# --- _reconciliar_tratativas_ausentes (Bloco H, 2026-08-24: fechamento -----
# automático geral quando uma tratativa some da fila do motor) -------------

def _preparar_mock_reconciliar(monkeypatch, tratativas_abertas_no_motor):
    monkeypatch.setattr(
        orch.supabase_client, "buscar_tratativas_abertas_no_motor", lambda: tratativas_abertas_no_motor
    )
    syncs = []
    monkeypatch.setattr(
        orch.supabase_client, "sincronizar_campos_atendente",
        lambda chave, campos: syncs.append((chave, campos)),
    )
    return syncs


def test_reconciliar_tratativas_ausentes_presente_com_contador_zerado_nao_escreve(monkeypatch):
    syncs = _preparar_mock_reconciliar(
        monkeypatch, [{"chave_unica": "chave-1", "status": "pendente", "rodadas_ausente_fila": 0}]
    )
    orch._reconciliar_tratativas_ausentes({"chave-1"})
    assert syncs == []


def test_reconciliar_tratativas_ausentes_presente_reseta_contador(monkeypatch):
    syncs = _preparar_mock_reconciliar(
        monkeypatch, [{"chave_unica": "chave-1", "status": "pendente", "rodadas_ausente_fila": 1}]
    )
    orch._reconciliar_tratativas_ausentes({"chave-1"})
    assert syncs == [("chave-1", {"rodadas_ausente_fila": 0})]


def test_reconciliar_tratativas_ausentes_1a_rodada_so_incrementa_sem_fechar(monkeypatch):
    syncs = _preparar_mock_reconciliar(
        monkeypatch, [{"chave_unica": "chave-1", "status": "pendente", "rodadas_ausente_fila": 0}]
    )
    orch._reconciliar_tratativas_ausentes(set())
    assert syncs == [("chave-1", {"rodadas_ausente_fila": 1})]


def test_reconciliar_tratativas_ausentes_2a_rodada_fecha(monkeypatch):
    syncs = _preparar_mock_reconciliar(
        monkeypatch, [{"chave_unica": "chave-1", "status": "pendente", "rodadas_ausente_fila": 1}]
    )
    orch._reconciliar_tratativas_ausentes(set())
    assert len(syncs) == 1
    chave, campos = syncs[0]
    assert chave == "chave-1"
    assert campos["status"] == orch.STATUS_FINALIZADO
    assert "2 rodadas reais consecutivas" in campos["observacao_sistema"]


def test_reconciliar_tratativas_ausentes_respondido_nunca_fecha_sozinho(monkeypatch):
    """Decisão do usuário (2026-08-24): resposta de cliente merece revisão
    humana antes da tratativa desaparecer, mesmo ausente há várias rodadas."""
    syncs = _preparar_mock_reconciliar(
        monkeypatch, [{"chave_unica": "chave-1", "status": "respondido", "rodadas_ausente_fila": 5}]
    )
    orch._reconciliar_tratativas_ausentes(set())
    assert syncs == []


# --- etapa_sincronizar_atendente_tratativas (achado 2026-08-20: "Fase F ----
# sozinha" não via marcação nova do atendente sem reprocessar Fase E) -------

def test_etapa_sincronizar_atendente_tratativas_sucesso(monkeypatch):
    sheet_atual = [_linha_atendente_sheet("chave-1", Selecionado=True, **{"Atendimento": "Base", "Base": "Base Teste"})]
    _reescritas, _upserts, syncs, _chamadas = _preparar_mocks_publicar(monkeypatch, sheet_atual)

    resultado = orch.etapa_sincronizar_atendente_tratativas(agora=datetime(2026, 8, 20, 9, 0, 0))

    assert resultado.sucesso is True
    assert resultado.dados == {"sincronizadas": 1}
    assert syncs == [("chave-1", {
        "selecionado": True, "tecnico": "", "situacao_manual": "", "observacao_manual": "",
        "discrepancia_revisada": False, "atendimento": "base", "base_id": "base-uuid-1", "ponto_acao_id": None,
    })]


def test_sincronizar_atendente_da_aba_espaca_chamadas_sequenciais(monkeypatch):
    """Achado 2026-08-21: `sincronizar_campos_atendente` roda 1x por linha
    da aba, sem lote -- com filas grandes isso é uma rajada de chamadas
    sequenciais que pode disparar a proteção anti-bot do Cloudflare no
    gateway da Supabase. Espaçar as chamadas reduz esse sinal."""
    sheet_atual = [_linha_atendente_sheet("chave-1"), _linha_atendente_sheet("chave-2"), _linha_atendente_sheet("chave-3")]
    chamadas_sleep = []
    monkeypatch.setattr(orch, "sleep", lambda segundos: chamadas_sleep.append(segundos))
    _, _, syncs, _ = _preparar_mocks_publicar(monkeypatch, sheet_atual)
    monkeypatch.setattr(orch, "sleep", lambda segundos: chamadas_sleep.append(segundos))  # sobrescreve o no-op do fixture

    orch._sincronizar_atendente_da_aba(datetime(2026, 8, 21, 9, 0, 0))

    assert len(syncs) == 3
    assert len(chamadas_sleep) == 3
    assert all(s == orch.ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS for s in chamadas_sleep)


def test_etapa_sincronizar_atendente_tratativas_falha_ao_ler_aba(monkeypatch):
    def _ler_aba_explode(planilha, aba):
        raise RuntimeError("Google Sheets indisponível")

    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_explode)

    resultado = orch.etapa_sincronizar_atendente_tratativas()

    assert resultado.sucesso is False
    assert "Google Sheets indisponível" in resultado.mensagem


# --- situacao_manual_definida_em (widget "Situação Manual parada", Fase 4) --

@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_situacao_manual_nova_grava_timestamp(monkeypatch):
    """Chave sem `situacao_manual` anterior (não veio na busca em lote,
    equivalente a vazio) que agora chega preenchida — muda de verdade."""
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave, **{"Situação Manual": "Agendado"})]
    _, _, syncs, _ = _preparar_mocks_publicar(monkeypatch, sheet_antiga)
    agora = datetime(2026, 8, 12, 10, 0, 0)

    await orch.etapa_publicar_fila_operacional([linha], agora=agora)

    assert syncs[0][1]["situacao_manual_definida_em"] == agora.isoformat()


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_situacao_manual_sem_mudanca_omite_campo(monkeypatch):
    """Mesmo valor de `situacao_manual` já gravado — não deve tocar o
    timestamp de novo (senão o widget nunca mostraria dias parados)."""
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave, **{"Situação Manual": "Agendado"})]
    _, _, syncs, _ = _preparar_mocks_publicar(
        monkeypatch, sheet_antiga, situacao_manual_atual_por_chave={chave: "Agendado"}
    )

    await orch.etapa_publicar_fila_operacional([linha])

    assert "situacao_manual_definida_em" not in syncs[0][1]


@pytest.mark.asyncio
async def test_etapa_publicar_fila_operacional_situacao_manual_limpa_grava_none(monkeypatch):
    """Valor antigo preenchido, aba atual vazia — mudou (foi limpo), grava
    `None` (não `agora`, não faz sentido "definido agora" pra um valor
    vazio)."""
    linha = _linha_manutencao()
    chave = _chave(linha)
    sheet_antiga = [_linha_atendente_sheet(chave)]  # "Situação Manual": "" (default)
    _, _, syncs, _ = _preparar_mocks_publicar(
        monkeypatch, sheet_antiga, situacao_manual_atual_por_chave={chave: "Agendado"}
    )

    await orch.etapa_publicar_fila_operacional([linha])

    assert syncs[0][1]["situacao_manual_definida_em"] is None


# --- etapa_disparo_mensagens -------------------------------------------------

_AGORA_DIA_UTIL = datetime(2026, 8, 12, 10, 0, 0)  # quarta-feira, sem feriado, antes do corte
_CONFIG_NEWMO = {
    "canal_guid": "guid-1", "setor_id": 3,
    "templates": {"instalacao_manutencao_rota_normal": 1001, "instalacao_manutencao_base_normal": 1002,
                  "instalacao_manutencao_acao_normal": 1003},
}


def _tratativa_disparo(**overrides):
    base = {
        "id": "tratativa-1", "origem": "manutencao", "cliente": "Fulano de Tal",
        "telefone": "+5581987654321", "placa": "ABC1234", "modelo": "",
        "atendimento": "rota", "base_id": None, "ponto_acao_id": None,
        "retorno_associado": "", "selecionado": True, "tentativas": 0,
        "status": "pendente", "situacao_manual": "", "ultimo_disparo": None,
    }
    base.update(overrides)
    return base


def _preparar_mocks_disparo(monkeypatch, envio_fake=None, config=None, parametros=None, bases=None, pontos_acao=None):
    monkeypatch.setattr(orch.manager, "carregar_config", lambda: {"newmo": config or _CONFIG_NEWMO})
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: parametros or {})
    monkeypatch.setattr(orch.supabase_client, "buscar_bases_ativas", lambda: bases or [])
    monkeypatch.setattr(orch.supabase_client, "buscar_pontos_acao_ativos", lambda: pontos_acao or [])

    chamadas_envio = []

    def _enviar_template_fake(destinatario, codigo, variaveis, canal, setor):
        chamadas_envio.append({"destinatario": destinatario, "codigo": codigo, "variaveis": variaveis, "canal": canal, "setor": setor})
        if envio_fake is not None:
            return envio_fake(destinatario, codigo, variaveis, canal, setor)
        return {"sucesso": True, "classificacao": "sucesso", "cod": 1, "mensagem": "ok", "atendimento_id": 10, "mensagem_id": 20}

    monkeypatch.setattr(orch.newmo_client, "enviar_template", _enviar_template_fake)

    chamadas_atualizar = []
    monkeypatch.setattr(
        orch.supabase_client, "atualizar_apos_envio",
        lambda *args: chamadas_atualizar.append(args),
    )
    chamadas_invalido = []
    monkeypatch.setattr(
        orch.supabase_client, "marcar_contato_invalido",
        lambda tratativa_id: chamadas_invalido.append(tratativa_id),
    )
    return chamadas_envio, chamadas_atualizar, chamadas_invalido


def test_etapa_disparo_mensagens_sucesso_atualiza_apos_envio(monkeypatch):
    chamadas_envio, chamadas_atualizar, _ = _preparar_mocks_disparo(monkeypatch)

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo()], agora=_AGORA_DIA_UTIL)

    assert resultado.sucesso is True
    assert resultado.dados == {
        "enviadas": 1, "contato_invalido": 0, "falhas": 0, "total_elegiveis": 1, "sem_atendimento": [],
    }
    assert len(chamadas_envio) == 1
    assert chamadas_envio[0]["destinatario"] == "+5581987654321"
    assert chamadas_envio[0]["codigo"] == 1001
    assert chamadas_atualizar == [("tratativa-1", 10, 20, "aguardando_resposta")]


def test_etapa_disparo_mensagens_contato_invalido_nao_consome_tentativa(monkeypatch):
    envio_fake = lambda *a: {"sucesso": False, "classificacao": "contato_invalido", "cod": 7, "mensagem": "x", "atendimento_id": None, "mensagem_id": None}
    _, chamadas_atualizar, chamadas_invalido = _preparar_mocks_disparo(monkeypatch, envio_fake=envio_fake)

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo()], agora=_AGORA_DIA_UTIL)

    assert resultado.dados == {
        "enviadas": 0, "contato_invalido": 1, "falhas": 0, "total_elegiveis": 1, "sem_atendimento": [],
    }
    assert chamadas_invalido == ["tratativa-1"]
    assert chamadas_atualizar == []


def test_etapa_disparo_mensagens_reporta_progresso_por_item(monkeypatch):
    _preparar_mocks_disparo(monkeypatch)
    chamadas_progresso = []

    resultado = orch.etapa_disparo_mensagens(
        [_tratativa_disparo(id="tratativa-1"), _tratativa_disparo(id="tratativa-2")],
        agora=_AGORA_DIA_UTIL,
        on_progresso=lambda concluidos, total: chamadas_progresso.append((concluidos, total)),
    )

    assert resultado.sucesso is True
    assert chamadas_progresso == [(1, 2), (2, 2)]


def test_etapa_disparo_mensagens_cancelado_preserva_contadores_parciais(monkeypatch):
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch)

    resultado = orch.etapa_disparo_mensagens(
        [_tratativa_disparo(id="tratativa-1"), _tratativa_disparo(id="tratativa-2")],
        agora=_AGORA_DIA_UTIL,
        cancelar_checker=lambda: len(chamadas_envio) >= 1,
    )

    assert resultado.sucesso is False
    assert resultado.cancelado is not None
    assert len(resultado.cancelado["pendentes"]) == 1
    assert resultado.dados["enviadas"] == 1
    assert len(chamadas_envio) == 1


def test_etapa_disparo_mensagens_falha_temporaria_nao_grava_nada(monkeypatch):
    envio_fake = lambda *a: {"sucesso": False, "classificacao": "falha_temporaria", "cod": 4, "mensagem": "x", "atendimento_id": None, "mensagem_id": None}
    _, chamadas_atualizar, chamadas_invalido = _preparar_mocks_disparo(monkeypatch, envio_fake=envio_fake)

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo()], agora=_AGORA_DIA_UTIL)

    assert resultado.dados == {
        "enviadas": 0, "contato_invalido": 0, "falhas": 1, "total_elegiveis": 1, "sem_atendimento": [],
    }
    assert chamadas_atualizar == []
    assert chamadas_invalido == []


def test_etapa_disparo_mensagens_excecao_de_transporte_conta_como_falha_sem_derrubar_lote(monkeypatch):
    def _envio_explode(destinatario, codigo, variaveis, canal, setor):
        raise RuntimeError("timeout")

    chamadas_envio, chamadas_atualizar, _ = _preparar_mocks_disparo(monkeypatch, envio_fake=_envio_explode)

    resultado = orch.etapa_disparo_mensagens(
        [_tratativa_disparo(id="t1"), _tratativa_disparo(id="t2")], agora=_AGORA_DIA_UTIL
    )

    assert resultado.sucesso is True
    assert resultado.dados == {
        "enviadas": 0, "contato_invalido": 0, "falhas": 2, "total_elegiveis": 2, "sem_atendimento": [],
    }
    assert len(chamadas_envio) == 2  # o 2º item foi tentado mesmo o 1º tendo estourado


def test_etapa_disparo_mensagens_sem_atendimento_pula(monkeypatch):
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch)

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo(atendimento="")], agora=_AGORA_DIA_UTIL)

    assert resultado.dados["enviadas"] == 0
    assert chamadas_envio == []


def test_etapa_disparo_mensagens_sem_atendimento_registra_aviso_em_vez_de_silencio(monkeypatch):
    """Bloco E2 (2026-08-24): antes, "Selecionado" sem "Atendimento" pulava
    em silêncio total — agora aparece em `dados["sem_atendimento"]` (mesmo
    espírito de `dados["falhas"]`, só que pra dado incompleto, não falha
    de envio)."""
    _preparar_mocks_disparo(monkeypatch)

    resultado = orch.etapa_disparo_mensagens(
        [_tratativa_disparo(chave_unica="chave-1", cliente="Fulano", identificador="CHASSI-1", atendimento="")],
        agora=_AGORA_DIA_UTIL,
    )

    assert resultado.dados["sem_atendimento"] == [{
        "item": "chave-1", "erro": "Atendimento não preenchido", "descricao": "Fulano (CHASSI-1)",
    }]


def test_etapa_disparo_mensagens_retorno_associado_pendente_pula(monkeypatch):
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch)

    resultado = orch.etapa_disparo_mensagens(
        [_tratativa_disparo(retorno_associado="Instalado — associado confirma, revisar")], agora=_AGORA_DIA_UTIL
    )

    assert resultado.dados["enviadas"] == 0
    assert chamadas_envio == []


def test_etapa_disparo_mensagens_nao_elegivel_pelo_core_escalonamento_pula(monkeypatch):
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch)

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo(tentativas=3)], agora=_AGORA_DIA_UTIL)

    assert resultado.dados["enviadas"] == 0
    assert chamadas_envio == []


def test_etapa_disparo_mensagens_limite_tentativas_customizado_via_parametros(monkeypatch):
    """`limite_tentativas_disparo` em `system_parameters` sobrepõe o
    default de 3 — 3 tentativas passa a ser elegível se o parâmetro
    for maior."""
    chamadas_envio, _, _ = _preparar_mocks_disparo(
        monkeypatch, parametros={"limite_tentativas_disparo": 5}
    )

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo(tentativas=3)], agora=_AGORA_DIA_UTIL)

    assert resultado.dados["enviadas"] == 1
    assert len(chamadas_envio) == 1


def test_etapa_disparo_mensagens_horario_corte_customizado_via_parametros(monkeypatch):
    """`horario_corte_disparo` em `system_parameters` sobrepõe o default
    17:30 — 18h passa a ser "antes do corte" se o parâmetro for mais
    tarde."""
    chamadas_envio, _, _ = _preparar_mocks_disparo(
        monkeypatch, parametros={"horario_corte_disparo": "20:00"}
    )
    agora_18h = datetime(2026, 8, 12, 18, 0, 0)

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo()], agora=agora_18h)

    assert resultado.dados["enviadas"] == 1
    assert len(chamadas_envio) == 1


def test_etapa_disparo_mensagens_fora_do_horario_de_corte_nao_envia_nada(monkeypatch):
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch)
    agora_depois_do_corte = datetime(2026, 8, 12, 18, 0, 0)

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo()], agora=agora_depois_do_corte)

    assert resultado.sucesso is True
    assert resultado.dados == {"enviadas": 0, "motivo": "fora do horário de disparo"}
    assert chamadas_envio == []


def test_etapa_disparo_mensagens_fora_de_dia_util_nao_envia_nada(monkeypatch):
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch)
    sabado = datetime(2026, 8, 15, 10, 0, 0)

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo()], agora=sabado)

    assert resultado.sucesso is True
    assert resultado.dados == {"enviadas": 0, "motivo": "fora de dia útil"}
    assert chamadas_envio == []


def test_etapa_disparo_mensagens_fim_de_semana_com_excecao_permitida_envia(monkeypatch):
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch, parametros={"disparar_em_feriados_fins_de_semana": True})
    sabado = datetime(2026, 8, 15, 10, 0, 0)

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo()], agora=sabado)

    assert resultado.dados["enviadas"] == 1
    assert len(chamadas_envio) == 1


def test_etapa_disparo_mensagens_resolve_base_para_variaveis(monkeypatch):
    bases = [{"id": "base-1", "nome": "Base Teste", "endereco": "Rua Exemplo, 123"}]
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch, bases=bases)
    tratativa = _tratativa_disparo(atendimento="base", base_id="base-1")

    orch.etapa_disparo_mensagens([tratativa], agora=_AGORA_DIA_UTIL)

    assert chamadas_envio[0]["codigo"] == 1002
    assert chamadas_envio[0]["variaveis"] == ["Fulano de Tal", "manutenção", "ABC1234", "Rua Exemplo, 123"]


def test_etapa_disparo_mensagens_resolve_ponto_acao_para_variaveis(monkeypatch):
    pontos_acao = [{"id": "ponto-1", "nome_local": "Ponto Teste", "endereco": "Praça Exemplo, s/n", "data": "2026-08-20"}]
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch, pontos_acao=pontos_acao)
    tratativa = _tratativa_disparo(atendimento="acao", ponto_acao_id="ponto-1")

    orch.etapa_disparo_mensagens([tratativa], agora=_AGORA_DIA_UTIL)

    assert chamadas_envio[0]["codigo"] == 1003
    assert chamadas_envio[0]["variaveis"] == ["Fulano de Tal", "manutenção", "ABC1234", "20/08/2026", "Praça Exemplo, s/n"]


def test_etapa_disparo_mensagens_placa_ficticia_usa_modelo(monkeypatch):
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch)
    tratativa = _tratativa_disparo(placa="SGA0612", modelo="CG 160 FAN")

    orch.etapa_disparo_mensagens([tratativa], agora=_AGORA_DIA_UTIL)

    assert chamadas_envio[0]["variaveis"] == ["Fulano de Tal", "manutenção", "CG 160 FAN"]


def test_etapa_disparo_mensagens_elegiveis_none_usa_busca_padrao(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_elegiveis_para_disparo", lambda: [_tratativa_disparo()])
    chamadas_envio, _, _ = _preparar_mocks_disparo(monkeypatch)

    resultado = orch.etapa_disparo_mensagens(agora=_AGORA_DIA_UTIL)

    assert resultado.dados["enviadas"] == 1
    assert len(chamadas_envio) == 1


def test_etapa_disparo_mensagens_falha_ao_buscar_parametros(monkeypatch):
    monkeypatch.setattr(orch.manager, "carregar_config", lambda: {"newmo": _CONFIG_NEWMO})

    def _buscar_parametros_falha():
        raise RuntimeError("Supabase indisponível")

    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", _buscar_parametros_falha)

    resultado = orch.etapa_disparo_mensagens([_tratativa_disparo()], agora=_AGORA_DIA_UTIL)

    assert resultado.sucesso is False
    assert resultado.mensagem == "Supabase indisponível"


# --- etapa_finalizar_atendimentos_diarios ------------------------------------

def _tratativa_finalizacao(**overrides):
    base = {
        "id": "tratativa-1", "atendimento_id": 2425148, "tentativas": 0,
        "status": "aguardando_resposta", "situacao_manual": "", "retorno_associado": "",
    }
    base.update(overrides)
    return base


def test_etapa_finalizar_atendimentos_diarios_finaliza_elegivel(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    chamadas = []
    monkeypatch.setattr(
        orch.newmo_client, "finalizar_atendimento",
        lambda atendimento_id: chamadas.append(atendimento_id) or "Atendimento finalizado com sucesso.",
    )

    resultado = orch.etapa_finalizar_atendimentos_diarios([_tratativa_finalizacao()])

    assert resultado.sucesso is True
    assert resultado.dados == {"finalizadas": 1, "falhas": 0, "total_candidatas": 1}
    assert chamadas == [2425148]


def test_etapa_finalizar_atendimentos_diarios_reporta_progresso_por_item(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    monkeypatch.setattr(orch.newmo_client, "finalizar_atendimento", lambda atendimento_id: "ok")
    chamadas_progresso = []

    resultado = orch.etapa_finalizar_atendimentos_diarios(
        [_tratativa_finalizacao(id="t1"), _tratativa_finalizacao(id="t2")],
        on_progresso=lambda concluidos, total: chamadas_progresso.append((concluidos, total)),
    )

    assert resultado.sucesso is True
    assert chamadas_progresso == [(1, 2), (2, 2)]


def test_etapa_finalizar_atendimentos_diarios_cancelado_preserva_contadores_parciais(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    chamadas = []
    monkeypatch.setattr(
        orch.newmo_client, "finalizar_atendimento",
        lambda atendimento_id: chamadas.append(atendimento_id) or "ok",
    )

    resultado = orch.etapa_finalizar_atendimentos_diarios(
        [_tratativa_finalizacao(id="t1"), _tratativa_finalizacao(id="t2")],
        cancelar_checker=lambda: len(chamadas) >= 1,
    )

    assert resultado.sucesso is False
    assert resultado.cancelado is not None
    assert len(resultado.cancelado["pendentes"]) == 1
    assert resultado.dados["finalizadas"] == 1
    assert len(chamadas) == 1


def test_etapa_finalizar_atendimentos_diarios_ja_escalou_pra_ligacao_pula(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    chamadas = []
    monkeypatch.setattr(orch.newmo_client, "finalizar_atendimento", lambda atendimento_id: chamadas.append(atendimento_id))

    resultado = orch.etapa_finalizar_atendimentos_diarios([_tratativa_finalizacao(tentativas=3)])

    assert resultado.dados["finalizadas"] == 0
    assert chamadas == []


def test_etapa_finalizar_atendimentos_diarios_situacao_manual_preenchida_pula(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    chamadas = []
    monkeypatch.setattr(orch.newmo_client, "finalizar_atendimento", lambda atendimento_id: chamadas.append(atendimento_id))

    resultado = orch.etapa_finalizar_atendimentos_diarios([_tratativa_finalizacao(situacao_manual="Agendado")])

    assert resultado.dados["finalizadas"] == 0
    assert chamadas == []


def test_etapa_finalizar_atendimentos_diarios_retorno_associado_pendente_pula(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    chamadas = []
    monkeypatch.setattr(orch.newmo_client, "finalizar_atendimento", lambda atendimento_id: chamadas.append(atendimento_id))

    resultado = orch.etapa_finalizar_atendimentos_diarios(
        [_tratativa_finalizacao(retorno_associado="Instalado — associado confirma, revisar")]
    )

    assert resultado.dados["finalizadas"] == 0
    assert chamadas == []


def test_etapa_finalizar_atendimentos_diarios_nao_grava_nada_no_supabase(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    monkeypatch.setattr(orch.newmo_client, "finalizar_atendimento", lambda atendimento_id: "ok")
    for nome_funcao in ("upsert_tratativas_em_lote", "atualizar_apos_envio", "marcar_contato_invalido", "sincronizar_campos_atendente"):
        monkeypatch.setattr(
            orch.supabase_client, nome_funcao,
            lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"{nome_funcao} não deveria ser chamada")),
        )

    resultado = orch.etapa_finalizar_atendimentos_diarios([_tratativa_finalizacao()])

    assert resultado.dados["finalizadas"] == 1


def test_etapa_finalizar_atendimentos_diarios_excecao_de_transporte_conta_como_falha_sem_derrubar_lote(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    chamadas = []

    def _finalizar_explode(atendimento_id):
        chamadas.append(atendimento_id)
        raise RuntimeError("timeout")

    monkeypatch.setattr(orch.newmo_client, "finalizar_atendimento", _finalizar_explode)

    resultado = orch.etapa_finalizar_atendimentos_diarios(
        [_tratativa_finalizacao(id="t1", atendimento_id=1), _tratativa_finalizacao(id="t2", atendimento_id=2)]
    )

    assert resultado.sucesso is True
    assert resultado.dados == {"finalizadas": 0, "falhas": 2, "total_candidatas": 2}
    assert chamadas == [1, 2]  # o 2º item foi tentado mesmo o 1º tendo estourado


def test_etapa_finalizar_atendimentos_diarios_candidatas_none_usa_busca_padrao(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_candidatas_finalizacao_atendimento", lambda: [_tratativa_finalizacao()])
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    chamadas = []
    monkeypatch.setattr(orch.newmo_client, "finalizar_atendimento", lambda atendimento_id: chamadas.append(atendimento_id))

    resultado = orch.etapa_finalizar_atendimentos_diarios()

    assert resultado.dados["finalizadas"] == 1
    assert chamadas == [2425148]


def test_etapa_finalizar_atendimentos_diarios_limite_tentativas_customizado_via_parametros(monkeypatch):
    """`limite_tentativas_disparo` sobrepõe o default 3 — com o limite
    em 5, quem tem 3 tentativas ainda "estaria elegível pra outra
    tentativa automática" (3 < 5) e por isso É finalizado (fecha o
    atendimento no Newmo); com 5 tentativas, já bateu o novo limite e
    NÃO é finalizado (mesmo critério de `test_etapa_finalizar_
    atendimentos_diarios_ja_escalou_pra_ligacao_pula`, só que com o
    limite deslocado)."""
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {"limite_tentativas_disparo": 5})
    chamadas = []
    monkeypatch.setattr(orch.newmo_client, "finalizar_atendimento", lambda atendimento_id: chamadas.append(atendimento_id))

    resultado = orch.etapa_finalizar_atendimentos_diarios(
        [_tratativa_finalizacao(id="t1", atendimento_id=1, tentativas=3),
         _tratativa_finalizacao(id="t2", atendimento_id=2, tentativas=5)]
    )

    assert resultado.dados["finalizadas"] == 1
    assert chamadas == [1]


def test_etapa_finalizar_atendimentos_diarios_falha_ao_buscar_candidatas(monkeypatch):
    def _busca_falha():
        raise RuntimeError("Supabase indisponível")

    monkeypatch.setattr(orch.supabase_client, "buscar_candidatas_finalizacao_atendimento", _busca_falha)

    resultado = orch.etapa_finalizar_atendimentos_diarios()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Supabase indisponível"


# --- etapa_escalonar_ligacao -------------------------------------------------

def _tratativa_candidata(**extra):
    base = {
        "id": "tratativa-uuid-1", "chave_unica": "chave-hash-1", "origem": "manutencao",
        "identificador": "CHASSI-M1", "chassi": "CHASSI-M1", "cliente": "Cliente M",
        "telefone": "+5581988887777", "cidade": "Recife", "bairro": "Boa Vista",
        "data_referencia": "01/08/2026", "sga": "ATIVO", "acao_sugerida": "Seguir esteira.",
        "observacao_sistema": "Sem comunicação.", "codigo_regra": "REGRA_1",
        "status": "aguardando_resposta", "tentativas": 3, "situacao_manual": "",
        "tentativa_1": "2026-08-01T10:00:00+00:00", "tentativa_2": "2026-08-03T10:00:00+00:00",
        "tentativa_3": "2026-08-05T10:00:00+00:00",
    }
    base.update(extra)
    return base


def _preparar_mocks_escalonar(
    monkeypatch, candidatas, linhas_sheet_antiga=None, templates=None, parametros=None,
    bases=None, pontos_acao=None,
):
    linhas_sheet_antiga = linhas_sheet_antiga if linhas_sheet_antiga is not None else []
    templates = templates if templates is not None else {"REGRA_1": {"nivel_urgencia": 5}}
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: parametros or {})
    monkeypatch.setattr(orch.supabase_client, "buscar_bases_ativas", lambda: bases or [])
    monkeypatch.setattr(orch.supabase_client, "buscar_pontos_acao_ativos", lambda: pontos_acao or [])

    marcadas = []

    def _marcar_fake(tratativa_id):
        marcadas.append(tratativa_id)

    def _ler_aba_fake(planilha, aba):
        return linhas_sheet_antiga

    reescritas = []

    def _reescrever_aba_fake(planilha, aba, linhas):
        reescritas.append((planilha, aba, linhas))

    monkeypatch.setattr(orch.supabase_client, "buscar_candidatas_escalonamento_ligacao", lambda: candidatas)
    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: templates)
    monkeypatch.setattr(orch.supabase_client, "marcar_aguardando_ligacao", _marcar_fake)
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_fake)
    monkeypatch.setattr(orch.google_sheets_client, "reescrever_aba", _reescrever_aba_fake)
    return marcadas, reescritas


def test_etapa_escalonar_ligacao_marca_e_escreve_linha_nova(monkeypatch):
    candidata = _tratativa_candidata()
    marcadas, reescritas = _preparar_mocks_escalonar(monkeypatch, [candidata])

    resultado = orch.etapa_escalonar_ligacao()

    assert resultado.sucesso is True
    assert resultado.dados == {"escaladas": 1, "total_candidatas": 1}
    assert marcadas == ["tratativa-uuid-1"]

    _, aba, linhas_escritas = reescritas[0]
    assert aba == "Pendente de Ligação"
    assert len(linhas_escritas) == 1
    escrita = linhas_escritas[0]
    assert escrita["ID (hash)"] == "chave-hash-1"
    assert escrita["Tipo Serviço"] == "Manutenção"
    assert escrita["Nível de Urgência"] == 5
    assert escrita["Histórico de Tentativas (msg)"] == "01/08/2026, 03/08/2026, 05/08/2026"
    assert escrita["Data Contato"] == ""
    assert escrita["Retornou?"] == ""
    assert escrita["Conseguiu Agendar?"] == ""


def test_etapa_escalonar_ligacao_reformata_data_referencia_iso_para_br(monkeypatch):
    """Achado 2026-08-20: `data_referencia` agora é gravada em ISO no
    Supabase (fix do erro de dia>12 na Fase E) — "Pendente de Ligação" lê
    de volta do Supabase, então precisa reformatar pro padrão brasileiro
    que o atendente já está acostumado a ver."""
    candidata = _tratativa_candidata(data_referencia="2026-08-20T00:30:33")
    _marcadas, reescritas = _preparar_mocks_escalonar(monkeypatch, [candidata])

    resultado = orch.etapa_escalonar_ligacao()

    assert resultado.sucesso is True
    _, _aba, linhas_escritas = reescritas[0]
    assert linhas_escritas[0]["Data Contrato / Data Incidente"] == "20/08/2026 00:30:33"


def test_etapa_escalonar_ligacao_reporta_progresso_por_item(monkeypatch):
    candidatas = [
        _tratativa_candidata(id="tratativa-uuid-1", chave_unica="chave-hash-1"),
        _tratativa_candidata(id="tratativa-uuid-2", chave_unica="chave-hash-2"),
    ]
    _preparar_mocks_escalonar(monkeypatch, candidatas)
    chamadas_progresso = []

    resultado = orch.etapa_escalonar_ligacao(
        on_progresso=lambda concluidos, total: chamadas_progresso.append((concluidos, total))
    )

    assert resultado.sucesso is True
    assert chamadas_progresso == [(1, 2), (2, 2)]


def test_etapa_escalonar_ligacao_cancelado_ainda_escreve_parcial(monkeypatch):
    candidatas = [
        _tratativa_candidata(id="tratativa-uuid-1", chave_unica="chave-hash-1"),
        _tratativa_candidata(id="tratativa-uuid-2", chave_unica="chave-hash-2"),
    ]
    marcadas, reescritas = _preparar_mocks_escalonar(monkeypatch, candidatas)

    resultado = orch.etapa_escalonar_ligacao(cancelar_checker=lambda: len(marcadas) >= 1)

    assert resultado.sucesso is False
    assert resultado.cancelado == {"pendentes": [candidatas[1]]}
    assert resultado.dados["escaladas"] == 1
    assert marcadas == ["tratativa-uuid-1"]
    _, aba, linhas_escritas = reescritas[0]
    assert aba == "Pendente de Ligação"
    assert len(linhas_escritas) == 1


def test_etapa_escalonar_ligacao_dias_sem_contato_usa_tentativa_3(monkeypatch):
    candidata = _tratativa_candidata()  # tentativa_3 = 2026-08-05T10:00:00+00:00 (quarta)
    _marcadas, reescritas = _preparar_mocks_escalonar(monkeypatch, [candidata])

    resultado = orch.etapa_escalonar_ligacao(agora=datetime(2026, 8, 7, 9, 0, 0))  # sexta

    assert resultado.sucesso is True
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas[0]["Dias sem contato"] == "2 dias sem contato"


def test_etapa_escalonar_ligacao_recalcula_dias_sem_contato_de_linha_existente(monkeypatch):
    """Linha que já estava na aba não é só carregada igual — "Dias sem
    contato" é recalculado a cada execução a partir do próprio
    "Histórico de Tentativas (msg)" já presente na linha (sem round-trip
    ao Supabase)."""
    linha_antiga = _linha_pendente_ligacao_sheet(
        "chave-antiga", **{
            "Histórico de Tentativas (msg)": "01/08/2026, 03/08/2026, 05/08/2026",
            "Dias sem contato": "0 dias sem contato",  # valor "velho", da execução anterior
        }
    )
    _marcadas, reescritas = _preparar_mocks_escalonar(monkeypatch, [], linhas_sheet_antiga=[linha_antiga])

    resultado = orch.etapa_escalonar_ligacao(agora=datetime(2026, 8, 7, 9, 0, 0))

    assert resultado.sucesso is True
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas[0]["Dias sem contato"] == "2 dias sem contato"


def test_etapa_escalonar_ligacao_ignora_candidata_nao_elegivel(monkeypatch):
    candidata = _tratativa_candidata(tentativas=2)  # ainda não bateu 3 tentativas
    marcadas, reescritas = _preparar_mocks_escalonar(monkeypatch, [candidata])

    resultado = orch.etapa_escalonar_ligacao()

    assert resultado.sucesso is True
    assert resultado.dados == {"escaladas": 0, "total_candidatas": 1}
    assert marcadas == []
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas == []


def test_etapa_escalonar_ligacao_limite_tentativas_customizado_via_parametros(monkeypatch):
    """`limite_tentativas_disparo` sobrepõe o default 3 — 3 tentativas
    deixa de escalar pra ligação se o parâmetro for maior."""
    candidata = _tratativa_candidata(tentativas=3)
    marcadas, reescritas = _preparar_mocks_escalonar(
        monkeypatch, [candidata], parametros={"limite_tentativas_disparo": 5}
    )

    resultado = orch.etapa_escalonar_ligacao()

    assert resultado.dados == {"escaladas": 0, "total_candidatas": 1}
    assert marcadas == []
    _, _, linhas_escritas = reescritas[0]
    assert linhas_escritas == []


def test_etapa_escalonar_ligacao_preserva_linhas_existentes(monkeypatch):
    linha_antiga = {"ID (hash)": "chave-antiga", "Retornou?": "", "Conseguiu Agendar?": "", "Dias sem contato": ""}
    candidata = _tratativa_candidata()
    marcadas, reescritas = _preparar_mocks_escalonar(monkeypatch, [candidata], linhas_sheet_antiga=[linha_antiga])

    resultado = orch.etapa_escalonar_ligacao()

    assert resultado.sucesso is True
    _, _, linhas_escritas = reescritas[0]
    assert len(linhas_escritas) == 2
    assert linhas_escritas[0] == linha_antiga
    assert linhas_escritas[1]["ID (hash)"] == "chave-hash-1"


def test_etapa_escalonar_ligacao_candidatas_none_usa_busca_padrao(monkeypatch):
    candidata = _tratativa_candidata()
    chamou_busca_padrao = []

    def _buscar_fake():
        chamou_busca_padrao.append(True)
        return [candidata]

    monkeypatch.setattr(orch.supabase_client, "buscar_candidatas_escalonamento_ligacao", _buscar_fake)
    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: {"REGRA_1": {"nivel_urgencia": 5}})
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    monkeypatch.setattr(orch.supabase_client, "marcar_aguardando_ligacao", lambda tratativa_id: None)
    monkeypatch.setattr(orch.supabase_client, "buscar_bases_ativas", lambda: [])
    monkeypatch.setattr(orch.supabase_client, "buscar_pontos_acao_ativos", lambda: [])
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", lambda planilha, aba: [])
    monkeypatch.setattr(orch.google_sheets_client, "reescrever_aba", lambda planilha, aba, linhas: None)

    resultado = orch.etapa_escalonar_ligacao()

    assert resultado.sucesso is True
    assert chamou_busca_padrao == [True]


def test_etapa_escalonar_ligacao_falha_ao_buscar_candidatas(monkeypatch):
    def _buscar_falha():
        raise RuntimeError("Supabase indisponível")

    monkeypatch.setattr(orch.supabase_client, "buscar_candidatas_escalonamento_ligacao", _buscar_falha)

    resultado = orch.etapa_escalonar_ligacao()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Supabase indisponível"


def test_etapa_escalonar_ligacao_falha_ao_ler_aba(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_candidatas_escalonamento_ligacao", lambda: [])
    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: {})
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    monkeypatch.setattr(orch.supabase_client, "buscar_bases_ativas", lambda: [])
    monkeypatch.setattr(orch.supabase_client, "buscar_pontos_acao_ativos", lambda: [])

    def _ler_aba_falha(planilha, aba):
        raise RuntimeError("Sheets indisponível pra leitura")

    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_falha)

    resultado = orch.etapa_escalonar_ligacao()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Sheets indisponível pra leitura"


def test_etapa_escalonar_ligacao_falha_ao_reescrever_aba(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_candidatas_escalonamento_ligacao", lambda: [])
    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: {})
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: {})
    monkeypatch.setattr(orch.supabase_client, "buscar_bases_ativas", lambda: [])
    monkeypatch.setattr(orch.supabase_client, "buscar_pontos_acao_ativos", lambda: [])
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", lambda planilha, aba: [])

    def _reescrever_aba_falha(planilha, aba, linhas):
        raise RuntimeError("Sheets indisponível pra escrita")

    monkeypatch.setattr(orch.google_sheets_client, "reescrever_aba", _reescrever_aba_falha)

    resultado = orch.etapa_escalonar_ligacao()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Sheets indisponível pra escrita"


def test_etapa_escalonar_ligacao_popula_atendimento_base_ponto_acao(monkeypatch):
    """2026-08-14 — achado ao vivo: essas 3 colunas existiam em "Pendente
    de Ligação", tinham dropdown, mas nunca eram populadas. Escalar
    precisa levar o que já estava decidido em Tratativas."""
    candidata = _tratativa_candidata(atendimento="base", base_id="base-uuid-1")
    marcadas, reescritas = _preparar_mocks_escalonar(
        monkeypatch, [candidata],
        bases=[{"id": "base-uuid-1", "nome": "Base Afogados"}],
        pontos_acao=[{"id": "ponto-uuid-1", "nome_local": "Ponto Centro"}],
    )

    resultado = orch.etapa_escalonar_ligacao()

    assert resultado.sucesso is True
    _, _, linhas_escritas = reescritas[0]
    escrita = linhas_escritas[0]
    assert escrita["Atendimento"] == "Base"
    assert escrita["Base"] == "Base Afogados"
    assert escrita["Ponto de Ação"] == ""


def test_etapa_escalonar_ligacao_atendimento_acao_popula_ponto_de_acao(monkeypatch):
    candidata = _tratativa_candidata(atendimento="acao", ponto_acao_id="ponto-uuid-1")
    _, reescritas = _preparar_mocks_escalonar(
        monkeypatch, [candidata],
        bases=[{"id": "base-uuid-1", "nome": "Base Afogados"}],
        pontos_acao=[{"id": "ponto-uuid-1", "nome_local": "Ponto Centro"}],
    )

    orch.etapa_escalonar_ligacao()

    escrita = reescritas[0][2][0]
    assert escrita["Atendimento"] == "Ação"
    assert escrita["Base"] == ""
    assert escrita["Ponto de Ação"] == "Ponto Centro"


def test_etapa_escalonar_ligacao_sem_atendimento_decidido_popula_vazio(monkeypatch):
    candidata = _tratativa_candidata(atendimento=None, base_id=None, ponto_acao_id=None)
    _, reescritas = _preparar_mocks_escalonar(monkeypatch, [candidata])

    orch.etapa_escalonar_ligacao()

    escrita = reescritas[0][2][0]
    assert escrita["Atendimento"] == ""
    assert escrita["Base"] == ""
    assert escrita["Ponto de Ação"] == ""


# --- etapa_processar_resultado_ligacao --------------------------------------

def _linha_pendente_ligacao_sheet(chave, **extra):
    base = {
        "ID (hash)": chave, "Retornou?": "", "Conseguiu Agendar?": "",
        "Data Contato": "", "Observação": "", "Finalizado": False,
        "Dias sem contato": "",
    }
    base.update(extra)
    return base


def _tratativa_puma(**extra):
    base = {
        "id": "tratativa-uuid-9", "chave_unica": "chave-hash-9", "origem": "manutencao",
        "identificador": "CHASSI-P1", "chassi": "CHASSI-P1", "cliente": "Cliente P",
        "telefone": "+5581988889999", "cidade": "Recife", "bairro": "Boa Vista",
        "data_referencia": "01/08/2026", "acao_sugerida": "Seguir esteira.",
        "observacao_sistema": "Sem comunicação.", "codigo_regra": "REGRA_1",
        "status": "aguardando_ligacao",
    }
    base.update(extra)
    return base


def _preparar_mocks_processar_ligacao(
    monkeypatch, linhas_pendente, linhas_puma_existentes=None, tratativas_por_chave=None, templates=None,
    bases=None, pontos_acao=None, equipamentos_administrador=None, incidentes_administrador=None,
    parametros=None,
):
    linhas_puma_existentes = linhas_puma_existentes if linhas_puma_existentes is not None else []
    tratativas_por_chave = tratativas_por_chave or {}
    templates = templates if templates is not None else {"REGRA_1": {"nivel_urgencia": 5}}
    equipamentos_administrador = equipamentos_administrador if equipamentos_administrador is not None else []
    incidentes_administrador = incidentes_administrador if incidentes_administrador is not None else []
    monkeypatch.setattr(orch.supabase_client, "buscar_bases_ativas", lambda: bases or [])
    monkeypatch.setattr(orch.supabase_client, "buscar_pontos_acao_ativos", lambda: pontos_acao or [])
    monkeypatch.setattr(orch.supabase_client, "buscar_parametros", lambda: parametros or {})

    chamadas_ler_aba = []

    def _ler_aba_fake(planilha, aba):
        chamadas_ler_aba.append(aba)
        if aba == "Pendente de Ligação":
            return linhas_pendente
        if aba == "Encaminhar pra Puma":
            return linhas_puma_existentes
        if aba == "Rastreadores Ativos":
            return equipamentos_administrador
        if aba == "Incidentes":
            return incidentes_administrador
        if aba == "Instalação-Remoção":
            return []
        raise AssertionError(f"aba inesperada: {aba}")

    reescritas = []

    def _reescrever_aba_fake(planilha, aba, linhas):
        reescritas.append((planilha, aba, linhas))

    syncs = []

    def _sincronizar_fake(chave_unica, campos):
        syncs.append((chave_unica, campos))

    registros_ligacao = []

    def _registrar_ligacao_fake(tratativa_id, dados):
        registros_ligacao.append((tratativa_id, dados))

    encaminhamentos = []

    def _encaminhar_puma_fake(tratativa_id, motivo):
        encaminhamentos.append((tratativa_id, motivo))

    def _buscar_tratativa_fake(chave_unica):
        return tratativas_por_chave.get(chave_unica)

    syncs_puma = []

    def _sincronizar_status_puma_fake(tratativa_id, status_novo):
        syncs_puma.append((tratativa_id, status_novo))

    syncs_puma_observacao = []

    def _sincronizar_observacao_puma_fake(tratativa_id, observacao):
        syncs_puma_observacao.append((tratativa_id, observacao))

    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_fake)
    monkeypatch.setattr(orch.google_sheets_client, "reescrever_aba", _reescrever_aba_fake)
    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: templates)
    monkeypatch.setattr(orch.supabase_client, "sincronizar_campos_atendente", _sincronizar_fake)
    monkeypatch.setattr(orch.supabase_client, "registrar_ligacao", _registrar_ligacao_fake)
    monkeypatch.setattr(orch.supabase_client, "encaminhar_puma", _encaminhar_puma_fake)
    monkeypatch.setattr(orch.supabase_client, "buscar_tratativa_por_chave", _buscar_tratativa_fake)
    monkeypatch.setattr(orch.supabase_client, "sincronizar_status_puma", _sincronizar_status_puma_fake)
    monkeypatch.setattr(orch.supabase_client, "sincronizar_observacao_puma", _sincronizar_observacao_puma_fake)

    return {
        "chamadas_ler_aba": chamadas_ler_aba,
        "reescritas": reescritas,
        "syncs": syncs,
        "registros_ligacao": registros_ligacao,
        "encaminhamentos": encaminhamentos,
        "syncs_puma": syncs_puma,
        "syncs_puma_observacao": syncs_puma_observacao,
    }


def _reescrita_de(mocks, aba):
    for _planilha, aba_escrita, linhas in mocks["reescritas"]:
        if aba_escrita == aba:
            return linhas
    raise AssertionError(f"reescrever_aba não foi chamado pra aba {aba!r}")


def test_etapa_processar_resultado_ligacao_reporta_progresso_por_item(monkeypatch):
    linhas = [
        _linha_pendente_ligacao_sheet("chave-1", **{"Finalizado": True}),
        _linha_pendente_ligacao_sheet("chave-2", **{"Finalizado": True}),
    ]
    _preparar_mocks_processar_ligacao(monkeypatch, linhas)
    chamadas_progresso = []

    resultado = orch.etapa_processar_resultado_ligacao(
        linhas, on_progresso=lambda concluidos, total: chamadas_progresso.append((concluidos, total))
    )

    assert resultado.sucesso is True
    assert chamadas_progresso == [(1, 2), (2, 2)]


def test_etapa_processar_resultado_ligacao_cancelado_preserva_linhas_nao_alcancadas(monkeypatch):
    linhas = [
        _linha_pendente_ligacao_sheet("chave-1", **{"Finalizado": True}),
        _linha_pendente_ligacao_sheet("chave-2", **{"Finalizado": True}),
    ]
    mocks = _preparar_mocks_processar_ligacao(monkeypatch, linhas)

    resultado = orch.etapa_processar_resultado_ligacao(
        linhas, cancelar_checker=lambda: len(mocks["syncs"]) >= 1
    )

    assert resultado.sucesso is False
    assert resultado.cancelado == {"pendentes": [linhas[1]]}
    assert resultado.dados["processadas"] == 1
    # a linha ainda não alcançada precisa continuar na aba, não pode sumir
    restantes = _reescrita_de(mocks, "Pendente de Ligação")
    assert len(restantes) == 1
    assert restantes[0]["ID (hash)"] == "chave-2"


def test_etapa_processar_resultado_ligacao_finalizado_manual_vence_tudo(monkeypatch):
    linha = _linha_pendente_ligacao_sheet(
        "chave-1", **{"Finalizado": True, "Retornou?": "Sim", "Conseguiu Agendar?": "Não"}
    )
    mocks = _preparar_mocks_processar_ligacao(monkeypatch, [linha])

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert mocks["syncs"] == [
        ("chave-1", {"atendimento": "", "base_id": None, "ponto_acao_id": None}),
        ("chave-1", {"status": "finalizado"}),
    ]
    assert mocks["registros_ligacao"] == []
    assert mocks["encaminhamentos"] == []
    assert _reescrita_de(mocks, "Pendente de Ligação") == []


def test_etapa_processar_resultado_ligacao_retornou_nao_vai_pra_puma(monkeypatch):
    tratativa = _tratativa_puma(status="aguardando_ligacao")
    linha = _linha_pendente_ligacao_sheet("chave-hash-9", **{"Retornou?": "Não", "Data Contato": "07/08/2026"})
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [linha], tratativas_por_chave={"chave-hash-9": tratativa}
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert mocks["registros_ligacao"] == [
        ("tratativa-uuid-9", {
            "data_contato": "2026-08-07", "retornou": False, "conseguiu_agendar": False, "observacao": "",
        })
    ]
    assert mocks["encaminhamentos"] == [("tratativa-uuid-9", "Associado não retornou a ligação.")]
    assert _reescrita_de(mocks, "Pendente de Ligação") == []
    linhas_puma = _reescrita_de(mocks, "Encaminhar pra Puma")
    assert len(linhas_puma) == 1
    assert linhas_puma[0]["ID (hash)"] == "chave-hash-9"
    assert linhas_puma[0]["Nível de Urgência"] == 5
    assert linhas_puma[0]["Motivo"] == "Associado não retornou a ligação."


def test_etapa_processar_resultado_ligacao_retornou_sim_conseguiu_sim_permanece(monkeypatch):
    tratativa = _tratativa_puma(status="aguardando_ligacao")
    linha = _linha_pendente_ligacao_sheet("chave-hash-9", **{"Retornou?": "Sim", "Conseguiu Agendar?": "Sim"})
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [linha], tratativas_por_chave={"chave-hash-9": tratativa}
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert mocks["registros_ligacao"] == [
        ("tratativa-uuid-9", {"data_contato": None, "retornou": True, "conseguiu_agendar": True, "observacao": ""})
    ]
    assert mocks["encaminhamentos"] == []
    assert _reescrita_de(mocks, "Pendente de Ligação") == [linha]


def test_etapa_processar_resultado_ligacao_retornou_sim_conseguiu_sim_ja_finalizado_nao_duplica(monkeypatch):
    """Ciclo seguinte: o atendente ainda não marcou `Finalizado`, os
    dropdowns continuam Sim/Sim — não registra a ligação de novo."""
    tratativa = _tratativa_puma(status="finalizado")
    linha = _linha_pendente_ligacao_sheet("chave-hash-9", **{"Retornou?": "Sim", "Conseguiu Agendar?": "Sim"})
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [linha], tratativas_por_chave={"chave-hash-9": tratativa}
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert mocks["registros_ligacao"] == []
    assert _reescrita_de(mocks, "Pendente de Ligação") == [linha]


def test_etapa_processar_resultado_ligacao_retornou_sim_conseguiu_nao_permanece(monkeypatch):
    linha = _linha_pendente_ligacao_sheet("chave-1", **{"Retornou?": "Sim", "Conseguiu Agendar?": "Não"})
    mocks = _preparar_mocks_processar_ligacao(monkeypatch, [linha])

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert mocks["registros_ligacao"] == []
    assert mocks["encaminhamentos"] == []
    assert mocks["syncs"] == [("chave-1", {"atendimento": "", "base_id": None, "ponto_acao_id": None})]
    assert _reescrita_de(mocks, "Pendente de Ligação") == [linha]


def test_etapa_processar_resultado_ligacao_retornou_sim_conseguiu_vazio_permanece(monkeypatch):
    linha = _linha_pendente_ligacao_sheet("chave-1", **{"Retornou?": "Sim"})
    mocks = _preparar_mocks_processar_ligacao(monkeypatch, [linha])

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert _reescrita_de(mocks, "Pendente de Ligação") == [linha]


def test_etapa_processar_resultado_ligacao_retornou_vazio_permanece(monkeypatch):
    linha = _linha_pendente_ligacao_sheet("chave-1")
    mocks = _preparar_mocks_processar_ligacao(monkeypatch, [linha])

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert _reescrita_de(mocks, "Pendente de Ligação") == [linha]


def test_etapa_processar_resultado_ligacao_tratativa_nao_encontrada_permanece(monkeypatch):
    linha = _linha_pendente_ligacao_sheet("chave-fantasma", **{"Retornou?": "Não"})
    mocks = _preparar_mocks_processar_ligacao(monkeypatch, [linha], tratativas_por_chave={})

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert mocks["registros_ligacao"] == []
    assert mocks["encaminhamentos"] == []
    assert _reescrita_de(mocks, "Pendente de Ligação") == [linha]


def test_etapa_processar_resultado_ligacao_sincroniza_atendimento_base_ponto_acao(monkeypatch):
    """2026-08-14 — a aba é a fonte de verdade pra esses 3 campos
    enquanto a pendência está na fila de ligação, mesma filosofia de
    `_sincronizar_atendente_da_aba` pra Tratativas."""
    linha = _linha_pendente_ligacao_sheet(
        "chave-1", **{"Atendimento": "Base", "Base": "Base Afogados", "Ponto de Ação": ""}
    )
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [linha], bases=[{"id": "base-uuid-1", "nome": "Base Afogados"}],
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert mocks["syncs"] == [
        ("chave-1", {"atendimento": "base", "base_id": "base-uuid-1", "ponto_acao_id": None}),
    ]


def test_etapa_processar_resultado_ligacao_recalcula_dias_sem_contato_de_linha_que_permanece(monkeypatch):
    """Confirma recálculo de verdade (não só "" == ""): "Histórico de
    Tentativas (msg)" já presente na linha dá o anchor, "Dias sem
    contato" muda mesmo a linha permanecendo (Retornou? vazio)."""
    linha = _linha_pendente_ligacao_sheet(
        "chave-1", **{
            "Histórico de Tentativas (msg)": "01/08/2026, 03/08/2026, 05/08/2026",
            "Dias sem contato": "0 dias sem contato",
        }
    )
    mocks = _preparar_mocks_processar_ligacao(monkeypatch, [linha])

    resultado = orch.etapa_processar_resultado_ligacao(agora=datetime(2026, 8, 7, 9, 0, 0))

    assert resultado.sucesso is True
    linhas_escritas = _reescrita_de(mocks, "Pendente de Ligação")
    assert linhas_escritas[0]["Dias sem contato"] == "2 dias sem contato"


def test_etapa_processar_resultado_ligacao_linha_nova_puma_nasce_zero_dias(monkeypatch):
    tratativa = _tratativa_puma(status="aguardando_ligacao")
    linha = _linha_pendente_ligacao_sheet("chave-hash-9", **{"Retornou?": "Não", "Data Contato": "07/08/2026"})
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [linha], tratativas_por_chave={"chave-hash-9": tratativa}
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    linhas_puma = _reescrita_de(mocks, "Encaminhar pra Puma")
    assert linhas_puma[0]["Dias sem contato"] == "0 dias sem contato"


def test_etapa_processar_resultado_ligacao_reformata_data_referencia_iso_para_br(monkeypatch):
    """Mesmo achado de `test_etapa_escalonar_ligacao_reformata_data_referencia_iso_para_br`,
    pro caminho "Encaminhar pra Puma" (Fase F.5)."""
    tratativa = _tratativa_puma(status="aguardando_ligacao", data_referencia="2026-08-01T00:00:00")
    linha = _linha_pendente_ligacao_sheet("chave-hash-9", **{"Retornou?": "Não", "Data Contato": "07/08/2026"})
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [linha], tratativas_por_chave={"chave-hash-9": tratativa}
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    linhas_puma = _reescrita_de(mocks, "Encaminhar pra Puma")
    assert linhas_puma[0]["Data Contrato / Data Incidente"] == "01/08/2026"


def test_etapa_processar_resultado_ligacao_recalcula_dias_sem_contato_de_puma_existente(monkeypatch):
    linha_puma_existente = {
        "ID (hash)": "p3", "Status": "aguardando_acao",
        "Data Encaminhamento": "05/08/2026", "Dias sem contato": "0 dias sem contato",
    }
    mocks = _preparar_mocks_processar_ligacao(monkeypatch, [], linhas_puma_existentes=[linha_puma_existente])

    resultado = orch.etapa_processar_resultado_ligacao(agora=datetime(2026, 8, 7, 9, 0, 0))

    assert resultado.sucesso is True
    linhas_puma = _reescrita_de(mocks, "Encaminhar pra Puma")
    assert linhas_puma[0]["Dias sem contato"] == "2 dias sem contato"


def test_etapa_processar_resultado_ligacao_remove_concluidos_de_puma(monkeypatch):
    linha_concluida = {"ID (hash)": "p1", "Status": "concluido"}
    linha_pendente_puma = {"ID (hash)": "p2", "Status": "aguardando_acao", "Dias sem contato": ""}
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_concluida, linha_pendente_puma],
        tratativas_por_chave={"p1": {"id": "tratativa-p1"}},
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert resultado.dados["removidas_de_puma_concluidas"] == 1
    assert _reescrita_de(mocks, "Encaminhar pra Puma") == [linha_pendente_puma]
    assert mocks["syncs_puma"] == [("tratativa-p1", "concluido")]
    assert mocks["syncs_puma_observacao"] == [("tratativa-p1", "")]


def test_etapa_processar_resultado_ligacao_concluido_sem_tratativa_nao_sincroniza(monkeypatch):
    """Linha "concluido" cuja `ID (hash)` não bate com nenhuma tratativa
    real (ex: placeholder/lixo na planilha) — mesma guarda defensiva já
    usada em outros pontos do módulo: não sincroniza, mas continua
    removendo a linha normalmente."""
    linha_concluida = {"ID (hash)": "fantasma", "Status": "concluido"}
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_concluida]
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert resultado.dados["removidas_de_puma_concluidas"] == 1
    assert mocks["syncs_puma"] == []
    assert mocks["syncs_puma_observacao"] == []


def test_etapa_processar_resultado_ligacao_sincroniza_observacao_puma_linha_concluida(monkeypatch):
    """2026-08-14 — achado ao vivo: "Observação Puma" nunca tinha caminho
    de volta pro Supabase. Sincroniza junto com o Status, na mesma
    passagem que remove a linha da aba."""
    linha_concluida = {"ID (hash)": "p1", "Status": "concluido", "Observação Puma": "Resolvido no local."}
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_concluida],
        tratativas_por_chave={"p1": {"id": "tratativa-p1"}},
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert mocks["syncs_puma_observacao"] == [("tratativa-p1", "Resolvido no local.")]


def test_etapa_processar_resultado_ligacao_sincroniza_observacao_puma_linha_nao_concluida(monkeypatch):
    """A anotação pode acontecer ANTES da conclusão — sincroniza mesmo
    a linha continuando na aba."""
    linha_pendente = {
        "ID (hash)": "p2", "Status": "aguardando_acao", "Dias sem contato": "",
        "Observação Puma": "Aguardando retorno do técnico.",
    }
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_pendente],
        tratativas_por_chave={"p2": {"id": "tratativa-p2"}},
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert mocks["syncs_puma"] == []  # não concluída, Status não é sincronizado
    assert mocks["syncs_puma_observacao"] == [("tratativa-p2", "Aguardando retorno do técnico.")]
    assert _reescrita_de(mocks, "Encaminhar pra Puma") == [linha_pendente]


# --- detecção automática de conclusão (decisão de negócio 2026-08-14) ------

def test_etapa_processar_resultado_ligacao_conclui_manutencao_automaticamente_sem_incidente_aberto(monkeypatch):
    linha_manutencao = {
        "ID (hash)": "p1", "Status": "", "Tipo Serviço": "Manutenção", "Observação Puma": "",
    }
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_manutencao],
        tratativas_por_chave={"p1": {"id": "tratativa-p1", "origem": "manutencao", "placa": "ABC1234"}},
        incidentes_administrador=[{"Placa": "ABC1234", "Status": "Resolvido"}],
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert resultado.dados["removidas_de_puma_concluidas"] == 1
    assert mocks["syncs_puma"] == [("tratativa-p1", "concluido")]
    observacao_sincronizada = mocks["syncs_puma_observacao"][0][1]
    assert observacao_sincronizada == "Detectado automaticamente: manutenção concluída em " + \
        datetime.now().strftime("%d/%m/%Y") + "."


def test_etapa_processar_resultado_ligacao_conclui_instalacao_automaticamente_quando_chassi_ja_ativo(monkeypatch):
    linha_instalacao = {
        "ID (hash)": "p1", "Status": "", "Tipo Serviço": "Instalação", "Observação Puma": "",
    }
    equipamento = {f"col_{COL_RASTREADORES_CHASSI}": "CHASSI-P1"}
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_instalacao],
        tratativas_por_chave={"p1": {"id": "tratativa-p1", "origem": "instalacao", "chassi": "CHASSI-P1"}},
        equipamentos_administrador=[equipamento],
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert resultado.dados["removidas_de_puma_concluidas"] == 1
    assert mocks["syncs_puma"] == [("tratativa-p1", "concluido")]


def test_etapa_processar_resultado_ligacao_manutencao_com_incidente_ainda_aberto_permanece(monkeypatch):
    linha_manutencao = {
        "ID (hash)": "p1", "Status": "", "Tipo Serviço": "Manutenção", "Observação Puma": "",
        "Dias sem contato": "",
    }
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_manutencao],
        tratativas_por_chave={"p1": {"id": "tratativa-p1", "origem": "manutencao", "placa": "ABC1234"}},
        incidentes_administrador=[{"Placa": "ABC1234", "Status": "Aberto"}],
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert resultado.dados["removidas_de_puma_concluidas"] == 0
    assert mocks["syncs_puma"] == []
    assert _reescrita_de(mocks, "Encaminhar pra Puma") == [linha_manutencao]


def test_etapa_processar_resultado_ligacao_remocao_nunca_conclui_sozinha(monkeypatch):
    """Decisão de negócio 2026-08-14: remoção fica de fora da detecção
    automática — "não existe um processo confiável ainda" pra ela."""
    linha_remocao = {
        "ID (hash)": "p1", "Status": "", "Tipo Serviço": "Remoção", "Observação Puma": "",
        "Dias sem contato": "",
    }
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_remocao],
        tratativas_por_chave={"p1": {"id": "tratativa-p1", "origem": "remocao", "chassi": "CHASSI-P1"}},
        equipamentos_administrador=[{f"col_{COL_RASTREADORES_CHASSI}": "CHASSI-P1"}],
        incidentes_administrador=[],
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert resultado.dados["removidas_de_puma_concluidas"] == 0
    assert mocks["syncs_puma"] == []
    assert _reescrita_de(mocks, "Encaminhar pra Puma") == [linha_remocao]


def test_etapa_processar_resultado_ligacao_nota_automatica_anexa_sem_sobrescrever_observacao_humana(monkeypatch):
    linha_manutencao = {
        "ID (hash)": "p1", "Status": "", "Tipo Serviço": "Manutenção",
        "Observação Puma": "Já entrei em contato com a Puma.",
    }
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_manutencao],
        tratativas_por_chave={"p1": {"id": "tratativa-p1", "origem": "manutencao", "placa": "ABC1234"}},
        incidentes_administrador=[],
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    observacao_sincronizada = mocks["syncs_puma_observacao"][0][1]
    assert observacao_sincronizada.startswith("Já entrei em contato com a Puma. | Detectado automaticamente:")


def test_etapa_processar_resultado_ligacao_nota_automatica_nao_duplica_em_2_ciclos(monkeypatch):
    """Se a linha já tem a nota automática (ex: sincronizou mas ainda não
    saiu da aba por algum motivo), rodar de novo não duplica a frase."""
    nota = "Detectado automaticamente: manutenção concluída em 01/01/2026."
    linha_manutencao = {
        "ID (hash)": "p1", "Status": "", "Tipo Serviço": "Manutenção", "Observação Puma": nota,
    }
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_manutencao],
        tratativas_por_chave={"p1": {"id": "tratativa-p1", "origem": "manutencao", "placa": "ABC1234"}},
        incidentes_administrador=[],
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    observacao_sincronizada = mocks["syncs_puma_observacao"][0][1]
    assert observacao_sincronizada == nota
    assert observacao_sincronizada.count("Detectado automaticamente:") == 1


def test_etapa_processar_resultado_ligacao_deteccao_automatica_sem_tratativa_nao_quebra(monkeypatch):
    linha_orfa = {
        "ID (hash)": "fantasma", "Status": "", "Tipo Serviço": "Manutenção", "Observação Puma": "",
        "Dias sem contato": "",
    }
    mocks = _preparar_mocks_processar_ligacao(
        monkeypatch, [], linhas_puma_existentes=[linha_orfa],
    )

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert resultado.dados["removidas_de_puma_concluidas"] == 0
    assert mocks["syncs_puma"] == []
    assert mocks["syncs_puma_observacao"] == []
    assert _reescrita_de(mocks, "Encaminhar pra Puma") == [linha_orfa]


def test_etapa_processar_resultado_ligacao_linhas_none_usa_ler_aba_padrao(monkeypatch):
    mocks = _preparar_mocks_processar_ligacao(monkeypatch, [])

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is True
    assert "Pendente de Ligação" in mocks["chamadas_ler_aba"]


def test_etapa_processar_resultado_ligacao_falha_ao_ler_pendente_ligacao(monkeypatch):
    def _ler_aba_falha(planilha, aba):
        raise RuntimeError("Sheets indisponível pra leitura")

    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: {})
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_falha)

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Sheets indisponível pra leitura"


def test_etapa_processar_resultado_ligacao_falha_ao_ler_encaminhar_puma(monkeypatch):
    def _ler_aba_falha(planilha, aba):
        if aba == "Encaminhar pra Puma":
            raise RuntimeError("Sheets indisponível pra leitura de Puma")
        return []

    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: {})
    monkeypatch.setattr(orch.supabase_client, "buscar_bases_ativas", lambda: [])
    monkeypatch.setattr(orch.supabase_client, "buscar_pontos_acao_ativos", lambda: [])
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_falha)

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Sheets indisponível pra leitura de Puma"


def test_etapa_processar_resultado_ligacao_falha_ao_reescrever_aba(monkeypatch):
    monkeypatch.setattr(orch.supabase_client, "buscar_rule_templates", lambda: {})
    monkeypatch.setattr(orch.supabase_client, "buscar_bases_ativas", lambda: [])
    monkeypatch.setattr(orch.supabase_client, "buscar_pontos_acao_ativos", lambda: [])
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", lambda planilha, aba: [])

    def _reescrever_aba_falha(planilha, aba, linhas):
        raise RuntimeError("Sheets indisponível pra escrita")

    monkeypatch.setattr(orch.google_sheets_client, "reescrever_aba", _reescrever_aba_falha)

    resultado = orch.etapa_processar_resultado_ligacao()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Sheets indisponível pra escrita"


# --- etapa_processar_alertas -------------------------------------------------

def _linha_alerta_sheet(chave, **extra):
    base = {
        "ID (hash)": chave, "Cliente": "", "Telefone": "", "Onde está": "",
        "Tipo de Alerta": "", "Contexto": "", "Ação": "", "Data Agendada": "",
    }
    base.update(extra)
    return base


def _tratativa_alerta(**extra):
    base = {
        "id": "tratativa-uuid-alerta", "chave_unica": "chave-alerta-1",
        "origem": "manutencao", "cliente": "Cliente Alerta", "telefone": "+5581988880000",
        "status": "aguardando_ligacao", "retorno_associado": "Instalado — associado confirma, revisar",
        "situacao_manual": "", "data_agendada": None,
    }
    base.update(extra)
    return base


def _preparar_mocks_processar_alertas(
    monkeypatch,
    linhas_alertas_atuais,
    linhas_pendente_existentes=None,
    linhas_puma_existentes=None,
    tratativas_por_chave=None,
    candidatas_frescas=None,
):
    linhas_pendente_existentes = linhas_pendente_existentes if linhas_pendente_existentes is not None else []
    linhas_puma_existentes = linhas_puma_existentes if linhas_puma_existentes is not None else []
    tratativas_por_chave = tratativas_por_chave or {}
    candidatas_frescas = candidatas_frescas if candidatas_frescas is not None else {
        "retorno_tardio": [], "agendado_sem_data": [],
    }

    chamadas_ler_aba = []

    def _ler_aba_fake(planilha, aba):
        chamadas_ler_aba.append(aba)
        if aba == "Alertas":
            return linhas_alertas_atuais
        if aba == "Pendente de Ligação":
            return linhas_pendente_existentes
        if aba == "Encaminhar pra Puma":
            return linhas_puma_existentes
        raise AssertionError(f"aba inesperada: {aba}")

    reescritas = []

    def _reescrever_aba_fake(planilha, aba, linhas):
        reescritas.append((planilha, aba, linhas))

    syncs = []

    def _sincronizar_fake(chave_unica, campos):
        syncs.append((chave_unica, campos))

    def _buscar_tratativa_fake(chave_unica):
        return tratativas_por_chave.get(chave_unica)

    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_fake)
    monkeypatch.setattr(orch.google_sheets_client, "reescrever_aba", _reescrever_aba_fake)
    monkeypatch.setattr(orch.supabase_client, "sincronizar_campos_atendente", _sincronizar_fake)
    monkeypatch.setattr(orch.supabase_client, "buscar_tratativa_por_chave", _buscar_tratativa_fake)
    monkeypatch.setattr(orch.supabase_client, "buscar_candidatas_alertas", lambda: candidatas_frescas)

    return {"chamadas_ler_aba": chamadas_ler_aba, "reescritas": reescritas, "syncs": syncs}


def test_etapa_processar_alertas_reporta_progresso_por_item(monkeypatch):
    linhas = [_linha_alerta_sheet("chave-1"), _linha_alerta_sheet("chave-2")]
    _preparar_mocks_processar_alertas(monkeypatch, linhas)
    chamadas_progresso = []

    resultado = orch.etapa_processar_alertas(
        on_progresso=lambda concluidos, total: chamadas_progresso.append((concluidos, total))
    )

    assert resultado.sucesso is True
    assert chamadas_progresso == [(1, 2), (2, 2)]


def test_etapa_processar_alertas_cancelado_para_no_meio(monkeypatch):
    linhas = [
        _linha_alerta_sheet("chave-1", Ação="Foi engano, ignorar"),
        _linha_alerta_sheet("chave-2", Ação="Foi engano, ignorar"),
    ]
    mocks = _preparar_mocks_processar_alertas(monkeypatch, linhas)

    resultado = orch.etapa_processar_alertas(cancelar_checker=lambda: len(mocks["syncs"]) >= 1)

    assert resultado.sucesso is False
    assert resultado.cancelado == {"pendentes": [linhas[1]]}
    assert resultado.dados["resolvidas"] == 1
    assert mocks["syncs"] == [("chave-1", {"retorno_associado": None})]


def test_etapa_processar_alertas_foi_engano_ignorar_so_limpa_retorno(monkeypatch):
    linha = _linha_alerta_sheet("chave-1", Ação="Foi engano, ignorar")
    mocks = _preparar_mocks_processar_alertas(monkeypatch, [linha])

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is True
    assert mocks["syncs"] == [("chave-1", {"retorno_associado": None})]
    assert "Pendente de Ligação" not in mocks["chamadas_ler_aba"]
    assert "Encaminhar pra Puma" not in mocks["chamadas_ler_aba"]


def test_etapa_processar_alertas_confirma_conclusao_aguardando_ligacao(monkeypatch):
    linha = _linha_alerta_sheet("chave-hash-9", Ação="Confirma conclusão")
    tratativa = _tratativa_alerta(chave_unica="chave-hash-9", status="aguardando_ligacao")
    linha_pendente = {"ID (hash)": "chave-hash-9", "Cliente": "Cliente Alerta"}
    outra_linha_pendente = {"ID (hash)": "outra-chave", "Cliente": "Outro"}
    mocks = _preparar_mocks_processar_alertas(
        monkeypatch, [linha],
        linhas_pendente_existentes=[linha_pendente, outra_linha_pendente],
        tratativas_por_chave={"chave-hash-9": tratativa},
    )

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is True
    assert mocks["syncs"] == [("chave-hash-9", {"status": "finalizado", "retorno_associado": None})]
    linhas_pendente_reescritas = _reescrita_de(mocks, "Pendente de Ligação")
    assert linhas_pendente_reescritas == [outra_linha_pendente]


def test_etapa_processar_alertas_confirma_conclusao_encaminhado_puma(monkeypatch):
    linha = _linha_alerta_sheet("chave-hash-9", Ação="Confirma conclusão")
    tratativa = _tratativa_alerta(chave_unica="chave-hash-9", status="encaminhado_puma")
    linha_puma = {"ID (hash)": "chave-hash-9", "Status": "aguardando_acao"}
    outra_linha_puma = {"ID (hash)": "outra-chave", "Status": "aguardando_acao"}
    mocks = _preparar_mocks_processar_alertas(
        monkeypatch, [linha],
        linhas_puma_existentes=[linha_puma, outra_linha_puma],
        tratativas_por_chave={"chave-hash-9": tratativa},
    )

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is True
    assert mocks["syncs"] == [("chave-hash-9", {"retorno_associado": None})]
    linhas_puma_reescritas = _reescrita_de(mocks, "Encaminhar pra Puma")
    assert {"ID (hash)": "chave-hash-9", "Status": "concluido"} in linhas_puma_reescritas
    assert outra_linha_puma in linhas_puma_reescritas
    assert "Pendente de Ligação" not in mocks["chamadas_ler_aba"]


def test_etapa_processar_alertas_confirma_conclusao_outro_status(monkeypatch):
    linha = _linha_alerta_sheet("chave-1", Ação="Confirma conclusão")
    tratativa = _tratativa_alerta(chave_unica="chave-1", status="finalizado")
    mocks = _preparar_mocks_processar_alertas(
        monkeypatch, [linha], tratativas_por_chave={"chave-1": tratativa},
    )

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is True
    assert mocks["syncs"] == [("chave-1", {"status": "finalizado", "retorno_associado": None})]
    assert "Pendente de Ligação" not in mocks["chamadas_ler_aba"]
    assert "Encaminhar pra Puma" not in mocks["chamadas_ler_aba"]


def test_etapa_processar_alertas_data_agendada_preenchida_sincroniza(monkeypatch):
    linha = _linha_alerta_sheet("chave-1", **{"Data Agendada": "20/08/2026"})
    mocks = _preparar_mocks_processar_alertas(monkeypatch, [linha])

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is True
    assert mocks["syncs"] == [("chave-1", {"data_agendada": "2026-08-20"})]


def test_etapa_processar_alertas_data_agendada_ilegivel_nao_sincroniza(monkeypatch):
    linha = _linha_alerta_sheet("chave-1", **{"Data Agendada": "não sei"})
    mocks = _preparar_mocks_processar_alertas(monkeypatch, [linha])

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is True
    assert mocks["syncs"] == []


def test_etapa_processar_alertas_linha_sem_chave_ignorada(monkeypatch):
    linha = _linha_alerta_sheet("", Ação="Confirma conclusão")
    mocks = _preparar_mocks_processar_alertas(monkeypatch, [linha])

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is True
    assert mocks["syncs"] == []


def test_etapa_processar_alertas_reescreve_com_candidatas_frescas_dos_2_tipos(monkeypatch):
    candidatas = {
        "retorno_tardio": [_tratativa_alerta(
            chave_unica="chave-a", status="aguardando_ligacao",
            retorno_associado="Instalado — associado confirma, revisar",
        )],
        "agendado_sem_data": [_tratativa_alerta(
            chave_unica="chave-b", status="aguardando_resposta", situacao_manual="Agendado",
        )],
    }
    mocks = _preparar_mocks_processar_alertas(monkeypatch, [], candidatas_frescas=candidatas)

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is True
    linhas = _reescrita_de(mocks, "Alertas")
    assert len(linhas) == 2
    retorno_tardio = next(l for l in linhas if l["ID (hash)"] == "chave-a")
    assert retorno_tardio["Tipo de Alerta"] == "Retorno tardio"
    assert retorno_tardio["Onde está"] == "Pendente de Ligação"
    assert retorno_tardio["Contexto"] == "Instalado — associado confirma, revisar"
    agendado = next(l for l in linhas if l["ID (hash)"] == "chave-b")
    assert agendado["Tipo de Alerta"] == "Agendado sem data"
    assert agendado["Contexto"] == "Confirmado via WhatsApp"
    assert agendado["Onde está"] == "Tratativas"
    assert resultado.dados == {"resolvidas": 0, "total_alertas": 2}


def test_etapa_processar_alertas_onde_esta_cobre_status_ainda_em_tratativas(monkeypatch):
    """Achado ao vivo (2026-08-11): "Agendado sem data" pode acontecer com
    a tratativa ainda em `pendente`/`aguardando_resposta`/`respondido` (o
    associado confirma o agendamento sem ter esgotado as 3 tentativas de
    WhatsApp) — as 3 devem virar "Tratativas", não o texto crú do status."""
    candidatas = {
        "retorno_tardio": [],
        "agendado_sem_data": [
            _tratativa_alerta(chave_unica="chave-pendente", status="pendente"),
            _tratativa_alerta(chave_unica="chave-aguardando", status="aguardando_resposta"),
            _tratativa_alerta(chave_unica="chave-respondido", status="respondido"),
        ],
    }
    mocks = _preparar_mocks_processar_alertas(monkeypatch, [], candidatas_frescas=candidatas)

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is True
    linhas = _reescrita_de(mocks, "Alertas")
    assert {l["Onde está"] for l in linhas} == {"Tratativas"}


def test_etapa_processar_alertas_tratativa_nao_encontrada_nao_quebra(monkeypatch):
    linha = _linha_alerta_sheet("chave-inexistente", Ação="Confirma conclusão")
    mocks = _preparar_mocks_processar_alertas(monkeypatch, [linha])

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is True
    assert mocks["syncs"] == []


def test_etapa_processar_alertas_falha_ao_ler_alertas(monkeypatch):
    def _ler_aba_falha(planilha, aba):
        raise RuntimeError("Sheets indisponível pra leitura de Alertas")

    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", _ler_aba_falha)

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Sheets indisponível pra leitura de Alertas"


def test_etapa_processar_alertas_falha_ao_reescrever_aba(monkeypatch):
    monkeypatch.setattr(orch.google_sheets_client, "ler_aba", lambda planilha, aba: [])
    monkeypatch.setattr(orch.supabase_client, "buscar_candidatas_alertas", lambda: {
        "retorno_tardio": [], "agendado_sem_data": [],
    })

    def _reescrever_aba_falha(planilha, aba, linhas):
        raise RuntimeError("Sheets indisponível pra escrita de Alertas")

    monkeypatch.setattr(orch.google_sheets_client, "reescrever_aba", _reescrever_aba_falha)

    resultado = orch.etapa_processar_alertas()

    assert resultado.sucesso is False
    assert resultado.mensagem == "Sheets indisponível pra escrita de Alertas"
