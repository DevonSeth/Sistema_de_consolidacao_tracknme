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
3. `postgrest.APIError` com `message == "JSON could not be generated"` —
   achado confirmado no CÓDIGO-FONTE do `postgrest-py`
   (`postgrest/exceptions.py::generate_default_error_message`): essa
   mensagem só é gerada quando o corpo da resposta HTTP de erro NÃO é
   JSON válido (`details` carrega o corpo bruto, ex: `"b'Bad Request'"`)
   — ou seja, algo ANTES do PostgREST/Postgres (o gateway Cloudflare da
   Supabase) rejeitou a requisição sem o banco nunca processar, nunca um
   erro de dado/negócio real (esses sempre chegam formatados em JSON).
   O próprio `postgrest-py` já tem um retry embutido pra "erros do
   Cloudflare" (`send_with_retry`), mas documentado como só cobrindo
   requisições GET/HEAD com status 503/520 — não cobre `upsert`/`insert`
   (POST) nem o status 400 visto aqui. Esta é a mesma categoria dos
   achados 1 e 2 (rede/borda instável), só que o Cloudflare devolveu
   texto puro em vez de fechar a conexão.
"""

import functools
import time

import httpx
from postgrest import APIError

_WINERRORS_TRANSITORIOS = {10035}
_MENSAGENS_GATEWAY_NAO_JSON = {"JSON could not be generated"}

ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS = 0.05
"""Pausa entre chamadas sequenciais ao Supabase em loops que fazem várias
seguidas (ex: 1 por linha da aba Tratativas, ou 1 por mini-lote de upsert)
-- achado 2026-08-21: a Supabase hospedada usa proteção DDoS/anti-bot do
Cloudflare no gateway da API REST (não é um rate-limit numérico
documentado), que é conhecida por rejeitar rajadas de chamadas
automatizadas com respostas genéricas não-JSON (`{'message': 'JSON could
not be generated', ...}`, ver `_e_resposta_nao_json_do_gateway` acima) --
mesmo com retry, se a rajada em si é o gatilho, repetir a MESMA rajada
tende a bater no mesmo bloqueio. Espaçar as chamadas reduz o "formato de
rajada" sem mudar nenhuma lógica de negócio; 50ms é conservador o
suficiente pra não ser perceptível numa fila de milhares de itens (~1
minuto extra a cada 1200 chamadas) mas quebra o padrão de picos
instantâneos."""


def _e_resposta_nao_json_do_gateway(e: BaseException) -> bool:
    """Assinatura exata de `postgrest.exceptions.generate_default_error_
    message` -- só é gerada quando o corpo da resposta HTTP de erro não é
    JSON válido, o que só acontece quando algo antes do PostgREST (o
    gateway Cloudflare da Supabase) rejeita a requisição sem o Postgres
    nunca ver. Critério estreito de propósito (mensagem exata, não
    "qualquer APIError"/qualquer 400) -- um erro de dado/negócio real
    (ex: coluna inexistente, violação de constraint) sempre chega
    formatado em JSON pelo PostgREST e NÃO deve ser retentado às cegas."""
    return isinstance(e, APIError) and e.message in _MENSAGENS_GATEWAY_NAO_JSON


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
                except APIError as e:
                    if not _e_resposta_nao_json_do_gateway(e) or tentativa == tentativas:
                        raise
                    time.sleep(espera_segundos * (2 ** (tentativa - 1)))
        return wrapper
    return decorador
