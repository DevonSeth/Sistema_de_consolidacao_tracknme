"""
Carregamento do "seed" — configuração pré-preenchida embutida no instalador,
usada só na primeira execução em uma máquina nova (config local ainda não existe).

Fluxo (ver decisão da conversa sobre distribuição multi-máquina):

1. Gerar, uma única vez, um arquivo `seed.enc` a partir dos valores reais de
   produção (Track N' Me, Newmo, Supabase, Google) — NUNCA versionar o `seed.enc`
   num repositório público; ele é gerado fora do controle de versão e embutido
   manualmente no pacote do instalador.
2. Em runtime, se `config/manager.carregar_config()` não encontrar nada no
   keyring, chama `carregar_seed()` daqui, decripta com uma chave derivada da
   máquina (ex: `keyring` + salt fixo do app, não uma senha visível ao usuário)
   e grava cada valor via `salvar_config(...)`.
3. Depois da primeira execução, este módulo não é mais chamado — o seed não é
   fonte de verdade contínua, é só bootstrap.

TODO: decidir mecanismo de criptografia (ex: `cryptography.fernet`) e onde a
chave de decriptação do seed fica embutida no próprio instalador.
"""


def gerar_seed(valores: dict, caminho_saida: str) -> None:
    """Ferramenta de uso único (linha de comando), roda na sua máquina de
    desenvolvimento para produzir o `seed.enc` que vai dentro do instalador.

    NÃO é chamada em runtime pelo app instalado.
    """
    raise NotImplementedError


def carregar_seed(caminho_seed: str) -> dict:
    """Lê e decripta o seed embutido no instalador. Chamado só quando não
    existe nenhuma configuração local ainda.
    """
    raise NotImplementedError
