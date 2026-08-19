import base64

import pytest

from core.constants import STATUS_SGA_ATIVO, STATUS_SGA_NAO_ENCONTRADO, TIPO_IDENTIFICADOR_CHASSI, TIPO_IDENTIFICADOR_PLACA
from integrations import sga_bot


def _resultado(status, cidade="", bairro=""):
    return {"status": status, "cidade": cidade, "bairro": bairro}


class TestConsultarSituacaoBuscaDeterministica:
    """Achado 2026-08-16: os campos Chassi e Placa da tela do SGA são
    INDEPENDENTES -- cada um só aceita o tipo certo de valor. Quem chama
    (`orchestrator.pipeline._alvos_consulta_sga`) já decide ANTES de
    consultar qual campo usar (chassi confirmado via cadastro ou placa
    real/válida) -- `consultar_situacao` nunca adivinha nem tenta os 2,
    só busca no campo indicado. Como não existe harness de fake `Page` do
    Playwright nesse projeto, esses testes cobrem só o CONTROLE DE FLUXO,
    monkeypatchando o helper interno `_buscar_por_identificador`."""

    @pytest.mark.asyncio
    async def test_tipo_chassi_busca_so_no_campo_chassi(self, monkeypatch):
        chamadas = []

        async def _buscar_fake(page, valor, seletor_filtro, seletor_ancora):
            chamadas.append((valor, seletor_filtro, seletor_ancora))
            return _resultado("ATIVO")

        monkeypatch.setattr(sga_bot, "_buscar_por_identificador", _buscar_fake)

        resultado = await sga_bot.consultar_situacao(None, TIPO_IDENTIFICADOR_CHASSI, "9BWZZZ377VT004251")

        assert chamadas == [
            ("9BWZZZ377VT004251", sga_bot.SELETOR_CAMPO_CHASSI_FILTRO, sga_bot.SELETOR_CAMPO_CHASSI_ANCORA)
        ]
        assert resultado["status"] == "ATIVO"
        assert resultado["encontrado_via"] == TIPO_IDENTIFICADOR_CHASSI

    @pytest.mark.asyncio
    async def test_tipo_placa_busca_so_no_campo_placa(self, monkeypatch):
        chamadas = []

        async def _buscar_fake(page, valor, seletor_filtro, seletor_ancora):
            chamadas.append((valor, seletor_filtro, seletor_ancora))
            return _resultado("ATIVO", cidade="Recife", bairro="Boa Vista")

        monkeypatch.setattr(sga_bot, "_buscar_por_identificador", _buscar_fake)

        resultado = await sga_bot.consultar_situacao(None, TIPO_IDENTIFICADOR_PLACA, "OYR7F55")

        assert chamadas == [("OYR7F55", sga_bot.SELETOR_CAMPO_PLACA_FILTRO, sga_bot.SELETOR_CAMPO_PLACA_ANCORA)]
        assert resultado["status"] == "ATIVO"
        assert resultado["cidade"] == "Recife"
        assert resultado["bairro"] == "Boa Vista"
        assert resultado["encontrado_via"] == TIPO_IDENTIFICADOR_PLACA

    @pytest.mark.asyncio
    async def test_chassi_confirmado_nao_encontrado_nao_tenta_placa(self, monkeypatch):
        # Decisão do usuário (2026-08-16): chassi confirmado + "NÃO
        # ENCONTRADO" no SGA já é resultado de negócio válido -- nunca
        # tenta a Placa depois (isso reintroduziria a mistura de campos
        # que causou o achado original).
        chamadas = []

        async def _buscar_fake(page, valor, seletor_filtro, seletor_ancora):
            chamadas.append(seletor_filtro)
            return _resultado(sga_bot.STATUS_NAO_ENCONTRADO)

        monkeypatch.setattr(sga_bot, "_buscar_por_identificador", _buscar_fake)

        resultado = await sga_bot.consultar_situacao(None, TIPO_IDENTIFICADOR_CHASSI, "9BWZZZ377VT004251")

        assert chamadas == [sga_bot.SELETOR_CAMPO_CHASSI_FILTRO]
        assert resultado["status"] == sga_bot.STATUS_NAO_ENCONTRADO
        assert resultado["encontrado_via"] == TIPO_IDENTIFICADOR_CHASSI


class TestGerarKey:
    """Reimplementação em Python de `fCriptografa(id, "INT")` do `cript.js`
    público do SGA -- achado 2026-08-19. Testa o round-trip decodificando
    o `key` manualmente, igual `fDescriptografa` faz do lado do SGA."""

    def test_round_trip_decodifica_para_o_id_original(self, monkeypatch):
        monkeypatch.setattr(sga_bot.random, "randint", lambda a, b: 438)

        key = sga_bot._gerar_key(31039)

        decodificado = base64.b64decode(key).decode()
        resto = decodificado[-1]
        rand = decodificado[-4:-1]
        multiplicado = decodificado[:-4]
        assert rand == "438"
        assert int(multiplicado) == 31039 * 438
        assert int(resto) == (31039 * 438) % 7


class TestExtrairIdVeiculo:
    def test_extrai_id_de_xml_valido(self):
        xml = (
            b'<?xml version="1.0" encoding="iso-8859-1" ?>'
            b'<results><rs id="31039" info="">9C6DG25G0S0006211</rs></results>'
        )
        assert sga_bot._extrair_id_veiculo(xml) == 31039

    def test_devolve_none_quando_resultado_vazio(self):
        xml = b'<?xml version="1.0" encoding="iso-8859-1" ?><results></results>'
        assert sga_bot._extrair_id_veiculo(xml) is None


class TestExtrairCamposHtml:
    """`carregaVeiculoDadosAlteracao.php` preenche via JS inline
    (`form.CAMPO.value = '...';`), não HTML pronto -- ver achado
    2026-08-19."""

    def test_extrai_os_3_campos_quando_presentes(self):
        html = (
            "form.cmbSituacaoVeiculo.value = '1';\n"
            "form.dfsCidadeCorrespondencia.value = 'ABREU E LIMA';\n"
            "form.dfsBairroCorrespondencia.value = 'DESTERRO';\n"
        )
        assert sga_bot._extrair_campos_html(html) == {
            "status_codigo": "1", "cidade": "ABREU E LIMA", "bairro": "DESTERRO",
        }

    def test_campo_ausente_vira_string_vazia_sem_levantar(self):
        html = "form.cmbSituacaoVeiculo.value = '1';"
        assert sga_bot._extrair_campos_html(html) == {"status_codigo": "1", "cidade": "", "bairro": ""}

    def test_html_sem_nenhum_campo_nao_levanta(self):
        assert sga_bot._extrair_campos_html("<html><body>erro</body></html>") == {
            "status_codigo": "", "cidade": "", "bairro": "",
        }


class TestLabelStatus:
    def test_codigo_1_e_ativo(self):
        assert sga_bot._label_status("1") == STATUS_SGA_ATIVO

    def test_codigo_19_e_cancelado_por_substituicao(self):
        assert sga_bot._label_status("19") == "CANCELADO POR SUBSTITUIÇÃO"

    def test_codigo_desconhecido_nunca_e_confundido_com_ativo_ou_nao_encontrado(self):
        label = sga_bot._label_status("999")
        assert label != STATUS_SGA_ATIVO
        assert label != STATUS_SGA_NAO_ENCONTRADO
        assert "999" in label


class _RespostaFake:
    def __init__(self, status=200, url="", corpo=b""):
        self.status = status
        self.url = url
        self._corpo = corpo

    async def body(self):
        return self._corpo


class _RequestContextFake:
    def __init__(self, respostas):
        self._respostas = respostas
        self.chamadas = []

    async def get(self, url, params=None):
        self.chamadas.append((url, params))
        return self._respostas[len(self.chamadas) - 1]


def _html_valido(status_codigo="1", cidade="ABREU E LIMA", bairro="DESTERRO"):
    campos = (
        f"form.cmbSituacaoVeiculo.value = '{status_codigo}';\n"
        f"form.dfsCidadeCorrespondencia.value = '{cidade}';\n"
        f"form.dfsBairroCorrespondencia.value = '{bairro}';\n"
    )
    enchimento = "<!-- preenchimento -->" * 600  # ultrapassa TAMANHO_MINIMO_HTML_VALIDO de propósito
    return (campos + enchimento).encode("utf-8")


class TestConsultarSituacaoHttp:
    """Achado 2026-08-19 -- equivalente HTTP de `consultar_situacao`,
    validado em 7 rodadas ao vivo contra o SGA real (ver memória
    `sga_api_http_viabilidade_confirmada`). Fake de `request_context`
    segue o mesmo espírito de `_buscar_fake` acima: não existe harness de
    rede real neste projeto, só controle de fluxo."""

    XML_ENCONTRADO = (
        b'<?xml version="1.0" encoding="iso-8859-1" ?>'
        b'<results><rs id="31039" info="">9C6DG25G0S0006211</rs></results>'
    )
    XML_NAO_ENCONTRADO = b'<?xml version="1.0" encoding="iso-8859-1" ?><results></results>'

    @pytest.mark.asyncio
    async def test_recusa_busca_por_placa(self):
        with pytest.raises(ValueError):
            await sga_bot.consultar_situacao_http(object(), TIPO_IDENTIFICADOR_PLACA, "ABC1234")

    @pytest.mark.asyncio
    async def test_id_encontrado_devolve_status_cidade_bairro(self):
        request_context = _RequestContextFake([
            _RespostaFake(corpo=self.XML_ENCONTRADO),
            _RespostaFake(corpo=_html_valido()),
        ])

        resultado = await sga_bot.consultar_situacao_http(
            request_context, TIPO_IDENTIFICADOR_CHASSI, "9C6DG25G0S0006211"
        )

        assert resultado == {
            "status": STATUS_SGA_ATIVO, "cidade": "ABREU E LIMA", "bairro": "DESTERRO",
            "encontrado_via": TIPO_IDENTIFICADOR_CHASSI,
        }
        assert len(request_context.chamadas) == 2

    @pytest.mark.asyncio
    async def test_id_nao_encontrado_nao_faz_segunda_chamada(self):
        request_context = _RequestContextFake([_RespostaFake(corpo=self.XML_NAO_ENCONTRADO)])

        resultado = await sga_bot.consultar_situacao_http(
            request_context, TIPO_IDENTIFICADOR_CHASSI, "0000000000000000X"
        )

        assert resultado["status"] == STATUS_SGA_NAO_ENCONTRADO
        assert len(request_context.chamadas) == 1

    @pytest.mark.asyncio
    async def test_html_pequeno_levanta_erro_tecnico(self):
        request_context = _RequestContextFake([
            _RespostaFake(corpo=self.XML_ENCONTRADO),
            _RespostaFake(corpo=b"pagina de erro"),
        ])

        with pytest.raises(RuntimeError):
            await sga_bot.consultar_situacao_http(request_context, TIPO_IDENTIFICADOR_CHASSI, "X")

    @pytest.mark.asyncio
    async def test_redirect_para_login_levanta_sessao_caida(self):
        request_context = _RequestContextFake([
            _RespostaFake(corpo=self.XML_ENCONTRADO, url="https://sga.hinova.com.br/sga/sgav4_pumabeneficios/v5/login.php"),
        ])

        with pytest.raises(sga_bot.SessaoCaidaError):
            await sga_bot.consultar_situacao_http(request_context, TIPO_IDENTIFICADOR_CHASSI, "X")

    @pytest.mark.asyncio
    async def test_status_http_diferente_de_200_levanta_erro_tecnico(self):
        request_context = _RequestContextFake([_RespostaFake(status=500, corpo=self.XML_ENCONTRADO)])

        with pytest.raises(RuntimeError):
            await sga_bot.consultar_situacao_http(request_context, TIPO_IDENTIFICADOR_CHASSI, "X")

    @pytest.mark.asyncio
    async def test_html_grande_sem_status_levanta_erro_tecnico(self):
        html_sem_status = ("<!-- preenchimento -->" * 600).encode("utf-8")
        request_context = _RequestContextFake([
            _RespostaFake(corpo=self.XML_ENCONTRADO),
            _RespostaFake(corpo=html_sem_status),
        ])

        with pytest.raises(RuntimeError):
            await sga_bot.consultar_situacao_http(request_context, TIPO_IDENTIFICADOR_CHASSI, "X")


class TestPrepararContextoHttp:
    @pytest.mark.asyncio
    async def test_fecha_navegador_e_devolve_contexto_http_autenticado(self):
        chamadas = []

        class _ContextoFake:
            async def storage_state(self):
                return {"cookies": ["fake"]}

            async def close(self):
                chamadas.append("context.close")

        class _BrowserFake:
            async def close(self):
                chamadas.append("browser.close")

        class _RequestFake:
            async def new_context(self, storage_state):
                chamadas.append(("request.new_context", storage_state))
                return "request_context_fake"

        class _PlaywrightFake:
            request = _RequestFake()

        resultado = await sga_bot.preparar_contexto_http(_PlaywrightFake(), _BrowserFake(), _ContextoFake())

        assert resultado == ("request_context_fake", {"cookies": ["fake"]})
        assert chamadas == [
            "context.close", "browser.close", ("request.new_context", {"cookies": ["fake"]}),
        ]


class TestAbrirContextoComSessao:
    @pytest.mark.asyncio
    async def test_abre_navegador_headless_com_o_estado_salvo(self):
        chamadas = []

        class _ContextoFake:
            pass

        class _BrowserFake:
            async def new_context(self, storage_state):
                chamadas.append(("new_context", storage_state))
                return _ContextoFake()

        class _ChromiumFake:
            async def launch(self, headless):
                chamadas.append(("launch", headless))
                return _BrowserFake()

        class _PlaywrightFake:
            chromium = _ChromiumFake()

        estado = {"cookies": ["fake"]}
        browser, context = await sga_bot.abrir_contexto_com_sessao(_PlaywrightFake(), estado)

        assert isinstance(browser, _BrowserFake)
        assert isinstance(context, _ContextoFake)
        assert chamadas == [("launch", True), ("new_context", estado)]
