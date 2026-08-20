"""Retry pra erro transitório de rede/socket nas chamadas de Sheets/
Supabase (achado 2026-08-20).

Cobre 2 famílias de erro, ambas transitórias por natureza (nunca indicam
bug de lógica/negócio):

1. `WinError 10035` (WSAEWOULDBLOCK, "uma operação de soquete sem
   bloqueio não pôde ser concluída imediatamente") — achado original,
   apareceu em produção em 2 funções síncronas diferentes
   (`google_sheets_client.ler_aba`/`supabase_client` via
   `orchestrator.pipeline.etapa_motor_de_regras`/`etapa_publicar_fila_
   operacional`) no mesmo dia em que o SGA passou a rodar com
   concorrência sustentada de 160 tarefas assíncronas via Playwright.
   Causa conhecida (não é bug do nosso código): o `asyncio.
   ProactorEventLoop` (exigido pelo Playwright no Windows pra abrir
   subprocessos) tem um mecanismo interno de "acordar" o loop (self-pipe)
   que pode saturar sob concorrência alta; qualquer chamada de socket
   síncrona bloqueante rodando no MESMO processo logo depois pode
   receber esse erro por tabela, mesmo sem relação com o que ela mesma
   está fazendo. Não dá pra evitar trocando o event loop (quebraria o
   Playwright).
2. `httpx.TransportError` (conexão HTTP derrubada pelo servidor, timeout,
   erro de protocolo HTTP/2 tipo `ConnectionTerminated`/`GOAWAY`) —
   achado no mesmo dia, na mesma sessão que o achado 1: o cliente do
   Supabase é um singleton (`get_client`, `@lru_cache`) reaproveitado
   pelo processo inteiro; depois de horas de uso contínuo (várias
   rodadas seguidas sem reiniciar o app), a conexão persistente pode ser
   encerrada do lado do servidor sem aviso — `httpx` normalmente
   reconecta sozinho, mas nem sempre no meio de uma requisição em
   andamento.
"""

import functools
import time

import httpx

_WINERRORS_TRANSITORIOS = {10035}


def retry_erro_transitorio_windows(tentativas: int = 5, espera_segundos: float = 0.5):
    """Decorator pra função síncrona que faz I/O de rede real (Google
    Sheets/Supabase). `getattr(e, "winerror", None)` devolve `None` em
    qualquer plataforma que não seja Windows -- vira no-op seguro fora
    do Windows, sem precisar checar `sys.platform`. `httpx.TransportError`
    (achado 2, ver docstring do módulo) é agnóstico de plataforma.

    Espera dobra a cada tentativa (`espera_segundos * 2**(tentativa-1)`,
    mesmo espírito do backoff já usado em `_executar_com_tentativas`)
    -- achado 2026-08-20, mesmo dia: 3 tentativas de 0.5s (1.5s de espera
    total) não foram suficientes numa rodada real onde o erro voltou a
    aparecer (a etapa loopa várias chamadas de rede — cada uma com seu
    próprio orçamento de retry — e algumas encontraram o self-pipe do
    Windows ainda saturado depois desse tempo curto). 5 tentativas
    escalonadas (0.5s/1s/2s/4s, ~7.5s de espera total no pior caso) dão
    mais margem real pro sistema se recuperar."""
    def decorador(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for tentativa in range(1, tentativas + 1):
                try:
                    return func(*args, **kwargs)
                except OSError as e:
                    if getattr(e, "winerror", None) not in _WINERRORS_TRANSITORIOS or tentativa == tentativas:
                        raise
                    time.sleep(espera_segundos * (2 ** (tentativa - 1)))
                except httpx.TransportError:
                    if tentativa == tentativas:
                        raise
                    time.sleep(espera_segundos * (2 ** (tentativa - 1)))
        return wrapper
    return decorador
