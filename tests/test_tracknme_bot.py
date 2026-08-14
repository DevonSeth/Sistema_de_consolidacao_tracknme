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
