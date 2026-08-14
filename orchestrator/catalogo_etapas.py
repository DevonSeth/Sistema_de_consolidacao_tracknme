"""
Catálogo das 13 etapas de `orchestrator/pipeline.py` + o wiring entre elas
quando várias rodam juntas na mesma cadeia (Painel Operador, tela
"Operação"). Sem isso, a etapa manual (SGA, login humano) seria
re-executada por engano sempre que o default `=None` de uma etapa
posterior decidisse rechamar `etapa_enriquecimento_sga()` do zero.

Cada `EtapaCatalogo.nome_funcao` é resolvido via `getattr(pipeline, ...)`
a cada chamada — nunca uma referência direta, senão monkeypatch/patch de
produção não teria efeito.

Trava de execução concorrente (Fase 2, `integrations.supabase_client.
adquirir_execucao_lock`/`liberar_execucao_lock`): adquirida em
`executar_cadeia` (único ponto de entrada de execução nova) e liberada num
`finally` lógico em qualquer desfecho, EXCETO `"aguardando_reconexao"` — a
trava continua presa até `continuar_apos_reconexao` terminar (TTL de 120
min cobre uma máquina que caiu no meio). `continuar_apos_reconexao` nunca
chama `executar_cadeia`/`adquirir_execucao_lock` de novo — a trava já está
presa por esta mesma máquina desde a execução original, adquiri-la de novo
reportaria falso-positivo de "travado" pra ela mesma.
"""

import socket
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from integrations import supabase_client
from orchestrator import pipeline


@dataclass(frozen=True)
class EtapaCatalogo:
    id: str
    fase: str
    label: str
    nome_funcao: str
    manual: bool = False
    async_: bool = True
    suporta_progresso: bool = False
    entradas: dict[str, str] = field(default_factory=dict)
    saidas: dict[str, str | None] = field(default_factory=dict)


CATALOGO: list[EtapaCatalogo] = [
    EtapaCatalogo(
        "baixar_relatorios", "A", "Baixar relatórios (Track N' Me)",
        "etapa_baixar_relatorios", saidas={"caminhos_relatorios": None},
    ),
    EtapaCatalogo(
        "ler_planilha_gestor", "A", "Ler planilha do gestor",
        "etapa_ler_planilha_gestor", async_=False,
        entradas={"caminhos_relatorios": "caminhos"},
        saidas={
            "dados_planilha": None, "equipamentos": "equipamentos",
            "instalacao_remocao": "instalacao_remocao", "incidentes": "incidentes",
        },
    ),
    EtapaCatalogo(
        "motor_de_regras", "B", "Motor de regras",
        "etapa_motor_de_regras", async_=False,
        entradas={"dados_planilha": "dados"}, saidas={"classificacao": None},
    ),
    EtapaCatalogo(
        "abrir_incidentes_automaticos", "C", "Abrir incidentes automáticos",
        "etapa_abrir_incidentes_automaticos", suporta_progresso=True,
        entradas={"classificacao": "dados"},
    ),
    EtapaCatalogo(
        "enriquecimento_sga", "D", "Consultar SGA (login manual)",
        "etapa_enriquecimento_sga", manual=True, suporta_progresso=True,
        entradas={"classificacao": "dados_classificacao", "instalacao_remocao": "instalacao_remocao"},
        saidas={"dados_sga": None},
    ),
    EtapaCatalogo(
        "consolidar_com_sga", "B", "Consolidar com SGA",
        "etapa_consolidar_com_sga",
        entradas={
            "classificacao": "dados_classificacao", "dados_sga": "dados_sga",
            "equipamentos": "equipamentos", "instalacao_remocao": "instalacao_remocao",
        },
        saidas={
            "consolidacao": None, "fila_operacional": "fila_operacional",
            "divergencias_instalacao": "divergencias_instalacao",
        },
    ),
    EtapaCatalogo(
        "fechar_incidentes_automaticos", "C", "Fechar incidentes automáticos",
        "etapa_fechar_incidentes_automaticos", suporta_progresso=True,
        entradas={"consolidacao": "dados"},
    ),
    EtapaCatalogo(
        "publicar_fila_operacional", "E", "Publicar fila operacional",
        "etapa_publicar_fila_operacional",
        entradas={"fila_operacional": "fila_operacional", "divergencias_instalacao": "divergencias_instalacao"},
    ),
    EtapaCatalogo(
        "disparo_mensagens", "F", "Disparo de mensagens (WhatsApp)",
        "etapa_disparo_mensagens", async_=False, suporta_progresso=True,
    ),
    EtapaCatalogo(
        "finalizar_atendimentos_diarios", "F", "Finalizar atendimentos diários",
        "etapa_finalizar_atendimentos_diarios", async_=False, suporta_progresso=True,
    ),
    EtapaCatalogo(
        "escalonar_ligacao", "F", "Escalonar para ligação",
        "etapa_escalonar_ligacao", async_=False, suporta_progresso=True,
    ),
    EtapaCatalogo(
        "processar_resultado_ligacao", "F", "Processar resultado de ligação",
        "etapa_processar_resultado_ligacao", async_=False, suporta_progresso=True,
        entradas={"equipamentos": "equipamentos", "incidentes": "incidentes"},
    ),
    EtapaCatalogo(
        "processar_alertas", "F", "Processar alertas",
        "etapa_processar_alertas", async_=False, suporta_progresso=True,
    ),
]

_POR_ID = {etapa.id: etapa for etapa in CATALOGO}


def etapa_por_id(etapa_id: str) -> EtapaCatalogo:
    if etapa_id not in _POR_ID:
        raise ValueError(f"Etapa desconhecida: {etapa_id!r}")
    return _POR_ID[etapa_id]


def kwargs_para(etapa: EtapaCatalogo, contexto: dict) -> dict:
    """Só inclui kwargs cujas chaves já estão no `contexto` — senão a
    etapa cai no próprio default (ex: relê a planilha/disco, mesmo
    princípio que já permite rodar cada etapa isolada)."""
    return {kwarg: contexto[chave] for chave, kwarg in etapa.entradas.items() if chave in contexto}


def registrar_saidas(etapa: EtapaCatalogo, resultado, contexto: dict) -> None:
    """Só grava no `contexto` se `resultado.sucesso` — uma etapa que falhou
    (ou está aguardando reconexão) nunca alimenta a próxima com dado
    parcial por esse caminho (reconexão tem seu próprio fluxo de fusão,
    ver `retomar_etapa`)."""
    if not resultado.sucesso:
        return
    for chave, caminho in etapa.saidas.items():
        contexto[chave] = resultado.dados if caminho is None else resultado.dados[caminho]


def resolver_etapas(modo: str, ids_selecionados: list[str] | None = None) -> list[EtapaCatalogo]:
    """`modo`: `"todas"` | `"selecionadas"` (filtra por `ids_selecionados`,
    preservando a ordem do catálogo) | `"a_partir_de:<id>"` (slice do
    catálogo a partir do índice de `id`)."""
    if modo == "todas":
        return list(CATALOGO)
    if modo == "selecionadas":
        ids = set(ids_selecionados or [])
        return [etapa for etapa in CATALOGO if etapa.id in ids]
    if modo.startswith("a_partir_de:"):
        etapa_id = modo.split(":", 1)[1]
        indices = [i for i, etapa in enumerate(CATALOGO) if etapa.id == etapa_id]
        if not indices:
            raise ValueError(f"Etapa desconhecida: {etapa_id!r}")
        return list(CATALOGO[indices[0]:])
    raise ValueError(f"Modo desconhecido: {modo!r}")


@dataclass
class ExecucaoCadeia:
    resultados: list = field(default_factory=list)
    motivo_parada: str | None = None  # None | "falha" | "cancelada" | "aguardando_reconexao" | "travado"
    etapa_travada_id: str | None = None
    etapas_restantes: list[EtapaCatalogo] = field(default_factory=list)
    execucao_id: str = ""  # "" só no caso "travado" (nenhuma etapa chegou a rodar)


def _motivo_do_resultado(resultado) -> str | None:
    if resultado.aguardando_reconexao is not None:
        return "aguardando_reconexao"
    if resultado.cancelado is not None:
        return "cancelada"
    if not resultado.sucesso:
        return "falha"
    return None


def _registrar_execucao_segura(execucao_id: str, etapa_id: str, iniciado_em: datetime, finalizado_em: datetime, resultado) -> None:
    """Grava a duração da etapa em `log_execucoes` — Observabilidade,
    fatia 1. Envolvido em try/except de propósito: uma falha de rede ao
    gravar telemetria nunca pode derrubar a etapa real nem, pior, vazar a
    trava de execução (se a exceção subisse, `executar_cadeia` pularia a
    linha de `liberar_execucao_lock()`)."""
    try:
        supabase_client.registrar_log_execucao(
            execucao_id=execucao_id,
            etapa_id=etapa_id,
            maquina=socket.gethostname(),
            iniciado_em=iniciado_em,
            finalizado_em=finalizado_em,
            sucesso=resultado.sucesso,
            motivo_parada=_motivo_do_resultado(resultado),
            mensagem=resultado.mensagem,
        )
    except Exception as exc:  # noqa: BLE001 - telemetria nunca pode derrubar a etapa real
        print(f"[observabilidade] falha ao registrar log_execucoes ({etapa_id}): {exc}", file=sys.stderr)


def _kwargs_com_progresso(
    etapa: EtapaCatalogo,
    contexto: dict,
    on_progresso_item: Callable[[str, int, int], None] | None,
    cancelar_checker: Callable[[], bool] | None = None,
) -> dict:
    kwargs = kwargs_para(etapa, contexto)
    if etapa.suporta_progresso:
        if on_progresso_item is not None:
            etapa_id = etapa.id
            kwargs["on_progresso"] = lambda concluidos, total: on_progresso_item(etapa_id, concluidos, total)
        if cancelar_checker is not None:
            kwargs["cancelar_checker"] = cancelar_checker
    return kwargs


async def executar_etapas_com_contexto(
    etapas: list[EtapaCatalogo],
    contexto: dict,
    cancelar_checker: Callable[[], bool] | None = None,
    on_progresso: Callable[[str], None] | None = None,
    on_progresso_item: Callable[[str, int, int], None] | None = None,
    on_resultado: Callable[[str, object], None] | None = None,
    execucao_id: str | None = None,
) -> ExecucaoCadeia:
    """Núcleo: itera `etapas`, resolve `getattr(pipeline, etapa.nome_funcao)`,
    monta kwargs (dataflow entre etapas via `kwargs_para` + progresso ao
    vivo/cancelamento via `on_progresso_item`/`cancelar_checker`, quando a
    etapa suporta), para no primeiro `not resultado.sucesso` — distinguindo
    `aguardando_reconexao` de falha comum. `cancelar_checker()` é checado
    ANTES de cada etapa (para sem nem começar a próxima) E É REPASSADO pra
    dentro da etapa que suporta fila (`suporta_progresso=True`) — ela
    mesma para no meio do processamento e devolve `resultado.cancelado`
    preenchido, checado logo abaixo.

    `execucao_id` (gerado aqui se `None` — permite chamar isolado, ex: 1
    etapa avulsa no painel) agrupa em `log_execucoes` todas as etapas
    desta rodada (Observabilidade, fatia 1): cada etapa tem sua duração
    (início/fim) medida e registrada, sempre de forma que uma falha ao
    gravar telemetria nunca derruba a etapa real (ver
    `_registrar_execucao_segura`)."""
    execucao_id = execucao_id or str(uuid.uuid4())
    execucao = ExecucaoCadeia(execucao_id=execucao_id)
    for indice, etapa in enumerate(etapas):
        if cancelar_checker is not None and cancelar_checker():
            execucao.motivo_parada = "cancelada"
            execucao.etapas_restantes = list(etapas[indice:])
            return execucao

        if on_progresso is not None:
            on_progresso(etapa.id)

        funcao = getattr(pipeline, etapa.nome_funcao)
        kwargs = _kwargs_com_progresso(etapa, contexto, on_progresso_item, cancelar_checker)
        iniciado_em = datetime.now(timezone.utc)
        resultado = await funcao(**kwargs) if etapa.async_ else funcao(**kwargs)
        _registrar_execucao_segura(execucao_id, etapa.id, iniciado_em, datetime.now(timezone.utc), resultado)

        execucao.resultados.append(resultado)
        if on_resultado is not None:
            on_resultado(etapa.id, resultado)

        if resultado.aguardando_reconexao is not None:
            execucao.motivo_parada = "aguardando_reconexao"
            execucao.etapa_travada_id = etapa.id
            execucao.etapas_restantes = list(etapas[indice + 1:])
            return execucao

        if resultado.cancelado is not None:
            execucao.motivo_parada = "cancelada"
            execucao.etapas_restantes = list(etapas[indice + 1:])
            return execucao

        if not resultado.sucesso:
            execucao.motivo_parada = "falha"
            execucao.etapas_restantes = list(etapas[indice + 1:])
            return execucao

        registrar_saidas(etapa, resultado, contexto)

    return execucao


async def executar_cadeia(
    ids_selecionados: list[str] | None,
    modo: str,
    cancelar_checker: Callable[[], bool] | None = None,
    on_progresso: Callable[[str], None] | None = None,
    on_progresso_item: Callable[[str, int, int], None] | None = None,
    on_resultado: Callable[[str, object], None] | None = None,
    contexto: dict | None = None,
) -> ExecucaoCadeia:
    """Ponto de entrada de uma execução NOVA — adquire a trava de execução
    concorrente antes de rodar qualquer etapa; se outra máquina já estiver
    executando, devolve `motivo_parada="travado"` sem rodar nada. Gera 1
    `execucao_id` (uuid) que agrupa todas as etapas desta rodada em
    `log_execucoes` — inclusive uma eventual retomada pós-reconexão, que
    reaproveita o mesmo id via `ExecucaoCadeia.execucao_id`."""
    etapas = resolver_etapas(modo, ids_selecionados)
    contexto = contexto if contexto is not None else {}
    execucao_id = str(uuid.uuid4())

    if not supabase_client.adquirir_execucao_lock(socket.gethostname()):
        return ExecucaoCadeia(motivo_parada="travado", execucao_id=execucao_id)

    execucao = await executar_etapas_com_contexto(
        etapas, contexto, cancelar_checker, on_progresso, on_progresso_item, on_resultado,
        execucao_id=execucao_id,
    )
    if execucao.motivo_parada != "aguardando_reconexao":
        supabase_client.liberar_execucao_lock()
    return execucao


async def retomar_etapa(
    etapa: EtapaCatalogo,
    resultado_travado,
    execucao_id: str | None = None,
    on_progresso_item: Callable[[str, int, int], None] | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
):
    """Chamado depois que o atendente confirma reconexão manual — reprocessa
    só os `pendentes` (preservados em `resultado_travado.aguardando_reconexao`)
    e funde com o trabalho que `resultado_travado.dados` já tinha de sucesso
    antes da queda (nunca duplica). Se cair de novo, devolve um novo
    `ResultadoEtapa` com `aguardando_reconexao` preenchido de novo, já com
    os pendentes atualizados. `cancelar_checker` (opcional) repassa pra
    dentro da retomada — cancelar no meio de uma reconexão propaga
    `cancelado` igual ao caminho normal.

    `execucao_id` (gerado aqui se `None`, mas normalmente vem de
    `continuar_apos_reconexao` reaproveitando o id da execução original) —
    a retomada em si vira mais 1 linha em `log_execucoes` pra essa etapa,
    dentro da mesma `execucao_id` (é um 2º trecho de execução real, não um
    problema ter 2 linhas)."""
    execucao_id = execucao_id or str(uuid.uuid4())
    pendentes = resultado_travado.aguardando_reconexao["pendentes"]
    kwargs = {}
    if etapa.suporta_progresso:
        if on_progresso_item is not None:
            etapa_id = etapa.id
            kwargs["on_progresso"] = lambda concluidos, total: on_progresso_item(etapa_id, concluidos, total)
        if cancelar_checker is not None:
            kwargs["cancelar_checker"] = cancelar_checker

    iniciado_em = datetime.now(timezone.utc)

    if etapa.id == "enriquecimento_sga":
        novo = await pipeline.etapa_enriquecimento_sga(chassis_override=pendentes, **kwargs)
        fundido = {**resultado_travado.dados["situacoes_sga"], **novo.dados["situacoes_sga"]}
        resultado_final = pipeline.ResultadoEtapa(
            etapa.id, sucesso=novo.sucesso, mensagem=novo.mensagem,
            dados={"situacoes_sga": fundido},
            aguardando_reconexao=novo.aguardando_reconexao, cancelado=novo.cancelado,
        )
        _registrar_execucao_segura(execucao_id, etapa.id, iniciado_em, datetime.now(timezone.utc), resultado_final)
        return resultado_final

    if etapa.id in ("abrir_incidentes_automaticos", "fechar_incidentes_automaticos"):
        if etapa.id == "abrir_incidentes_automaticos":
            chave_sucesso, chave_grupo, funcao = "abertos", "grupo_1_abrir", pipeline.etapa_abrir_incidentes_automaticos
        else:
            chave_sucesso, chave_grupo, funcao = "concluidos", "grupo_2_concluir", pipeline.etapa_fechar_incidentes_automaticos

        novo = await funcao(dados={chave_grupo: pendentes}, **kwargs)
        sucesso_fundido = resultado_travado.dados[chave_sucesso] + novo.dados[chave_sucesso]
        falhas_fundidas = resultado_travado.dados["falhas"] + novo.dados["falhas"]
        resultado_final = pipeline.ResultadoEtapa(
            etapa.id, sucesso=novo.sucesso, mensagem=novo.mensagem,
            dados={chave_sucesso: sucesso_fundido, "falhas": falhas_fundidas},
            aguardando_reconexao=novo.aguardando_reconexao, cancelado=novo.cancelado,
        )
        _registrar_execucao_segura(execucao_id, etapa.id, iniciado_em, datetime.now(timezone.utc), resultado_final)
        return resultado_final

    raise ValueError(f"Etapa sem retomada suportada: {etapa.id!r}")


async def continuar_apos_reconexao(
    etapa_travada: EtapaCatalogo,
    contexto: dict,
    resultado_travado,
    etapas_restantes: list[EtapaCatalogo],
    execucao_id: str | None = None,
    cancelar_checker: Callable[[], bool] | None = None,
    on_progresso: Callable[[str], None] | None = None,
    on_progresso_item: Callable[[str, int, int], None] | None = None,
    on_resultado: Callable[[str, object], None] | None = None,
) -> ExecucaoCadeia:
    """Continuação de uma cadeia pausada por `aguardando_reconexao` — chamada
    direto pela UI, NUNCA via `executar_cadeia` (a trava já está presa por
    esta mesma máquina desde a execução original; adquiri-la de novo aqui
    reportaria falso-positivo de "travado" pra ela mesma). `execucao_id`
    (gerado aqui se `None`) normalmente é o mesmo id da execução original —
    a UI guarda o `ExecucaoCadeia.execucao_id` devolvido lá pra passar de
    volta aqui, mantendo a rodada inteira sob 1 só id em `log_execucoes`."""
    execucao_id = execucao_id or str(uuid.uuid4())
    resultado_fundido = await retomar_etapa(etapa_travada, resultado_travado, execucao_id, on_progresso_item, cancelar_checker)
    if on_resultado is not None:
        on_resultado(etapa_travada.id, resultado_fundido)

    if resultado_fundido.aguardando_reconexao is not None:
        return ExecucaoCadeia(
            resultados=[resultado_fundido], motivo_parada="aguardando_reconexao",
            etapa_travada_id=etapa_travada.id, etapas_restantes=etapas_restantes, execucao_id=execucao_id,
        )

    if resultado_fundido.cancelado is not None:
        supabase_client.liberar_execucao_lock()
        return ExecucaoCadeia(
            resultados=[resultado_fundido], motivo_parada="cancelada",
            etapas_restantes=etapas_restantes, execucao_id=execucao_id,
        )

    if not resultado_fundido.sucesso:
        supabase_client.liberar_execucao_lock()
        return ExecucaoCadeia(
            resultados=[resultado_fundido], motivo_parada="falha",
            etapas_restantes=etapas_restantes, execucao_id=execucao_id,
        )

    registrar_saidas(etapa_travada, resultado_fundido, contexto)
    execucao_continuacao = await executar_etapas_com_contexto(
        etapas_restantes, contexto, cancelar_checker, on_progresso, on_progresso_item, on_resultado,
        execucao_id=execucao_id,
    )
    if execucao_continuacao.motivo_parada != "aguardando_reconexao":
        supabase_client.liberar_execucao_lock()
    execucao_continuacao.resultados = [resultado_fundido] + execucao_continuacao.resultados
    return execucao_continuacao
