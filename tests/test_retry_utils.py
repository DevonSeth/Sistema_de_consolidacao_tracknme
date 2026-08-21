import httpx
import pytest
from postgrest import APIError

from integrations.retry_utils import ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS, retry_erro_transitorio_windows


def test_atraso_entre_chamadas_supabase_e_pequeno_e_positivo():
    """Sanity: espaçamento anti-rajada (achado 2026-08-21) precisa ser
    perceptível o suficiente pra quebrar o padrão de rajada, mas pequeno o
    suficiente pra não pesar em filas com milhares de itens."""
    assert 0 < ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS <= 0.5


def _api_error_json_nao_gerado():
    return APIError({
        "message": "JSON could not be generated", "code": 400,
        "hint": "Refer to full message for details", "details": "b'Bad Request'",
    })


def _os_error_com_winerror(winerror):
    erro = OSError("mensagem simulada")
    erro.winerror = winerror
    return erro


def test_sucesso_de_primeira_nao_retenta():
    chamadas = []

    @retry_erro_transitorio_windows()
    def funcao():
        chamadas.append(1)
        return "ok"

    assert funcao() == "ok"
    assert len(chamadas) == 1


def test_winerror_10035_retenta_e_sucede(monkeypatch):
    monkeypatch.setattr("integrations.retry_utils.time.sleep", lambda segundos: None)
    chamadas = []

    @retry_erro_transitorio_windows()
    def funcao():
        chamadas.append(1)
        if len(chamadas) < 2:
            raise _os_error_com_winerror(10035)
        return "recuperado"

    assert funcao() == "recuperado"
    assert len(chamadas) == 2


def test_winerror_10035_esgota_tentativas_e_propaga(monkeypatch):
    monkeypatch.setattr("integrations.retry_utils.time.sleep", lambda segundos: None)
    chamadas = []

    @retry_erro_transitorio_windows(tentativas=3)
    def funcao():
        chamadas.append(1)
        raise _os_error_com_winerror(10035)

    with pytest.raises(OSError):
        funcao()
    assert len(chamadas) == 3


def test_default_e_5_tentativas_com_espera_escalonada(monkeypatch):
    """Achado 2026-08-20 (mesmo dia): 3 tentativas de 0.5s não foram
    suficientes numa rodada real -- default subiu pra 5, com a espera
    dobrando a cada tentativa (mesmo espírito do backoff assíncrono)."""
    esperas = []
    monkeypatch.setattr("integrations.retry_utils.time.sleep", lambda segundos: esperas.append(segundos))
    chamadas = []

    @retry_erro_transitorio_windows()
    def funcao():
        chamadas.append(1)
        raise _os_error_com_winerror(10035)

    with pytest.raises(OSError):
        funcao()

    assert len(chamadas) == 5
    assert esperas == [0.5, 1.0, 2.0, 4.0]  # 4 esperas entre as 5 tentativas, dobrando


def test_httpx_transport_error_retenta_e_sucede(monkeypatch):
    """Achado 2 (mesmo dia): ConnectionTerminated (HTTP/2, conexão
    persistente do Supabase derrubada pelo servidor) chega como
    httpx.TransportError -- não é OSError, precisa do próprio except."""
    monkeypatch.setattr("integrations.retry_utils.time.sleep", lambda segundos: None)
    chamadas = []

    @retry_erro_transitorio_windows()
    def funcao():
        chamadas.append(1)
        if len(chamadas) < 2:
            raise httpx.RemoteProtocolError("ConnectionTerminated error_code:1, last_stream_id:5255")
        return "recuperado"

    assert funcao() == "recuperado"
    assert len(chamadas) == 2


def test_httpx_transport_error_esgota_tentativas_e_propaga(monkeypatch):
    monkeypatch.setattr("integrations.retry_utils.time.sleep", lambda segundos: None)
    chamadas = []

    @retry_erro_transitorio_windows(tentativas=3)
    def funcao():
        chamadas.append(1)
        raise httpx.ConnectError("conexão recusada")

    with pytest.raises(httpx.TransportError):
        funcao()
    assert len(chamadas) == 3


def test_api_error_json_nao_gerado_retenta_e_sucede(monkeypatch):
    """Achado 3 (mesmo dia, confirmado no código-fonte do postgrest-py):
    'JSON could not be generated' é a mensagem sintética que a lib gera
    quando o corpo da resposta HTTP de erro não é JSON válido -- sinal de
    que algo antes do PostgREST (gateway Cloudflare) rejeitou a
    requisição, não um erro de dado real."""
    monkeypatch.setattr("integrations.retry_utils.time.sleep", lambda segundos: None)
    chamadas = []

    @retry_erro_transitorio_windows()
    def funcao():
        chamadas.append(1)
        if len(chamadas) < 2:
            raise _api_error_json_nao_gerado()
        return "recuperado"

    assert funcao() == "recuperado"
    assert len(chamadas) == 2


def test_api_error_json_nao_gerado_esgota_tentativas_e_propaga(monkeypatch):
    monkeypatch.setattr("integrations.retry_utils.time.sleep", lambda segundos: None)
    chamadas = []

    @retry_erro_transitorio_windows(tentativas=3)
    def funcao():
        chamadas.append(1)
        raise _api_error_json_nao_gerado()

    with pytest.raises(APIError):
        funcao()
    assert len(chamadas) == 3


def test_api_error_com_outra_mensagem_nao_retenta():
    """Critério estreito: um erro de dado/negócio real (ex: coluna
    inexistente, violação de constraint) chega formatado em JSON pelo
    PostgREST, com uma mensagem diferente -- NÃO deve ser retentado às
    cegas (mascararia bugs reais, como o achado de data_referencia de
    hoje, que também apareceu como erro 400)."""
    chamadas = []

    @retry_erro_transitorio_windows()
    def funcao():
        chamadas.append(1)
        raise APIError({
            "message": 'column tratativas.situacao does not exist', "code": "42703",
            "hint": None, "details": None,
        })

    with pytest.raises(APIError):
        funcao()
    assert len(chamadas) == 1


def test_outro_winerror_nao_retenta():
    chamadas = []

    @retry_erro_transitorio_windows()
    def funcao():
        chamadas.append(1)
        raise _os_error_com_winerror(10054)  # WSAECONNRESET, outro erro, não o transitório

    with pytest.raises(OSError):
        funcao()
    assert len(chamadas) == 1


def test_oserror_sem_winerror_nao_retenta():
    """Fora do Windows, OSError não tem atributo winerror -- getattr
    devolve None, o decorator vira no-op seguro."""
    chamadas = []

    @retry_erro_transitorio_windows()
    def funcao():
        chamadas.append(1)
        raise OSError("erro genérico sem winerror")

    with pytest.raises(OSError):
        funcao()
    assert len(chamadas) == 1


def test_excecao_nao_oserror_nao_retenta():
    chamadas = []

    @retry_erro_transitorio_windows()
    def funcao():
        chamadas.append(1)
        raise ValueError("erro de negócio real, não é isso que o decorator cobre")

    with pytest.raises(ValueError):
        funcao()
    assert len(chamadas) == 1


def test_repassa_argumentos_e_retorno_corretamente():
    @retry_erro_transitorio_windows()
    def soma(a, b, c=0):
        return a + b + c

    assert soma(1, 2, c=3) == 6
