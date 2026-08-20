import pytest

from integrations.retry_utils import retry_erro_transitorio_windows


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
