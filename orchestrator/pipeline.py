"""
Orquestrador — único lugar que conhece a ORDEM das etapas do sistema.
A UI só chama funções daqui; nenhuma lógica de fluxo deve viver na UI.

Etapas (Fases A-G já fechadas na conversa), cada uma exposta como uma
função independente para permitir o "seletor de execução por etapa"
(rodar uma, rodar a partir daqui, rodar tudo). Ordem real de execução —
revista em 2026-08-07: o SGA precisa entrar DEPOIS de abrir o Grupo 1 e
ANTES de decidir o que fecha (`classificar_instalacao_remocao` só
consegue classificar com o SGA disponível, e o SGA também passou a
decidir fechamento automático de Manutenção) — por isso Fase B e Fase C
aparecem em duas partes cada, com a Fase D no meio:

    etapa_baixar_relatorios()           -> Fase A.1 (tracknme_bot)
    etapa_ler_planilha_gestor()         -> Fase A.2 (google_sheets_client)
    etapa_motor_de_regras()             -> Fase B.1 (core.motor_regras, sem SGA)
    etapa_abrir_incidentes_automaticos()-> Fase C.1 (tracknme_bot.abrir_incidente)
    etapa_enriquecimento_sga()          -> Fase D   (sga_bot) — única manual,
                                                       cobre Manutenção + Instalação + Remoção
    etapa_consolidar_com_sga()          -> Fase B.2 (aplica SGA em Manutenção +
                                                       classifica Instalação/Remoção +
                                                       junta a fila final)
    etapa_fechar_incidentes_automaticos()-> Fase C.2 (tracknme_bot.concluir_incidente)
    etapa_publicar_fila_operacional()   -> Fase E   (sheets_client + supabase)
    etapa_disparo_mensagens()           -> Fase F.1 (newmo_client + core.mensagens + escalonamento)
    etapa_finalizar_atendimentos_diarios() -> Fase F.1, rotina noturna separada
                                                       (não faz parte do disparo em si —
                                                       ver docstring da própria função)
    etapa_processar_webhook_respostas() -> Fase F.2 (chamado pela Edge Function,
                                                       não por aqui diretamente)
    etapa_escalonar_ligacao()           -> Fase F.3
    etapa_processar_resultado_ligacao() -> Fase F.4
    etapa_processar_alertas()           -> Fase F.5 (fila de exceção "Alertas" —
                                                       retorno tardio do associado /
                                                       agendamento confirmado sem data)
    etapa_relatorio_diario()            -> Fase G

Cada etapa nunca lança exceção não tratada até a UI — captura e devolve um
`ResultadoEtapa` estruturado (sucesso/falha). Registrar início/fim em
`log_execucoes` (Supabase) faz parte do contrato original, mas essa tabela
ainda não existe (observabilidade fica pra depois, decisão já registrada) —
`integrations.supabase_client.registrar_log_execucao` continua
`NotImplementedError` de propósito; não chamar daqui ainda.

Cada etapa é independente e sem estado compartilhado implícito: parâmetros
que uma etapa anterior produziria (ex: os caminhos dos xlsx baixados) têm
default para os mesmos caminhos fixos que a etapa produtora sempre usa —
isso permite rodar uma etapa isolada no painel sem precisar ter rodado (ou
guardado em memória) a etapa anterior na mesma sessão.

TODO: `etapa_relatorio_diario` (Fase G) ainda é `NotImplementedError` —
o resto (A até F.4, incluindo F.1) já está implementado. `config["newmo"]
["templates"]` só é preenchido com os 14 códigos reais quando a Meta
aprovar todos os templates (ver `docs/templates_whatsapp.md`) — até lá,
`etapa_disparo_mensagens` roda normalmente mas não encontra código pra
nenhuma combinação (`codigo = None`), então `newmo_client.enviar_template`
falharia; não é tratado como caso defensivo aqui de propósito (confirmado
com o usuário que isso não acontece em produção, só antes da aprovação
completa).
"""

import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from functools import lru_cache
from pathlib import Path
from time import sleep
from typing import Any, Callable

import holidays
from playwright.async_api import async_playwright

from core import dedup, escalonamento, mensagens, motor_regras, motor_regras_instalacao_remocao
from core.constants import (
    ATENDIMENTO_ACAO,
    ATENDIMENTO_BASE,
    ATENDIMENTO_ROTA,
    CODIGOS_REMOCAO_ATIVA,
    ORIGEM_INSTALACAO,
    ORIGEM_MANUTENCAO,
    ORIGEM_REMOCAO,
    PUMA_STATUS_CONCLUIDO,
    STATUS_AGUARDANDO_LIGACAO,
    STATUS_AGUARDANDO_RESPOSTA,
    STATUS_ENCAMINHADO_PUMA,
    STATUS_FINALIZADO,
    STATUS_PENDENTE,
    STATUS_RESPONDIDO,
    TIPO_IDENTIFICADOR_CHASSI,
    TIPO_IDENTIFICADOR_PLACA,
)
from core.normalizacao import formatar_data_br_sem_hora, normalizar_placa
from config import manager
from integrations import google_sheets_client, newmo_client, playwright_utils, sga_bot, supabase_client, tracknme_bot
from integrations.retry_utils import ATRASO_ENTRE_CHAMADAS_SUPABASE_SEGUNDOS


@dataclass
class ResultadoEtapa:
    etapa: str
    sucesso: bool
    mensagem: str = ""
    dados: dict = field(default_factory=dict)
    aguardando_reconexao: dict | None = None
    cancelado: dict | None = None


@contextmanager
def _anotar_erro(contexto: str):
    """Anota QUAL sub-etapa estava rodando quando uma exceção aconteceu,
    sem trocar o tipo dela (`Exception.add_note`, não um wrap novo) --
    crítico pra não quebrar `retry_erro_transitorio_windows`, que decide
    se retenta com base no TIPO da exceção original. Achado 2026-08-21:
    um `try/except` único envolvendo vários sub-passos de rede deixa o
    erro genérico chegar até a tela sem dizer QUAL desses pontos falhou --
    cada falha nova virava uma investigação do zero. Complementa
    `integrations.supabase_client._registrar_resposta_de_erro` (esse diz
    O QUE o servidor respondeu; isto diz QUAL sub-passo do nosso código
    estava rodando)."""
    try:
        yield
    except Exception as e:
        e.add_note(f"[{contexto}]")
        raise


def _mensagem_com_notas(e: BaseException) -> str:
    """`str(e)` não inclui `__notes__` por padrão -- usar no lugar de
    `str(e)` ao montar `ResultadoEtapa.mensagem` de qualquer etapa que
    passa por `_anotar_erro`, pra a tela mostrar o contexto junto com o
    erro. Sem notas (maioria das etapas, que ainda não usam
    `_anotar_erro`), cai pra `str(e)` puro -- sem efeito colateral."""
    notas = getattr(e, "__notes__", None)
    if not notas:
        return str(e)
    return f"{e} | contexto: {' > '.join(notas)}"


def _diretorio_downloads() -> Path:
    """Mesma convenção de `integrations.tracknme_bot._diretorio_downloads`
    (duplicada de propósito — já é o mesmo padrão usado por
    `config.manager._diretorio_config`): pasta `downloads/` ao lado do
    código-fonte em dev; quando empacotado, em `%LOCALAPPDATA%\\
    ConsolidacaoTrackNMe\\downloads` (`config.manager._diretorio_dados_
    local`) — fixo por máquina, independente de qual pasta de versão do
    `.exe` está rodando (Fase 1, Launcher)."""
    if getattr(sys, "frozen", False):
        base = manager._diretorio_dados_local()
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "downloads"


def _diretorio_logs() -> Path:
    """Mesma convenção de `_diretorio_downloads` (e de
    `orchestrator.catalogo_etapas._diretorio_logs`, duplicada aqui de
    propósito pra não criar import circular — `catalogo_etapas` já
    importa `pipeline`)."""
    if getattr(sys, "frozen", False):
        base = manager._diretorio_dados_local()
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "logs"


def _caminhos_padrao() -> dict:
    """Os mesmos 2 caminhos fixos que `tracknme_bot.baixar_relatorios`
    sempre usa (sempre sobrescreve, nunca acumula histórico) — default de
    `etapa_ler_planilha_gestor` pra permitir rodá-la isolada."""
    diretorio = _diretorio_downloads()
    return {
        "incidentes": str(diretorio / "incidentes.xlsx"),
        "rastreadores_ativos": str(diretorio / "rastreadores_ativos.xlsx"),
    }


def _normalizar_linhas(linhas: list[list]) -> list[list]:
    """Corpo de xlsx do Track N' Me pode trazer célula tipada como número
    (`int`/`float`) ou vazia como `None`, dependendo de como a célula foi
    formatada na origem (confirmado ao vivo: coluna `Linha` de Incidentes
    vem sempre como `int`) — normaliza pra string/"" antes de alimentar o
    motor de regras (que assume string em todo lugar) ou o Sheets."""
    return [["" if valor is None else str(valor) for valor in linha] for linha in linhas]


def _ler_corpo_normalizado(caminho: Path) -> tuple[list, list[list]]:
    linhas = tracknme_bot.ler_linhas_xlsx(caminho)
    cabecalho, corpo = linhas[0], linhas[1:]
    return list(cabecalho), _normalizar_linhas(corpo)


async def etapa_baixar_relatorios() -> ResultadoEtapa:
    try:
        caminhos = await tracknme_bot.baixar_relatorios()
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("baixar_relatorios", sucesso=False, mensagem=_mensagem_com_notas(e))
    return ResultadoEtapa("baixar_relatorios", sucesso=True, dados=caminhos)


def etapa_ler_planilha_gestor(caminhos: dict | None = None) -> ResultadoEtapa:
    """Lê os 2 xlsx baixados (`caminhos`, ou os caminhos fixos padrão se
    rodada isolada), monta os datasets que `core.motor_regras` precisa
    direto em memória (sem round-trip de Sheets), espelha Incidentes e
    Rastreadores Ativos na planilha Administrador (`carregar_administrador`,
    sempre os dois juntos) e lê `Instalação-Remoção` (100% humana, única
    fonte real desse dado)."""
    caminhos = caminhos or _caminhos_padrao()
    try:
        cabecalho_incidentes, corpo_incidentes = _ler_corpo_normalizado(Path(caminhos["incidentes"]))
        _cabecalho_rastreadores, corpo_rastreadores = _ler_corpo_normalizado(
            Path(caminhos["rastreadores_ativos"])
        )

        incidentes = [dict(zip(cabecalho_incidentes, linha)) for linha in corpo_incidentes]
        equipamentos = [
            {f"col_{i + 1}": valor for i, valor in enumerate(linha)}
            for linha in corpo_rastreadores
        ]

        google_sheets_client.carregar_administrador("Incidentes", corpo_incidentes)
        google_sheets_client.carregar_administrador("Rastreadores Ativos", corpo_rastreadores)

        instalacao_remocao = google_sheets_client.ler_aba(
            google_sheets_client.NOME_PLANILHA_ADMINISTRADOR, "Instalação-Remoção"
        )
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("ler_planilha_gestor", sucesso=False, mensagem=_mensagem_com_notas(e))

    return ResultadoEtapa(
        "ler_planilha_gestor",
        sucesso=True,
        dados={
            "incidentes": incidentes,
            "equipamentos": equipamentos,
            "instalacao_remocao": instalacao_remocao,
        },
    )


def _ler_dados_administrador() -> dict:
    """Mesmas 3 abas que `etapa_ler_planilha_gestor` já deixa consistentes
    na planilha Administrador (`Incidentes`/`Rastreadores Ativos` são
    espelho escrito por ela; `Instalação-Remoção` é 100% humana, só
    leitura) — default de `etapa_motor_de_regras` pra permitir rodá-la
    isolada no painel."""
    planilha = google_sheets_client.NOME_PLANILHA_ADMINISTRADOR
    return {
        "incidentes": google_sheets_client.ler_aba(planilha, "Incidentes"),
        "equipamentos": google_sheets_client.ler_aba(planilha, "Rastreadores Ativos"),
        "instalacao_remocao": google_sheets_client.ler_aba(planilha, "Instalação-Remoção"),
    }


def etapa_motor_de_regras(dados: dict | None = None) -> ResultadoEtapa:
    """`dados` é o retorno de `etapa_ler_planilha_gestor().dados` (ou lido
    direto da planilha Administrador se `None`, pra rodar isolada).

    `parametros`/`templates` são buscados uma vez por execução, não por
    incidente — `core.motor_regras.classificar_incidentes` já processa a
    lista inteira numa só chamada (o loop é interno a `core/`), e buscar
    uma vez evita reclassificar o mesmo lote com config diferente no meio
    da execução."""
    try:
        dados = dados or _ler_dados_administrador()
        parametros = supabase_client.buscar_parametros()
        templates = supabase_client.buscar_rule_templates()
        grupos = motor_regras.classificar_incidentes(
            dados["incidentes"], dados["equipamentos"], dados["instalacao_remocao"],
            parametros, templates,
        )
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("motor_de_regras", sucesso=False, mensagem=_mensagem_com_notas(e))

    return ResultadoEtapa("motor_de_regras", sucesso=True, dados=grupos)


async def _acao_abrir(page, linha: dict) -> dict:
    resultado = await tracknme_bot.abrir_incidente(page, linha["placa"], linha["cliente"])
    return {"linha": linha, "resultado": resultado}


async def _acao_concluir(page, linha: dict) -> dict:
    resultado = await tracknme_bot.concluir_incidente(
        page, linha["placa"], linha["observacao_sistema"],
        numero_incidente=linha.get("id") or None,
    )
    return {"linha": linha, "resultado": resultado}


async def _processar_fila_com_navegador(
    itens,
    acao,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    descrever_item: Callable[[Any], str] | None = None,
    on_worker_status: Callable[[int, str], None] | None = None,
    eh_erro_definitivo: Callable[[Exception], bool] | None = None,
) -> tuple[
    list, str | None, "playwright_utils.AguardandoReconexao | None", "playwright_utils.CancelamentoSolicitado | None"
]:
    """Abre um navegador autenticado do Track N' Me, roda `processar_fila`
    e sempre fecha `context`/`browser` no `finally` — mesmo esqueleto
    reaproveitado por `etapa_abrir_incidentes_automaticos` e
    `etapa_fechar_incidentes_automaticos` (antes vivia junto numa função
    só, `etapa_reconciliacao_automatica` — dividida em duas pra caber a
    consulta ao SGA no meio, 2026-08-07). Retorna `(resultados, erro,
    reconexao, cancelamento)`: `reconexao` não-`None` significa que
    `processar_fila` detectou sessão caída (`AguardandoReconexao`, com
    `.pendentes`/`.processados` preservados) — capturada ANTES do `except
    Exception` genérico, senão esses atributos se perderiam;
    `cancelamento` não-`None` é o mesmo espírito, pra
    `CancelamentoSolicitado`; `erro` não-`None` é qualquer outra falha,
    quem chamou deve devolver `ResultadoEtapa(sucesso=False,
    mensagem=erro)` sem tentar ler `resultados`. `on_progresso`/
    `cancelar_checker` (opcionais) repassam direto pra
    `playwright_utils.processar_fila` — ver docstring de lá.

    `descrever_item`/`on_worker_status` (opcionais): quando informados,
    monta o `on_item_iniciado(worker_id, item)` de `processar_fila`
    convertendo `item` num texto legível (`descrever_item`, default
    `str`) antes de repassar pro chamador — `playwright_utils` continua
    sem saber a forma do item (`linha` dict pra incidentes, chassi string
    pro SGA), só quem chama aqui sabe."""
    on_item_iniciado = None
    if on_worker_status is not None:
        descrever = descrever_item or str
        on_item_iniciado = lambda worker_id, item: on_worker_status(worker_id, descrever(item))  # noqa: E731

    try:
        async with async_playwright() as playwright:
            browser, context = await tracknme_bot.abrir_navegador_autenticado(playwright)
            try:
                resultados = await playwright_utils.processar_fila(
                    context, itens, acao, on_progresso=on_progresso, cancelar_checker=cancelar_checker,
                    on_item_iniciado=on_item_iniciado, eh_erro_definitivo=eh_erro_definitivo,
                )
            finally:
                await context.close()
                await browser.close()
    except playwright_utils.AguardandoReconexao as e:
        return [], None, e, None
    except playwright_utils.CancelamentoSolicitado as e:
        return [], None, None, e
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return [], str(e), None, None
    return resultados, None, None, None


def _descrever_linha_incidente(linha: dict) -> str:
    return f"Placa {linha.get('placa', '—')} — {linha.get('cliente', '—')}"


def _descrever_chassi_sga(chassi: str) -> str:
    return f"Chassi {chassi}"


def _falhas_com_descricao(resultados: list, descrever_item: Callable[[Any], str]) -> list[dict]:
    """Formato comum de `dados["falhas"]` (item cru + erro + descrição
    legível) — usado por `etapa_enriquecimento_sga` nos 3 desfechos
    (sucesso parcial, reconexão, cancelamento)."""
    return [
        {"item": r.item, "erro": r.erro, "descricao": descrever_item(r.item)}
        for r in resultados if not r.sucesso
    ]


async def _acao_abrir_http(contexto_http: "tracknme_bot.ContextoHttp", linha: dict) -> dict:
    resultado = await tracknme_bot.abrir_incidente_http(contexto_http, linha["placa"], linha["cliente"])
    return {"linha": linha, "resultado": resultado}


async def _acao_concluir_http(contexto_http: "tracknme_bot.ContextoHttp", linha: dict) -> dict:
    resultado = await tracknme_bot.concluir_incidente_http(
        contexto_http, linha["placa"], linha["observacao_sistema"],
        numero_incidente=linha.get("id") or None,
    )
    return {"linha": linha, "resultado": resultado}


def _avaliar_circuit_breaker_tracknme_http(resultados_canario: list, parametros: dict, etapa: str = "") -> dict | None:
    """Mesmo espírito do circuit breaker do SGA (`_avaliar_circuit_
    breaker_sga_http`), mas só com a perna de falha técnica — "veículo
    não encontrado" aqui já é tratado como falha de item normal
    (`RuntimeError`, mesma semântica do caminho Playwright de hoje), não
    é uma resposta válida separada como o "não encontrado" do SGA, então
    não precisa de um limiar próprio.

    **Achado ao vivo 2026-08-19** (teste de escala, 100 candidatos reais,
    concorrência 100): 8% das "falhas" eram `IncidenteDuplicadoError`
    (device já tinha incidente aberto — a API corretamente recusando,
    0% de falha técnica de verdade). Isso É um resultado de negócio
    esperado, não falha técnica — excluído do cálculo via `tracknme_bot.
    eh_erro_de_negocio_esperado` (senão o circuit breaker abortaria o
    caminho HTTP à toa sempre que a taxa natural de duplicado/ambíguo
    passasse do limiar técnico, que é bem mais apertado)."""
    total = len(resultados_canario)
    if total == 0:
        return None
    falha_tecnica = sum(
        1 for r in resultados_canario
        if not r.sucesso and not tracknme_bot.eh_erro_de_negocio_esperado(r.erro)
    )
    erro_negocio_esperado = sum(
        1 for r in resultados_canario
        if not r.sucesso and tracknme_bot.eh_erro_de_negocio_esperado(r.erro)
    )
    taxa_falha_tecnica = falha_tecnica / total
    limiar_falha_tecnica = float(parametros.get("tracknme_http_limiar_falha_tecnica", 0.05))
    abortado = taxa_falha_tecnica > limiar_falha_tecnica

    _registrar_diagnostico_circuit_breaker("diagnostico_circuit_breaker_tracknme_http.jsonl", {
        "etapa": etapa,
        "total_canario": total,
        "falha_tecnica": falha_tecnica,
        "erro_negocio_esperado": erro_negocio_esperado,
        "taxa_falha_tecnica": taxa_falha_tecnica,
        "limiar_falha_tecnica": limiar_falha_tecnica,
        "abortado": abortado,
    })

    if abortado:
        return {"motivo": "taxa_falha_tecnica", "taxa_falha_tecnica": taxa_falha_tecnica}
    return None


async def _etapa_incidente_2_estagios(
    etapa: str,
    itens: list[dict],
    acao_http: Callable[["tracknme_bot.ContextoHttp", dict], Any],
    acao_playwright: Callable[[Any, dict], Any],
    chave_sucesso: str,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    on_worker_status: Callable[[int, str], None] | None = None,
) -> ResultadoEtapa:
    """Achado 2026-08-19: mesmo padrão de 2 estágios já usado em `etapa_
    enriquecimento_sga` (Estágio HTTP com canário + circuit breaker,
    fallback Estágio Playwright), reaproveitado aqui por `etapa_abrir_
    incidentes_automaticos`/`etapa_fechar_incidentes_automaticos`.
    Diferente do SGA, aqui não existe uma divisão permanente de tipo por
    item (chassi confirmado vs placa) — TODO item tenta o Estágio HTTP
    primeiro (se o kill switch estiver ligado); a única divisão é
    temporária (canário vs resto, decidida pelo circuit breaker). Por
    isso `pendentes` (sempre uma lista de `linha` dicts, autocontida) já
    basta pra `orchestrator.catalogo_etapas.retomar_etapa` reprocessar do
    zero, sem precisar de um `alvos_override` equivalente ao do SGA.

    Kill switch `tracknme_http_habilitado` (`system_parameters`, default
    desligado) — com ele desligado, o fluxo é 100% Estágio Playwright,
    idêntico ao existente antes desta mudança.

    `_eh_erro_definitivo` (achado 2026-08-25, revisão da decisão antiga
    de 2026-08-06 — volume real pós-reset, 1671 itens, ~90% duplicado no
    canário): repassado pra `processar_fila_http`/`processar_fila`
    (HTTP e Playwright) — `IncidenteDuplicadoError`/
    `MultiplosIncidentesAbertosError`/"incidente já está..." (`tracknme_
    bot.eh_erro_de_negocio_esperado`) são resultado de negócio
    DETERMINÍSTICO, nunca mudam tentando de novo — não gastam as
    tentativas restantes nem entram no round 2. Antes disso, esses erros
    seguiam o retry padrão igual qualquer falha técnica, o que em
    volume alto dobrava o tempo de execução à toa.
    """
    parametros = supabase_client.buscar_parametros()

    def _eh_erro_definitivo(e: Exception) -> bool:
        return tracknme_bot.eh_erro_de_negocio_esperado(str(e))

    on_item_iniciado = None
    if on_worker_status is not None:
        on_item_iniciado = lambda worker_id, linha: on_worker_status(worker_id, _descrever_linha_incidente(linha))  # noqa: E731

    resultados_totais: list = []
    http_abortado: dict | None = None
    itens_playwright_pendentes = list(itens)

    def _resultado_final(
        sucesso: bool, mensagem: str = "",
        aguardando_reconexao: dict | None = None, cancelado: dict | None = None,
    ) -> ResultadoEtapa:
        dados = {
            chave_sucesso: [r.resultado["linha"] for r in resultados_totais if r.sucesso],
            "falhas": [
                {"linha": r.item, "erro": r.erro, "descricao": _descrever_linha_incidente(r.item)}
                for r in resultados_totais if not r.sucesso
            ],
        }
        if http_abortado is not None:
            dados["tracknme_http_abortado"] = http_abortado
        return ResultadoEtapa(
            etapa, sucesso=sucesso, mensagem=mensagem, dados=dados,
            aguardando_reconexao=aguardando_reconexao, cancelado=cancelado,
        )

    if bool(parametros.get("tracknme_http_habilitado", False)):
        tamanho_canario = min(int(parametros.get("tracknme_http_tamanho_canario", 20)), len(itens))
        canario, resto = itens[:tamanho_canario], itens[tamanho_canario:]
        concorrencia = int(parametros.get("tracknme_http_concorrencia", 10))
        timeout_segundos = int(parametros.get("tracknme_http_timeout_base_ms", 30000)) / 1000
        fase_http_atual = "canario"
        itens_playwright_pendentes = []

        try:
            contexto_http = await tracknme_bot.preparar_contexto_http()
            try:
                resultados_canario = await playwright_utils.processar_fila_http(
                    contexto_http, canario, acao_http, concorrencia=concorrencia,
                    on_progresso=on_progresso, cancelar_checker=cancelar_checker,
                    on_item_iniciado=on_item_iniciado, timeout_segundos=timeout_segundos,
                    eh_erro_definitivo=_eh_erro_definitivo,
                )
                resultados_totais.extend(resultados_canario)

                http_abortado = _avaliar_circuit_breaker_tracknme_http(resultados_canario, parametros, etapa)
                if http_abortado is not None:
                    itens_playwright_pendentes = resto
                elif resto:
                    fase_http_atual = "resto"
                    resultados_resto = await playwright_utils.processar_fila_http(
                        contexto_http, resto, acao_http, concorrencia=concorrencia,
                        on_progresso=on_progresso, cancelar_checker=cancelar_checker,
                        on_item_iniciado=on_item_iniciado, timeout_segundos=timeout_segundos,
                        eh_erro_definitivo=_eh_erro_definitivo,
                    )
                    resultados_totais.extend(resultados_resto)
            finally:
                await contexto_http.cliente.aclose()
        except playwright_utils.AguardandoReconexao as e:
            resultados_totais.extend(e.processados)
            pendentes = list(e.pendentes) + (resto if fase_http_atual == "canario" else [])
            return _resultado_final(
                False, f"Sessão caída — aguardando reconexão manual ({len(pendentes)} pendente(s)).",
                aguardando_reconexao={"pendentes": pendentes},
            )
        except playwright_utils.CancelamentoSolicitado as e:
            resultados_totais.extend(e.processados)
            pendentes = list(e.pendentes) + (resto if fase_http_atual == "canario" else [])
            return _resultado_final(
                False, f"Cancelado pelo usuário ({len(pendentes)} pendente(s)).",
                cancelado={"pendentes": pendentes},
            )
        except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
            return ResultadoEtapa(etapa, sucesso=False, mensagem=_mensagem_com_notas(e))

    if itens_playwright_pendentes:
        resultados_pw, erro, reconexao, cancelamento = await _processar_fila_com_navegador(
            itens_playwright_pendentes, acao_playwright, on_progresso=on_progresso,
            cancelar_checker=cancelar_checker, descrever_item=_descrever_linha_incidente,
            on_worker_status=on_worker_status, eh_erro_definitivo=_eh_erro_definitivo,
        )
        if reconexao is not None:
            resultados_totais.extend(reconexao.processados)
            return _resultado_final(
                False, f"Sessão caída — aguardando reconexão manual ({len(reconexao.pendentes)} pendente(s)).",
                aguardando_reconexao={"pendentes": list(reconexao.pendentes)},
            )
        if cancelamento is not None:
            resultados_totais.extend(cancelamento.processados)
            return _resultado_final(
                False, f"Cancelado pelo usuário ({len(cancelamento.pendentes)} pendente(s)).",
                cancelado={"pendentes": list(cancelamento.pendentes)},
            )
        if erro is not None:
            return ResultadoEtapa(etapa, sucesso=False, mensagem=erro)
        resultados_totais.extend(resultados_pw)

    return _resultado_final(True)


async def etapa_abrir_incidentes_automaticos(
    dados: dict | None = None,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    on_worker_status: Callable[[int, str], None] | None = None,
) -> ResultadoEtapa:
    """`dados` é o retorno de `etapa_motor_de_regras().dados` (só lê
    `grupo_1_abrir`). Se `None` (rodando isolada no painel), chama
    `etapa_motor_de_regras()` direto — `grupo_1_abrir` é efêmero, não
    existe planilha/disco pra reler como nas fases anteriores.

    **2 ESTÁGIOS, achado 2026-08-19** (mesmo padrão do SGA, ver
    `_etapa_incidente_2_estagios`): Estágio HTTP (`tracknme_bot.
    abrir_incidente_http`, kill switch `tracknme_http_habilitado`) com
    canário + circuit breaker, fallback Estágio Playwright (`tracknme_bot.
    abrir_incidente`, como já era antes desta mudança). Com o kill switch
    desligado (default), o fluxo é 100% Estágio Playwright, idêntico ao
    existente antes desta mudança.

    `on_progresso`/`cancelar_checker` (opcionais) repassam pro
    `processar_fila`/`processar_fila_http` subjacente. `on_worker_status`
    (opcional) reporta o que cada worker está processando agora
    (`"Placa X — Cliente Y"`)."""
    if dados is None:
        resultado_motor = etapa_motor_de_regras()
        if not resultado_motor.sucesso:
            return ResultadoEtapa(
                "abrir_incidentes_automaticos", sucesso=False, mensagem=resultado_motor.mensagem
            )
        dados = resultado_motor.dados

    return await _etapa_incidente_2_estagios(
        "abrir_incidentes_automaticos", dados["grupo_1_abrir"], _acao_abrir_http, _acao_abrir, "abertos",
        on_progresso=on_progresso, cancelar_checker=cancelar_checker, on_worker_status=on_worker_status,
    )


async def etapa_fechar_incidentes_automaticos(
    dados: dict | None = None,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    on_worker_status: Callable[[int, str], None] | None = None,
) -> ResultadoEtapa:
    """`dados` é o retorno de `etapa_consolidar_com_sga().dados` (só lê
    `grupo_2_concluir`, já recalculado com o SGA). Se `None` (rodando
    isolada), chama `etapa_consolidar_com_sga()` direto — mesma lógica de
    default das outras etapas efêmeras.

    `motivo` de `concluir_incidente`/`concluir_incidente_http` =
    `observacao_sistema` da linha; `numero_incidente` = `id` da linha
    (confirmado que é o mesmo número que a tela Operador busca) — evita
    ambiguidade quando a placa tem mais de um incidente aberto.
    `IncidenteDuplicadoError`/`MultiplosIncidentesAbertosError` (revisão
    2026-08-25 da decisão original de 2026-08-06) agora têm tratamento
    diferenciado — são erro de negócio determinístico
    (`_eh_erro_definitivo`, `_etapa_incidente_2_estagios`), param na
    primeira ocorrência, sem gastar retry à toa.

    **2 ESTÁGIOS, achado 2026-08-19** — mesmo padrão de `etapa_abrir_
    incidentes_automaticos`/`_etapa_incidente_2_estagios`, usando
    `tracknme_bot.concluir_incidente_http`/`concluir_incidente`. Achado
    importante sobre `concluir_incidente_http`: a conclusão real não é 1
    chamada só, é uma sequência de 4 (Atribuir -> Alterar situação ->
    Acompanhamento -> Concluir) — ver docstring da função.

    `on_progresso`/`cancelar_checker` (opcionais) repassam pro
    `processar_fila`/`processar_fila_http` subjacente. `on_worker_status`
    (opcional) reporta o que cada worker está processando agora.
    """
    if dados is None:
        resultado_consolidacao = await etapa_consolidar_com_sga()
        if not resultado_consolidacao.sucesso:
            return ResultadoEtapa(
                "fechar_incidentes_automaticos", sucesso=False, mensagem=resultado_consolidacao.mensagem
            )
        dados = resultado_consolidacao.dados

    return await _etapa_incidente_2_estagios(
        "fechar_incidentes_automaticos", dados["grupo_2_concluir"], _acao_concluir_http, _acao_concluir, "concluidos",
        on_progresso=on_progresso, cancelar_checker=cancelar_checker, on_worker_status=on_worker_status,
    )


def _placas_genericas(parametros: dict) -> list[str]:
    """Mesmo split de `system_parameters.placas_genericas` (texto CSV) já
    feito em `core.motor_regras._placas_genericas`/`integrations.
    supabase_client` — duplicado aqui de propósito (função privada de
    outro módulo, não é pra importar através da linha de underscore)."""
    valor = parametros.get("placas_genericas", "")
    if isinstance(valor, list):
        return valor
    return [item.strip() for item in str(valor).split(",") if item.strip()]


def _alvos_consulta_sga(
    dados_classificacao: dict, instalacao_remocao: list[dict], parametros: dict
) -> dict[str, tuple[str, str]]:
    """Decide, ANTES de consultar o SGA, o que buscar por veículo — nunca
    o identificador de dedup cru (achado 2026-08-16: `core.motor_regras.
    _resolver_chassi` às vezes usa o IMEI ou a placa normalizada como
    identificador só pra dedup interno, e o SGA tem campos Chassi/Placa
    INDEPENDENTES — mandar um IMEI pro campo Chassi não faz sentido).

    Devolve `{identificador_dedup (upper): (tipo, valor)}` — a chave
    continua sendo o identificador de dedup (mesma chave usada em
    `situacoes_sga`/`aplicar_situacoes_sga`/`classificar_instalacao_
    remocao`, só pra bookkeeping interno), mas `(tipo, valor)` é sempre
    algo que o SGA de fato aceita: `TIPO_IDENTIFICADOR_CHASSI` com
    `linha["chassi_sga"]` (chassi CONFIRMADO via cadastro, ver
    `core.motor_regras._montar_linha_resultado`/`_equipamento_para_
    abertura`) quando existe; senão `TIPO_IDENTIFICADOR_PLACA` com a placa
    do incidente normalizada (só se válida — não genérica/fictícia).
    Veículo sem chassi confirmado e sem placa válida fica de fora do
    dict — não há nada confiável pra digitar em nenhum dos 2 campos, e
    "não consultado" já é tratado graciosamente por quem consome
    `situacoes_sga` (mantém a classificação original).

    Pra Instalação-Remoção, o "Chassi" da própria aba é sempre tratado
    como confirmado (decisão já existente, `core.motor_regras_
    instalacao_remocao`: chassi sempre presente nessa aba)."""
    genericas = _placas_genericas(parametros)
    alvos: dict[str, tuple[str, str]] = {}
    for linha in (
        dados_classificacao["grupo_1_abrir"]
        + dados_classificacao["grupo_2_concluir"]
        + dados_classificacao["grupo_3_tratativa_humana"]
    ):
        chave = (linha.get("chassi") or "").strip().upper()
        if not chave:
            continue
        chassi_sga = (linha.get("chassi_sga") or "").strip().upper()
        if chassi_sga:
            alvos.setdefault(chave, (TIPO_IDENTIFICADOR_CHASSI, chassi_sga))
            continue
        placa_valida = normalizar_placa(linha.get("placa", ""), genericas)
        if placa_valida:
            alvos.setdefault(chave, (TIPO_IDENTIFICADOR_PLACA, placa_valida))
    for registro in instalacao_remocao:
        chassi = (registro.get("Chassi") or "").strip().upper()
        if chassi:
            alvos.setdefault(chassi, (TIPO_IDENTIFICADOR_CHASSI, chassi))
    return alvos


def _situacoes_veiculo_sga_recentes(
    chaves: list[str], parametros: dict, agora: datetime
) -> dict[str, dict]:
    """Checkpoint (achado 2026-08-17): antes de consultar o SGA ao vivo,
    lê em 1 lote só (`buscar_situacoes_veiculo_sga_em_lote`) quem entre
    `chaves` já foi atualizado há menos de `tempo_limiar_atualizacao_sga_
    horas` (`system_parameters`, default 24h) — esses ficam de fora da
    consulta ao vivo desta vez. Sem isso, TODA execução reconsultava
    TODOS os veículos conhecidos, mesmo os checados minutos atrás — na
    escala real (milhares de registros de Instalação-Remoção) isso
    sozinho já bastava pra esticar a Fase D por horas.

    Retorna `{chave: registro}` só de quem está recente o bastante pra
    pular. Uma falha de rede aqui não pode derrubar a etapa nem esconder
    veículos da consulta por engano — em caso de erro, devolve `{}`
    (nenhum é tratado como recente, comportamento equivalente ao de antes
    do checkpoint existir: consulta tudo)."""
    if not chaves:
        return {}
    try:
        conhecidas = supabase_client.buscar_situacoes_veiculo_sga_em_lote(chaves)
    except Exception:  # noqa: BLE001 - checkpoint é otimização, nunca motivo de falha da etapa
        return {}

    limiar_horas = float(parametros.get("tempo_limiar_atualizacao_sga_horas", 24))
    recentes = {}
    for chave, registro in conhecidas.items():
        momento = registro.get("atualizado_em")
        if not momento:
            continue
        # `buscar_situacoes_veiculo_sga_em_lote` (achado 2026-08-25) já
        # devolve `atualizado_em` convertido pra `datetime` -- nunca mais
        # string ISO crua aqui, então nada de `fromisoformat` de novo.
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=timezone.utc)
        if (agora - momento).total_seconds() < limiar_horas * 3600:
            recentes[chave] = registro
    return recentes


def _formatar_situacoes_recentes(recentes: dict[str, dict]) -> dict[str, dict]:
    """`situacoes_veiculo_sga` (linha crua do Supabase) -> mesmo formato de
    `_persistir_situacoes_sga` ({status, desde, cidade, bairro,
    encontrado_via}). Cidade/bairro (Bloco C1, 2026-08-24) já vêm
    persistidos no checkpoint — `.get(..., "")` só cobre linha antiga
    gravada antes deste fix, sem esses campos ainda."""
    return {
        chave: {
            "status": registro.get("status"),
            "desde": registro.get("desde"),
            "cidade": registro.get("cidade", ""),
            "bairro": registro.get("bairro", ""),
            "encontrado_via": registro.get("encontrado_via"),
        }
        for chave, registro in recentes.items()
    }


def _persistir_situacoes_sga(resultados: list, agora: datetime) -> tuple[dict, list[dict]]:
    """Persiste em `situacao_veiculo_sga` (achado 2026-08-17: 1 leitura +
    1 gravação EM LOTE — a versão anterior fazia 3 idas ao Supabase POR
    chassi, o maior motivo das execuções de horas na Fase D em escala
    real) e devolve `({chassi: {status, desde, cidade, bairro,
    encontrado_via}}, falhas_persistencia)` — corpo compartilhado entre o
    caminho normal e o de reconexão parcial de `etapa_enriquecimento_sga`.

    NUNCA levanta exceção (contrato da etapa: nenhuma falha de rede pode
    escapar até a UI) — se a leitura ou a gravação em lote falhar, os
    resultados da consulta ao SGA ainda são devolvidos normalmente (são
    válidos pra uso imediato nesta mesma execução, ver `aplicar_situacoes_
    sga`/`classificar_instalacao_remocao`), só não ficam persistidos pra
    a próxima vez — `falhas_persistencia` sinaliza isso, nunca falha
    silenciosa. `encontrado_via` ("chassi"/"placa", ver `integrations.
    sga_bot.consultar_situacao`) é só repassado, não afeta `desde`."""
    sucesso_itens = [r for r in resultados if r.sucesso]
    if not sucesso_itens:
        return {}, []

    chassis = [r.item for r in sucesso_itens]
    try:
        anteriores = supabase_client.buscar_situacoes_veiculo_sga_em_lote(chassis)
    except Exception:  # noqa: BLE001 - leitura falhou, segue sem histórico anterior
        anteriores = {}

    atualizados: dict[str, dict] = {}
    for r in sucesso_itens:
        chassi = r.item
        atualizados[chassi] = motor_regras_instalacao_remocao.atualizar_situacao_sga(
            chassi, r.resultado["status"], anteriores.get(chassi), agora,
            encontrado_via=r.resultado.get("encontrado_via"),
            cidade=r.resultado.get("cidade", ""), bairro=r.resultado.get("bairro", ""),
        )

    falhas_persistencia: list[dict] = []
    try:
        supabase_client.upsert_situacoes_veiculo_sga_em_lote(list(atualizados.values()))
    except Exception as e:  # noqa: BLE001 - gravação falhou, resultado ao vivo não pode se perder
        falhas_persistencia = [
            {"item": chassi, "erro": str(e), "descricao": _descrever_chassi_sga(chassi)}
            for chassi in atualizados
        ]

    situacoes_sga = {
        chassi: {
            "status": atualizados[chassi]["status"],
            "desde": atualizados[chassi]["desde"],
            "cidade": r.resultado["cidade"],
            "bairro": r.resultado["bairro"],
            "encontrado_via": atualizados[chassi]["encontrado_via"],
        }
        for r in sucesso_itens
        for chassi in [r.item]
    }
    return situacoes_sga, falhas_persistencia


def _registrar_diagnostico_circuit_breaker(nome_arquivo: str, diagnostico: dict) -> None:
    """Log resiliente (2026-08-25, achado da sessão anterior: fechar a
    janela do Painel Operador no meio de uma etapa mata o processo sem
    nenhum resultado ser persistido — nem Supabase, nem `execucoes.log`,
    nem o resultado da etapa em si — então a decisão de um circuit
    breaker pode se perder por completo se algo interromper a execução
    depois dela). Escreve UMA linha JSON em `logs/{nome_arquivo}`, na
    hora exata da decisão — antes de qualquer chamada de rede adicional
    (Playwright/resto do HTTP) que possa travar/cair depois. Reaproveitada
    pelos 2 circuit breakers (SGA e Track N'Me). Nunca derruba a etapa
    real (mesmo espírito de `catalogo_etapas._registrar_log_arquivo`)."""
    try:
        caminho = _diretorio_logs() / nome_arquivo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as f:
            f.write(json.dumps({**diagnostico, "registrado_em": datetime.now().isoformat()}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - diagnóstico nunca pode derrubar a etapa real
        pass


def _avaliar_circuit_breaker_sga_http(resultados_canario: list, parametros: dict) -> dict | None:
    """Acha se o Estágio HTTP (achado 2026-08-19) deve ser abortado pro
    resto da execução, baseado na taxa de "não encontrado"/falha técnica
    num lote canário pequeno processado antes do resto — cobre o risco
    real de o SGA mudar a ofuscação do `key`/layout do HTML sem aviso
    (não é API documentada). Baseline real medido em escala completa
    (5953 veículos): ~2.35% de "não encontrado" (veículo órfão/pseudo-
    chassi, já diagnosticado à parte), ~0% de falha técnica —
    `sga_http_limiar_nao_encontrado`/`_limiar_falha_tecnica` (system_
    parameters, ver `_handoff/sql_sga_http_parametros.sql`) dão margem
    generosa acima disso antes de considerar anômalo. Devolve `None` se
    dentro do limiar (segue com HTTP normalmente) ou um dict com o motivo
    do abort."""
    total = len(resultados_canario)
    if total == 0:
        return None
    nao_encontrado = sum(
        1 for r in resultados_canario
        if r.sucesso and r.resultado.get("status") == sga_bot.STATUS_NAO_ENCONTRADO
    )
    falha_tecnica = sum(1 for r in resultados_canario if not r.sucesso)
    taxa_nao_encontrado = nao_encontrado / total
    taxa_falha_tecnica = falha_tecnica / total
    limiar_nao_encontrado = float(parametros.get("sga_http_limiar_nao_encontrado", 0.15))
    limiar_falha_tecnica = float(parametros.get("sga_http_limiar_falha_tecnica", 0.05))

    if taxa_falha_tecnica > limiar_falha_tecnica:
        motivo = "taxa_falha_tecnica"
    elif taxa_nao_encontrado > limiar_nao_encontrado:
        motivo = "taxa_nao_encontrado"
    else:
        motivo = None

    _registrar_diagnostico_circuit_breaker("diagnostico_circuit_breaker_sga_http.jsonl", {
        "total_canario": total,
        "nao_encontrado": nao_encontrado,
        "falha_tecnica": falha_tecnica,
        "taxa_nao_encontrado": taxa_nao_encontrado,
        "taxa_falha_tecnica": taxa_falha_tecnica,
        "limiar_nao_encontrado": limiar_nao_encontrado,
        "limiar_falha_tecnica": limiar_falha_tecnica,
        "abortado": motivo is not None,
        "motivo": motivo,
    })

    if motivo is None:
        return None
    return {"motivo": motivo, "taxa_nao_encontrado": taxa_nao_encontrado, "taxa_falha_tecnica": taxa_falha_tecnica}


async def etapa_enriquecimento_sga(
    dados_classificacao: dict | None = None,
    instalacao_remocao: list[dict] | None = None,
    chassis_override: list[str] | None = None,
    alvos_override: dict[str, tuple[str, str]] | None = None,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    on_worker_status: Callable[[int, str], None] | None = None,
) -> ResultadoEtapa:
    """Único login manual real (SGA exige captcha) — cobre Manutenção,
    Instalação e Remoção numa consulta só, decisão do usuário: `dados_
    classificacao` (default: `etapa_motor_de_regras()`) dá os veículos de
    Manutenção; `instalacao_remocao` (default: `ler_aba(...)`) dá os de
    Instalação/Remoção. `chassis_override`/`alvos_override` (usados só na
    retomada depois de uma reconexão manual) ignoram os dois defaults
    caros e consultam só os identificadores informados — nunca rechama
    `etapa_motor_de_regras()`/`ler_aba(...)` nesse caminho (e nesse
    caminho também não passa pelo checkpoint de novo — já passou na
    tentativa original).

    Pra cada veículo candidato (`_alvos_consulta_sga` decide Chassi
    confirmado via cadastro ou Placa válida — achado 2026-08-16: nunca
    manda o identificador de dedup cru, que pode ser um IMEI, pro campo
    Chassi), primeiro passa pelo CHECKPOINT (achado 2026-08-17,
    `_situacoes_veiculo_sga_recentes`): quem já foi atualizado há menos de
    `tempo_limiar_atualizacao_sga_horas` (default 24h) nem entra na fila
    de consulta ao vivo — reusa o último status conhecido.

    **2 ESTÁGIOS sequenciais, achado 2026-08-19** (dentro da mesma sessão,
    1 login manual só): Estágio HTTP — só chassi confirmado, via
    `sga_bot.consultar_situacao_http` (~37.5x mais rápido que o navegador,
    ver memória `sga_api_http_viabilidade_confirmada`), controlado por
    `system_parameters.sga_http_habilitado` (kill switch, default
    desligado) — processa um lote canário primeiro e usa
    `_avaliar_circuit_breaker_sga_http` pra decidir se continua ou aborta
    o resto pro Playwright (SGA pode mudar sem aviso, não é API
    documentada). Estágio Playwright — busca por Placa (nunca validada
    via HTTP) e qualquer sobra do circuit breaker/kill switch desligado —
    reabre o navegador a partir da MESMA sessão salva no Estágio HTTP
    (`sga_bot.abrir_contexto_com_sessao`, sem captcha de novo) quando o
    Estágio HTTP rodou; senão faz o login manual normal. Cada estágio só
    roda se tiver algo pra fazer — com o kill switch desligado (default),
    o fluxo é 100% Estágio Playwright, idêntico ao existente antes desta
    mudança.

    Se a sessão do SGA cair no meio da consulta (`AguardandoReconexao`,
    em QUALQUER estágio), os identificadores que já tinham resultado ANTES
    da queda são persistidos mesmo assim — só os `pendentes` (inclusive o
    que nunca chegou a ser tentado no estágio seguinte) voltam pra tela
    pedir reconexão manual. `dados["falhas"]` lista os identificadores que
    não tiveram sucesso na consulta OU cujo resultado não conseguiu ser
    gravado no Supabase (`_persistir_situacoes_sga` nunca perde isso em
    silêncio nem levanta exceção). `dados["alvos_consulta_sga"]` é sempre
    incluído (mesmo nos desfechos de interrupção) só pra
    `orchestrator.catalogo_etapas.retomar_etapa` repassar como
    `alvos_override` na retomada — que re-divide os pendentes entre os 2
    estágios do zero, sem precisar saber de qual estágio cada um veio.
    """
    situacoes_puladas: dict[str, dict] = {}
    if chassis_override is not None:
        alvos = alvos_override or {}
        parametros = supabase_client.buscar_parametros()
    else:
        if dados_classificacao is None:
            resultado_motor = etapa_motor_de_regras()
            if not resultado_motor.sucesso:
                return ResultadoEtapa("enriquecimento_sga", sucesso=False, mensagem=resultado_motor.mensagem)
            dados_classificacao = resultado_motor.dados
        if instalacao_remocao is None:
            instalacao_remocao = google_sheets_client.ler_aba(
                google_sheets_client.NOME_PLANILHA_ADMINISTRADOR, "Instalação-Remoção"
            )
        parametros = supabase_client.buscar_parametros()
        todos_alvos = _alvos_consulta_sga(dados_classificacao, instalacao_remocao, parametros)
        recentes = _situacoes_veiculo_sga_recentes(
            list(todos_alvos.keys()), parametros, datetime.now(timezone.utc)
        )
        situacoes_puladas = _formatar_situacoes_recentes(recentes)
        alvos = {chave: alvo for chave, alvo in todos_alvos.items() if chave not in recentes}

    on_item_iniciado = None
    if on_worker_status is not None:
        on_item_iniciado = lambda worker_id, chassi: on_worker_status(worker_id, _descrever_chassi_sga(chassi))  # noqa: E731

    if bool(parametros.get("sga_http_habilitado", False)):
        alvos_chassi = {c: v for c, v in alvos.items() if v[0] == TIPO_IDENTIFICADOR_CHASSI}
        alvos_playwright_pendentes = {c: v for c, v in alvos.items() if v[0] != TIPO_IDENTIFICADOR_CHASSI}
    else:
        alvos_chassi = {}
        alvos_playwright_pendentes = dict(alvos)

    async def _consultar_http(request_context, chave):
        tipo, valor = alvos_chassi[chave]
        return await sga_bot.consultar_situacao_http(request_context, tipo, valor)

    async def _consultar_playwright(page, chave):
        tipo, valor = alvos_playwright_pendentes[chave]
        return await sga_bot.consultar_situacao(page, tipo, valor)

    resultados_totais: list = []
    sga_http_abortado: dict | None = None
    estado_sessao_sga: dict | None = None

    def _resultado_final(
        sucesso: bool, mensagem: str = "",
        aguardando_reconexao: dict | None = None, cancelado: dict | None = None,
    ) -> ResultadoEtapa:
        situacoes_sga, falhas_persistencia = _persistir_situacoes_sga(resultados_totais, datetime.now())
        dados = {
            "situacoes_sga": {**situacoes_puladas, **situacoes_sga},
            "falhas": _falhas_com_descricao(resultados_totais, _descrever_chassi_sga) + falhas_persistencia,
            "alvos_consulta_sga": alvos,
        }
        if sga_http_abortado is not None:
            dados["sga_http_abortado"] = sga_http_abortado
        return ResultadoEtapa(
            "enriquecimento_sga", sucesso=sucesso, mensagem=mensagem, dados=dados,
            aguardando_reconexao=aguardando_reconexao, cancelado=cancelado,
        )

    if alvos_chassi:
        chassis_chassi = sorted(alvos_chassi.keys())
        tamanho_canario = min(int(parametros.get("sga_http_tamanho_canario", 200)), len(chassis_chassi))
        canario, resto = chassis_chassi[:tamanho_canario], chassis_chassi[tamanho_canario:]
        concorrencia = int(parametros.get("sga_http_concorrencia", 80))
        timeout_segundos = int(parametros.get("sga_http_timeout_base_ms", 30000)) / 1000
        fase_http_atual = "canario"

        try:
            async with async_playwright() as playwright:
                browser, context = await sga_bot.aguardar_login_manual(playwright)
                request_context, estado_sessao_sga = await sga_bot.preparar_contexto_http(playwright, browser, context)
                try:
                    resultados_canario = await playwright_utils.processar_fila_http(
                        request_context, canario, _consultar_http, concorrencia=concorrencia,
                        on_progresso=on_progresso, cancelar_checker=cancelar_checker,
                        on_item_iniciado=on_item_iniciado, timeout_segundos=timeout_segundos,
                    )
                    resultados_totais.extend(resultados_canario)

                    sga_http_abortado = _avaliar_circuit_breaker_sga_http(resultados_canario, parametros)
                    if sga_http_abortado is not None:
                        alvos_playwright_pendentes.update({c: alvos_chassi[c] for c in resto})
                    elif resto:
                        fase_http_atual = "resto"
                        resultados_resto = await playwright_utils.processar_fila_http(
                            request_context, resto, _consultar_http, concorrencia=concorrencia,
                            on_progresso=on_progresso, cancelar_checker=cancelar_checker,
                            on_item_iniciado=on_item_iniciado, timeout_segundos=timeout_segundos,
                        )
                        resultados_totais.extend(resultados_resto)
                finally:
                    await request_context.dispose()
        except playwright_utils.AguardandoReconexao as e:
            resultados_totais.extend(e.processados)
            pendentes = (
                list(e.pendentes) + (resto if fase_http_atual == "canario" else [])
                + list(alvos_playwright_pendentes.keys())
            )
            return _resultado_final(
                False, f"Sessão caída — aguardando reconexão manual ({len(pendentes)} pendente(s)).",
                aguardando_reconexao={"pendentes": pendentes},
            )
        except playwright_utils.CancelamentoSolicitado as e:
            resultados_totais.extend(e.processados)
            pendentes = (
                list(e.pendentes) + (resto if fase_http_atual == "canario" else [])
                + list(alvos_playwright_pendentes.keys())
            )
            return _resultado_final(
                False, f"Cancelado pelo usuário ({len(pendentes)} pendente(s)).",
                cancelado={"pendentes": pendentes},
            )
        except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
            return ResultadoEtapa("enriquecimento_sga", sucesso=False, mensagem=_mensagem_com_notas(e))

    if alvos_playwright_pendentes:
        chassis_playwright = sorted(alvos_playwright_pendentes.keys())
        try:
            async with async_playwright() as playwright:
                if estado_sessao_sga is not None:
                    browser, context = await sga_bot.abrir_contexto_com_sessao(playwright, estado_sessao_sga)
                else:
                    browser, context = await sga_bot.aguardar_login_manual(playwright)
                try:
                    resultados_playwright = await playwright_utils.processar_fila(
                        context, chassis_playwright, _consultar_playwright,
                        on_progresso=on_progresso, cancelar_checker=cancelar_checker,
                        on_item_iniciado=on_item_iniciado,
                    )
                    resultados_totais.extend(resultados_playwright)
                finally:
                    await context.close()
                    await browser.close()
        except playwright_utils.AguardandoReconexao as e:
            resultados_totais.extend(e.processados)
            return _resultado_final(
                False, f"Sessão caída — aguardando reconexão manual ({len(e.pendentes)} pendente(s)).",
                aguardando_reconexao={"pendentes": list(e.pendentes)},
            )
        except playwright_utils.CancelamentoSolicitado as e:
            resultados_totais.extend(e.processados)
            return _resultado_final(
                False, f"Cancelado pelo usuário ({len(e.pendentes)} pendente(s)).",
                cancelado={"pendentes": list(e.pendentes)},
            )
        except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
            return ResultadoEtapa("enriquecimento_sga", sucesso=False, mensagem=_mensagem_com_notas(e))

    return _resultado_final(True)


async def etapa_consolidar_com_sga(
    dados_classificacao: dict | None = None,
    dados_sga: dict | None = None,
    equipamentos: list[dict] | None = None,
    instalacao_remocao: list[dict] | None = None,
) -> ResultadoEtapa:
    """Fase B.2 — só dá pra rodar depois da Fase D (SGA). Aplica o SGA
    sobre a classificação de Manutenção (`core.motor_regras.
    aplicar_situacoes_sga` — pode virar fechamento automático) e
    classifica Instalação/Remoção (`core.motor_regras_instalacao_remocao.
    classificar_instalacao_remocao` — só dá pra classificar agora, com o
    SGA disponível). Junta a fila real: `grupo_3_tratativa_humana` (com
    `origem='manutencao'` adicionado) + a lista de Instalação/Remoção (já
    vem com `origem` própria). `divergencias_instalacao`/`divergencias_
    remocao` (Bloco B, 2026-08-24: itens `REGRA_INSTALACAO_JA_FEITA`/
    `REGRA_TITULARIDADE` e `REGRA_REMOCAO_SGA_ATIVO`/`REGRA_REMOCAO_
    EQUIPAMENTO_NAO_PERMITIDO`/`REGRA_REMOCAO_TITULARIDADE_*`) e
    `divergencias_manutencao` (2026-08-25: `REGRA_MANUTENCAO_
    DIVERGENCIA_SGA`, mesmo item que já fecha o incidente automaticamente
    via `REGRA_SGA_INATIVO`, só que também sinalizado aqui) vêm separados
    no retorno — nunca entram em `fila_operacional`/Tratativas, alimentam
    as abas próprias "Análise de Divergência - Instalação"/"- Remoção"/"-
    Manutenção" na Fase E.

    Todos os parâmetros são opcionais, com o mesmo espírito de default
    das etapas anteriores (permitir rodar isolada no painel) — mas como
    `dados_sga` depende de login manual no SGA, o default dele
    (`etapa_enriquecimento_sga()`) só deve ser usado sabendo disso.
    """
    try:
        if dados_classificacao is None:
            resultado_motor = etapa_motor_de_regras()
            if not resultado_motor.sucesso:
                return ResultadoEtapa("consolidar_com_sga", sucesso=False, mensagem=resultado_motor.mensagem)
            dados_classificacao = resultado_motor.dados
        if equipamentos is None or instalacao_remocao is None:
            dados_administrador = _ler_dados_administrador()
            equipamentos = equipamentos if equipamentos is not None else dados_administrador["equipamentos"]
            instalacao_remocao = (
                instalacao_remocao if instalacao_remocao is not None else dados_administrador["instalacao_remocao"]
            )
        if dados_sga is None:
            resultado_sga = await etapa_enriquecimento_sga(dados_classificacao, instalacao_remocao)
            if not resultado_sga.sucesso:
                return ResultadoEtapa("consolidar_com_sga", sucesso=False, mensagem=resultado_sga.mensagem)
            dados_sga = resultado_sga.dados

        parametros = supabase_client.buscar_parametros()
        templates = supabase_client.buscar_rule_templates()

        grupos_manutencao = motor_regras.aplicar_situacoes_sga(
            dados_classificacao, dados_sga["situacoes_sga"], templates
        )
        tratativas_instalacao_remocao, divergencias_instalacao, divergencias_remocao = (
            motor_regras_instalacao_remocao.classificar_instalacao_remocao(
                instalacao_remocao, equipamentos, dados_sga["situacoes_sga"], parametros, templates
            )
        )

        fila_operacional = [
            {**linha, "origem": "manutencao"} for linha in grupos_manutencao["grupo_3_tratativa_humana"]
        ] + tratativas_instalacao_remocao
        divergencias_manutencao = grupos_manutencao.get("divergencias_manutencao", [])
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("consolidar_com_sga", sucesso=False, mensagem=_mensagem_com_notas(e))

    return ResultadoEtapa(
        "consolidar_com_sga",
        sucesso=True,
        dados={
            "grupo_2_concluir": grupos_manutencao["grupo_2_concluir"],
            "fila_operacional": fila_operacional,
            "divergencias_instalacao": divergencias_instalacao,
            "divergencias_remocao": divergencias_remocao,
            "divergencias_manutencao": divergencias_manutencao,
        },
    )


_TIPO_SERVICO_LABEL = {
    ORIGEM_INSTALACAO: "Instalação",
    ORIGEM_REMOCAO: "Remoção",
    ORIGEM_MANUTENCAO: "Manutenção",
}

_FORMATOS_DATA_ATENDENTE = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y")

_ATENDENTE_DEFAULT = {
    "Selecionado": False, "Técnico": "", "Situação Manual": "", "Data Agendada": "",
    "Observação Manual": "", "Discrepância revisada": False, "Finalizado": False,
    "Atendimento": "", "Base": "", "Ponto de Ação": "",
}

# "Atendimento" (dropdown "Base"/"Em rota"/"Ação") -> código interno usado
# em `tratativas.atendimento` e por `core.mensagens.nome_template_logico`.
_ATENDIMENTO_LABEL_PARA_CODIGO = {
    "Base": ATENDIMENTO_BASE,
    "Em rota": ATENDIMENTO_ROTA,
    "Ação": ATENDIMENTO_ACAO,
}

# Uma tratativa que já saiu do ciclo de mensagens (escalada pra ligação pela
# Fase F.3, encaminhada pra Puma, ou finalizada por qualquer caminho que não
# passe pelo checkbox `Finalizado` da aba) não deve mais aparecer em
# `Tratativas` — evita o atendente ver a mesma pendência em duas abas ao
# mesmo tempo.
_STATUS_EXCLUIR_DE_TRATATIVAS = {STATUS_AGUARDANDO_LIGACAO, STATUS_ENCAMINHADO_PUMA, STATUS_FINALIZADO}

# `Discrepância revisada` (Tratativas, linhas de REGRA_REMOÇÃO_ATIVA) — o
# atendente marca pra parar de ver o mesmo alerta repetido todo dia enquanto
# está resolvendo a discrepância (rastreador ainda ativo, mas consta na
# lista de retirada). Suprime só a exibição na aba (mesmo mecanismo de
# `Finalizado` — o upsert do motor continua rodando); some sozinho assim
# que a condição real deixar de bater (o equipamento sai de Rastreadores
# Ativos, ou o código de regra muda), sem exigir desmarcar o checkbox.
# Constante em core.constants.CODIGOS_REMOCAO_ATIVA (também usada por
# orchestrator/dashboards_operador.py).


def _checkbox_sheets(valor) -> bool:
    """Checkbox do Google Sheets: gspread pode devolver bool nativo ou
    texto "TRUE"/"FALSE" (formatação de célula) — cobre os dois."""
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().upper() == "TRUE"


def _parse_data_atendente(valor: str) -> str | None:
    """"Data Agendada" é digitada à mão (dd/mm/aaaa) — converte pro
    formato que uma coluna de data do Supabase aceita; `None` se vazia
    ou ilegível (a sincronização simplesmente não toca o campo)."""
    valor = (valor or "").strip()
    if not valor:
        return None
    for formato in _FORMATOS_DATA_ATENDENTE:
        try:
            return datetime.strptime(valor, formato).date().isoformat()
        except ValueError:
            continue
    return None


def _data_referencia_iso(linha: dict) -> str | None:
    """Converte data_incidente/data_contrato (formato BR, com ou sem hora)
    pro ISO que o Postgres aceita sem erro — mesmos 2 formatos de
    `_FORMATOS_DATA_ATENDENTE`, mas preserva hora/segundos (`isoformat()`
    completo, não `.date()`) porque `data_referencia` carrega timestamp
    real, diferente de `data_agendada`. `None` se vazia ou ilegível (vira
    NULL no Supabase em vez de repetir o erro de parse do Postgres)."""
    valor = (_data_referencia(linha) or "").strip()
    if not valor:
        return None
    for formato in _FORMATOS_DATA_ATENDENTE:
        try:
            return datetime.strptime(valor, formato).isoformat()
        except ValueError:
            continue
    return None


def _formatar_data_referencia_para_exibicao(valor_iso: str | None) -> str:
    """Reverte o ISO gravado em `tratativas.data_referencia` pro formato
    brasileiro exibido nas abas Pendente de Ligação/Encaminhar pra Puma —
    mantém a mesma aparência da aba Tratativas (que nunca passa por aqui,
    recalcula direto do dado fresco via `_data_referencia`)."""
    if not valor_iso:
        return ""
    try:
        dt = datetime.fromisoformat(valor_iso)
    except ValueError:
        return valor_iso
    if dt.time() == time(0, 0, 0):
        return dt.strftime("%d/%m/%Y")
    return dt.strftime("%d/%m/%Y %H:%M:%S")


def _sincronizar_atendente_da_aba(agora: datetime | None = None) -> dict[str, dict]:
    """Lê a aba `Tratativas` atual, sincroniza pro Supabase (por
    "ID (hash)" = chave_unica) os campos que só o atendente edita, e
    devolve `{chave_unica: {colunas lidas}}` pra reaproveitar na
    regravação sem precisar ler a aba de novo depois do upsert.

    "Atendimento"/"Base"/"Ponto de Ação" (Fase F.1) resolvidos aqui
    também — "Base"/"Ponto de Ação" são o NOME escolhido no dropdown, que
    precisa virar uuid real (`base_id`/`ponto_acao_id`) antes de gravar;
    o mapa nome->id é buscado uma vez por execução, não por linha.

    "Telefone corrigido" (checkbox) não é um campo de estado — é um
    disparo de ação: quando marcado, limpa `status_contato` (reabre a
    elegibilidade de disparo, bloqueada por contato inválido). Some
    sozinho no próximo ciclo (mesmo mecanismo de sempre, `_linha_para_aba`
    já grava `"Telefone corrigido": False` de novo).

    `situacao_manual_definida_em` (widget "Situação Manual parada" do
    Painel de apoio, Fase 4) só é gravado quando `Situação Manual` muda de
    verdade — comparado contra o valor atual no Supabase (busca em lote,
    `buscar_situacao_manual_atual_por_chaves`), nunca contra `updated_at`
    (que `sincronizar_campos_atendente` toca todo ciclo, mesmo sem
    mudança de valor).
    """
    linhas = google_sheets_client.ler_aba(google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Tratativas")
    atendente_por_chave: dict[str, dict] = {}
    bases_por_nome = {b["nome"]: b["id"] for b in supabase_client.buscar_bases_ativas()}
    pontos_acao_por_nome = {p["nome_local"]: p["id"] for p in supabase_client.buscar_pontos_acao_ativos()}
    chaves_da_aba = [chave for chave in ((linha.get("ID (hash)") or "").strip() for linha in linhas) if chave]
    situacao_manual_atual_por_chave = supabase_client.buscar_situacao_manual_atual_por_chaves(chaves_da_aba)
    agora_dt = agora or datetime.now()
    atualizacoes: dict[str, dict] = {}

    for linha in linhas:
        chave = (linha.get("ID (hash)") or "").strip()
        if not chave:
            continue

        selecionado = _checkbox_sheets(linha.get("Selecionado"))
        discrepancia_revisada = _checkbox_sheets(linha.get("Discrepância revisada"))
        finalizado = _checkbox_sheets(linha.get("Finalizado"))
        telefone_corrigido = _checkbox_sheets(linha.get("Telefone corrigido"))
        tecnico = linha.get("Técnico") or ""
        situacao_manual = linha.get("Situação Manual") or ""
        data_agendada = linha.get("Data Agendada") or ""
        observacao_manual = linha.get("Observação Manual") or ""
        atendimento_label = linha.get("Atendimento") or ""
        nome_base = linha.get("Base") or ""
        nome_ponto_acao = linha.get("Ponto de Ação") or ""

        atendente_por_chave[chave] = {
            "Selecionado": selecionado,
            "Técnico": tecnico,
            "Situação Manual": situacao_manual,
            "Data Agendada": data_agendada,
            "Observação Manual": observacao_manual,
            "Discrepância revisada": discrepancia_revisada,
            "Finalizado": finalizado,
            "Atendimento": atendimento_label,
            "Base": nome_base,
            "Ponto de Ação": nome_ponto_acao,
        }

        campos_sync = {
            "selecionado": selecionado,
            "tecnico": tecnico,
            "situacao_manual": situacao_manual,
            "observacao_manual": observacao_manual,
            "discrepancia_revisada": discrepancia_revisada,
            "atendimento": _ATENDIMENTO_LABEL_PARA_CODIGO.get(atendimento_label, ""),
            "base_id": bases_por_nome.get(nome_base),
            "ponto_acao_id": pontos_acao_por_nome.get(nome_ponto_acao),
        }
        data_agendada_iso = _parse_data_atendente(data_agendada)
        if data_agendada_iso is not None:
            campos_sync["data_agendada"] = data_agendada_iso
        if finalizado:
            campos_sync["status"] = STATUS_FINALIZADO
        if telefone_corrigido:
            campos_sync["status_contato"] = None
        if situacao_manual != situacao_manual_atual_por_chave.get(chave, ""):
            campos_sync["situacao_manual_definida_em"] = agora_dt.isoformat() if situacao_manual else None

        atualizacoes[chave] = campos_sync

    supabase_client.sincronizar_campos_atendente_em_lote(atualizacoes)
    return atendente_por_chave


def etapa_sincronizar_atendente_tratativas(agora: datetime | None = None) -> ResultadoEtapa:
    """Sincroniza só os campos que o atendente marca na aba Tratativas
    (Selecionado, Atendimento, Base, Técnico etc.) pro Supabase, SEM
    reprocessar classificação/SGA — achado 2026-08-20: antes disso, a
    única forma de ver uma marcação nova era rodar `etapa_publicar_fila_
    operacional` (Fase E) inteira de novo, mesmo quando a fila já tinha
    sido publicada antes e só a seleção do atendente mudou. Permite rodar
    só esta etapa + `etapa_disparo_mensagens` quando for esse o caso."""
    try:
        atendente_por_chave = _sincronizar_atendente_da_aba(agora or datetime.now())
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("sincronizar_atendente_tratativas", sucesso=False, mensagem=_mensagem_com_notas(e))
    return ResultadoEtapa(
        "sincronizar_atendente_tratativas", sucesso=True,
        dados={"sincronizadas": len(atendente_por_chave)},
    )


def _dados_hash_chave_unica(linha: dict) -> dict:
    """Campos que `core.dedup.gerar_chave_unica` espera por origem —
    ver `core/dedup.py`. Vêm crus do `incidente`/`registro` original
    (não recalculados aqui), já produzidos por `_montar_linha_resultado`
    nos dois motores."""
    if linha.get("origem") == ORIGEM_MANUTENCAO:
        return {
            "placa": linha.get("placa", ""),
            "evento": linha.get("evento", ""),
        }
    return {
        "cpf": linha.get("cpf", ""),
        "chassi": linha.get("chassi", ""),
        "situacao": linha.get("situacao", ""),
        "data_contrato": linha.get("data_contrato", ""),
    }


def _data_referencia(linha: dict) -> str:
    return linha.get("data_incidente") or linha.get("data_contrato") or ""


def _payload_tratativa(linha: dict, chave_unica: str) -> dict:
    """Só os campos que o motor decide — nunca `nivel_urgencia` (deriva
    de `codigo_regra` no relatório, decisão já fechada) nem nenhum campo
    de atendente (esses são sincronizados separadamente, ver
    `_sincronizar_atendente_da_aba`, pra não pisar no trabalho manual)."""
    return {
        "chave_unica": chave_unica,
        "origem": linha.get("origem", ""),
        "identificador": linha.get("identificador", ""),
        "chassi": linha.get("chassi", ""),
        "placa": linha.get("placa", ""),
        "modelo": linha.get("modelo", ""),
        "tipo_identificador": linha.get("tipo_identificador", ""),
        "cliente": linha.get("cliente", ""),
        "telefone": linha.get("telefone", ""),
        "cidade": linha.get("cidade", ""),
        "bairro": linha.get("bairro", ""),
        "data_referencia": _data_referencia_iso(linha),
        "sga": linha.get("sga", ""),
        "acao_sugerida": linha.get("acao_sugerida", ""),
        "observacao_sistema": linha.get("observacao_sistema", ""),
        "codigo_regra": linha.get("codigo_regra", ""),
    }


@lru_cache(maxsize=8)
def _feriados_pe(ano: int) -> frozenset[date]:
    """Feriados de Pernambuco (BR) pro cálculo de dias úteis do
    indicador "Dias sem contato" — mesmo conceito de dia útil de
    `core.escalonamento.dia_permite_disparo`, calculado aqui porque
    `core/` não importa a lib `holidays` de propósito (feriados são
    responsabilidade de quem chama)."""
    return frozenset(holidays.Brazil(years=ano, subdiv="PE").keys())


def _feriados_para_intervalo(inicio: date, fim: date) -> set[date]:
    return set().union(*(_feriados_pe(ano) for ano in {inicio.year, fim.year}))


def _formatar_dias_sem_contato(dias: int) -> str:
    return "1 dia sem contato" if dias == 1 else f"{dias} dias sem contato"


def _dias_sem_contato(ultimo_contato: date | None, agora: date) -> str:
    if ultimo_contato is None:
        return ""
    feriados = _feriados_para_intervalo(ultimo_contato, agora)
    dias = escalonamento.dias_uteis_entre(ultimo_contato, agora, feriados)
    return _formatar_dias_sem_contato(dias)


def _ultimo_contato_tratativa(dados: dict) -> date | None:
    """Último evento de contato registrado numa tratativa (dict com os
    campos crus do Supabase — `tentativa_1/2/3`/`created_at`), do mais
    recente pro mais antigo (preenchidos em sequência cronológica, então
    o primeiro não-vazio já é o marco certo). Serve tanto pro
    `estado_disparo` de `Tratativas` quanto pra `tratativa` recém-
    escalada de `Pendente de Ligação` — mesmos nomes de campo nos dois."""
    for campo in ("tentativa_3", "tentativa_2", "tentativa_1", "created_at"):
        valor = dados.get(campo)
        if not valor:
            continue
        try:
            return datetime.fromisoformat(valor).date()
        except (TypeError, ValueError):
            continue
    return None


def _linha_para_aba(linha: dict, chave_unica: str, atendente: dict, estado_disparo: dict, agora: date) -> dict:
    """Monta uma linha da aba `Tratativas` a partir do resultado desta
    execução (`linha`), do que o atendente já tinha editado (`atendente`,
    lido em `_sincronizar_atendente_da_aba`, snapshot da aba anterior) e
    do estado do ciclo de disparo (`estado_disparo`, lido em
    `supabase_client.buscar_estado_disparo_por_chaves` — sempre fresco do
    Supabase, NUNCA do snapshot da aba, porque quem escreve esses campos
    é outro processo, Fase F/webhook, não o atendente editando a
    planilha). Antes da Fase F existir, `estado_disparo` normalmente só
    tem `status` (o default `'pendente'` que `upsert_tratativa` aplica a
    uma tratativa nova) — os demais campos ficam vazios até o disparo de
    verdade escrever neles.
    """
    return {
        "ID (hash)": chave_unica,
        "Tipo Serviço": _TIPO_SERVICO_LABEL.get(linha.get("origem", ""), linha.get("origem", "")),
        "Identificador": linha.get("identificador", ""),
        "Chassi": linha.get("chassi", ""),
        "Placa": mensagens.resolver_placa_para_mensagem(linha.get("placa", ""), linha.get("modelo", "")),
        "Cliente": linha.get("cliente", ""),
        "Telefone": linha.get("telefone", ""),
        "Cidade": linha.get("cidade", ""),
        "Bairro": linha.get("bairro", ""),
        "Data Contrato / Data Incidente": formatar_data_br_sem_hora(_data_referencia(linha)),
        "SGA": linha.get("sga", ""),
        "Ação Sugerida": linha.get("acao_sugerida", ""),
        "Observação do Sistema": linha.get("observacao_sistema", ""),
        "Nível de Urgência": linha.get("nivel_urgencia", ""),
        "Dias sem contato": _dias_sem_contato(_ultimo_contato_tratativa(estado_disparo), agora),
        "Discrepância revisada": atendente["Discrepância revisada"],
        "Atendimento": atendente["Atendimento"],
        "Base": atendente["Base"],
        "Ponto de Ação": atendente["Ponto de Ação"],
        "Selecionado": atendente["Selecionado"],
        "Status Contato": estado_disparo.get("status_contato") or "",
        "Telefone corrigido": False,
        "Status Sistema": estado_disparo.get("status") or "",
        "Tentativa 1": estado_disparo.get("tentativa_1") or "",
        "Tentativa 2": estado_disparo.get("tentativa_2") or "",
        "Tentativa 3": estado_disparo.get("tentativa_3") or "",
        "Resposta": estado_disparo.get("resposta") or "",
        "Data Resposta": formatar_data_br_sem_hora(estado_disparo.get("data_resposta")),
        "Retorno do Associado": estado_disparo.get("retorno_associado") or "",
        "Situação Manual": atendente["Situação Manual"],
        "Data Agendada": formatar_data_br_sem_hora(atendente["Data Agendada"]),
        "Técnico": atendente["Técnico"],
        "Observação Manual": atendente["Observação Manual"],
        "Finalizado": atendente["Finalizado"],
    }


def _linha_divergencia_para_aba(linha: dict, chave_unica: str) -> dict:
    """Monta uma linha da aba "Análise de Divergência - Instalação" a
    partir de um item de `divergencias_instalacao` (`core.motor_regras_
    instalacao_remocao._montar_linha_divergencia`). Diferente de
    `_linha_para_aba`: sem estado de atendente (aba é relatório mecânico,
    reescrita do zero a cada ciclo, sem edição humana a preservar)."""
    return {
        "ID (hash)": chave_unica,
        "Chassi": linha.get("chassi", ""),
        "Placa": linha.get("placa", ""),
        "Cliente (Instalação-Remoção cadastro)": linha.get("cliente_cadastro", ""),
        "Cliente (Rastreadores Ativos)": linha.get("cliente_rastreadores", ""),
        "Data Contrato": linha.get("data_contrato", ""),
        "Data de Instalação": linha.get("data_instalacao", ""),
        "IMEI": linha.get("imei", ""),
        "Motivo": linha.get("motivo", ""),
        "Observação": linha.get("observacao", ""),
        "Ação": linha.get("acao", ""),
    }


def _linha_divergencia_remocao_para_aba(linha: dict, chave_unica: str) -> dict:
    """Monta uma linha da aba "Análise de Divergência - Remoção" (Bloco
    B, 2026-08-24) a partir de um item de `divergencias_remocao`
    (`core.motor_regras_instalacao_remocao._montar_linha_divergencia_
    remocao`). Mesmo espírito de `_linha_divergencia_para_aba`: sem
    estado de atendente."""
    return {
        "ID (hash)": chave_unica,
        "Chassi": linha.get("chassi", ""),
        "Placa": linha.get("placa", ""),
        "Cliente cadastro": linha.get("cliente_cadastro", ""),
        "Cliente Rastreadores Ativos": linha.get("cliente_rastreadores", ""),
        "Modelo do Equipamento": linha.get("modelo_equipamento", ""),
        "Status SGA": linha.get("status_sga", ""),
        "Motivo": linha.get("motivo", ""),
        "Observação": linha.get("observacao", ""),
        "Ação": linha.get("acao", ""),
    }


def _linha_divergencia_manutencao_para_aba(linha: dict, chave_unica: str) -> dict:
    """Monta uma linha da aba "Análise de Divergência - Manutenção"
    (2026-08-25) a partir de um item de `divergencias_manutencao`
    (`core.motor_regras._montar_linha_divergencia_manutencao`). Mesmo
    espírito de `_linha_divergencia_para_aba`/`_linha_divergencia_
    remocao_para_aba`: sem estado de atendente, sem "Motivo" (só existe
    1 causa possível)."""
    return {
        "ID (hash)": chave_unica,
        "Chassi": linha.get("chassi", ""),
        "Placa": linha.get("placa", ""),
        "Cliente": linha.get("cliente", ""),
        "Evento": linha.get("evento", ""),
        "Status SGA": linha.get("status_sga", ""),
        "Observação": linha.get("observacao", ""),
        "Ação": linha.get("acao", ""),
    }


_LIMITE_RODADAS_AUSENTE_PARA_FECHAR = 2


def _reconciliar_tratativas_ausentes(chaves_desta_rodada: set[str]) -> None:
    """Fecha sozinha uma tratativa que sumiu da fila do motor por 2 rodadas
    reais consecutivas (Bloco H, 2026-08-24 — achado original: chave de
    manutenção instável, mas este mecanismo cobre qualquer origem/causa
    futura de "sumiço"). Mesmo espírito de `_puma_concluido_automaticamente`
    (reler o estado atual e sincronizar de volta sem exigir ação humana),
    generalizado. `status='respondido'` nunca fecha sozinho — decisão do
    usuário: uma resposta de cliente merece revisão humana antes de a
    tratativa desaparecer."""
    for tratativa in supabase_client.buscar_tratativas_abertas_no_motor():
        chave = tratativa["chave_unica"]
        rodadas_atual = tratativa.get("rodadas_ausente_fila") or 0

        if chave in chaves_desta_rodada:
            if rodadas_atual != 0:
                supabase_client.sincronizar_campos_atendente(chave, {"rodadas_ausente_fila": 0})
            continue

        if tratativa.get("status") == STATUS_RESPONDIDO:
            continue

        rodadas_nova = rodadas_atual + 1
        if rodadas_nova >= _LIMITE_RODADAS_AUSENTE_PARA_FECHAR:
            supabase_client.sincronizar_campos_atendente(chave, {
                "status": STATUS_FINALIZADO,
                "observacao_sistema": (
                    "Encerrado automaticamente: ausente da fila do motor por "
                    f"{rodadas_nova} rodadas reais consecutivas."
                ),
            })
        else:
            supabase_client.sincronizar_campos_atendente(chave, {"rodadas_ausente_fila": rodadas_nova})


async def etapa_publicar_fila_operacional(
    fila_operacional: list[dict] | None = None,
    agora: datetime | None = None,
    divergencias_instalacao: list[dict] | None = None,
    divergencias_remocao: list[dict] | None = None,
    divergencias_manutencao: list[dict] | None = None,
) -> ResultadoEtapa:
    """Fase E — persiste `fila_operacional` (saída de
    `etapa_consolidar_com_sga`) em `tratativas` (Supabase) e reescreve a
    aba "Tratativas" da planilha Operacional. Também reescreve as abas
    "Análise de Divergência - Instalação" (`divergencias_instalacao`),
    "Análise de Divergência - Remoção" (`divergencias_remocao`, Bloco B,
    2026-08-24) e "Análise de Divergência - Manutenção"
    (`divergencias_manutencao`, 2026-08-25) — mecânico, sem Supabase, sem
    estado de atendente (ver passo 5 abaixo).

    Async só por causa do default de `fila_operacional` (chama
    `etapa_consolidar_com_sga()`, que depende de Playwright/SGA) — o
    corpo desta etapa em si não abre navegador, é só Sheets + Supabase.

    Ordem (decisão fechada com o usuário, 2026-08-07 — ver HANDOFF):
    1. Lê a aba `Tratativas` atual e sincroniza pro Supabase os campos
       que só o atendente edita, ANTES de sobrescrever com os dados do
       motor — sem isso, `reescrever_aba` (que limpa a aba inteira)
       apagaria esse trabalho a cada ciclo.
    2. Upsert de cada linha de `fila_operacional` em `tratativas`
       (`core.dedup.gerar_chave_unica` + só os campos que o motor
       decide).
    3. Busca em lote o estado do ciclo de disparo (`status`/
       `status_contato`/tentativas/resposta) de todas as chaves recém-
       upsertadas — sempre fresco do Supabase (`buscar_estado_disparo_
       por_chaves`), nunca do snapshot da aba lido no passo 1 (esses
       campos são escritos por outro processo, não pelo atendente).
    4. Reescreve a aba com as linhas de `fila_operacional` que não
       estão marcadas `Finalizado` (lido no passo 1) — uma linha some da
       aba quando o atendente confirma que já está resolvida, mesmo que
       o motor ainda a gere neste ciclo.
    5. Reescreve "Análise de Divergência - Instalação"/"- Remoção"/"-
       Manutenção" do zero com `divergencias_instalacao`/`divergencias_
       remocao`/`divergencias_manutencao` — sem upsert em `tratativas`
       (não são tratativas) e sem sincronizar nada da aba antiga antes
       (não há campo editável pelo atendente pra preservar).
    """
    if fila_operacional is None:
        resultado_consolidacao = await etapa_consolidar_com_sga()
        if not resultado_consolidacao.sucesso:
            return ResultadoEtapa(
                "publicar_fila_operacional", sucesso=False, mensagem=resultado_consolidacao.mensagem
            )
        fila_operacional = resultado_consolidacao.dados["fila_operacional"]
        if divergencias_instalacao is None:
            divergencias_instalacao = resultado_consolidacao.dados.get("divergencias_instalacao", [])
        if divergencias_remocao is None:
            divergencias_remocao = resultado_consolidacao.dados.get("divergencias_remocao", [])
        if divergencias_manutencao is None:
            divergencias_manutencao = resultado_consolidacao.dados.get("divergencias_manutencao", [])
    divergencias_instalacao = divergencias_instalacao if divergencias_instalacao is not None else []
    divergencias_remocao = divergencias_remocao if divergencias_remocao is not None else []
    divergencias_manutencao = divergencias_manutencao if divergencias_manutencao is not None else []

    try:
        agora_dt = agora or datetime.now()
        agora_data = agora_dt.date()
        with _anotar_erro("sincronizar_atendente_da_aba"):
            atendente_por_chave = _sincronizar_atendente_da_aba(agora_dt)

        linhas_com_chave = [
            (linha, dedup.gerar_chave_unica(linha["origem"], _dados_hash_chave_unica(linha)))
            for linha in fila_operacional
        ]
        with _anotar_erro("upsert_tratativas_em_lote"):
            supabase_client.upsert_tratativas_em_lote(
                [_payload_tratativa(linha, chave_unica) for linha, chave_unica in linhas_com_chave]
            )

        with _anotar_erro("buscar_estado_disparo_por_chaves"):
            estado_disparo_por_chave = supabase_client.buscar_estado_disparo_por_chaves(
                [chave_unica for _, chave_unica in linhas_com_chave]
            )

        linhas_aba = []
        for linha, chave_unica in linhas_com_chave:
            atendente = atendente_por_chave.get(chave_unica, _ATENDENTE_DEFAULT)
            estado_disparo = estado_disparo_por_chave.get(chave_unica, {})
            discrepancia_suprimida = (
                atendente["Discrepância revisada"] and linha.get("codigo_regra") in CODIGOS_REMOCAO_ATIVA
            )
            if (
                atendente["Finalizado"]
                or estado_disparo.get("status") in _STATUS_EXCLUIR_DE_TRATATIVAS
                or discrepancia_suprimida
            ):
                continue
            linhas_aba.append(_linha_para_aba(linha, chave_unica, atendente, estado_disparo, agora_data))

        with _anotar_erro("reescrever_aba:Tratativas"):
            google_sheets_client.reescrever_aba(
                google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Tratativas", linhas_aba
            )

        linhas_divergencia = [
            _linha_divergencia_para_aba(
                linha, dedup.gerar_chave_unica(ORIGEM_INSTALACAO, _dados_hash_chave_unica(linha))
            )
            for linha in divergencias_instalacao
        ]
        with _anotar_erro("reescrever_aba:Analise_Divergencia_Instalacao"):
            google_sheets_client.reescrever_aba(
                google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Análise de Divergência - Instalação", linhas_divergencia
            )

        linhas_divergencia_remocao = [
            _linha_divergencia_remocao_para_aba(
                linha, dedup.gerar_chave_unica(ORIGEM_REMOCAO, _dados_hash_chave_unica(linha))
            )
            for linha in divergencias_remocao
        ]
        with _anotar_erro("reescrever_aba:Analise_Divergencia_Remocao"):
            google_sheets_client.reescrever_aba(
                google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Análise de Divergência - Remoção", linhas_divergencia_remocao
            )

        linhas_divergencia_manutencao = [
            _linha_divergencia_manutencao_para_aba(
                linha, dedup.gerar_chave_unica(
                    ORIGEM_MANUTENCAO, {"placa": linha.get("placa", ""), "evento": linha.get("evento", "")}
                )
            )
            for linha in divergencias_manutencao
        ]
        with _anotar_erro("reescrever_aba:Analise_Divergencia_Manutencao"):
            google_sheets_client.reescrever_aba(
                google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Análise de Divergência - Manutenção", linhas_divergencia_manutencao
            )

        # Depois das escritas principais (Sheets/Supabase já refletem esta
        # rodada) — uma falha aqui (ex: coluna `rodadas_ausente_fila` ainda
        # não criada, ver `_handoff/sql_tratativas_rodadas_ausente_fila.sql`)
        # não deve impedir o operador de ver o resultado da publicação.
        with _anotar_erro("reconciliar_tratativas_ausentes"):
            _reconciliar_tratativas_ausentes({chave_unica for _, chave_unica in linhas_com_chave})
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("publicar_fila_operacional", sucesso=False, mensagem=_mensagem_com_notas(e))

    return ResultadoEtapa(
        "publicar_fila_operacional",
        sucesso=True,
        dados={"publicadas": len(linhas_aba), "total_fila": len(fila_operacional)},
    )


_HORARIO_CORTE_DISPARO = "17:30"
_FUSO_DISPARO = "America/Recife"


def etapa_disparo_mensagens(
    elegiveis: list[dict] | None = None, agora: datetime | None = None,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
) -> ResultadoEtapa:
    """Fase F.1 — dispara WhatsApp (Newmo) pras tratativas elegíveis.
    `elegiveis` (default: `supabase_client.buscar_elegiveis_para_disparo`)
    já vem filtrado grosso (selecionado/status/status_contato); o filtro
    fino é feito aqui, item a item:

    - `Atendimento` vazio ou `Retorno do Associado` pendente de revisão
      pausam a esteira — gates de F.1 que vivem aqui, não em
      `core.escalonamento` (decisão já fechada na auditoria desta
      sessão, esse módulo não deve ser reaberto sem necessidade).
    - `core.escalonamento.elegivel_para_disparo` cobre o resto
      (tentativas < `limite_tentativas_disparo`, não bloqueada por SGA,
      sem Situação Manual, não disparada hoje ainda).
    - Dia útil / horário de corte (`horario_corte_disparo`/`fuso_horario`,
      `system_parameters`, default 17:30 América/Recife) são checados
      uma vez só, não por item — `agora` é fixo pra toda a execução,
      então o resultado seria idêntico item a item.

    Uma falha de envio de UM item (transporte, ou qualquer classificação
    da Newmo que não seja "sucesso"/"contato_invalido" — cods 2/3/5/6/8,
    ver `integrations.newmo_client`) não derruba o lote inteiro —
    registrada em `dados["falhas"]` (lista, mesmo contrato de
    `sem_atendimento`: `item`/`erro`/`descricao` por linha, com o código
    e mensagem reais da Newmo) e segue pro próximo. Achado 2026-08-26:
    antes disso era só um contador cego, sem detalhe nenhum — 1ª rodada
    real de F.1 teve ~122 falhas assim, impossível diagnosticar depois.
    """
    try:
        agora = agora or datetime.now()
        elegiveis = elegiveis if elegiveis is not None else supabase_client.buscar_elegiveis_para_disparo()

        config = manager.carregar_config()["newmo"]
        parametros = supabase_client.buscar_parametros()
        feriados = _feriados_para_intervalo(agora.date(), agora.date())

        if not escalonamento.dia_permite_disparo(
            agora.date(), feriados, parametros.get("disparar_em_feriados_fins_de_semana", False)
        ):
            return ResultadoEtapa("disparo_mensagens", sucesso=True, dados={"enviadas": 0, "motivo": "fora de dia útil"})
        horario_corte = parametros.get("horario_corte_disparo", _HORARIO_CORTE_DISPARO)
        fuso_disparo = parametros.get("fuso_horario", _FUSO_DISPARO)
        if escalonamento.passou_do_horario_corte(agora, horario_corte, fuso_disparo):
            return ResultadoEtapa("disparo_mensagens", sucesso=True, dados={"enviadas": 0, "motivo": "fora do horário de disparo"})

        limite_tentativas = int(parametros.get("limite_tentativas_disparo", 3))
        bases_por_id = {b["id"]: b for b in supabase_client.buscar_bases_ativas()}
        pontos_acao_por_id = {p["id"]: p for p in supabase_client.buscar_pontos_acao_ativos()}

        enviadas = contatos_invalidos = 0
        falhas: list[dict] = []
        sem_atendimento: list[dict] = []
        total_elegiveis = len(elegiveis)
        for indice, tratativa in enumerate(elegiveis):
            if cancelar_checker is not None and cancelar_checker():
                return ResultadoEtapa(
                    "disparo_mensagens", sucesso=False,
                    mensagem=f"Cancelado pelo usuário ({total_elegiveis - indice} pendente(s)).",
                    dados={
                        "enviadas": enviadas, "contato_invalido": contatos_invalidos,
                        "falhas": falhas, "total_elegiveis": total_elegiveis,
                        "sem_atendimento": sem_atendimento,
                    },
                    cancelado={"pendentes": elegiveis[indice:]},
                )
            if on_progresso is not None:
                on_progresso(indice + 1, total_elegiveis)
            atendimento = tratativa.get("atendimento")
            if not atendimento:
                # Achado 2026-08-21 (Bloco E2): "Selecionado" marcado sem
                # "Atendimento" preenchido pulava em silêncio -- o atendente
                # não tinha como saber que a mensagem nunca ia sair sem
                # investigar (dados["falhas"] só cobre falha de ENVIO, não
                # item incompleto que nunca chegou a tentar enviar).
                sem_atendimento.append({
                    "item": tratativa.get("chave_unica"), "erro": "Atendimento não preenchido",
                    "descricao": f"{tratativa.get('cliente', '')} ({tratativa.get('identificador', '')})",
                })
                continue
            if (tratativa.get("retorno_associado") or "").strip():
                continue
            if not escalonamento.elegivel_para_disparo(tratativa, agora, limite_tentativas):
                continue

            base = bases_por_id.get(tratativa.get("base_id")) if atendimento == ATENDIMENTO_BASE else None
            ponto_acao = pontos_acao_por_id.get(tratativa.get("ponto_acao_id")) if atendimento == ATENDIMENTO_ACAO else None
            nome_logico = mensagens.nome_template_logico(tratativa["origem"], atendimento, tratativa.get("tentativas", 0))
            codigo = config["templates"].get(nome_logico)
            variaveis = mensagens.variaveis_template(nome_logico, tratativa, base, ponto_acao)

            descricao_item = f"{tratativa.get('cliente', '')} ({tratativa.get('identificador', '')})"
            try:
                resultado_envio = newmo_client.enviar_template(
                    tratativa["telefone"], codigo, variaveis, config["canal_guid"], config["setor_id"]
                )
            except Exception as e:  # noqa: BLE001 - um item ruim não derruba o lote inteiro
                falhas.append({
                    "item": tratativa.get("chave_unica"), "erro": str(e), "descricao": descricao_item,
                })
                continue

            if resultado_envio["classificacao"] == "sucesso":
                supabase_client.atualizar_apos_envio(
                    tratativa["id"], resultado_envio["atendimento_id"], resultado_envio["mensagem_id"],
                    STATUS_AGUARDANDO_RESPOSTA,
                )
                enviadas += 1
            elif resultado_envio["classificacao"] == "contato_invalido":
                supabase_client.marcar_contato_invalido(tratativa["id"])
                contatos_invalidos += 1
            else:
                # Achado 2026-08-26: antes só incrementava um contador cego
                # (dados["falhas"] como int) -- sem item/código/mensagem
                # registrado em lugar nenhum (nem tela, nem log em arquivo,
                # já que `_registrar_log_arquivo` espera uma LISTA aqui,
                # mesmo contrato de `sem_atendimento`). 1ª rodada real de
                # F.1 (Base Afogados) teve ~122 falhas assim, sem nenhum
                # jeito de saber o motivo depois do fato.
                falhas.append({
                    "item": tratativa.get("chave_unica"),
                    "erro": f"cod {resultado_envio.get('cod')}: {resultado_envio.get('mensagem') or 'erro desconhecido'}",
                    "descricao": descricao_item,
                })
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("disparo_mensagens", sucesso=False, mensagem=_mensagem_com_notas(e))

    return ResultadoEtapa(
        "disparo_mensagens",
        sucesso=True,
        dados={
            "enviadas": enviadas, "contato_invalido": contatos_invalidos,
            "falhas": falhas, "total_elegiveis": len(elegiveis),
            "sem_atendimento": sem_atendimento,
        },
    )


def _deve_finalizar_atendimento(tratativa: dict, limite_tentativas: int = 3) -> bool:
    """Critério fechado em `docs/templates_whatsapp.md` seção 9: finaliza
    quem ainda estaria elegível pra outra tentativa automática de WhatsApp
    (mesmo texto do critério, exceto horário/já-disparou-hoje, que é sobre
    *quando* e não se aplica a uma rotina noturna) — `status` já veio
    filtrado por `buscar_candidatas_finalizacao_atendimento`. Mesmo
    `limite_tentativas` de `system_parameters.limite_tentativas_disparo`
    usado em `core.escalonamento`, pra nunca divergir."""
    if tratativa.get("tentativas", 0) >= limite_tentativas:
        return False
    if (tratativa.get("situacao_manual") or "").strip():
        return False
    if (tratativa.get("retorno_associado") or "").strip():
        return False
    return True


def etapa_finalizar_atendimentos_diarios(
    candidatas: list[dict] | None = None,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
) -> ResultadoEtapa:
    """Fase F.1 — rotina noturna, separada de `etapa_disparo_mensagens`
    (mesmo princípio de "cada fase é uma peça independente" usado no
    resto do projeto). Fecha do lado do Newmo (`POST /atendimento/
    finalizar`) o atendimento de quem ainda seria elegível pra outra
    tentativa automática — sem isso, o `cod=13` da Newmo ("já existe um
    atendimento pendente") bloquearia a 2ª/3ª tentativa da mesma
    pendência (ver `docs/templates_whatsapp.md` seção 9). Não grava nada
    de volta no Supabase: só fecha o atendimento do lado do Newmo — se a
    tratativa ainda estiver elegível, o próximo disparo abre um
    atendimento novo sozinho. Quando/quem dispara essa rotina à noite
    (agendador do SO, botão manual no painel) é decisão de operação/UI,
    fora do escopo desta função.

    Uma falha ao finalizar UM item (transporte, ou qualquer exceção
    inesperada de `newmo_client.finalizar_atendimento`) não derruba o
    lote inteiro — conta como falha e segue pro próximo.
    """
    try:
        candidatas = (
            candidatas if candidatas is not None else supabase_client.buscar_candidatas_finalizacao_atendimento()
        )
        limite_tentativas = int(supabase_client.buscar_parametros().get("limite_tentativas_disparo", 3))

        finalizadas = falhas = 0
        total_candidatas = len(candidatas)
        for indice, tratativa in enumerate(candidatas):
            if cancelar_checker is not None and cancelar_checker():
                return ResultadoEtapa(
                    "finalizar_atendimentos_diarios", sucesso=False,
                    mensagem=f"Cancelado pelo usuário ({total_candidatas - indice} pendente(s)).",
                    dados={"finalizadas": finalizadas, "falhas": falhas, "total_candidatas": total_candidatas},
                    cancelado={"pendentes": candidatas[indice:]},
                )
            if on_progresso is not None:
                on_progresso(indice + 1, total_candidatas)
            if not _deve_finalizar_atendimento(tratativa, limite_tentativas):
                continue
            try:
                newmo_client.finalizar_atendimento(tratativa["atendimento_id"])
                finalizadas += 1
            except Exception:  # noqa: BLE001 - um item ruim não derruba o lote inteiro
                falhas += 1
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("finalizar_atendimentos_diarios", sucesso=False, mensagem=_mensagem_com_notas(e))

    return ResultadoEtapa(
        "finalizar_atendimentos_diarios",
        sucesso=True,
        dados={"finalizadas": finalizadas, "falhas": falhas, "total_candidatas": len(candidatas)},
    )


def _historico_tentativas(tratativa: dict) -> str:
    """3 datas de disparo (`tentativa_1/2/3`), formato `dd/mm/aaaa`
    separadas por vírgula — não existe texto de mensagem pra mostrar
    ainda (Newmo não implementado), só as datas, suficiente pro atendente
    ter métrica de quando cada tentativa aconteceu."""
    datas = []
    for campo in ("tentativa_1", "tentativa_2", "tentativa_3"):
        valor = tratativa.get(campo)
        if not valor:
            continue
        try:
            datas.append(datetime.fromisoformat(valor).strftime("%d/%m/%Y"))
        except ValueError:
            datas.append(str(valor))
    return ", ".join(datas)


def _ultimo_contato_sheet_pendente_ligacao(linha: dict) -> date | None:
    """Recalcula o anchor de "Dias sem contato" a partir dos próprios
    campos de texto já presentes na linha da aba "Pendente de Ligação"
    (sem round-trip ao Supabase) — usado pra linhas que já estavam na
    aba, não passaram (de novo) por `_linha_pendente_ligacao`. "Data
    Contato" preenchida (uma ligação já aconteceu) tem prioridade sobre
    a última data de "Histórico de Tentativas (msg)"."""
    data_contato = _parse_data_atendente(linha.get("Data Contato", ""))
    if data_contato:
        return date.fromisoformat(data_contato)
    historico = (linha.get("Histórico de Tentativas (msg)") or "").strip()
    if not historico:
        return None
    ultima_tentativa = _parse_data_atendente(historico.split(",")[-1].strip())
    return date.fromisoformat(ultima_tentativa) if ultima_tentativa else None


def _recomputar_dias_sem_contato_pendente_ligacao(linha: dict, agora: date) -> dict:
    linha = dict(linha)
    linha["Dias sem contato"] = _dias_sem_contato(_ultimo_contato_sheet_pendente_ligacao(linha), agora)
    return linha


_ATENDIMENTO_CODIGO_PARA_LABEL = {v: k for k, v in _ATENDIMENTO_LABEL_PARA_CODIGO.items()}


def _linha_pendente_ligacao(
    tratativa: dict, templates: dict, agora: date,
    bases_por_id: dict | None = None, pontos_acao_por_id: dict | None = None,
) -> dict:
    """Monta uma linha da aba "Pendente de Ligação" a partir de uma
    tratativa recém-escalada (linha crua do Supabase, não de
    `_montar_linha_resultado`). `Nível de Urgência` deriva de
    `codigo_regra` via `rule_templates` — mesmo princípio já fechado pra
    `tratativas.codigo_regra` ("deriva a urgência via consulta em
    rule_templates no momento do relatório"), porque a tabela não
    denormaliza `nivel_urgencia`. Campos que só o atendente preenche
    depois (`Data Contato`/`Retornou?`/`Conseguiu Agendar?`/`Observação`)
    nascem vazios.

    "Atendimento"/"Base"/"Ponto de Ação" (2026-08-14): populados com o
    que já estava decidido em Tratativas antes da escalada — achado ao
    vivo de que essas 3 colunas existiam na aba, tinham dropdown, mas
    nunca eram preenchidas nem lidas de volta (dead columns). `bases_
    por_id`/`pontos_acao_por_id` (mapas id->nome, default `{}` pra
    permitir chamar isolado) convertem os uuids de volta pro nome
    exibido no dropdown."""
    codigo_regra = tratativa.get("codigo_regra") or ""
    bases_por_id = bases_por_id or {}
    pontos_acao_por_id = pontos_acao_por_id or {}
    return {
        "ID (hash)": tratativa.get("chave_unica", ""),
        "Tipo Serviço": _TIPO_SERVICO_LABEL.get(tratativa.get("origem", ""), tratativa.get("origem", "")),
        "Identificador": tratativa.get("identificador", ""),
        "Chassi": tratativa.get("chassi", ""),
        "Cliente": tratativa.get("cliente", ""),
        "Telefone": tratativa.get("telefone", ""),
        "Cidade": tratativa.get("cidade", ""),
        "Bairro": tratativa.get("bairro", ""),
        "Data Contrato / Data Incidente": _formatar_data_referencia_para_exibicao(tratativa.get("data_referencia")),
        "SGA": tratativa.get("sga", ""),
        "Ação Sugerida": tratativa.get("acao_sugerida", ""),
        "Observação do Sistema": tratativa.get("observacao_sistema", ""),
        "Nível de Urgência": templates.get(codigo_regra, {}).get("nivel_urgencia", ""),
        "Dias sem contato": _dias_sem_contato(_ultimo_contato_tratativa(tratativa), agora),
        "Atendimento": _ATENDIMENTO_CODIGO_PARA_LABEL.get(tratativa.get("atendimento"), ""),
        "Base": bases_por_id.get(tratativa.get("base_id"), ""),
        "Ponto de Ação": pontos_acao_por_id.get(tratativa.get("ponto_acao_id"), ""),
        "Histórico de Tentativas (msg)": _historico_tentativas(tratativa),
        "Data Contato": "",
        "Retornou?": "",
        "Conseguiu Agendar?": "",
        "Observação": "",
        "Finalizado": False,
    }


def etapa_escalonar_ligacao(
    candidatas: list[dict] | None = None, agora: datetime | None = None,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
) -> ResultadoEtapa:
    """Fase F.3 — 3 tentativas de mensagem sem resposta escalam pra
    ligação, automático (`core.escalonamento.deve_escalar_para_ligacao`).
    `candidatas` (default: `supabase_client.buscar_candidatas_
    escalonamento_ligacao()`, `status == aguardando_resposta`) recebe o
    filtro grosso do Supabase; a checagem fina (tentativas >= 3, sem
    `situacao_manual`) é feita aqui, item a item.

    Marca cada elegível como `aguardando_ligacao`
    (`supabase_client.marcar_aguardando_ligacao`) — a partir daí some das
    próximas buscas por `status == aguardando_resposta`, então nunca é
    escalada duas vezes.

    Publica em "Pendente de Ligação" preservando as linhas que já
    estavam lá e ainda não foram processadas (lê antes de reescrever,
    mesmo princípio da Fase E) — só acrescenta as recém-escaladas nesta
    execução.
    """
    try:
        agora_data = (agora or datetime.now()).date()
        candidatas = (
            candidatas if candidatas is not None
            else supabase_client.buscar_candidatas_escalonamento_ligacao()
        )
        templates = supabase_client.buscar_rule_templates()
        limite_tentativas = int(supabase_client.buscar_parametros().get("limite_tentativas_disparo", 3))
        bases_por_id = {b["id"]: b["nome"] for b in supabase_client.buscar_bases_ativas()}
        pontos_acao_por_id = {p["id"]: p["nome_local"] for p in supabase_client.buscar_pontos_acao_ativos()}

        escaladas = []
        total_candidatas = len(candidatas)
        cancelado_em = None
        for indice, tratativa in enumerate(candidatas):
            if cancelar_checker is not None and cancelar_checker():
                cancelado_em = indice
                break
            if on_progresso is not None:
                on_progresso(indice + 1, total_candidatas)
            if not escalonamento.deve_escalar_para_ligacao(tratativa, limite_tentativas):
                continue
            supabase_client.marcar_aguardando_ligacao(tratativa["id"])
            escaladas.append(tratativa)

        linhas_existentes = [
            _recomputar_dias_sem_contato_pendente_ligacao(linha, agora_data)
            for linha in google_sheets_client.ler_aba(
                google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Pendente de Ligação"
            )
        ]
        linhas_novas = [
            _linha_pendente_ligacao(t, templates, agora_data, bases_por_id, pontos_acao_por_id)
            for t in escaladas
        ]
        google_sheets_client.reescrever_aba(
            google_sheets_client.NOME_PLANILHA_OPERACIONAL,
            "Pendente de Ligação",
            linhas_existentes + linhas_novas,
        )
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("escalonar_ligacao", sucesso=False, mensagem=_mensagem_com_notas(e))

    if cancelado_em is not None:
        return ResultadoEtapa(
            "escalonar_ligacao", sucesso=False,
            mensagem=f"Cancelado pelo usuário ({total_candidatas - cancelado_em} pendente(s)).",
            dados={"escaladas": len(escaladas), "total_candidatas": total_candidatas},
            cancelado={"pendentes": candidatas[cancelado_em:]},
        )

    return ResultadoEtapa(
        "escalonar_ligacao",
        sucesso=True,
        dados={"escaladas": len(escaladas), "total_candidatas": len(candidatas)},
    )


def _resultado_ligacao_da_linha(linha: dict) -> tuple[bool, bool] | None:
    """`None` se a linha ainda não está pronta pra processar (`Retornou?`
    vazio, ou "Sim" com `Conseguiu Agendar?` ainda vazio/"Não" — o
    atendente continua tentando agendar). Devolve `(retornou,
    conseguiu_agendar)` só nas duas combinações que geram ação: "Não"
    (`False, False`, vai pra Puma) e "Sim"+"Sim" (`True, True`, mas a
    linha não sai da aba sozinha — ver `Finalizado`, checado antes desta
    função ser chamada)."""
    retornou_raw = (linha.get("Retornou?") or "").strip().lower()
    if retornou_raw not in ("sim", "não", "nao"):
        return None
    if retornou_raw != "sim":
        return False, False
    conseguiu_raw = (linha.get("Conseguiu Agendar?") or "").strip().lower()
    if conseguiu_raw != "sim":
        return None
    return True, True


def _ultimo_contato_sheet_puma(linha: dict) -> date | None:
    """Anchor de "Dias sem contato" pra linha já existente em
    "Encaminhar pra Puma" (só acompanhamento, sem limite/destaque) —
    parseado direto de "Data Encaminhamento", já presente na linha."""
    iso = _parse_data_atendente(linha.get("Data Encaminhamento", ""))
    return date.fromisoformat(iso) if iso else None


def _recomputar_dias_sem_contato_puma(linha: dict, agora: date) -> dict:
    linha = dict(linha)
    linha["Dias sem contato"] = _dias_sem_contato(_ultimo_contato_sheet_puma(linha), agora)
    return linha


def _linha_encaminhar_puma(tratativa: dict, motivo: str, templates: dict) -> dict:
    """Monta uma linha nova em "Encaminhar pra Puma" a partir da
    tratativa crua do Supabase — mesmo princípio de `_linha_pendente_
    ligacao` (F.3): `Nível de Urgência` deriva de `codigo_regra` via
    `rule_templates`, porque a tabela não denormaliza isso."""
    codigo_regra = tratativa.get("codigo_regra") or ""
    return {
        "ID (hash)": tratativa.get("chave_unica", ""),
        "Tipo Serviço": _TIPO_SERVICO_LABEL.get(tratativa.get("origem", ""), tratativa.get("origem", "")),
        "Identificador": tratativa.get("identificador", ""),
        "Chassi": tratativa.get("chassi", ""),
        "Cliente": tratativa.get("cliente", ""),
        "Telefone": tratativa.get("telefone", ""),
        "Cidade": tratativa.get("cidade", ""),
        "Bairro": tratativa.get("bairro", ""),
        "Data Contrato / Data Incidente": _formatar_data_referencia_para_exibicao(tratativa.get("data_referencia")),
        "Ação Sugerida": tratativa.get("acao_sugerida", ""),
        "Observação do Sistema": tratativa.get("observacao_sistema", ""),
        "Nível de Urgência": templates.get(codigo_regra, {}).get("nivel_urgencia", ""),
        "Dias sem contato": _formatar_dias_sem_contato(0),
        "Data Encaminhamento": datetime.now().strftime("%d/%m/%Y"),
        "Motivo": motivo,
        "Status": "",
        "Observação Puma": "",
    }


def _puma_concluido(linha: dict) -> bool:
    return (linha.get("Status") or "").strip().lower() == PUMA_STATUS_CONCLUIDO


def _puma_concluido_automaticamente(
    tratativa: dict, equipamentos: list[dict], incidentes: list[dict], parametros: dict
) -> bool:
    """Decisão de negócio fechada 2026-08-14 (chat #16): o sistema
    reconhece sozinho quando manutenção/instalação já escaladas pra Puma
    foram concluídas por fora, reaproveitando a mesma condição de
    `REGRA_2`/`REGRA_5_3` (incidente não está mais aberto) e de
    `REGRA_INSTALACAO_JA_FEITA` (chassi já em Rastreadores Ativos).
    Remoção fica de fora — "não existe um processo confiável ainda pra
    garantir essa automação", decisão explícita do usuário."""
    origem = tratativa.get("origem", "")
    if origem == ORIGEM_MANUTENCAO:
        return motor_regras.manutencao_concluida_para_placa(
            tratativa.get("placa", ""), incidentes, parametros
        )
    if origem == ORIGEM_INSTALACAO:
        return motor_regras_instalacao_remocao.chassi_ja_instalado(
            tratativa.get("chassi", ""), equipamentos
        )
    return False


_MARCADOR_CONCLUSAO_AUTOMATICA = "Detectado automaticamente:"


def _observacao_com_nota_automatica(observacao_atual: str, tipo_servico_label: str, agora: date) -> str:
    """Anexa a nota automática sem sobrescrever anotação humana já
    existente, e sem duplicar se o ciclo rodar de novo antes da linha
    sair da aba (mesma linha pode ser avaliada em mais de uma execução
    até `Status=concluido` ser sincronizado e ela sumir)."""
    observacao_atual = observacao_atual or ""
    if _MARCADOR_CONCLUSAO_AUTOMATICA in observacao_atual:
        return observacao_atual
    nota = f"{_MARCADOR_CONCLUSAO_AUTOMATICA} {tipo_servico_label.lower()} concluída em {agora.strftime('%d/%m/%Y')}."
    return f"{observacao_atual} | {nota}" if observacao_atual else nota


def etapa_processar_resultado_ligacao(
    linhas: list[dict] | None = None, agora: datetime | None = None,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    equipamentos: list[dict] | None = None, incidentes: list[dict] | None = None,
) -> ResultadoEtapa:
    """Fase F.4 — lê o resultado que o atendente registrou em "Pendente
    de Ligação" e decide o destino final de cada linha. Regra de leitura
    (ensinada pelo usuário em 3 rodadas, 2026-08-07 — `Retornou?`/
    `Conseguiu Agendar?` são dropdown de texto Sim/Não, não checkbox):

    1. `Finalizado` (checkbox) marcado -> sempre vence, não importa o
       resto. Cobre qualquer desfecho fora do padrão (ex: associado
       resolveu por outro canal) e é o único jeito de tirar a linha da
       aba depois de um agendamento (caminho 4). Marca a tratativa
       finalizada via `sincronizar_campos_atendente` (sem passar por
       `registrar_ligacao` — não é uma ligação de verdade).
    2. `Retornou?` vazio -> ainda não trabalhada, fica como está.
    3. `Retornou?` = "Não" -> único caminho automático pra "Encaminhar
       pra Puma". Registra a ligação e já sai de "Pendente de Ligação"
       neste ciclo.
    4. `Retornou?` = "Sim" e `Conseguiu Agendar?` = "Sim" -> registra a
       ligação (só na primeira vez — guarda contra duplicar em
       `ligacoes` checando se a tratativa já está `finalizado`), mas a
       linha **continua** na aba: "conseguiu agendar" é um agendamento
       futuro, não uma conclusão.
    5. `Retornou?` = "Sim" e `Conseguiu Agendar?` = "Não"/vazio -> linha
       permanece, nenhuma chamada ao Supabase.

    Também limpa "Encaminhar pra Puma": remove as linhas com
    `Status = concluido` a cada execução — e, antes de remover, sincroniza
    esse status de volta pro Supabase via `supabase_client.
    sincronizar_status_puma` (fecha o gap achado na implementação do
    Dashboard: `puma_encaminhamentos.status` nunca era escrito antes).
    Também sincroniza "Observação Puma" (todo ciclo, linha concluída ou
    não). E sincroniza "Atendimento"/"Base"/"Ponto de Ação" de volta pra
    `tratativas` pra toda linha desta aba (2026-08-14 — achado ao vivo de
    que essas 3 colunas existiam, tinham dropdown, mas nunca eram lidas
    de volta; mesma filosofia incondicional de `_sincronizar_atendente_
    da_aba`, a aba é a fonte de verdade enquanto a pendência está aqui).

    Além da conclusão manual (`Status=concluido` editado por humano), o
    sistema agora reconhece sozinho quando manutenção/instalação
    escaladas pra Puma foram concluídas por fora (`_puma_concluido_
    automaticamente`, decisão de negócio 2026-08-14) — reaproveita
    `equipamentos`/`incidentes` já lidos nesta mesma execução (via
    `orchestrator.catalogo_etapas`) ou relidos da planilha Administrador
    quando a etapa roda isolada, mesmo padrão de `equipamentos`/
    `instalacao_remocao` no resto do módulo.
    """
    try:
        agora_data = (agora or datetime.now()).date()
        linhas = (
            linhas if linhas is not None
            else google_sheets_client.ler_aba(google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Pendente de Ligação")
        )
        if equipamentos is None or incidentes is None:
            dados_administrador = _ler_dados_administrador()
            equipamentos = equipamentos if equipamentos is not None else dados_administrador["equipamentos"]
            incidentes = incidentes if incidentes is not None else dados_administrador["incidentes"]
        parametros = supabase_client.buscar_parametros()
        templates = supabase_client.buscar_rule_templates()
        bases_por_nome = {b["nome"]: b["id"] for b in supabase_client.buscar_bases_ativas()}
        pontos_acao_por_nome = {p["nome_local"]: p["id"] for p in supabase_client.buscar_pontos_acao_ativos()}

        linhas_restantes = []
        linhas_puma_novas = []
        processadas = 0
        total_linhas = len(linhas)
        cancelado_em = None
        for indice, linha in enumerate(linhas):
            if cancelar_checker is not None and cancelar_checker():
                cancelado_em = indice
                # linhas ainda não alcançadas continuam na aba como estavam
                # (senão o reescrever_aba de baixo as apagaria)
                linhas_restantes.extend(
                    _recomputar_dias_sem_contato_pendente_ligacao(l, agora_data) for l in linhas[indice:]
                )
                break
            if on_progresso is not None:
                on_progresso(indice + 1, total_linhas)
            chave = linha.get("ID (hash)", "")

            supabase_client.sincronizar_campos_atendente(chave, {
                "atendimento": _ATENDIMENTO_LABEL_PARA_CODIGO.get(linha.get("Atendimento") or "", ""),
                "base_id": bases_por_nome.get(linha.get("Base") or ""),
                "ponto_acao_id": pontos_acao_por_nome.get(linha.get("Ponto de Ação") or ""),
            })

            if _checkbox_sheets(linha.get("Finalizado")):
                supabase_client.sincronizar_campos_atendente(chave, {"status": STATUS_FINALIZADO})
                processadas += 1
                continue

            resultado = _resultado_ligacao_da_linha(linha)
            if resultado is None:
                linhas_restantes.append(_recomputar_dias_sem_contato_pendente_ligacao(linha, agora_data))
                continue

            tratativa = supabase_client.buscar_tratativa_por_chave(chave)
            if tratativa is None:
                linhas_restantes.append(_recomputar_dias_sem_contato_pendente_ligacao(linha, agora_data))
                continue

            retornou, conseguiu_agendar = resultado
            destino = escalonamento.resultado_ligacao(conseguiu_agendar)

            if destino == STATUS_ENCAMINHADO_PUMA:
                supabase_client.registrar_ligacao(tratativa["id"], {
                    "data_contato": _parse_data_atendente(linha.get("Data Contato", "")),
                    "retornou": retornou, "conseguiu_agendar": conseguiu_agendar,
                    "observacao": linha.get("Observação", ""),
                })
                motivo = "Associado não retornou a ligação."
                supabase_client.encaminhar_puma(tratativa["id"], motivo)
                linhas_puma_novas.append(_linha_encaminhar_puma(tratativa, motivo, templates))
                processadas += 1
                continue

            if tratativa.get("status") != STATUS_FINALIZADO:
                supabase_client.registrar_ligacao(tratativa["id"], {
                    "data_contato": _parse_data_atendente(linha.get("Data Contato", "")),
                    "retornou": retornou, "conseguiu_agendar": conseguiu_agendar,
                    "observacao": linha.get("Observação", ""),
                })
                processadas += 1
            linhas_restantes.append(_recomputar_dias_sem_contato_pendente_ligacao(linha, agora_data))

        existentes_puma = google_sheets_client.ler_aba(
            google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Encaminhar pra Puma"
        )
        puma_restantes = []
        for linha in existentes_puma:
            # Busca a tratativa ANTES de decidir `concluido` — a checagem
            # automática nova precisa de `origem`/`chassi`/`placa`, que só
            # vêm daqui (a linha da planilha não carrega esses campos).
            tratativa_puma = supabase_client.buscar_tratativa_por_chave(linha.get("ID (hash)", ""))

            concluido_manual = _puma_concluido(linha)
            concluido_automatico = (
                not concluido_manual and tratativa_puma is not None
                and _puma_concluido_automaticamente(tratativa_puma, equipamentos, incidentes, parametros)
            )
            if concluido_automatico:
                linha = {
                    **linha,
                    "Status": PUMA_STATUS_CONCLUIDO,
                    "Observação Puma": _observacao_com_nota_automatica(
                        linha.get("Observação Puma") or "", linha.get("Tipo Serviço", ""), agora_data
                    ),
                }
            concluido = concluido_manual or concluido_automatico

            if not concluido:
                puma_restantes.append(_recomputar_dias_sem_contato_puma(linha, agora_data))
            # Único ponto de leitura das colunas "Status"/"Observação Puma"
            # desta aba — fecha o caminho de volta pro Supabase (achado do
            # Dashboard: `puma_encaminhamentos.status` nunca era escrito
            # antes, só nascia com o default; "Observação Puma" nunca
            # tinha caminho de volta nenhum, 2026-08-14). Cobre as 3
            # origens do "Status=concluido" na planilha (edição humana,
            # `_resolver_acao_alerta`, e a checagem automática acima).
            # Roda pra toda linha, concluída ou não — a observação pode
            # ser anotada antes da conclusão.
            if tratativa_puma is None:
                continue
            if concluido:
                supabase_client.sincronizar_status_puma(tratativa_puma["id"], PUMA_STATUS_CONCLUIDO)
            supabase_client.sincronizar_observacao_puma(tratativa_puma["id"], linha.get("Observação Puma") or "")
        google_sheets_client.reescrever_aba(
            google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Encaminhar pra Puma",
            puma_restantes + linhas_puma_novas,
        )

        google_sheets_client.reescrever_aba(
            google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Pendente de Ligação", linhas_restantes
        )
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("processar_resultado_ligacao", sucesso=False, mensagem=_mensagem_com_notas(e))

    if cancelado_em is not None:
        return ResultadoEtapa(
            "processar_resultado_ligacao", sucesso=False,
            mensagem=f"Cancelado pelo usuário ({total_linhas - cancelado_em} pendente(s)).",
            dados={
                "processadas": processadas,
                "encaminhadas_puma": len(linhas_puma_novas),
                "restantes": len(linhas_restantes),
                "removidas_de_puma_concluidas": len(existentes_puma) - len(puma_restantes),
            },
            cancelado={"pendentes": linhas[cancelado_em:]},
        )

    return ResultadoEtapa(
        "processar_resultado_ligacao",
        sucesso=True,
        dados={
            "processadas": processadas,
            "encaminhadas_puma": len(linhas_puma_novas),
            "restantes": len(linhas_restantes),
            "removidas_de_puma_concluidas": len(existentes_puma) - len(puma_restantes),
        },
    )


_TIPO_ALERTA_RETORNO_TARDIO = "Retorno tardio"
_TIPO_ALERTA_AGENDADO_SEM_DATA = "Agendado sem data"
_CONTEXTO_AGENDADO_SEM_DATA = "Confirmado via WhatsApp"

_ONDE_ESTA_LABEL = {
    STATUS_PENDENTE: "Tratativas",
    STATUS_AGUARDANDO_RESPOSTA: "Tratativas",
    STATUS_RESPONDIDO: "Tratativas",
    STATUS_AGUARDANDO_LIGACAO: "Pendente de Ligação",
    STATUS_ENCAMINHADO_PUMA: "Encaminhar pra Puma",
    STATUS_FINALIZADO: "Finalizado",
}


def _linha_alerta(tratativa: dict, tipo: str, contexto: str) -> dict:
    status = tratativa.get("status", "")
    return {
        "ID (hash)": tratativa.get("chave_unica", ""),
        "Cliente": tratativa.get("cliente", ""),
        "Telefone": tratativa.get("telefone", ""),
        "Onde está": _ONDE_ESTA_LABEL.get(status, status),
        "Tipo de Alerta": tipo,
        "Contexto": contexto,
        "Ação": "",
        "Data Agendada": "",
    }


def _linhas_alertas(candidatas: dict[str, list[dict]]) -> list[dict]:
    linhas = [
        _linha_alerta(t, _TIPO_ALERTA_RETORNO_TARDIO, t.get("retorno_associado", ""))
        for t in candidatas["retorno_tardio"]
    ]
    linhas += [
        _linha_alerta(t, _TIPO_ALERTA_AGENDADO_SEM_DATA, _CONTEXTO_AGENDADO_SEM_DATA)
        for t in candidatas["agendado_sem_data"]
    ]
    return linhas


def _remover_linha_por_chave(aba: str, chave: str) -> None:
    linhas = google_sheets_client.ler_aba(google_sheets_client.NOME_PLANILHA_OPERACIONAL, aba)
    restantes = [linha for linha in linhas if linha.get("ID (hash)") != chave]
    google_sheets_client.reescrever_aba(google_sheets_client.NOME_PLANILHA_OPERACIONAL, aba, restantes)


def _marcar_status_puma_por_chave(chave: str) -> None:
    """Escreve `Status = concluido` na linha correspondente de "Encaminhar
    pra Puma" — a limpeza automática que `etapa_processar_resultado_
    ligacao` já faz nesse campo remove a linha no PRÓXIMO ciclo (reaproveita
    100% o mecanismo existente, não duplica lógica de remoção aqui)."""
    linhas = google_sheets_client.ler_aba(google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Encaminhar pra Puma")
    atualizadas = [
        {**linha, "Status": PUMA_STATUS_CONCLUIDO} if linha.get("ID (hash)") == chave else linha
        for linha in linhas
    ]
    google_sheets_client.reescrever_aba(
        google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Encaminhar pra Puma", atualizadas
    )


def _resolver_acao_alerta(chave: str, acao: str) -> None:
    """"Foi engano, ignorar" só limpa o sinal. "Confirma conclusão" resolve
    a tratativa na aba de origem CERTA (olhando o `status` atual) — nunca
    toca `tratativas.status` no caminho `encaminhado_puma` direto; só marca
    `Status=concluido` na planilha e deixa `etapa_processar_resultado_
    ligacao` (único ponto de leitura dessa coluna) sincronizar de volta
    pro Supabase no próximo ciclo."""
    if acao == "Foi engano, ignorar":
        supabase_client.sincronizar_campos_atendente(chave, {"retorno_associado": None})
        return
    if acao != "Confirma conclusão":
        return

    tratativa = supabase_client.buscar_tratativa_por_chave(chave)
    if tratativa is None:
        return

    status = tratativa.get("status")
    if status == STATUS_AGUARDANDO_LIGACAO:
        supabase_client.sincronizar_campos_atendente(
            chave, {"status": STATUS_FINALIZADO, "retorno_associado": None}
        )
        _remover_linha_por_chave("Pendente de Ligação", chave)
    elif status == STATUS_ENCAMINHADO_PUMA:
        supabase_client.sincronizar_campos_atendente(chave, {"retorno_associado": None})
        _marcar_status_puma_por_chave(chave)
    else:
        supabase_client.sincronizar_campos_atendente(
            chave, {"status": STATUS_FINALIZADO, "retorno_associado": None}
        )


def etapa_processar_alertas(
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
) -> ResultadoEtapa:
    """Fase F.5 — fila de exceção "Alertas", complementar às 3 abas
    operacionais (não substitui `Finalizado`/`Status=concluido`, que já
    resolvem o caminho previsível sozinhas). Roda depois de F.3/F.4 no
    ciclo, pra ver o `status` mais recente que elas produzem.

    Ordem (mesmo princípio de "sincroniza a edição do atendente ANTES de
    republicar" já usado na Fase E/F.3/F.4):
    1. Lê a aba "Alertas" atual; pra cada linha com "Ação" preenchida,
       resolve (`_resolver_acao_alerta`); pra cada linha (sem "Ação") com
       "Data Agendada" preenchida e parseável, grava a data na tratativa.
    2. Busca candidatas frescas (`buscar_candidatas_alertas` — já reflete
       as resoluções do passo 1) e reescreve "Alertas" do zero.
    """
    try:
        linhas_atuais = google_sheets_client.ler_aba(google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Alertas")

        resolvidas = 0
        total_linhas_atuais = len(linhas_atuais)
        cancelado_em = None
        for indice, linha in enumerate(linhas_atuais):
            if cancelar_checker is not None and cancelar_checker():
                cancelado_em = indice
                break
            if on_progresso is not None:
                on_progresso(indice + 1, total_linhas_atuais)
            chave = (linha.get("ID (hash)") or "").strip()
            if not chave:
                continue

            acao = (linha.get("Ação") or "").strip()
            if acao:
                _resolver_acao_alerta(chave, acao)
                resolvidas += 1
                continue

            data_agendada_iso = _parse_data_atendente(linha.get("Data Agendada", ""))
            if data_agendada_iso is not None:
                supabase_client.sincronizar_campos_atendente(chave, {"data_agendada": data_agendada_iso})
                resolvidas += 1

        candidatas = supabase_client.buscar_candidatas_alertas()
        linhas_novas = _linhas_alertas(candidatas)
        google_sheets_client.reescrever_aba(google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Alertas", linhas_novas)
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("processar_alertas", sucesso=False, mensagem=_mensagem_com_notas(e))

    if cancelado_em is not None:
        return ResultadoEtapa(
            "processar_alertas", sucesso=False,
            mensagem=f"Cancelado pelo usuário ({total_linhas_atuais - cancelado_em} pendente(s)).",
            dados={"resolvidas": resolvidas, "total_alertas": len(linhas_novas)},
            cancelado={"pendentes": linhas_atuais[cancelado_em:]},
        )

    return ResultadoEtapa(
        "processar_alertas",
        sucesso=True,
        dados={"resolvidas": resolvidas, "total_alertas": len(linhas_novas)},
    )


def etapa_relatorio_diario():
    raise NotImplementedError
