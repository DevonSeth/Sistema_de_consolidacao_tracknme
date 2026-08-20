import pytest

from integrations import tracknme_bot as bot


class _ContextoFake:
    def __init__(self):
        self.fechado = False

    async def new_page(self):
        return object()

    async def close(self):
        self.fechado = True


class _BrowserFake:
    def __init__(self, contexto):
        self.fechado = False
        self._contexto = contexto

    async def new_context(self):
        return self._contexto

    async def close(self):
        self.fechado = True


class _ChromiumFake:
    def __init__(self, browser):
        self._browser = browser

    async def launch(self, headless=True):
        return self._browser


class _PlaywrightFake:
    def __init__(self, browser):
        self.chromium = _ChromiumFake(browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_testar_login_fecha_browser_e_context_e_devolve_resultado(monkeypatch):
    """`testar_login` reaproveita `_fazer_login_automatico` (headless, sem
    handoff pro humano) e SEMPRE fecha context/browser no finally, mesmo
    quando o login falha."""
    contexto_fake = _ContextoFake()
    browser_fake = _BrowserFake(contexto_fake)
    monkeypatch.setattr(bot, "async_playwright", lambda: _PlaywrightFake(browser_fake))

    async def _login_fake(page):
        return False

    monkeypatch.setattr(bot, "_fazer_login_automatico", _login_fake)

    resultado = await bot.testar_login()

    assert resultado is False
    assert contexto_fake.fechado is True
    assert browser_fake.fechado is True


# ---------------------------------------------------------------------------
# Caminho HTTP puro -- mesmo espírito dos fakes de test_sga_bot.py
# (não existe harness de rede real neste projeto, só controle de fluxo).
# ---------------------------------------------------------------------------

_CONFIG_TRACKNME = {"tracknme": {"usuario": "user@teste.com", "senha": "senha123"}}


class _RespostaHttpxFake:
    def __init__(self, status_code=200, corpo=None):
        self.status_code = status_code
        self._corpo = corpo if corpo is not None else {}

    def json(self):
        return self._corpo


class _ClienteHttpxFake:
    def __init__(self, respostas_get=None, respostas_post=None):
        self._respostas_get = list(respostas_get or [])
        self._respostas_post = list(respostas_post or [])
        self.chamadas_get = []
        self.chamadas_post = []
        self.headers = {}
        self.fechado = False

    async def get(self, url, params=None):
        self.chamadas_get.append((url, params))
        return self._respostas_get.pop(0)

    async def post(self, url, params=None, json=None):
        self.chamadas_post.append((url, params, json))
        return self._respostas_post.pop(0)

    async def aclose(self):
        self.fechado = True


_LOGIN_OK = {
    "accessToken": "Bearer token-fake",
    "user": {"id": 999},
}
_MARCA_ALVO = {"brandChildId": 111, "brandChildName": bot.NOME_MARCA_ALVO}
_ARVORE_OK = {"content": [{"brandChildId": 222, "brandChildName": "OUTRA MARCA"}, _MARCA_ALVO]}


class TestPrepararContextoHttp:
    """Achado 2026-08-19 -- Track N' Me não tem captcha, login inteiro é
    1 POST. Corpo/header capturados ao vivo do tráfego real (ver
    `_handoff/investigacao_lag_relatorio_tracknme.md`)."""

    @pytest.mark.asyncio
    async def test_sucesso_devolve_contexto_com_authorization_setado(self, monkeypatch):
        cliente_fake = _ClienteHttpxFake(
            respostas_get=[
                _RespostaHttpxFake(corpo={"content": [{"id": 555}]}),  # brands-by-login
                _RespostaHttpxFake(corpo=_ARVORE_OK),  # brands/tree
            ],
            respostas_post=[_RespostaHttpxFake(status_code=201, corpo=_LOGIN_OK)],
        )
        monkeypatch.setattr(bot.manager, "carregar_config", lambda: _CONFIG_TRACKNME)
        monkeypatch.setattr(bot.httpx, "AsyncClient", lambda **kwargs: cliente_fake)

        contexto = await bot.preparar_contexto_http()

        assert contexto.brand_id == _MARCA_ALVO["brandChildId"]
        assert contexto.user_id == 999
        assert contexto.cliente.headers["Authorization"] == "Bearer token-fake"

    @pytest.mark.asyncio
    async def test_mais_de_1_marca_raiz_levanta_erro(self, monkeypatch):
        cliente_fake = _ClienteHttpxFake(
            respostas_get=[_RespostaHttpxFake(corpo={"content": [{"id": 1}, {"id": 2}]})]
        )
        monkeypatch.setattr(bot.manager, "carregar_config", lambda: _CONFIG_TRACKNME)
        monkeypatch.setattr(bot.httpx, "AsyncClient", lambda **kwargs: cliente_fake)

        with pytest.raises(RuntimeError):
            await bot.preparar_contexto_http()
        assert cliente_fake.fechado is True

    @pytest.mark.asyncio
    async def test_login_com_status_de_erro_levanta_erro_e_fecha_cliente(self, monkeypatch):
        cliente_fake = _ClienteHttpxFake(
            respostas_get=[_RespostaHttpxFake(corpo={"content": [{"id": 555}]})],
            respostas_post=[_RespostaHttpxFake(status_code=400, corpo={"message": "erro"})],
        )
        monkeypatch.setattr(bot.manager, "carregar_config", lambda: _CONFIG_TRACKNME)
        monkeypatch.setattr(bot.httpx, "AsyncClient", lambda **kwargs: cliente_fake)

        with pytest.raises(RuntimeError):
            await bot.preparar_contexto_http()
        assert cliente_fake.fechado is True

    @pytest.mark.asyncio
    async def test_marca_alvo_ausente_na_arvore_levanta_erro(self, monkeypatch):
        cliente_fake = _ClienteHttpxFake(
            respostas_get=[
                _RespostaHttpxFake(corpo={"content": [{"id": 555}]}),
                _RespostaHttpxFake(corpo={"content": [{"brandChildId": 222, "brandChildName": "OUTRA MARCA"}]}),
            ],
            respostas_post=[_RespostaHttpxFake(status_code=201, corpo=_LOGIN_OK)],
        )
        monkeypatch.setattr(bot.manager, "carregar_config", lambda: _CONFIG_TRACKNME)
        monkeypatch.setattr(bot.httpx, "AsyncClient", lambda **kwargs: cliente_fake)

        with pytest.raises(RuntimeError):
            await bot.preparar_contexto_http()
        assert cliente_fake.fechado is True


def _contexto_http_fake(cliente_fake) -> "bot.ContextoHttp":
    return bot.ContextoHttp(cliente=cliente_fake, brand_id=111, user_id=999)


class TestBuscarDevicePorPlaca:
    _DEVICE_ALVO = {"id": 777, "vehicleId": 888, "brandId": 111, "customerName": "FULANO DE TAL"}
    _DEVICE_OUTRA_MARCA = {"id": 700, "vehicleId": 800, "brandId": 222, "customerName": "FULANO DE TAL"}

    @pytest.mark.asyncio
    async def test_encontra_filtrando_marca_e_cliente(self):
        cliente_fake = _ClienteHttpxFake(
            respostas_get=[_RespostaHttpxFake(corpo={"content": [self._DEVICE_OUTRA_MARCA, self._DEVICE_ALVO]})]
        )
        contexto = _contexto_http_fake(cliente_fake)

        device = await bot._buscar_device_por_placa(contexto, "ABC1234", "Fulano de Tal")

        assert device == self._DEVICE_ALVO

    @pytest.mark.asyncio
    async def test_nao_encontra_devolve_none(self):
        cliente_fake = _ClienteHttpxFake(respostas_get=[_RespostaHttpxFake(corpo={"content": []})])
        contexto = _contexto_http_fake(cliente_fake)

        assert await bot._buscar_device_por_placa(contexto, "ABC1234", "Fulano") is None

    @pytest.mark.asyncio
    async def test_cliente_nao_bate_devolve_none(self):
        cliente_fake = _ClienteHttpxFake(respostas_get=[_RespostaHttpxFake(corpo={"content": [self._DEVICE_ALVO]})])
        contexto = _contexto_http_fake(cliente_fake)

        assert await bot._buscar_device_por_placa(contexto, "ABC1234", "Outra Pessoa") is None

    @pytest.mark.asyncio
    async def test_status_de_erro_levanta_excecao(self):
        cliente_fake = _ClienteHttpxFake(respostas_get=[_RespostaHttpxFake(status_code=500)])
        contexto = _contexto_http_fake(cliente_fake)

        with pytest.raises(RuntimeError):
            await bot._buscar_device_por_placa(contexto, "ABC1234", "Fulano")


class TestAbrirIncidenteHttp:
    _DEVICE = {"id": 777, "vehicleId": 888, "brandId": 111, "customerName": "Fulano"}

    @pytest.mark.asyncio
    async def test_sucesso_devolve_incidente_aberto(self, monkeypatch):
        async def _buscar_fake(contexto, placa, cliente):
            return self._DEVICE

        monkeypatch.setattr(bot, "_buscar_device_por_placa", _buscar_fake)
        cliente_fake = _ClienteHttpxFake(respostas_post=[_RespostaHttpxFake(corpo={"id": 12345})])
        contexto = _contexto_http_fake(cliente_fake)

        resultado = await bot.abrir_incidente_http(contexto, "ABC1234", "Fulano")

        assert resultado == "Incidente aberto"
        url, params, payload = cliente_fake.chamadas_post[0]
        assert url == "/v2/incidents/operation/create"
        assert payload == {
            "brandId": 111, "deviceId": 777, "vehicleId": 888, "userOperatorId": 999,
            "type": bot.TIPO_SEM_COMUNICACAO_API, "observation": "", "returned": None,
        }

    @pytest.mark.asyncio
    async def test_veiculo_nao_encontrado_levanta_erro(self, monkeypatch):
        async def _buscar_fake(contexto, placa, cliente):
            return None

        monkeypatch.setattr(bot, "_buscar_device_por_placa", _buscar_fake)
        contexto = _contexto_http_fake(_ClienteHttpxFake())

        with pytest.raises(RuntimeError):
            await bot.abrir_incidente_http(contexto, "ABC1234", "Fulano")

    @pytest.mark.asyncio
    async def test_criacao_com_erro_levanta_excecao(self, monkeypatch):
        async def _buscar_fake(contexto, placa, cliente):
            return self._DEVICE

        monkeypatch.setattr(bot, "_buscar_device_por_placa", _buscar_fake)
        cliente_fake = _ClienteHttpxFake(respostas_post=[_RespostaHttpxFake(status_code=400)])
        contexto = _contexto_http_fake(cliente_fake)

        with pytest.raises(RuntimeError):
            await bot.abrir_incidente_http(contexto, "ABC1234", "Fulano")

    @pytest.mark.asyncio
    async def test_criacao_duplicada_levanta_incidente_duplicado_error(self, monkeypatch):
        """Confirmado ao vivo (2026-08-19, `_handoff/testar_duplicado_via_
        api.py`): a API rejeita com 400 e essa mensagem exata quando o
        device já tem um incidente do mesmo tipo aberto."""
        async def _buscar_fake(contexto, placa, cliente):
            return self._DEVICE

        monkeypatch.setattr(bot, "_buscar_device_por_placa", _buscar_fake)
        cliente_fake = _ClienteHttpxFake(respostas_post=[_RespostaHttpxFake(
            status_code=400,
            corpo={"message": "já existe um incidente do tipo NO_COMMUNICATION_48HS aberto para esse dispositivo"},
        )])
        contexto = _contexto_http_fake(cliente_fake)

        with pytest.raises(bot.IncidenteDuplicadoError):
            await bot.abrir_incidente_http(contexto, "ABC1234", "Fulano")

    @pytest.mark.asyncio
    async def test_criacao_sem_id_na_resposta_levanta_excecao(self, monkeypatch):
        async def _buscar_fake(contexto, placa, cliente):
            return self._DEVICE

        monkeypatch.setattr(bot, "_buscar_device_por_placa", _buscar_fake)
        cliente_fake = _ClienteHttpxFake(respostas_post=[_RespostaHttpxFake(corpo={})])
        contexto = _contexto_http_fake(cliente_fake)

        with pytest.raises(RuntimeError):
            await bot.abrir_incidente_http(contexto, "ABC1234", "Fulano")


class TestEhErroDeNegocioEsperado:
    def test_incidente_duplicado_e_esperado(self):
        assert bot.eh_erro_de_negocio_esperado(
            "Track N' Me rejeitou a criação via HTTP -- incidente já aberto (placa=X): já existe ..."
        )

    def test_multiplos_abertos_e_esperado(self):
        assert bot.eh_erro_de_negocio_esperado("Mais de um incidente aberto pra placa=X -- informe numero_incidente")

    def test_erro_tecnico_generico_nao_e_esperado(self):
        assert not bot.eh_erro_de_negocio_esperado("criar incidente via HTTP falhou: status 500 (placa=X)")

    def test_mensagem_vazia_nao_e_esperado(self):
        assert not bot.eh_erro_de_negocio_esperado(None)
        assert not bot.eh_erro_de_negocio_esperado("")


class TestConcluirIncidenteHttp:
    """Achado 2026-08-19 -- a conclusão real da tela é uma sequência de 4
    chamadas (Atribuir -> Alterar situação -> Acompanhamento -> Concluir),
    não 1 -- capturada ao vivo do tráfego de rede de uma conclusão de
    produção real. Testar só o passo final concluiria o incidente mas
    ficaria com `situation` vazia e sem comentário registrado."""

    @pytest.mark.asyncio
    async def test_sucesso_faz_as_4_chamadas_na_ordem_certa(self):
        cliente_fake = _ClienteHttpxFake(
            respostas_post=[
                _RespostaHttpxFake(corpo={"message": "Operador atribuido com sucesso"}),
                _RespostaHttpxFake(corpo={"message": "Situação alterada com sucesso"}),
                _RespostaHttpxFake(corpo={"message": "Comentario adicionado com sucesso"}),
                _RespostaHttpxFake(corpo={"message": "Incidente resolvido com sucesso"}),
            ]
        )
        contexto = _contexto_http_fake(cliente_fake)

        resultado = await bot.concluir_incidente_http(contexto, "ABC1234", "motivo de teste", numero_incidente="42")

        assert resultado == "Incidente 42 concluído"
        urls = [chamada[0] for chamada in cliente_fake.chamadas_post]
        assert urls == [
            "/v2/incidents/operation/assing/42",
            "/v2/incidents/operation/situation/42",
            "/v2/incidents/operation/comment/42",
            "/v2/incidents/operation/resolved/42",
        ]
        _, _, payload_comentario = cliente_fake.chamadas_post[2]
        assert payload_comentario == {
            "comment": "motivo de teste", "type": bot.TIPO_ACOMPANHAMENTO_INFORMACAO, "createdUserId": 999,
        }
        _, _, payload_resolver = cliente_fake.chamadas_post[3]
        assert payload_resolver == {"data": {"loggedUser": "999"}}

    @pytest.mark.asyncio
    async def test_falha_no_atribuir_nao_tenta_os_proximos_passos(self):
        cliente_fake = _ClienteHttpxFake(respostas_post=[_RespostaHttpxFake(status_code=500)])
        contexto = _contexto_http_fake(cliente_fake)

        with pytest.raises(RuntimeError):
            await bot.concluir_incidente_http(contexto, "ABC1234", "motivo", numero_incidente="42")
        assert len(cliente_fake.chamadas_post) == 1

    @pytest.mark.asyncio
    async def test_falha_na_situacao_nao_tenta_os_proximos_passos(self):
        cliente_fake = _ClienteHttpxFake(
            respostas_post=[_RespostaHttpxFake(), _RespostaHttpxFake(status_code=500)]
        )
        contexto = _contexto_http_fake(cliente_fake)

        with pytest.raises(RuntimeError):
            await bot.concluir_incidente_http(contexto, "ABC1234", "motivo", numero_incidente="42")
        assert len(cliente_fake.chamadas_post) == 2

    @pytest.mark.asyncio
    async def test_falha_no_comentario_nao_tenta_os_proximos_passos(self):
        cliente_fake = _ClienteHttpxFake(
            respostas_post=[_RespostaHttpxFake(), _RespostaHttpxFake(), _RespostaHttpxFake(status_code=500)]
        )
        contexto = _contexto_http_fake(cliente_fake)

        with pytest.raises(RuntimeError):
            await bot.concluir_incidente_http(contexto, "ABC1234", "motivo", numero_incidente="42")
        assert len(cliente_fake.chamadas_post) == 3

    @pytest.mark.asyncio
    async def test_falha_no_resolver(self):
        cliente_fake = _ClienteHttpxFake(
            respostas_post=[
                _RespostaHttpxFake(), _RespostaHttpxFake(), _RespostaHttpxFake(),
                _RespostaHttpxFake(status_code=500),
            ]
        )
        contexto = _contexto_http_fake(cliente_fake)

        with pytest.raises(RuntimeError):
            await bot.concluir_incidente_http(contexto, "ABC1234", "motivo", numero_incidente="42")
        assert len(cliente_fake.chamadas_post) == 4

    @pytest.mark.asyncio
    async def test_sem_numero_incidente_busca_por_placa(self):
        cliente_fake = _ClienteHttpxFake(
            respostas_get=[_RespostaHttpxFake(corpo={"content": [{"id": 42}]})],
            respostas_post=[
                _RespostaHttpxFake(), _RespostaHttpxFake(), _RespostaHttpxFake(), _RespostaHttpxFake(),
            ],
        )
        contexto = _contexto_http_fake(cliente_fake)

        resultado = await bot.concluir_incidente_http(contexto, "ABC1234", "motivo")

        assert resultado == "Incidente 42 concluído"

    @pytest.mark.asyncio
    async def test_sem_numero_incidente_e_sem_incidente_aberto_levanta_erro(self):
        cliente_fake = _ClienteHttpxFake(respostas_get=[_RespostaHttpxFake(corpo={"content": []})])
        contexto = _contexto_http_fake(cliente_fake)

        with pytest.raises(RuntimeError):
            await bot.concluir_incidente_http(contexto, "ABC1234", "motivo")

    @pytest.mark.asyncio
    async def test_sem_numero_incidente_e_multiplos_abertos_levanta_erro_especifico(self):
        cliente_fake = _ClienteHttpxFake(
            respostas_get=[_RespostaHttpxFake(corpo={"content": [{"id": 42}, {"id": 43}]})]
        )
        contexto = _contexto_http_fake(cliente_fake)

        with pytest.raises(bot.MultiplosIncidentesAbertosError):
            await bot.concluir_incidente_http(contexto, "ABC1234", "motivo")
