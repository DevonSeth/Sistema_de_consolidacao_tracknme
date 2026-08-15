import pytest


def test_main_carrega_config_sincroniza_e_inicia_interface(monkeypatch):
    chamadas = []

    monkeypatch.setattr(
        "config.manager.carregar_config",
        lambda: chamadas.append("carregar_config") or {},
    )
    monkeypatch.setattr(
        "integrations.provisionamento_client.verificar_e_sincronizar",
        lambda: chamadas.append("verificar_e_sincronizar"),
    )
    monkeypatch.setattr(
        "main.iniciar_interface",
        lambda: chamadas.append("iniciar_interface"),
    )

    import main

    main.main(argv=[])

    assert chamadas == ["carregar_config", "verificar_e_sincronizar", "iniciar_interface"]


def test_main_provisionar_exige_base_url(monkeypatch):
    import main

    with pytest.raises(SystemExit, match="base-url"):
        main.main(argv=["--provisionar", "token-x"])


def test_main_provisionar_chama_o_client_e_nao_abre_interface(monkeypatch):
    chamadas = []

    monkeypatch.setattr(
        "integrations.provisionamento_client.provisionar_maquina",
        lambda base_url, token: chamadas.append((base_url, token)),
    )
    monkeypatch.setattr(
        "main.iniciar_interface",
        lambda: chamadas.append("iniciar_interface"),
    )

    import main

    main.main(argv=["--provisionar", "token-x", "--base-url", "http://localhost:3000"])

    assert chamadas == [("http://localhost:3000", "token-x")]
