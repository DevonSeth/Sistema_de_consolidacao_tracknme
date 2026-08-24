"""
Ponto de entrada da interface — janela nativa via `pywebview`, servindo o
HTML/CSS/JS de `ui/web/` (ainda placeholder, Passo 6).

A comunicação com o Python é feita expondo uma classe de API para o
JavaScript (`window.pywebview.api.<metodo>` no front-end), sem precisar de
servidor HTTP local.

Métodos expostos (chamados pelo `app.js`, Passo 6):
    autenticar(email, senha)            # gate de login, roda antes de tudo (ver _diretorio_web/index.html)
    listar_etapas_com_status()          # catálogo + status (5 estados) + contagem por origem
    obter_dashboards_operador()         # os 6 widgets nativos do "Painel de apoio"
    obter_metricas_admin_operador(desde, ate)  # métricas "emprestadas" do Admin, só as marcadas
                                        # visivel_operador=true; desde/ate (strings "AAAA-MM-DD",
                                        # opcionais) só afetam as métricas "de período" (2026-08-14)
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
import os
import sys
import threading
import webbrowser
from datetime import date
from pathlib import Path

from config import manager
from integrations import supabase_client
from orchestrator import catalogo_etapas, dashboards_operador, metricas_admin_operador, watchdog

# gid da aba "Tratativas" na planilha Operacional — não muda ao reordenar/
# renomear colunas, só se a aba em si for apagada e recriada (ver
# _handoff/obter_gid_abas_botoes.py, script de descoberta read-only).
GID_TRATATIVAS = "1481201362"

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
        self.progresso_workers: dict[int, str] | None = None
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
        _estado.progresso_workers = None


def _on_progresso_item(etapa_id: str, concluidos: int, total: int) -> None:
    with _estado.lock:
        _estado.progresso_item = {"etapa_id": etapa_id, "concluidos": concluidos, "total": total}


def _on_worker_status(etapa_id: str, worker_id: int, descricao: str) -> None:
    """Reportado quando um worker PEGA um item (ver `orchestrator.
    catalogo_etapas`/`integrations.playwright_utils.processar_fila`) —
    cada worker atualiza só sua própria entrada, sem apagar as dos
    outros, então a tela sempre mostra o último item que cada worker
    começou a processar, mesmo em execuções longas com retry (round 2)."""
    with _estado.lock:
        if _estado.progresso_workers is None:
            _estado.progresso_workers = {}
        _estado.progresso_workers[worker_id] = descricao


def _on_resultado(etapa_id: str, resultado) -> None:
    with _estado.lock:
        _estado.resultados[etapa_id] = resultado
        _estado.progresso_item = None
        _estado.progresso_workers = None


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
            on_worker_status=_on_worker_status,
        )
        _finalizar_execucao(execucao)
    except Exception as exc:  # nunca deixa a thread do loop morrer silenciosa
        with _estado.lock:
            _estado.rodando = False
            _estado.erro_geral = str(exc)
            _estado.progresso_item = None
            _estado.progresso_workers = None


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
            on_worker_status=_on_worker_status,
        )
        _finalizar_execucao(execucao)
    except Exception as exc:
        with _estado.lock:
            _estado.rodando = False
            _estado.erro_geral = str(exc)
            _estado.progresso_item = None
            _estado.progresso_workers = None


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
        _estado.progresso_workers = None


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
                        # Falhas por item que persistiram depois dos retries
                        # (`ResultadoEtapa.dados["falhas"]`) — aparece mesmo
                        # quando a etapa como um todo teve sucesso (alguns
                        # itens falharam, outros não), pro atendente poder
                        # corrigir o que precisar.
                        "falhas_item": (
                            resultado.dados.get("falhas") if resultado is not None and resultado.dados else None
                        ),
                        # Selecionado sem Atendimento definido (Bloco E2,
                        # 2026-08-24) — antes pulava em silêncio na Fase F.1;
                        # mesmo princípio de `falhas_item` (aparece mesmo com
                        # a etapa em sucesso), mas é aviso de dado incompleto,
                        # não falha de envio.
                        "sem_atendimento_item": (
                            resultado.dados.get("sem_atendimento") if resultado is not None and resultado.dados else None
                        ),
                    }
                )
        contagem = supabase_client.contar_pendencias_por_origem()
        return _para_json_seguro({"etapas": etapas, "contagem_por_origem": contagem})

    def obter_dashboards_operador(self) -> dict:
        return _para_json_seguro(dashboards_operador.montar_dashboards_operador())

    def obter_metricas_admin_operador(self, desde: str | None = None, ate: str | None = None) -> dict:
        desde_data = date.fromisoformat(desde) if desde else None
        ate_data = date.fromisoformat(ate) if ate else None
        return _para_json_seguro(metricas_admin_operador.montar_metricas_admin_operador(desde_data, ate_data))

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
            _estado.progresso_workers = None
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

    def autenticar(self, email: str, senha: str) -> dict:
        """Gate de login do Painel Operador — valida contra o Supabase Auth
        usando um client DESCARTÁVEL, nunca `supabase_client.get_client()`
        (singleton reaproveitado por toda leitura/escrita de negócio): o SDK
        troca o header Authorization do client inteiro pro JWT da sessão
        recém-logada, o que quebraria todas as chamadas de negócio
        seguintes (elas rodariam com o token do operador, não service_role,
        sem nenhuma policy de RLS pensada pra isso).

        Achado 2026-08-19: `carregar_config`/`create_client` ficavam FORA
        do `try` (só `sign_in_with_password` era protegido) -- qualquer
        exceção aí subia crua pela ponte JS/Python do pywebview, travando
        a tela de login sem nenhum erro visível (o front nem tinha
        `try/catch` no `await`, ver `ui/web/app.js::init`). Agora todo o
        corpo está protegido e a mensagem de erro reflete a exceção real
        em vez de mascarar tudo como "E-mail ou senha inválidos."."""
        from supabase import create_client

        try:
            cfg = manager.carregar_config()["supabase"]
            cliente_login = create_client(cfg["url"], cfg["service_role_key"])
            resposta = cliente_login.auth.sign_in_with_password({"email": email, "password": senha})
        except Exception as e:  # noqa: BLE001 - qualquer falha aqui vira erro visível, nunca escapa muda
            return _para_json_seguro({"sucesso": False, "erro": f"Falha ao autenticar: {e}"})

        papel = (resposta.user.app_metadata or {}).get("role") if resposta.user else None
        if papel != "operador":
            return _para_json_seguro({"sucesso": False, "erro": "E-mail ou senha inválidos."})
        return _para_json_seguro({"sucesso": True})

    def abrir_tratativas(self) -> dict:
        """Abre a aba "Tratativas" da planilha Operacional no navegador
        padrão do sistema — botão "Ir para tratativas" do menu lateral."""
        planilha_id = manager.carregar_config()["google_sheets"]["planilha_operacional_id"]
        webbrowser.open(f"https://docs.google.com/spreadsheets/d/{planilha_id}/edit#gid={GID_TRATATIVAS}")
        return _para_json_seguro({"aberto": True})

    def abrir_log_execucoes(self) -> dict:
        """Abre o log local de execuções (erros/falhas por item, ver
        `catalogo_etapas._registrar_log_arquivo`) no app padrão do Windows
        pra `.log` — botão "Abrir log de execução" do menu lateral.
        Devolve `aberto=False` sem lançar se nenhuma etapa nunca falhou
        ainda (arquivo não existe)."""
        caminho = catalogo_etapas.caminho_log_execucoes()
        if not caminho.exists():
            return _para_json_seguro({"aberto": False})
        os.startfile(caminho)
        return _para_json_seguro({"aberto": True})

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
                    "workers": _estado.progresso_workers,
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
            _estado.progresso_workers = None
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
