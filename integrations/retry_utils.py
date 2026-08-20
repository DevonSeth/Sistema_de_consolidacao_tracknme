"""Retry pra erro transitório de socket no Windows (achado 2026-08-20).

`WinError 10035` (WSAEWOULDBLOCK, "uma operação de soquete sem bloqueio
não pôde ser concluída imediatamente") apareceu em produção em 2
funções síncronas diferentes (`google_sheets_client.ler_aba`/
`supabase_client` via `orchestrator.pipeline.etapa_motor_de_regras`/
`etapa_publicar_fila_operacional`) no mesmo dia em que o SGA passou a
rodar com concorrência sustentada de 160 tarefas assíncronas via
Playwright. Causa conhecida (não é bug do nosso código): o
`asyncio.ProactorEventLoop` (exigido pelo Playwright no Windows pra abrir
subprocessos — `SelectorEventLoop` não suporta) tem um mecanismo interno
de "acordar" o loop (self-pipe) que pode saturar sob concorrência alta;
qualquer chamada de socket síncrona bloqueante rodando no MESMO processo
logo depois pode receber esse erro por tabela, mesmo sem relação
nenhuma com o que ela mesma está fazendo. É, por definição, um sinal
transitório ("o socket não estava pronto nesse instante exato"), não uma
falha real -- não dá pra evitar trocando o event loop (quebraria o
Playwright), só retry curto resolve.
"""

import functools
import time

_WINERRORS_TRANSITORIOS = {10035}


def retry_erro_transitorio_windows(tentativas: int = 3, espera_segundos: float = 0.5):
    """Decorator pra função síncrona que faz I/O de rede real (Google
    Sheets/Supabase). `getattr(e, "winerror", None)` devolve `None` em
    qualquer plataforma que não seja Windows -- vira no-op seguro fora
    do Windows, sem precisar checar `sys.platform`."""
    def decorador(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for tentativa in range(1, tentativas + 1):
                try:
                    return func(*args, **kwargs)
                except OSError as e:
                    if getattr(e, "winerror", None) not in _WINERRORS_TRANSITORIOS or tentativa == tentativas:
                        raise
                    time.sleep(espera_segundos)
        return wrapper
    return decorador
