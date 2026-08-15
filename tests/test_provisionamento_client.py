import httpx
import pytest

from config import manager
from integrations import provisionamento_client as prov


class _RespostaFake:
    def __init__(self, status_code, corpo):
        self.status_code = status_code
        self._corpo = corpo

    def json(self):
        return self._corpo


def _credenciais_completas():
    return {
        "tracknme": {"usuario": "u", "senha": "s"},
        "newmo": {"token": "t", "canal_guid": "g", "setor_id": 1, "templates": {}},
        "supabase": {"url": "https://x.supabase.co", "service_role_key": "k"},
        "google_sheets": {
            "credenciais_path": "/caminho/da/maquina/original.json",
            "planilha_administrador_id": "id-a",
            "planilha_operacional_id": "id-o",
        },
        "google_sheets_arquivo_credenciais": '{"type": "service_account"}',
    }


@pytest.fixture
def chamadas_salvar_config(monkeypatch):
    chamadas = []
    monkeypatch.setattr(manager, "salvar_config", lambda secao, valores: chamadas.append((secao, valores)))
    return chamadas


@pytest.fixture(autouse=True)
def _isola_diretorio_config(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "_diretorio_config", lambda: tmp_path)
    return tmp_path


def test_provisionar_maquina_aplica_as_4_secoes_e_grava_provisionamento(
    monkeypatch, chamadas_salvar_config, tmp_path
):
    pedidos = []

    def _post_fake(url, json, timeout):
        pedidos.append((url, json, timeout))
        return _RespostaFake(200, {"chave_maquina": "chave-nova-123", "credenciais": _credenciais_completas()})

    monkeypatch.setattr(prov.httpx, "post", _post_fake)

    prov.provisionar_maquina("http://localhost:3000", "token-de-teste")

    assert pedidos == [
        ("http://localhost:3000/api/operador/provisionar", {"token": "token-de-teste"}, prov.TIMEOUT_SEGUNDOS)
    ]

    por_secao = dict(chamadas_salvar_config)
    assert por_secao["tracknme"] == {"usuario": "u", "senha": "s"}
    assert por_secao["newmo"] == {"token": "t", "canal_guid": "g", "setor_id": 1, "templates": {}}
    assert por_secao["supabase"] == {"url": "https://x.supabase.co", "service_role_key": "k"}

    # google_sheets nunca usa o credenciais_path que veio do Vault — sempre
    # o caminho local recém-escrito.
    caminho_esperado = str(tmp_path / prov.NOME_ARQUIVO_GOOGLE_SHEETS_CREDENCIAIS)
    assert por_secao["google_sheets"]["credenciais_path"] == caminho_esperado
    assert por_secao["google_sheets"]["planilha_administrador_id"] == "id-a"
    assert (tmp_path / prov.NOME_ARQUIVO_GOOGLE_SHEETS_CREDENCIAIS).read_text(encoding="utf-8") == (
        '{"type": "service_account"}'
    )

    assert por_secao["provisionamento"] == {
        "base_url": "http://localhost:3000",
        "chave_maquina": "chave-nova-123",
        "versoes_conhecidas": {},
    }


def test_provisionar_maquina_token_invalido_levanta_runtime_error(monkeypatch):
    monkeypatch.setattr(
        prov.httpx, "post", lambda url, json, timeout: _RespostaFake(401, {"erro": "Token inválido, expirado ou já utilizado."})
    )

    with pytest.raises(RuntimeError, match="Token inválido"):
        prov.provisionar_maquina("http://localhost:3000", "token-ruim")


def test_verificar_e_sincronizar_noop_quando_maquina_nunca_provisionada(monkeypatch):
    monkeypatch.setattr(manager, "carregar_config", lambda: {"provisionamento": {}})

    def _get_fake(*a, **k):
        raise AssertionError("não deveria chamar a API sem chave_maquina configurada")

    monkeypatch.setattr(prov.httpx, "get", _get_fake)

    prov.verificar_e_sincronizar()  # não levanta, não chama a API


def test_verificar_e_sincronizar_soft_fail_em_erro_de_rede(monkeypatch):
    monkeypatch.setattr(
        manager,
        "carregar_config",
        lambda: {"provisionamento": {"base_url": "http://localhost:3000", "chave_maquina": "abc"}},
    )

    def _get_fake(*a, **k):
        raise httpx.ConnectError("recusado")

    monkeypatch.setattr(prov.httpx, "get", _get_fake)

    prov.verificar_e_sincronizar()  # não propaga a exceção


def test_verificar_e_sincronizar_aplica_so_a_secao_que_mudou(monkeypatch, chamadas_salvar_config):
    monkeypatch.setattr(
        manager,
        "carregar_config",
        lambda: {
            "provisionamento": {
                "base_url": "http://localhost:3000",
                "chave_maquina": "abc",
                "versoes_conhecidas": {
                    "tracknme": "2026-01-01T00:00:00Z",
                    "newmo": "2026-01-01T00:00:00Z",
                    "supabase": "2026-01-01T00:00:00Z",
                    "google_sheets": "2026-01-01T00:00:00Z",
                    "google_sheets_arquivo_credenciais": "2026-01-01T00:00:00Z",
                },
            }
        },
    )
    pedidos = []

    def _get_fake(url, headers, timeout):
        pedidos.append((url, headers, timeout))
        return _RespostaFake(
            200,
            {
                "versoes": {
                    "tracknme": "2026-01-01T00:00:00Z",
                    "newmo": "2026-02-01T00:00:00Z",  # única mudança
                    "supabase": "2026-01-01T00:00:00Z",
                    "google_sheets": "2026-01-01T00:00:00Z",
                    "google_sheets_arquivo_credenciais": "2026-01-01T00:00:00Z",
                },
                "credenciais": _credenciais_completas(),
            },
        )

    monkeypatch.setattr(prov.httpx, "get", _get_fake)

    prov.verificar_e_sincronizar()

    assert pedidos == [
        (
            "http://localhost:3000/api/operador/credenciais/versao",
            {"Authorization": "Bearer abc"},
            prov.TIMEOUT_SEGUNDOS,
        )
    ]
    secoes_gravadas = {secao for secao, _ in chamadas_salvar_config}
    assert secoes_gravadas == {"newmo", "provisionamento"}


def test_verificar_e_sincronizar_sem_diferenca_nenhuma_nao_grava_nada(monkeypatch, chamadas_salvar_config):
    versoes = {
        "tracknme": "2026-01-01T00:00:00Z",
        "newmo": "2026-01-01T00:00:00Z",
        "supabase": "2026-01-01T00:00:00Z",
        "google_sheets": "2026-01-01T00:00:00Z",
        "google_sheets_arquivo_credenciais": "2026-01-01T00:00:00Z",
    }
    monkeypatch.setattr(
        manager,
        "carregar_config",
        lambda: {
            "provisionamento": {
                "base_url": "http://localhost:3000",
                "chave_maquina": "abc",
                "versoes_conhecidas": versoes,
            }
        },
    )
    monkeypatch.setattr(
        prov.httpx, "get", lambda *a, **k: _RespostaFake(200, {"versoes": versoes, "credenciais": _credenciais_completas()})
    )

    prov.verificar_e_sincronizar()

    assert chamadas_salvar_config == []
