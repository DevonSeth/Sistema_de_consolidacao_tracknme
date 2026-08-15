# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller — Launcher.exe (Fase 1, passo 1.4). `--onefile` (ao contrário
do `PainelOperador.spec`, que é `--onedir`): o Launcher não carrega
Playwright/pywebview/Chromium embutido — a única dependência "pesada"
herdada é `keyring` (via `config.manager`, reaproveitado pra resolver
`%LOCALAPPDATA%\ConsolidacaoTrackNMe\`), cuja reextração pra `%TEMP%` a
cada abertura é da ordem de poucos MB, sub-segundo — não o problema de
centenas de MB que motivou o onedir do Painel. Onefile também combina
melhor com o papel do Launcher como "o único .exe que o usuário final
abre": um único arquivo, sem pasta pra confundir com as pastas de versão
do Painel.

Rodar (raiz do projeto, dentro do venv):
    pyinstaller Launcher.spec
Gera `dist/Launcher.exe` (arquivo único).
"""

from PyInstaller.utils.hooks import collect_data_files

# keyring escolhe o backend do SO (aqui, WinVaultKeyring) por
# importlib.metadata entry points (import dinâmico) — sem o hidden
# import explícito, cai num backend errado em silêncio (mesmo achado do
# PainelOperador.spec). `launcher.py` importa `config.manager`, que
# importa `keyring` no topo do módulo.
hiddenimports = ["keyring.backends.Windows"]

# tzdata é pacote de dado puro (sem código) — barato incluir, caso
# keyring/stdlib toquem timezone.
datas = collect_data_files("tzdata")

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Defensivo: o Launcher não importa nada disso, mas evita que a
    # análise estática do PyInstaller puxe transitivo do resto do
    # projeto por engano (ambos ficam no mesmo repositório).
    excludes=["pytest", "pytest_asyncio", "playwright", "pywebview"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# Onefile: `a.binaries`/`a.datas` vão direto pro EXE (sem
# `exclude_binaries=True` nem `COLLECT()` depois — isso é o padrão de
# onedir do `PainelOperador.spec`, o oposto do que queremos aqui).
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Launcher",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # UI é tkinter, sem console
)
