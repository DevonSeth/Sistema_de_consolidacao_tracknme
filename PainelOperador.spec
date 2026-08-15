# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller — Painel Operador (Fase 1, passo 1.3, 1ª vez empacotando este
projeto). `--onedir` (não `--onefile`) de propósito: com o Chromium do
Playwright embutido como DADO (não código compilado), `--onefile`
reextrairia ~400MB pra uma pasta temp toda vez que o app abre — mesmo
problema que `config/manager.py` já documenta pra outros recursos
("pasta temp do PyInstaller é apagada a cada execução"). `--onedir`
produz uma pasta com o `.exe` + tudo do lado, sem reextração, e já
combina com o esquema do Launcher (Fase 1, passo 1.4) de zipar/extrair
uma pasta por versão.

Rodar (raiz do projeto, dentro do venv):
    pyinstaller PainelOperador.spec
Gera `dist/PainelOperador/`.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = []
binaries = []
hiddenimports = []

# pywebview e playwright escolhem backend/driver por import dinâmico —
# `collect_all` garante que o PyInstaller não deixe nada de fora por
# análise estática não enxergar essas importações (achado na exploração
# da Fase 1: mesma classe de bug do `keyring` abaixo).
for pacote in ("pywebview", "playwright"):
    d, b, h = collect_all(pacote)
    datas += d
    binaries += b
    hiddenimports += h

# tzdata é pacote de dado puro (sem código) — zoneinfo não acha nada sem
# isso no Windows (usado por core.escalonamento).
datas += collect_data_files("tzdata")

# keyring escolhe o backend do SO (aqui, WinVaultKeyring) por
# importlib.metadata entry points — mesmo import dinâmico, precisa do
# hidden import explícito.
hiddenimports += ["keyring.backends.Windows"]

# --- assets estáticos da UI (ui/web/) -------------------------------------
# `ui/app.py::_diretorio_web()` resolve isso ao lado do `.exe`, não dentro
# do bundle do PyInstaller — por isso vai em `datas` mapeado pra "web",
# não como recurso interno do onedir.
datas += [("ui/web", "web")]

# --- Chromium do Playwright embutido ---------------------------------------
# Decisão já fechada no plano de arquitetura: navegador embutido, evita
# `playwright install` em cada máquina. Descoberto dinamicamente (não
# hardcoded) pra não quebrar quando a versão do Chromium mudar.
#
# Achado ao vivo (Fase 1, passo 1.3, testando o artefato descartável):
# `chromium.launch(headless=True)` e `chromium.launch(headless=False)`
# (`integrations/tracknme_bot.py` usa os 2 — 1º tenta automático headless,
# se falhar reabre headed pro humano) resolvem pra 2 BINÁRIOS DIFERENTES —
# `chromium_headless_shell-<rev>/chrome-headless-shell-win64/` pro headless,
# `chromium-<rev>/chrome-win64/` pro headed. `playwright.chromium.
# executable_path` só reporta o 2º, então checar só ele (como a 1ª versão
# deste `.spec` fazia) engana — o headless quebra em silêncio até alguém
# rodar de verdade. Bundla os 2.
_ms_playwright = Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"
_chromium_dirs = sorted(p for p in _ms_playwright.glob("chromium*") if p.is_dir())
if not _chromium_dirs:
    raise SystemExit(
        "Chromium do Playwright não encontrado em %LOCALAPPDATA%\\ms-playwright — "
        "rode '.venv\\Scripts\\playwright install chromium' antes de empacotar."
    )
for _chromium_dir in _chromium_dirs:
    datas += [(str(_chromium_dir), f"ms-playwright/{_chromium_dir.name}")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_asyncio"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PainelOperador",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    # Desliga o layout "_internal/" (padrão desde PyInstaller 6) — todo o
    # código do projeto (config/manager.py::_diretorio_config, ui/app.py::
    # _diretorio_web, main.py::PLAYWRIGHT_BROWSERS_PATH) já assume que
    # tudo mora DIRETO ao lado do `.exe`, mesma convenção de sempre.
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PainelOperador",
)
