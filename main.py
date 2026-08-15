"""
Ponto de entrada do executável final.
"""

import argparse

from config import manager
from integrations import provisionamento_client
from ui.app import main as iniciar_interface


def main(argv=None):
    """`argv=None` (padrão) faz o `argparse` ler o `sys.argv` real do
    processo — passar uma lista explícita (ex: `[]`) evita depender do
    `sys.argv` de quem chamou `main()` programaticamente (ex: testes)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--provisionar", metavar="TOKEN", help="Token de uso único gerado pelo Painel Admin")
    parser.add_argument("--base-url", help="URL do webapp na Vercel (obrigatório junto com --provisionar)")
    args = parser.parse_args(argv)

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
