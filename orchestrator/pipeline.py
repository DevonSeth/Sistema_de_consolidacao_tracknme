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

import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
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
)
from config import manager
from integrations import google_sheets_client, newmo_client, playwright_utils, sga_bot, supabase_client, tracknme_bot


@dataclass
class ResultadoEtapa:
    etapa: str
    sucesso: bool
    mensagem: str = ""
    dados: dict = field(default_factory=dict)
    aguardando_reconexao: dict | None = None
    cancelado: dict | None = None


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
        return ResultadoEtapa("baixar_relatorios", sucesso=False, mensagem=str(e))
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
        return ResultadoEtapa("ler_planilha_gestor", sucesso=False, mensagem=str(e))

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
        return ResultadoEtapa("motor_de_regras", sucesso=False, mensagem=str(e))

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
                    on_item_iniciado=on_item_iniciado,
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


def _resultado_aguardando_reconexao(
    etapa: str, reconexao: "playwright_utils.AguardandoReconexao", chave_sucesso: str,
    descrever_item: Callable[[Any], str] | None = None,
) -> ResultadoEtapa:
    """Monta o `ResultadoEtapa` de uma queda de sessão a partir de
    `reconexao.processados` (itens que já tinham resultado antes da
    queda) — mesmo padrão de separação sucesso/falha já usado no
    caminho normal (`[r.resultado["linha"] for r in resultados if
    r.sucesso]`). `descrever_item` (opcional) preenche `"descricao"` em
    cada falha, mesmo formato usado no caminho normal (ver
    `_descrever_linha_incidente`)."""
    descrever = descrever_item or str
    return ResultadoEtapa(
        etapa,
        sucesso=False,
        mensagem=f"Sessão caída — aguardando reconexão manual ({len(reconexao.pendentes)} pendente(s)).",
        dados={
            chave_sucesso: [r.resultado["linha"] for r in reconexao.processados if r.sucesso],
            "falhas": [
                {"linha": r.item, "erro": r.erro, "descricao": descrever(r.item)}
                for r in reconexao.processados if not r.sucesso
            ],
        },
        aguardando_reconexao={"pendentes": reconexao.pendentes},
    )


def _resultado_cancelado(
    etapa: str, cancelamento: "playwright_utils.CancelamentoSolicitado", chave_sucesso: str,
    descrever_item: Callable[[Any], str] | None = None,
) -> ResultadoEtapa:
    """Mesmo padrão de `_resultado_aguardando_reconexao`, mas pra
    cancelamento pedido pelo usuário no meio do processamento da fila —
    nunca retomada automática, quem chama trata como parada definitiva."""
    descrever = descrever_item or str
    return ResultadoEtapa(
        etapa,
        sucesso=False,
        mensagem=f"Cancelado pelo usuário ({len(cancelamento.pendentes)} pendente(s)).",
        dados={
            chave_sucesso: [r.resultado["linha"] for r in cancelamento.processados if r.sucesso],
            "falhas": [
                {"linha": r.item, "erro": r.erro, "descricao": descrever(r.item)}
                for r in cancelamento.processados if not r.sucesso
            ],
        },
        cancelado={"pendentes": cancelamento.pendentes},
    )


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
    `on_progresso`/`cancelar_checker` (opcionais) repassam pro
    `processar_fila` subjacente — ver
    `integrations.playwright_utils.processar_fila`. `on_worker_status`
    (opcional) reporta o que cada worker está processando agora
    (`"Placa X — Cliente Y"`), ver `_processar_fila_com_navegador`."""
    if dados is None:
        resultado_motor = etapa_motor_de_regras()
        if not resultado_motor.sucesso:
            return ResultadoEtapa(
                "abrir_incidentes_automaticos", sucesso=False, mensagem=resultado_motor.mensagem
            )
        dados = resultado_motor.dados

    resultados, erro, reconexao, cancelamento = await _processar_fila_com_navegador(
        dados["grupo_1_abrir"], _acao_abrir, on_progresso=on_progresso, cancelar_checker=cancelar_checker,
        descrever_item=_descrever_linha_incidente, on_worker_status=on_worker_status,
    )
    if reconexao is not None:
        return _resultado_aguardando_reconexao(
            "abrir_incidentes_automaticos", reconexao, "abertos", _descrever_linha_incidente
        )
    if cancelamento is not None:
        return _resultado_cancelado(
            "abrir_incidentes_automaticos", cancelamento, "abertos", _descrever_linha_incidente
        )
    if erro is not None:
        return ResultadoEtapa("abrir_incidentes_automaticos", sucesso=False, mensagem=erro)

    return ResultadoEtapa(
        "abrir_incidentes_automaticos",
        sucesso=True,
        dados={
            "abertos": [r.resultado["linha"] for r in resultados if r.sucesso],
            "falhas": [
                {"linha": r.item, "erro": r.erro, "descricao": _descrever_linha_incidente(r.item)}
                for r in resultados if not r.sucesso
            ],
        },
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

    `motivo` de `concluir_incidente` = `observacao_sistema` da linha;
    `numero_incidente` = `id` da linha (confirmado que é o mesmo número
    que a tela Operador busca) — evita ambiguidade quando a placa tem
    mais de um incidente aberto. `IncidenteDuplicadoError`/
    `MultiplosIncidentesAbertosError` não têm tratamento diferenciado
    (decisão do usuário) — seguem o retry padrão de `processar_fila`
    igual qualquer outra falha de item. `on_progresso`/`cancelar_checker`
    (opcionais) repassam pro `processar_fila` subjacente. `on_worker_status`
    (opcional) reporta o que cada worker está processando agora.
    """
    if dados is None:
        resultado_consolidacao = await etapa_consolidar_com_sga()
        if not resultado_consolidacao.sucesso:
            return ResultadoEtapa(
                "fechar_incidentes_automaticos", sucesso=False, mensagem=resultado_consolidacao.mensagem
            )
        dados = resultado_consolidacao.dados

    resultados, erro, reconexao, cancelamento = await _processar_fila_com_navegador(
        dados["grupo_2_concluir"], _acao_concluir, on_progresso=on_progresso, cancelar_checker=cancelar_checker,
        descrever_item=_descrever_linha_incidente, on_worker_status=on_worker_status,
    )
    if reconexao is not None:
        return _resultado_aguardando_reconexao(
            "fechar_incidentes_automaticos", reconexao, "concluidos", _descrever_linha_incidente
        )
    if cancelamento is not None:
        return _resultado_cancelado(
            "fechar_incidentes_automaticos", cancelamento, "concluidos", _descrever_linha_incidente
        )
    if erro is not None:
        return ResultadoEtapa("fechar_incidentes_automaticos", sucesso=False, mensagem=erro)

    return ResultadoEtapa(
        "fechar_incidentes_automaticos",
        sucesso=True,
        dados={
            "concluidos": [r.resultado["linha"] for r in resultados if r.sucesso],
            "falhas": [
                {"linha": r.item, "erro": r.erro, "descricao": _descrever_linha_incidente(r.item)}
                for r in resultados if not r.sucesso
            ],
        },
    )


def _chassis_para_consultar_sga(dados_classificacao: dict, instalacao_remocao: list[dict]) -> list[str]:
    """União dos chassis que sobraram na classificação de Manutenção
    (`grupo_1_abrir`/`grupo_2_concluir`/`grupo_3_tratativa_humana` —
    inclui os recém-abertos do Grupo 1, decisão do usuário) com os
    chassis de Instalação-Remoção. Uma consulta só ao SGA, reaproveitada
    pelas 3 origens."""
    chassis = set()
    for linha in (
        dados_classificacao["grupo_1_abrir"]
        + dados_classificacao["grupo_2_concluir"]
        + dados_classificacao["grupo_3_tratativa_humana"]
    ):
        chassi = (linha.get("chassi") or "").strip().upper()
        if chassi:
            chassis.add(chassi)
    for registro in instalacao_remocao:
        chassi = (registro.get("Chassi") or "").strip().upper()
        if chassi:
            chassis.add(chassi)
    return sorted(chassis)


def _persistir_situacoes_sga(resultados: list, agora: datetime) -> dict:
    """Persiste em `situacao_veiculo_sga` cada chassi com resultado bem
    sucedido e devolve `{chassi: {status, desde, cidade, bairro}}` —
    corpo compartilhado entre o caminho normal e o de reconexão parcial
    de `etapa_enriquecimento_sga` (mesmo trabalho, chassis diferentes)."""
    situacoes_sga = {}
    for r in resultados:
        if not r.sucesso:
            continue
        chassi = r.item
        status_novo = r.resultado["status"]
        anterior = supabase_client.buscar_situacao_veiculo_sga(chassi)
        atualizado = motor_regras_instalacao_remocao.atualizar_situacao_sga(chassi, status_novo, anterior, agora)
        supabase_client.upsert_situacao_veiculo_sga(atualizado)
        situacoes_sga[chassi] = {
            "status": atualizado["status"],
            "desde": atualizado["desde"],
            "cidade": r.resultado["cidade"],
            "bairro": r.resultado["bairro"],
        }
    return situacoes_sga


async def etapa_enriquecimento_sga(
    dados_classificacao: dict | None = None,
    instalacao_remocao: list[dict] | None = None,
    chassis_override: list[str] | None = None,
    on_progresso: Callable[[int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    on_worker_status: Callable[[int, str], None] | None = None,
) -> ResultadoEtapa:
    """Única etapa manual (login no SGA exige captcha) — cobre Manutenção,
    Instalação e Remoção numa consulta só, decisão do usuário: `dados_
    classificacao` (default: `etapa_motor_de_regras()`) dá os chassis de
    Manutenção; `instalacao_remocao` (default: `ler_aba(...)`) dá os
    chassis de Instalação/Remoção. `chassis_override` (usado só na
    retomada depois de uma reconexão manual) ignora os dois defaults
    caros e consulta só os chassis informados — nunca rechama
    `etapa_motor_de_regras()`/`ler_aba(...)` nesse caminho.

    Pra cada chassi: consulta o SGA ao vivo, lê o registro anterior de
    `situacao_veiculo_sga` (Supabase), recalcula com `core.motor_regras_
    instalacao_remocao.atualizar_situacao_sga` (pura — decide se `desde`
    reinicia) e persiste de volta. Devolve um dict só,
    `situacoes_sga` ({chassi: {status, desde, cidade, bairro}}) —
    reaproveitado tanto por `core.motor_regras.aplicar_situacoes_sga`
    (Manutenção, usa status/cidade/bairro) quanto por
    `core.motor_regras_instalacao_remocao.classificar_instalacao_remocao`
    (usa status/desde pro gating de remoção). Antes desta sessão
    (2026-08-07) essa etapa produzia dois dicts em paralelo e descartava
    `cidade`/`bairro` do retorno de `sga_bot.consultar_situacao` — bug
    corrigido junto com a unificação.

    Se a sessão do SGA cair no meio da consulta (`AguardandoReconexao`),
    os chassis que já tinham resultado ANTES da queda são persistidos
    mesmo assim — só os `pendentes` voltam pra tela pedir reconexão
    manual. `on_progresso`/`on_worker_status` (opcionais) repassam pro
    `processar_fila` subjacente — `on_worker_status` reporta o chassi que
    cada worker está consultando agora. `dados["falhas"]` lista os
    chassis que não tiveram sucesso na consulta (antes descartados
    silenciosamente por `_persistir_situacoes_sga`, que só grava quem
    teve sucesso).
    """
    if chassis_override is not None:
        chassis = chassis_override
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
        chassis = _chassis_para_consultar_sga(dados_classificacao, instalacao_remocao)

    on_item_iniciado = None
    if on_worker_status is not None:
        on_item_iniciado = lambda worker_id, chassi: on_worker_status(worker_id, _descrever_chassi_sga(chassi))  # noqa: E731

    try:
        async with async_playwright() as playwright:
            browser, context = await sga_bot.aguardar_login_manual(playwright)
            try:
                resultados = await playwright_utils.processar_fila(
                    context, chassis, sga_bot.consultar_situacao,
                    on_progresso=on_progresso, cancelar_checker=cancelar_checker,
                    on_item_iniciado=on_item_iniciado,
                )
            finally:
                await context.close()
                await browser.close()
    except playwright_utils.AguardandoReconexao as e:
        agora = datetime.now()
        situacoes_sga = _persistir_situacoes_sga(e.processados, agora)
        return ResultadoEtapa(
            "enriquecimento_sga",
            sucesso=False,
            mensagem=f"Sessão caída — aguardando reconexão manual ({len(e.pendentes)} pendente(s)).",
            dados={
                "situacoes_sga": situacoes_sga,
                "falhas": _falhas_com_descricao(e.processados, _descrever_chassi_sga),
            },
            aguardando_reconexao={"pendentes": e.pendentes},
        )
    except playwright_utils.CancelamentoSolicitado as e:
        situacoes_sga = _persistir_situacoes_sga(e.processados, datetime.now())
        return ResultadoEtapa(
            "enriquecimento_sga",
            sucesso=False,
            mensagem=f"Cancelado pelo usuário ({len(e.pendentes)} pendente(s)).",
            dados={
                "situacoes_sga": situacoes_sga,
                "falhas": _falhas_com_descricao(e.processados, _descrever_chassi_sga),
            },
            cancelado={"pendentes": e.pendentes},
        )
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("enriquecimento_sga", sucesso=False, mensagem=str(e))

    situacoes_sga = _persistir_situacoes_sga(resultados, datetime.now())
    return ResultadoEtapa(
        "enriquecimento_sga",
        sucesso=True,
        dados={
            "situacoes_sga": situacoes_sga,
            "falhas": _falhas_com_descricao(resultados, _descrever_chassi_sga),
        },
    )


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
    vem com `origem` própria). `divergencias_instalacao` (itens
    `REGRA_INSTALACAO_JA_FEITA`) vem separado no retorno — nunca entra em
    `fila_operacional`/Tratativas, alimenta a aba própria "Análise de
    Divergência - Instalação" na Fase E.

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
        tratativas_instalacao_remocao, divergencias_instalacao = (
            motor_regras_instalacao_remocao.classificar_instalacao_remocao(
                instalacao_remocao, equipamentos, dados_sga["situacoes_sga"], parametros, templates
            )
        )

        fila_operacional = [
            {**linha, "origem": "manutencao"} for linha in grupos_manutencao["grupo_3_tratativa_humana"]
        ] + tratativas_instalacao_remocao
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("consolidar_com_sga", sucesso=False, mensagem=str(e))

    return ResultadoEtapa(
        "consolidar_com_sga",
        sucesso=True,
        dados={
            "grupo_2_concluir": grupos_manutencao["grupo_2_concluir"],
            "fila_operacional": fila_operacional,
            "divergencias_instalacao": divergencias_instalacao,
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

        supabase_client.sincronizar_campos_atendente(chave, campos_sync)

    return atendente_por_chave


def _dados_hash_chave_unica(linha: dict) -> dict:
    """Campos que `core.dedup.gerar_chave_unica` espera por origem —
    ver `core/dedup.py`. Vêm crus do `incidente`/`registro` original
    (não recalculados aqui), já produzidos por `_montar_linha_resultado`
    nos dois motores."""
    if linha.get("origem") == ORIGEM_MANUTENCAO:
        return {
            "placa": linha.get("placa", ""),
            "data_incidente": linha.get("data_incidente", ""),
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
        "data_referencia": _data_referencia(linha),
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
        "Cliente": linha.get("cliente", ""),
        "Telefone": linha.get("telefone", ""),
        "Cidade": linha.get("cidade", ""),
        "Bairro": linha.get("bairro", ""),
        "Data Contrato / Data Incidente": _data_referencia(linha),
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
        "Data Resposta": estado_disparo.get("data_resposta") or "",
        "Retorno do Associado": estado_disparo.get("retorno_associado") or "",
        "Situação Manual": atendente["Situação Manual"],
        "Data Agendada": atendente["Data Agendada"],
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
        "Observação": linha.get("observacao", ""),
        "Ação": linha.get("acao", ""),
    }


async def etapa_publicar_fila_operacional(
    fila_operacional: list[dict] | None = None,
    agora: datetime | None = None,
    divergencias_instalacao: list[dict] | None = None,
) -> ResultadoEtapa:
    """Fase E — persiste `fila_operacional` (saída de
    `etapa_consolidar_com_sga`) em `tratativas` (Supabase) e reescreve a
    aba "Tratativas" da planilha Operacional. Também reescreve a aba
    "Análise de Divergência - Instalação" com `divergencias_instalacao`
    (itens `REGRA_INSTALACAO_JA_FEITA`) — mecânico, sem Supabase, sem
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
    5. Reescreve "Análise de Divergência - Instalação" do zero com
       `divergencias_instalacao` — sem upsert em `tratativas` (não são
       tratativas) e sem sincronizar nada da aba antiga antes (não há
       campo editável pelo atendente pra preservar).
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
    divergencias_instalacao = divergencias_instalacao if divergencias_instalacao is not None else []

    try:
        agora_dt = agora or datetime.now()
        agora_data = agora_dt.date()
        atendente_por_chave = _sincronizar_atendente_da_aba(agora_dt)

        linhas_com_chave = []
        for linha in fila_operacional:
            chave_unica = dedup.gerar_chave_unica(linha["origem"], _dados_hash_chave_unica(linha))
            supabase_client.upsert_tratativa(_payload_tratativa(linha, chave_unica))
            linhas_com_chave.append((linha, chave_unica))

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

        google_sheets_client.reescrever_aba(
            google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Tratativas", linhas_aba
        )

        linhas_divergencia = [
            _linha_divergencia_para_aba(
                linha, dedup.gerar_chave_unica(ORIGEM_INSTALACAO, _dados_hash_chave_unica(linha))
            )
            for linha in divergencias_instalacao
        ]
        google_sheets_client.reescrever_aba(
            google_sheets_client.NOME_PLANILHA_OPERACIONAL, "Análise de Divergência - Instalação", linhas_divergencia
        )
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("publicar_fila_operacional", sucesso=False, mensagem=str(e))

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

    Uma falha de envio de UM item (transporte, ou qualquer exceção
    inesperada de `newmo_client.enviar_template`) não derruba o lote
    inteiro — conta como falha e segue pro próximo.
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

        enviadas = contatos_invalidos = falhas = 0
        total_elegiveis = len(elegiveis)
        for indice, tratativa in enumerate(elegiveis):
            if cancelar_checker is not None and cancelar_checker():
                return ResultadoEtapa(
                    "disparo_mensagens", sucesso=False,
                    mensagem=f"Cancelado pelo usuário ({total_elegiveis - indice} pendente(s)).",
                    dados={
                        "enviadas": enviadas, "contato_invalido": contatos_invalidos,
                        "falhas": falhas, "total_elegiveis": total_elegiveis,
                    },
                    cancelado={"pendentes": elegiveis[indice:]},
                )
            if on_progresso is not None:
                on_progresso(indice + 1, total_elegiveis)
            atendimento = tratativa.get("atendimento")
            if not atendimento:
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

            try:
                resultado_envio = newmo_client.enviar_template(
                    tratativa["telefone"], codigo, variaveis, config["canal_guid"], config["setor_id"]
                )
            except Exception:  # noqa: BLE001 - um item ruim não derruba o lote inteiro
                falhas += 1
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
                falhas += 1
    except Exception as e:  # noqa: BLE001 - nunca deixa exceção subir até a UI
        return ResultadoEtapa("disparo_mensagens", sucesso=False, mensagem=str(e))

    return ResultadoEtapa(
        "disparo_mensagens",
        sucesso=True,
        dados={
            "enviadas": enviadas, "contato_invalido": contatos_invalidos,
            "falhas": falhas, "total_elegiveis": len(elegiveis),
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
        return ResultadoEtapa("finalizar_atendimentos_diarios", sucesso=False, mensagem=str(e))

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
        "Data Contrato / Data Incidente": tratativa.get("data_referencia", ""),
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
        return ResultadoEtapa("escalonar_ligacao", sucesso=False, mensagem=str(e))

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
        "Data Contrato / Data Incidente": tratativa.get("data_referencia", ""),
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
        return ResultadoEtapa("processar_resultado_ligacao", sucesso=False, mensagem=str(e))

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
        return ResultadoEtapa("processar_alertas", sucesso=False, mensagem=str(e))

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
