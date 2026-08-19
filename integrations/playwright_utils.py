"""
Infraestrutura compartilhada de execução Playwright — usada por
`integrations/tracknme_bot.py` e `integrations/sga_bot.py`.

Modelo (decidido na sessão de captura Playwright, 2026-08-04):
    1 login (manual ou automático) cria 1 `BrowserContext` autenticado.
    N `Page`s dentro desse MESMO contexto (compartilham o cookie de sessão)
    processam uma fila de itens em paralelo — resolve "N workers = N
    recaptchas" no SGA, e evita logins redundantes no Track N' Me.

Política de retry — falha de CARREGAMENTO/técnica (não confundir com as
tentativas de negócio do WhatsApp em `core.escalonamento`, que são um
contador completamente separado):
    Round 1: cada item tenta até `max_tentativas` vezes; falha não trava a
        fila, segue pro próximo item.
    Round 2: depois que a fila inteira passar pela primeira rodada, só os
        itens que falharam tentam mais `max_tentativas` vezes.
    Ainda falhando depois disso -> item marcado como falho no resultado
        final, nunca descartado silenciosamente (quem chama decide o que
        fazer, ex: sinalizar pro atendente).

Falha de SESSÃO (deslogou, site fora do ar, recaptcha expirou) é diferente
de falha de item: a `acao` deve levantar `SessaoCaidaError` quando detectar
isso (ex: URL redirecionou pra /login sem motivo). Isso pausa a fila
INTEIRA imediatamente (não gasta tentativas por item à toa) e levanta
`AguardandoReconexao`, com os itens ainda não processados preservados —
quem chama (orchestrator) sinaliza "aguardando reconexão manual" na UI e
chama `processar_fila` de novo só com os itens pendentes depois que o
atendente confirmar que a sessão foi restabelecida.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


class SessaoCaidaError(Exception):
    """A `acao` deve levantar isso ao detectar que a SESSÃO inteira caiu —
    não um problema pontual do item sendo processado."""


@dataclass
class ResultadoItem:
    item: Any
    sucesso: bool
    resultado: Any = None
    erro: str | None = None
    tentativas: int = 0


class AguardandoReconexao(Exception):
    """Levantada por `processar_fila` quando uma `SessaoCaidaError` é
    detectada. `pendentes` são os itens ainda não processados (preservados,
    nunca descartados); `processados` é o que já tinha resultado até então.
    """

    def __init__(self, pendentes: list, processados: list[ResultadoItem]):
        self.pendentes = pendentes
        self.processados = processados
        super().__init__(f"Sessão caída — {len(pendentes)} item(ns) ainda pendente(s).")


class CancelamentoSolicitado(Exception):
    """Levantada por `processar_fila` quando `cancelar_checker` sinaliza
    pedido de cancelamento no meio do processamento da fila — mesmo
    formato de `AguardandoReconexao`, mas sem exigir reconexão manual:
    quem chama sempre trata como cancelamento definitivo (nunca retomada
    automática)."""

    def __init__(self, pendentes: list, processados: list[ResultadoItem]):
        self.pendentes = pendentes
        self.processados = processados
        super().__init__(f"Cancelamento solicitado — {len(pendentes)} item(ns) não processado(s).")


async def _executar_com_tentativas(
    page: Any,
    item: Any,
    acao: Callable[[Any, Any], Awaitable[Any]],
    max_tentativas: int,
) -> ResultadoItem:
    ultimo_erro: str | None = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            resultado = await acao(page, item)
            return ResultadoItem(item=item, sucesso=True, resultado=resultado, tentativas=tentativa)
        except SessaoCaidaError:
            raise
        except Exception as e:  # noqa: BLE001 - queremos capturar qualquer falha técnica do item
            ultimo_erro = str(e)
            if tentativa < max_tentativas:
                await asyncio.sleep(2**tentativa)
    return ResultadoItem(item=item, sucesso=False, erro=ultimo_erro, tentativas=max_tentativas)


async def _rodar_round(
    contexto: Any,
    itens: list,
    acao: Callable[[Any, Any], Awaitable[Any]],
    num_workers: int,
    max_tentativas: int,
    on_item_concluido: Callable[[], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    on_item_iniciado: Callable[[int, Any], None] | None = None,
) -> tuple[list[ResultadoItem], SessaoCaidaError | None, list, bool]:
    fila: asyncio.Queue = asyncio.Queue()
    for item in itens:
        fila.put_nowait(item)

    resultados: list[ResultadoItem] = []
    sessao_caida: SessaoCaidaError | None = None
    cancelado = False

    async def worker(worker_id: int) -> None:
        nonlocal sessao_caida, cancelado
        page = await contexto.new_page()
        try:
            while sessao_caida is None and not cancelado:
                if cancelar_checker is not None and cancelar_checker():
                    cancelado = True
                    break
                try:
                    item = fila.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if on_item_iniciado is not None:
                    on_item_iniciado(worker_id, item)
                try:
                    resultado = await _executar_com_tentativas(page, item, acao, max_tentativas)
                    resultados.append(resultado)
                    if on_item_concluido is not None:
                        on_item_concluido()
                except SessaoCaidaError as e:
                    sessao_caida = e
                    fila.put_nowait(item)  # devolve o item que estava em andamento
                    break
        finally:
            await page.close()

    workers = [
        asyncio.create_task(worker(worker_id))
        for worker_id in range(min(num_workers, max(len(itens), 1)))
    ]
    await asyncio.gather(*workers)

    pendentes: list = []
    while not fila.empty():
        pendentes.append(fila.get_nowait())

    return resultados, sessao_caida, pendentes, cancelado


async def processar_fila(
    contexto: Any,
    itens: list,
    acao: Callable[[Any, Any], Awaitable[Any]],
    num_workers: int = 3,
    max_tentativas: int = 3,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    on_item_iniciado: Callable[[int, Any], None] | None = None,
) -> list[ResultadoItem]:
    """Processa `itens` em paralelo usando `num_workers` páginas dentro do
    mesmo `contexto` (já autenticado, criado por quem chama).

    `acao(page, item)` é chamada pra cada item; deve levantar
    `SessaoCaidaError` se detectar queda de sessão em vez de falha pontual.

    2 rounds de retry: round 1 processa a fila inteira (até `max_tentativas`
    cada); round 2 processa só quem falhou no round 1 (até `max_tentativas`
    cada de novo). Itens ainda falhando depois disso voltam com
    `sucesso=False` no resultado final — nunca são descartados.

    `on_progresso(concluidos, total)` (opcional, default `None` — sem
    mudança de comportamento pra quem não passa) é chamado a cada item que
    termina (sucesso ou falha final daquele round), `total` fixo em
    `len(itens)` original. **Aproximação, não métrica de auditoria**: um
    item que precisa do round 2 conta 2x no acumulado interno — por isso o
    `min(..., total)` aqui, pra nunca reportar mais que 100%.

    `on_item_iniciado(worker_id, item)` (opcional, default `None`) é
    chamado quando um worker PEGA um item da fila, antes de processá-lo
    (diferente de `on_progresso`, que só dispara na conclusão) — dá
    visibilidade do que está rodando agora durante etapas longas, sem
    depender de saber em qual round está (`worker_id` é 0..`num_workers`-1,
    reatribuído do zero a cada round, então o mesmo callback cobre os 2
    rounds sem distinção).

    `cancelar_checker()` (opcional, default `None` — sem mudança de
    comportamento pra quem não passa) é checado por cada worker antes de
    pegar o PRÓXIMO item da fila (nunca interrompe um item já em
    andamento) — se `True`, levanta `CancelamentoSolicitado` com os itens
    já processados preservados e os restantes em `pendentes`.
    """
    if not itens:
        return []

    total = len(itens)
    concluidos = 0

    def _reportar_progresso() -> None:
        nonlocal concluidos
        concluidos += 1
        if on_progresso is not None:
            on_progresso(min(concluidos, total), total)

    on_item_concluido = _reportar_progresso if on_progresso is not None else None

    resultados_r1, sessao_caida, pendentes, cancelado = await _rodar_round(
        contexto, itens, acao, num_workers, max_tentativas, on_item_concluido, cancelar_checker,
        on_item_iniciado,
    )
    if sessao_caida is not None:
        raise AguardandoReconexao(pendentes=pendentes, processados=resultados_r1)
    if cancelado:
        raise CancelamentoSolicitado(pendentes=pendentes, processados=resultados_r1)

    falharam = [r.item for r in resultados_r1 if not r.sucesso]
    if not falharam:
        return resultados_r1

    resultados_r2, sessao_caida2, pendentes2, cancelado2 = await _rodar_round(
        contexto, falharam, acao, num_workers, max_tentativas, on_item_concluido, cancelar_checker,
        on_item_iniciado,
    )
    if sessao_caida2 is not None:
        raise AguardandoReconexao(pendentes=pendentes2, processados=resultados_r1 + resultados_r2)
    if cancelado2:
        raise CancelamentoSolicitado(pendentes=pendentes2, processados=resultados_r1 + resultados_r2)

    resultados_r2_por_item = {id(r.item): r for r in resultados_r2}
    final: list[ResultadoItem] = []
    for r in resultados_r1:
        final.append(r if r.sucesso else resultados_r2_por_item.get(id(r.item), r))
    return final


# ---------------------------------------------------------------------------
# processar_fila_http -- equivalente de processar_fila pra trabalho HTTP puro
# (achado 2026-08-19, integrations.sga_bot.consultar_situacao_http). Função
# dedicada em vez de generalizar processar_fila/_rodar_round: os dois usam
# "recursos por worker" incompatíveis (Page real vs. nenhum recurso, só um
# APIRequestContext compartilhado) -- misturar isso na mesma função obrigaria
# a mexer numa peça crítica também usada por integrations/tracknme_bot.py.
# Reusa _executar_com_tentativas (já é agnóstico ao que "page" significa,
# só repassa pra `acao`) e espelha o mesmo contrato de retorno/exceções de
# processar_fila -- ResultadoItem, 2 rounds de retry, SessaoCaidaError ->
# AguardandoReconexao, cancelar_checker -> CancelamentoSolicitado.
# ---------------------------------------------------------------------------


async def _rodar_round_http(
    request_context: Any,
    itens: list,
    acao: Callable[[Any, Any], Awaitable[Any]],
    concorrencia: int,
    max_tentativas: int,
    on_item_concluido: Callable[[], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    on_item_iniciado: Callable[[int, Any], None] | None = None,
) -> tuple[list[ResultadoItem], SessaoCaidaError | None, list, bool]:
    fila: asyncio.Queue = asyncio.Queue()
    for item in itens:
        fila.put_nowait(item)

    resultados: list[ResultadoItem] = []
    sessao_caida: SessaoCaidaError | None = None
    cancelado = False

    async def worker(worker_id: int) -> None:
        nonlocal sessao_caida, cancelado
        while sessao_caida is None and not cancelado:
            if cancelar_checker is not None and cancelar_checker():
                cancelado = True
                break
            try:
                item = fila.get_nowait()
            except asyncio.QueueEmpty:
                break
            if on_item_iniciado is not None:
                on_item_iniciado(worker_id, item)
            try:
                resultado = await _executar_com_tentativas(request_context, item, acao, max_tentativas)
                resultados.append(resultado)
                if on_item_concluido is not None:
                    on_item_concluido()
            except SessaoCaidaError as e:
                sessao_caida = e
                fila.put_nowait(item)  # devolve o item que estava em andamento
                break

    workers = [
        asyncio.create_task(worker(worker_id))
        for worker_id in range(min(concorrencia, max(len(itens), 1)))
    ]
    await asyncio.gather(*workers)

    pendentes: list = []
    while not fila.empty():
        pendentes.append(fila.get_nowait())

    return resultados, sessao_caida, pendentes, cancelado


async def processar_fila_http(
    request_context: Any,
    itens: list,
    acao: Callable[[Any, Any], Awaitable[Any]],
    concorrencia: int = 80,
    max_tentativas: int = 3,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    on_item_iniciado: Callable[[int, Any], None] | None = None,
) -> list[ResultadoItem]:
    """Processa `itens` via HTTP puro, sem `Page`/`BrowserContext.
    new_page()` nenhuma -- `concorrencia` tarefas `asyncio` competem por
    uma fila compartilhada sobre `request_context` (`playwright.request`
    `APIRequestContext`, já autenticado — ver `integrations.sga_bot.
    preparar_contexto_http`, que fecha o navegador ANTES de qualquer
    consulta). `concorrencia` pode ser bem mais alta que `num_workers` de
    `processar_fila` (validado até 100 simultâneas sem erro técnico numa
    rodada de escala real de 5953 itens, throughput estável) — não há
    recurso pesado por trabalhador, só uma tarefa presa à fila.

    Mesmo contrato de `processar_fila`: `acao(request_context, item)`
    deve levantar `SessaoCaidaError` ao detectar sessão inválida (ex:
    resposta redirecionada pra login) — pausa a fila inteira e vira
    `AguardandoReconexao`, nunca gasta tentativas à toa. 2 rounds de retry
    (round 1 fila inteira, round 2 só quem falhou), `on_progresso`/
    `on_item_iniciado`/`cancelar_checker` com o mesmo significado de
    `processar_fila` (ver docstring de lá — não repetido aqui)."""
    if not itens:
        return []

    total = len(itens)
    concluidos = 0

    def _reportar_progresso() -> None:
        nonlocal concluidos
        concluidos += 1
        if on_progresso is not None:
            on_progresso(min(concluidos, total), total)

    on_item_concluido = _reportar_progresso if on_progresso is not None else None

    resultados_r1, sessao_caida, pendentes, cancelado = await _rodar_round_http(
        request_context, itens, acao, concorrencia, max_tentativas, on_item_concluido, cancelar_checker,
        on_item_iniciado,
    )
    if sessao_caida is not None:
        raise AguardandoReconexao(pendentes=pendentes, processados=resultados_r1)
    if cancelado:
        raise CancelamentoSolicitado(pendentes=pendentes, processados=resultados_r1)

    falharam = [r.item for r in resultados_r1 if not r.sucesso]
    if not falharam:
        return resultados_r1

    resultados_r2, sessao_caida2, pendentes2, cancelado2 = await _rodar_round_http(
        request_context, falharam, acao, concorrencia, max_tentativas, on_item_concluido, cancelar_checker,
        on_item_iniciado,
    )
    if sessao_caida2 is not None:
        raise AguardandoReconexao(pendentes=pendentes2, processados=resultados_r1 + resultados_r2)
    if cancelado2:
        raise CancelamentoSolicitado(pendentes=pendentes2, processados=resultados_r1 + resultados_r2)

    resultados_r2_por_item = {id(r.item): r for r in resultados_r2}
    final: list[ResultadoItem] = []
    for r in resultados_r1:
        final.append(r if r.sucesso else resultados_r2_por_item.get(id(r.item), r))
    return final
