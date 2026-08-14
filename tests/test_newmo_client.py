import httpx
import pytest

from integrations import newmo_client as newmo


@pytest.fixture(autouse=True)
def _sem_espera(monkeypatch):
    """Nenhum teste deve esperar de verdade o backoff exponencial."""
    monkeypatch.setattr(newmo.time, "sleep", lambda _segundos: None)


def _resultado(cod, mensagem="msg"):
    return {"cod": cod, "mensagem": mensagem}


def test_listar_canais_devolve_lista(monkeypatch):
    chamadas = []

    def _get_fake(path, params):
        chamadas.append((path, params))
        return {"erro": False, "canais": [{"id": "guid-1", "nome": "Canal 1"}]}

    monkeypatch.setattr(newmo, "_get", _get_fake)

    assert newmo.listar_canais() == [{"id": "guid-1", "nome": "Canal 1"}]
    assert chamadas == [("/canal", {"status": "todos"})]


def test_listar_setores_devolve_lista_e_passa_canal(monkeypatch):
    chamadas = []

    def _get_fake(path, params):
        chamadas.append((path, params))
        return {"erro": False, "setores": [{"id": 1, "nome": "Setor 1"}]}

    monkeypatch.setattr(newmo, "_get", _get_fake)

    assert newmo.listar_setores("guid-1") == [{"id": 1, "nome": "Setor 1"}]
    assert chamadas == [("/setor", {"status": "todos", "canal": "guid-1"})]


def test_finalizar_atendimento_devolve_resultado_e_passa_payload_correto(monkeypatch):
    chamadas = []

    def _post_fake(path, payload):
        chamadas.append((path, payload))
        return {"erro": False, "resultado": "Atendimento finalizado com sucesso"}

    monkeypatch.setattr(newmo, "_post", _post_fake)

    assert newmo.finalizar_atendimento(123) == "Atendimento finalizado com sucesso"
    assert chamadas == [("/atendimento/finalizar", {"id": 123, "bot": False})]


def test_corpo_levanta_runtime_error_quando_erro_true():
    resposta = httpx.Response(400, json={"erro": True, "descricao": "canal inválido"})
    with pytest.raises(RuntimeError, match="canal inválido"):
        newmo._corpo(resposta)


def test_corpo_levanta_runtime_error_quando_json_invalido():
    resposta = httpx.Response(500, text="<html>erro interno</html>")
    with pytest.raises(RuntimeError, match="status 500"):
        newmo._corpo(resposta)


def test_enviar_template_payload_remove_prefixo_mais_e_fixa_flags(monkeypatch):
    payloads = []

    def _post_fake(path, payload):
        payloads.append((path, payload))
        return {"erro": False, "atendimentoId": 10, "mensagemId": 20,
                "resultado": _resultado(1)}

    monkeypatch.setattr(newmo, "_post", _post_fake)

    newmo.enviar_template("+5581987654321", 7, ["Fulano"], "guid-1", 3)

    assert len(payloads) == 1
    path, payload = payloads[0]
    assert path == "/mensagem/template"
    assert payload["destinatario"] == "5581987654321"
    assert payload["canal"] == "guid-1"
    assert payload["setor"] == 3
    assert payload["abrirAtendimento"] is True
    assert payload["bot"] is False
    assert payload["ignorarSePossuiAtendimento"] is True
    assert payload["template"] == {"codigo": 7, "variaveis": ["Fulano"]}


@pytest.mark.parametrize(
    "cod,classificacao,sucesso",
    [
        (1, "sucesso", True),
        (7, "contato_invalido", False),
        (2, "falha_permanente", False),
        (3, "falha_permanente", False),
        (5, "falha_permanente", False),
        (8, "falha_permanente", False),
    ],
)
def test_enviar_template_classifica_sem_retry(monkeypatch, cod, classificacao, sucesso):
    chamadas = []

    def _post_fake(path, payload):
        chamadas.append(payload)
        return {"erro": False, "atendimentoId": 10, "mensagemId": 20,
                "resultado": _resultado(cod)}

    monkeypatch.setattr(newmo, "_post", _post_fake)

    resultado = newmo.enviar_template("+5581987654321", 1, [], "guid-1", 3)

    assert len(chamadas) == 1
    assert resultado["classificacao"] == classificacao
    assert resultado["sucesso"] is sucesso
    assert resultado["cod"] == cod
    assert resultado["atendimento_id"] == 10
    assert resultado["mensagem_id"] == 20


@pytest.mark.parametrize("cod_temporario", [4, 6])
def test_enviar_template_retry_cod_temporario_ate_sucesso(monkeypatch, cod_temporario):
    respostas = [
        {"erro": False, "resultado": _resultado(cod_temporario)},
        {"erro": False, "resultado": _resultado(cod_temporario)},
        {"erro": False, "atendimentoId": 10, "mensagemId": 20, "resultado": _resultado(1)},
    ]
    chamadas = []

    def _post_fake(path, payload):
        chamadas.append(payload)
        return respostas[len(chamadas) - 1]

    monkeypatch.setattr(newmo, "_post", _post_fake)

    resultado = newmo.enviar_template("+5581987654321", 1, [], "guid-1", 3)

    assert len(chamadas) == 3
    assert resultado["classificacao"] == "sucesso"


@pytest.mark.parametrize("cod_temporario", [4, 6])
def test_enviar_template_retry_cod_temporario_esgota_tentativas(monkeypatch, cod_temporario):
    def _post_fake(path, payload):
        return {"erro": False, "resultado": _resultado(cod_temporario)}

    monkeypatch.setattr(newmo, "_post", _post_fake)

    resultado = newmo.enviar_template("+5581987654321", 1, [], "guid-1", 3)

    assert resultado["classificacao"] == "falha_temporaria"
    assert resultado["sucesso"] is False


def test_enviar_template_retry_falha_transporte_ate_sucesso(monkeypatch):
    chamadas = []

    def _post_fake(path, payload):
        chamadas.append(payload)
        if len(chamadas) < 2:
            raise httpx.ConnectError("conexão recusada")
        return {"erro": False, "atendimentoId": 10, "mensagemId": 20, "resultado": _resultado(1)}

    monkeypatch.setattr(newmo, "_post", _post_fake)

    resultado = newmo.enviar_template("+5581987654321", 1, [], "guid-1", 3)

    assert len(chamadas) == 2
    assert resultado["classificacao"] == "sucesso"


def test_enviar_template_retry_falha_transporte_esgota_tentativas_sobe_exececao(monkeypatch):
    def _post_fake(path, payload):
        raise httpx.ConnectError("conexão recusada")

    monkeypatch.setattr(newmo, "_post", _post_fake)

    with pytest.raises(httpx.ConnectError):
        newmo.enviar_template("+5581987654321", 1, [], "guid-1", 3)
