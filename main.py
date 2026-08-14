"""
Ponto de entrada do executável final.
"""

from config import manager
from ui.app import main as iniciar_interface


def main():
    manager.carregar_config()
    iniciar_interface()


if __name__ == "__main__":
    main()
