import pytest

from config import manager


def test_testar_conexao_sga_levanta_erro():
    with pytest.raises(ValueError, match="manual"):
        manager.testar_conexao("sga")


def test_testar_conexao_gmail_levanta_erro():
    with pytest.raises(ValueError, match="descartado"):
        manager.testar_conexao("gmail")


def test_testar_conexao_secao_desconhecida_levanta_erro():
    with pytest.raises(ValueError, match="desconhecida"):
        manager.testar_conexao("secao_que_nao_existe")


def test_testar_conexao_supabase_faz_select_trivial(monkeypatch):
    from integrations import supabase_client

    chamadas = []

    class _Query:
        def select(self, *a, **k):
            chamadas.append(("select", a, k))
            return self

        def table(self, nome):
            chamadas.append(("table", nome))
            return self

        def limit(self, *a, **k):
            chamadas.append(("limit", a, k))
            return self

        def execute(self):
            chamadas.append(("execute",))
            return None

    cliente_fake = _Query()
    monkeypatch.setattr(supabase_client, "get_client", lambda: cliente_fake)
    supabase_client.get_client.cache_clear = lambda: None

    assert manager.testar_conexao("supabase") is True
    assert ("table", "system_parameters") in chamadas
    assert ("execute",) in chamadas


def test_testar_conexao_google_sheets_abre_as_duas_planilhas(monkeypatch):
    from integrations import google_sheets_client

    chamadas = []

    class _ClienteFake:
        def open_by_key(self, chave):
            chamadas.append(chave)

    monkeypatch.setattr(google_sheets_client, "_cliente", lambda: _ClienteFake())
    google_sheets_client._cliente.cache_clear = lambda: None
    monkeypatch.setattr(
        manager,
        "carregar_config",
        lambda: {
            "google_sheets": {
                "planilha_administrador_id": "admin-id",
                "planilha_operacional_id": "operacional-id",
            }
        },
    )

    assert manager.testar_conexao("google_sheets") is True
    assert chamadas == ["admin-id", "operacional-id"]


def test_testar_conexao_newmo_chama_listar_canais(monkeypatch):
    from integrations import newmo_client

    chamadas = []
    monkeypatch.setattr(newmo_client, "listar_canais", lambda: chamadas.append("chamou") or [])
    # `_client.cache_clear()` real é chamado de verdade — é um lru_cache
    # comum, limpar não faz I/O nenhum, seguro deixar rodar sem mock.

    assert manager.testar_conexao("newmo") is True
    assert chamadas == ["chamou"]


def test_testar_conexao_tracknme_chama_testar_login(monkeypatch):
    from integrations import tracknme_bot

    async def _testar_login_fake():
        return True

    monkeypatch.setattr(tracknme_bot, "testar_login", _testar_login_fake)

    assert manager.testar_conexao("tracknme") is True


def test_testar_conexao_tracknme_propaga_falha(monkeypatch):
    from integrations import tracknme_bot

    async def _testar_login_fake():
        return False

    monkeypatch.setattr(tracknme_bot, "testar_login", _testar_login_fake)

    assert manager.testar_conexao("tracknme") is False


def test_diretorio_dados_local_usa_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert manager._diretorio_dados_local() == tmp_path / manager.NOME_PASTA_DADOS_LOCAL


def test_diretorio_config_em_dev_ignora_localappdata(monkeypatch, tmp_path):
    """Achado ao empacotar pela 1ª vez (Fase 1, passo 1.3): só o ramo
    `frozen` deve mudar pra %LOCALAPPDATA%; em dev o comportamento de
    sempre (relativo ao código-fonte) não pode mudar."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(manager.sys, "frozen", False, raising=False)

    assert str(tmp_path) not in str(manager._diretorio_config())


def test_diretorio_config_quando_frozen_usa_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(manager.sys, "frozen", True, raising=False)

    assert manager._diretorio_config() == tmp_path / manager.NOME_PASTA_DADOS_LOCAL / "config"


def test_diretorio_downloads_pipeline_quando_frozen_usa_localappdata(monkeypatch, tmp_path):
    from orchestrator import pipeline

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(pipeline.sys, "frozen", True, raising=False)

    assert pipeline._diretorio_downloads() == tmp_path / manager.NOME_PASTA_DADOS_LOCAL / "downloads"


def test_diretorio_downloads_tracknme_bot_quando_frozen_usa_localappdata(monkeypatch, tmp_path):
    from integrations import tracknme_bot

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(tracknme_bot.sys, "frozen", True, raising=False)

    assert tracknme_bot._diretorio_downloads() == tmp_path / manager.NOME_PASTA_DADOS_LOCAL / "downloads"
