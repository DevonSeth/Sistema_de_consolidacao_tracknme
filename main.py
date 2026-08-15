"""
Ponto de entrada do executável final.
"""

import os
import sys
from pathlib import Path

# Precisa rodar ANTES de qualquer import que puxe `playwright`
# (orchestrator.pipeline, integrations.tracknme_bot/sga_bot, importados
# transitivamente por `ui.app` logo abaixo) — achado empacotando pela 1ª
# vez (Fase 1, passo 1.3): sem isso, o Chromium embutido no `.exe` nunca
# seria encontrado (`chromium.launch()` esperaria `playwright install`
# ter rodado na máquina do atendente). `setdefault` não sobrescreve se
# alguém já setou a variável de verdade (ex: teste manual).
if getattr(sys, "frozen", False):
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(Path(sys.executable).resolve().parent / "ms-playwright")
    )

import argparse

from config import manager
from integrations import provisionamento_client
from ui.app import main as iniciar_interface


def _testar_playwright() -> None:
    """Diagnóstico standalone — confirma que o Chromium embutido no
    `.exe` (Fase 1, passo 1.3) é encontrado e abre, sem depender do
    fluxo completo de nenhum robô. Útil pra validar uma máquina nova
    (ou o próprio empacotamento) sem precisar disparar um login real."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        print(f"Chromium esperado em: {p.chromium.executable_path}")
        navegador = p.chromium.launch(headless=True)
        print("Chromium abriu com sucesso.")
        navegador.close()


def main(argv=None):
    """`argv=None` (padrão) faz o `argparse` ler o `sys.argv` real do
    processo — passar uma lista explícita (ex: `[]`) evita depender do
    `sys.argv` de quem chamou `main()` programaticamente (ex: testes)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--provisionar", metavar="TOKEN", help="Token de uso único gerado pelo Painel Admin")
    parser.add_argument("--base-url", help="URL do webapp na Vercel (obrigatório junto com --provisionar)")
    parser.add_argument(
        "--testar-playwright", action="store_true",
        help="Diagnóstico: confirma que o Chromium embutido abre, sem abrir a interface",
    )
    args = parser.parse_args(argv)

    if args.testar_playwright:
        _testar_playwright()
        return

    if args.provisionar:
        if not args.base_url:
            raise SystemExit("--provisionar exige --base-url")
        provisionamento_client.provisionar_maquina(args.base_url, args.provisionar)
        print("Máquina provisionada com sucesso.")
        return

    manager.carregar_config()
    provisionamento_client.verificar_e_sincronizar()
    iniciar_interface()


if __name__ == "__main__":
    main()
