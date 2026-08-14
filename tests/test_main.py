def test_main_carrega_config_antes_de_iniciar_interface(monkeypatch):
    chamadas = []

    monkeypatch.setattr(
        "config.manager.carregar_config",
        lambda: chamadas.append("carregar_config") or {},
    )
    monkeypatch.setattr(
        "main.iniciar_interface",
        lambda: chamadas.append("iniciar_interface"),
    )

    import main

    main.main()

    assert chamadas == ["carregar_config", "iniciar_interface"]
