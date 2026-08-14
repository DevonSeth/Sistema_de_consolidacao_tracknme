"""
Ponto de entrada da interface — janela nativa via `pywebview`, servindo o
HTML/CSS/JS de `ui/web/` (ainda placeholder, Passo 6).

A comunicação com o Python é feita expondo uma classe de API para o
JavaScript (`window.pywebview.api.<metodo>` no front-end), sem precisar de
servidor HTTP local.

Métodos expostos (chamados pelo `app.js`, Passo 6):
    listar_etapas_com_status()          # catálogo + status (5 estados) + contagem por origem
    obter_dashboards_operador()         # os 6 widgets nativos do "Painel de apoio"
    obter_metricas_admin_operador()     # métricas "emprestadas" do Admin, só as marcadas visivel_operador=true
    obter_status_watchdog()             # Observabilidade fatia 1: travada/etapas com falha/etapas lentas
    executar_etapas(lista_ids, modo)    # modo: 'todas' | 'selecionadas' | 'a_partir_de:<id>'
    cancelar_execucao()
    obter_progresso_atual()             # polling da UI durante execução
    continuar_apos_reconexao_manual()   # botão "Já logei, continuar"

Sem chamada a `/api/operador/*` nesta fatia (decisão já fechada, Fase 4 —
100% local; version-check via API fica pro Launcher, Fase 1).

Arquitetura de concorrência: `executar_etapas`/`continuar_apos_reconexao_
manual` são "fire-and-forget" — devolvem imediato, e o trabalho real
(coroutines de `orchestrator.catalogo_etapas`) roda numa thread daemon
dedicada com seu próprio event loop (`_LoopAssincronoPersistente`), porque
pywebview chama a `Api` de uma thread síncrona (não dá pra `await` direto).
Todo o estado da execução (`_EstadoExecucao`) é compartilhado entre essa
thread e a thread da UI só através de `_estado.lock`.
"""

import asyncio
import json
import sys
import threading
from pathlib import Path

from integrations import supabase_client
from orchestrator import catalogo_etapas, dashboards_operador, metricas_admin_operador, watchdog

STATUS_OCIOSO = "ocioso"
STATUS_RODANDO = "rodando"
STATUS_SUCESSO = "sucesso"
STATUS_ERRO = "erro"
STATUS_AGUARDANDO_RECONEXAO = "aguardando_reconexao"
STATUS_CANCELADA = "cancelada"


class _LoopAssincronoPersistente:
    """1 event loop numa thread daemon dedicada — todo `submeter()` roda
    nesse loop, nunca bloqueia a thread síncrona que o pywebview usa pra
    chamar a `Api`."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._rodar, daemon=True)
        self._thread.start()

    def _rodar(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submeter(self, corrotina):
        """Agenda `corrotina` no loop persistente; devolve um
        `concurrent.futures.Future` thread-safe (não um `asyncio.Future`)."""
        return asyncio.run_coroutine_threadsafe(corrotina, self._loop)


class _EstadoExecucao:
    """Estado compartilhado entre a thread da UI (pywebview) e a thread do
    loop assíncrono persistente — todo acesso deve passar por `lock`."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rodando = False
        self.etapa_atual_id: str | None = None
        self.resultados: dict[str, object] = {}
        self.motivo_parada: str | None = None
        self.etapa_travada_id: str | None = None
        self.resultado_travado = None
        self.etapas_restantes: list = []
        self.contexto: dict = {}
        self.erro_geral: str | None = None
        self.progresso_item: dict | None = None
        self.cancelar_solicitado = False
        self.execucao_id: str | None = None


_estado = _EstadoExecucao()
_loop_persistente: _LoopAssincronoPersistente | None = None


def _obter_loop() -> _LoopAssincronoPersistente:
    global _loop_persistente
    if _loop_persistente is None:
        _loop_persistente = _LoopAssincronoPersistente()
    return _loop_persistente


def _status_da_etapa(etapa: catalogo_etapas.EtapaCatalogo, estado: _EstadoExecucao) -> str:
    """6 estados: Ocioso/Rodando/Sucesso/Erro/Cancelada/Aguardando você.
    Ordem de checagem importa: uma etapa travada aguardando reconexão
    nunca tem `resultado` em `estado.resultados` ainda (só é gravado quando
    a etapa termina de verdade), então checar isso primeiro é suficiente —
    não precisa de `elif` explícito. `cancelado` também vem antes de
    sucesso/erro — uma etapa que o próprio usuário interrompeu não deveria
    aparecer como "Erro"."""
    if estado.motivo_parada == "aguardando_reconexao" and estado.etapa_travada_id == etapa.id:
        return STATUS_AGUARDANDO_RECONEXAO
    resultado = estado.resultados.get(etapa.id)
    if resultado is not None:
        if resultado.cancelado is not None:
            return STATUS_CANCELADA
        return STATUS_SUCESSO if resultado.sucesso else STATUS_ERRO
    if estado.rodando and estado.etapa_atual_id == etapa.id:
        return STATUS_RODANDO
    return STATUS_OCIOSO


def _para_json_seguro(obj):
    """Round-trip `json.dumps`/`json.loads` — garante que qualquer coisa
    devolvida pra `Api` chega no JS como dado plano (`default=str` cobre
    qualquer valor que não seja nativo de JSON, ex: um objeto que escape
    por engano), nunca um objeto Python vazando pra serialização própria
    do pywebview."""
    return json.loads(json.dumps(obj, default=str))


def _ler_cancelar() -> bool:
    with _estado.lock:
        return _estado.cancelar_solicitado


def _on_progresso(etapa_id: str) -> None:
    with _estado.lock:
        _estado.etapa_atual_id = etapa_id
        _estado.progresso_item = None


def _on_progresso_item(etapa_id: str, concluidos: int, total: int) -> None:
    with _estado.lock:
        _estado.progresso_item = {"etapa_id": etapa_id, "concluidos": concluidos, "total": total}


def _on_resultado(etapa_id: str, resultado) -> None:
    with _estado.lock:
        _estado.resultados[etapa_id] = resultado
        _estado.progresso_item = None


async def _rodar_cadeia(lista_ids: list[str] | None, modo: str) -> None:
    try:
        execucao = await catalogo_etapas.executar_cadeia(
            lista_ids,
            modo,
            cancelar_checker=_ler_cancelar,
            on_progresso=_on_progresso,
            on_progresso_item=_on_progresso_item,
            on_resultado=_on_resultado,
            contexto=_estado.contexto,
        )
        _finalizar_execucao(execucao)
    except Exception as exc:  # nunca deixa a thread do loop morrer silenciosa
        with _estado.lock:
            _estado.rodando = False
            _estado.erro_geral = str(exc)
            _estado.progresso_item = None


async def _continuar_cadeia(etapa_travada, resultado_travado, etapas_restantes: list, execucao_id: str | None) -> None:
    try:
        execucao = await catalogo_etapas.continuar_apos_reconexao(
            etapa_travada,
            _estado.contexto,
            resultado_travado,
            etapas_restantes,
            execucao_id=execucao_id,
            cancelar_checker=_ler_cancelar,
            on_progresso=_on_progresso,
            on_progresso_item=_on_progresso_item,
            on_resultado=_on_resultado,
        )
        _finalizar_execucao(execucao)
    except Exception as exc:
        with _estado.lock:
            _estado.rodando = False
            _estado.erro_geral = str(exc)
            _estado.progresso_item = None


def _finalizar_execucao(execucao: catalogo_etapas.ExecucaoCadeia) -> None:
    with _estado.lock:
        _estado.rodando = False
        _estado.execucao_id = execucao.execucao_id
        _estado.motivo_parada = execucao.motivo_parada
        _estado.etapa_travada_id = execucao.etapa_travada_id
        _estado.etapas_restantes = execucao.etapas_restantes
        _estado.resultado_travado = (
            execucao.resultados[-1] if execucao.motivo_parada == "aguardando_reconexao" and execucao.resultados else None
        )
        _estado.progresso_item = None


class Api:
    def listar_etapas_com_status(self) -> dict:
        with _estado.lock:
            etapas = []
            for etapa in catalogo_etapas.CATALOGO:
                resultado = _estado.resultados.get(etapa.id)
                etapas.append(
                    {
                        "id": etapa.id,
                        "fase": etapa.fase,
                        "label": etapa.label,
                        "manual": etapa.manual,
                        "status": _status_da_etapa(etapa, _estado),
                        # Só preenchido quando a etapa terminou em erro — dá pro
                        # atendente entender O QUE falhou (ex: "Falha ao baixar
                        # relatório(s): [...]") sem precisar investigar logs,
                        # em vez de só o selo genérico "Erro".
                        "mensagem_erro": (
                            resultado.mensagem if resultado is not None and not resultado.sucesso and resultado.cancelado is None else None
                        ),
                    }
                )
        contagem = supabase_client.contar_pendencias_por_origem()
        return _para_json_seguro({"etapas": etapas, "contagem_por_origem": contagem})

    def obter_dashboards_operador(self) -> dict:
        return _para_json_seguro(dashboards_operador.montar_dashboards_operador())

    def obter_metricas_admin_operador(self) -> dict:
        return _para_json_seguro(metricas_admin_operador.montar_metricas_admin_operador())

    def obter_status_watchdog(self) -> dict:
        return _para_json_seguro(watchdog.avaliar_watchdog())

    def executar_etapas(self, lista_ids: list[str], modo: str) -> dict:
        with _estado.lock:
            if _estado.rodando:
                return _para_json_seguro({"iniciado": False, "motivo": "ja_rodando"})
            _estado.rodando = True
            _estado.etapa_atual_id = None
            _estado.resultados = {}
            _estado.motivo_parada = None
            _estado.etapa_travada_id = None
            _estado.resultado_travado = None
            _estado.etapas_restantes = []
            _estado.contexto = {}
            _estado.erro_geral = None
            _estado.progresso_item = None
            _estado.cancelar_solicitado = False
            _estado.execucao_id = None

        _obter_loop().submeter(_rodar_cadeia(lista_ids, modo))
        return _para_json_seguro({"iniciado": True})

    def cancelar_execucao(self) -> dict:
        with _estado.lock:
            if not _estado.rodando:
                return _para_json_seguro({"aceito": False, "motivo": "nao_esta_rodando"})
            _estado.cancelar_solicitado = True
        return _para_json_seguro({"aceito": True})

    def obter_progresso_atual(self) -> dict:
        with _estado.lock:
            return _para_json_seguro(
                {
                    "rodando": _estado.rodando,
                    "etapa_atual_id": _estado.etapa_atual_id,
                    "motivo_parada": _estado.motivo_parada,
                    "etapa_travada_id": _estado.etapa_travada_id,
                    "etapas_restantes": [etapa.id for etapa in _estado.etapas_restantes],
                    "progresso_item": _estado.progresso_item,
                    "erro_geral": _estado.erro_geral,
                    # `mensagem` de `ResultadoEtapa` já vem formatada com a
                    # contagem de pendentes (ex: "... aguardando reconexão
                    # manual (2 pendente(s))."), reaproveitada direto no
                    # banner da UI (Passo 6) em vez de derivar de novo aqui.
                    "mensagem_etapa_travada": _estado.resultado_travado.mensagem if _estado.resultado_travado else None,
                }
            )

    def continuar_apos_reconexao_manual(self) -> dict:
        with _estado.lock:
            if _estado.motivo_parada != "aguardando_reconexao" or _estado.etapa_travada_id is None:
                return _para_json_seguro({"iniciado": False, "motivo": "nada_aguardando_reconexao"})
            etapa_travada = catalogo_etapas.etapa_por_id(_estado.etapa_travada_id)
            resultado_travado = _estado.resultado_travado
            etapas_restantes = list(_estado.etapas_restantes)
            execucao_id = _estado.execucao_id
            _estado.rodando = True
            _estado.motivo_parada = None
            _estado.progresso_item = None
            _estado.cancelar_solicitado = False

        _obter_loop().submeter(_continuar_cadeia(etapa_travada, resultado_travado, etapas_restantes, execucao_id))
        return _para_json_seguro({"iniciado": True})


def _diretorio_web() -> Path:
    """Pasta `ui/web/`, resolvida ao lado do .exe quando empacotado (mesmo
    padrão de `config.manager._diretorio_config`/`tracknme_bot.
    _diretorio_downloads`)."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    return base / "web"


def _execucao_em_andamento() -> bool:
    """Cobre tanto `rodando=True` quanto `aguardando_reconexao` (a trava de
    execução continua presa nesse estado, mesmo com `rodando=False`) —
    fechar a janela em qualquer um dos dois deixaria o processo órfão em
    segundo plano, segurando a trava do Supabase até o TTL."""
    with _estado.lock:
        return _estado.rodando or _estado.motivo_parada == "aguardando_reconexao"


def _avisar_bloqueio_fechamento() -> None:
    """Aviso nativo do Windows (síncrono, sem depender da ponte JS do
    pywebview) — chamado de dentro do handler de `events.closing`, que já
    roda de forma bloqueante por design do pywebview."""
    import ctypes

    ctypes.windll.user32.MessageBoxW(
        0,
        "Uma execução do robô está em andamento (ou aguardando reconexão manual). "
        "Cancele e aguarde a etapa atual terminar antes de fechar o Painel Operador.",
        "Execução em andamento",
        0x30,  # MB_ICONWARNING
    )


def _ao_fechar() -> bool:
    """Handler de `window.events.closing` — devolve `True`/`False`
    explícito (nunca `None`): o pywebview cancela o fechamento quando
    QUALQUER handler devolve exatamente `False`. Sem exceção "fechar mesmo
    assim" de propósito — evita processo órfão + trava presa no Supabase
    (achado ao vivo: fechar durante uma execução não mata o processo)."""
    if not _execucao_em_andamento():
        return True
    _avisar_bloqueio_fechamento()
    return False


def main() -> None:
    import webview

    index_html = _diretorio_web() / "index.html"
    api = Api()
    janela = webview.create_window("Painel Operador — Consolidação Track N'Me", str(index_html), js_api=api)
    janela.events.closing += _ao_fechar
    webview.start()


if __name__ == "__main__":
    main()
