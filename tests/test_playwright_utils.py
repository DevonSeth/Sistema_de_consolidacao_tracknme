import asyncio
import time

import pytest

from integrations import playwright_utils as pu
from integrations.playwright_utils import (
    AguardandoReconexao,
    CancelamentoSolicitado,
    SessaoCaidaError,
    processar_fila,
    processar_fila_http,
)


class PageFalsa:
    async def close(self):
        pass


class ContextoFalso:
    """Substitui um BrowserContext real — só sabe criar PageFalsa."""

    async def new_page(self):
        return PageFalsa()


@pytest.mark.asyncio
async def test_todos_sucesso_de_primeira():
    async def acao(page, item):
        return item * 2

    resultados = await processar_fila(ContextoFalso(), [1, 2, 3], acao, num_workers=2)

    assert len(resultados) == 3
    assert all(r.sucesso for r in resultados)
    assert all(r.tentativas == 1 for r in resultados)
    assert sorted(r.resultado for r in resultados) == [2, 4, 6]


@pytest.mark.asyncio
async def test_item_falha_duas_vezes_e_sucede_na_terceira():
    contadores = {}

    async def acao(page, item):
        contadores[item] = contadores.get(item, 0) + 1
        if contadores[item] < 3:
            raise RuntimeError("falha de carregamento simulada")
        return "ok"

    resultados = await processar_fila(ContextoFalso(), ["x"], acao, num_workers=1)

    assert len(resultados) == 1
    assert resultados[0].sucesso is True
    assert resultados[0].tentativas == 3


@pytest.mark.asyncio
async def test_item_falha_round1_mas_sucede_no_round2():
    tentativas_totais = {}

    async def acao(page, item):
        tentativas_totais[item] = tentativas_totais.get(item, 0) + 1
        # falha as 3 tentativas do round 1 (tentativas 1-3), sucede no round 2 (tentativa 4)
        if tentativas_totais[item] < 4:
            raise RuntimeError("falha")
        return "recuperado"

    resultados = await processar_fila(
        ContextoFalso(), ["a", "b"], acao, num_workers=2, max_tentativas=3
    )

    assert len(resultados) == 2
    for r in resultados:
        assert r.sucesso is True
        assert r.resultado == "recuperado"


@pytest.mark.asyncio
async def test_erro_definitivo_nao_entra_no_round2():
    """2026-08-25 — erro de negócio determinístico (ex: Track N' Me
    recusando incidente já aberto) não deve gastar as tentativas
    restantes nem ser reprocessado no round 2."""
    chamadas = {}

    async def acao(page, item):
        chamadas[item] = chamadas.get(item, 0) + 1
        raise RuntimeError("já aberto")

    resultados = await processar_fila(
        ContextoFalso(), ["a"], acao, num_workers=1, max_tentativas=3,
        eh_erro_definitivo=lambda e: "já aberto" in str(e),
    )

    assert len(resultados) == 1
    assert resultados[0].sucesso is False
    assert resultados[0].definitivo is True
    assert resultados[0].tentativas == 1
    assert chamadas["a"] == 1  # nunca tentou de novo (nem dentro do round 1, nem no round 2)


@pytest.mark.asyncio
async def test_erro_nao_definitivo_continua_indo_pro_round2():
    """Confirma que `eh_erro_definitivo` não afeta falha técnica comum —
    sem regressão no comportamento de round 2 já existente."""
    tentativas_totais = {}

    async def acao(page, item):
        tentativas_totais[item] = tentativas_totais.get(item, 0) + 1
        if tentativas_totais[item] < 4:
            raise RuntimeError("falha técnica")
        return "recuperado"

    resultados = await processar_fila(
        ContextoFalso(), ["a"], acao, num_workers=1, max_tentativas=3,
        eh_erro_definitivo=lambda e: "já aberto" in str(e),
    )

    assert len(resultados) == 1
    assert resultados[0].sucesso is True
    assert resultados[0].resultado == "recuperado"
    assert tentativas_totais["a"] == 4  # 3 tentativas do round 1 + 1 do round 2


@pytest.mark.asyncio
async def test_item_falha_nas_duas_rodadas_fica_marcado_como_falho():
    async def acao(page, item):
        raise RuntimeError("sempre falha")

    resultados = await processar_fila(
        ContextoFalso(), ["z"], acao, num_workers=1, max_tentativas=2
    )

    assert len(resultados) == 1
    assert resultados[0].sucesso is False
    assert "sempre falha" in resultados[0].erro


@pytest.mark.asyncio
async def test_outros_itens_nao_sao_bloqueados_por_um_item_ruim():
    async def acao(page, item):
        if item == "ruim":
            raise RuntimeError("sempre falha")
        return "ok"

    resultados = await processar_fila(
        ContextoFalso(), ["bom1", "ruim", "bom2"], acao, num_workers=3, max_tentativas=2
    )

    por_item = {r.item: r for r in resultados}
    assert por_item["bom1"].sucesso is True
    assert por_item["bom2"].sucesso is True
    assert por_item["ruim"].sucesso is False


@pytest.mark.asyncio
async def test_sessao_caida_pausa_fila_e_preserva_pendentes():
    processados_antes_da_queda = []

    async def acao(page, item):
        if item == 3:
            raise SessaoCaidaError("sessão expirou")
        processados_antes_da_queda.append(item)
        return "ok"

    with pytest.raises(AguardandoReconexao) as exc_info:
        await processar_fila(ContextoFalso(), [1, 2, 3, 4, 5], acao, num_workers=1)

    erro = exc_info.value
    # item 3 (que causou a queda) + os que ainda nem tinham sido tentados
    assert 3 in erro.pendentes
    assert set(erro.pendentes) <= {3, 4, 5}
    assert len(erro.processados) + len(erro.pendentes) == 5


@pytest.mark.asyncio
async def test_fila_vazia_nao_faz_nada():
    async def acao(page, item):
        raise AssertionError("não deveria ser chamada")

    resultados = await processar_fila(ContextoFalso(), [], acao)
    assert resultados == []


@pytest.mark.asyncio
async def test_on_progresso_reporta_contagem_crescente_ate_o_total():
    chamadas = []

    async def acao(page, item):
        return item

    resultados = await processar_fila(
        ContextoFalso(), ["a", "b", "c"], acao, num_workers=1,
        on_progresso=lambda concluidos, total: chamadas.append((concluidos, total)),
    )

    assert len(resultados) == 3
    assert chamadas == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.asyncio
async def test_on_progresso_nunca_passa_do_total_mesmo_com_retry_no_round2():
    tentativas_totais = {}
    chamadas = []

    async def acao(page, item):
        tentativas_totais[item] = tentativas_totais.get(item, 0) + 1
        if tentativas_totais[item] < 4:
            raise RuntimeError("falha")
        return "recuperado"

    await processar_fila(
        ContextoFalso(), ["a", "b"], acao, num_workers=1, max_tentativas=3,
        on_progresso=lambda concluidos, total: chamadas.append((concluidos, total)),
    )

    assert all(concluidos <= total for concluidos, total in chamadas)
    assert chamadas[-1] == (2, 2)


@pytest.mark.asyncio
async def test_cancelar_checker_interrompe_processamento_da_fila():
    processados = []

    async def acao(page, item):
        processados.append(item)
        return "ok"

    def cancelar_checker():
        return len(processados) >= 2

    with pytest.raises(CancelamentoSolicitado) as exc_info:
        await processar_fila(
            ContextoFalso(), [1, 2, 3, 4, 5], acao, num_workers=1, cancelar_checker=cancelar_checker
        )

    erro = exc_info.value
    assert len(erro.processados) == 2
    assert set(erro.pendentes) == {3, 4, 5}
    assert len(processados) == 2  # nunca chega a chamar acao pro item 3 em diante


@pytest.mark.asyncio
async def test_sem_cancelar_checker_comportamento_identico_ao_padrao():
    async def acao(page, item):
        return item * 2

    resultados = await processar_fila(
        ContextoFalso(), [1, 2, 3], acao, num_workers=2, cancelar_checker=None
    )

    assert len(resultados) == 3
    assert all(r.sucesso for r in resultados)


@pytest.mark.asyncio
async def test_on_item_iniciado_reporta_worker_id_e_item_antes_de_processar():
    chamadas = []

    async def acao(page, item):
        return item

    resultados = await processar_fila(
        ContextoFalso(), ["a", "b", "c"], acao, num_workers=1,
        on_item_iniciado=lambda worker_id, item: chamadas.append((worker_id, item)),
    )

    assert len(resultados) == 3
    # num_workers=1 -> ordem determinística, worker 0 sempre
    assert chamadas == [(0, "a"), (0, "b"), (0, "c")]


@pytest.mark.asyncio
async def test_on_item_iniciado_usa_worker_id_dentro_do_intervalo_valido():
    chamadas = []

    async def acao(page, item):
        await asyncio.sleep(0.001)
        return item

    itens = list(range(10))
    resultados = await processar_fila(
        ContextoFalso(), itens, acao, num_workers=3,
        on_item_iniciado=lambda worker_id, item: chamadas.append((worker_id, item)),
    )

    assert len(resultados) == 10
    assert len(chamadas) == 10
    assert all(0 <= worker_id < 3 for worker_id, _ in chamadas)
    assert sorted(item for _, item in chamadas) == itens


@pytest.mark.asyncio
async def test_on_item_iniciado_dispara_de_novo_no_round2_pro_mesmo_item():
    tentativas_totais = {}
    chamadas = []

    async def acao(page, item):
        tentativas_totais[item] = tentativas_totais.get(item, 0) + 1
        if tentativas_totais[item] < 4:
            raise RuntimeError("falha")
        return "recuperado"

    resultados = await processar_fila(
        ContextoFalso(), ["a"], acao, num_workers=1, max_tentativas=3,
        on_item_iniciado=lambda worker_id, item: chamadas.append((worker_id, item)),
    )

    assert resultados[0].sucesso is True
    # 1x quando o worker pega o item no round 1 (falha as 3 tentativas
    # internas), 1x de novo quando o round 2 pega o mesmo item de volta —
    # nunca 1x por tentativa interna de `_executar_com_tentativas`.
    assert chamadas == [(0, "a"), (0, "a")]


@pytest.mark.asyncio
async def test_varios_workers_processam_tudo_sem_perder_item():
    contador_paginas_abertas = 0

    class ContextoContador(ContextoFalso):
        async def new_page(self):
            nonlocal contador_paginas_abertas
            contador_paginas_abertas += 1
            return await super().new_page()

    async def acao(page, item):
        await asyncio.sleep(0.001)
        return item

    itens = list(range(20))
    resultados = await processar_fila(ContextoContador(), itens, acao, num_workers=4)

    assert len(resultados) == 20
    assert sorted(r.resultado for r in resultados) == itens
    assert contador_paginas_abertas <= 4


# ---------------------------------------------------------------------------
# processar_fila_http -- achado 2026-08-19, mesmo contrato de processar_fila
# mas sem Page/BrowserContext.new_page() nenhuma (só uma fila de tarefas
# asyncio sobre um "request_context" qualquer -- aqui um sentinela `object()`,
# já que a função nunca abre recurso nenhum por trabalhador).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_todos_sucesso_de_primeira():
    async def acao(request_context, item):
        return item * 2

    resultados = await processar_fila_http(object(), [1, 2, 3], acao, concorrencia=2)

    assert len(resultados) == 3
    assert all(r.sucesso for r in resultados)
    assert all(r.tentativas == 1 for r in resultados)
    assert sorted(r.resultado for r in resultados) == [2, 4, 6]


@pytest.mark.asyncio
async def test_http_item_falha_duas_vezes_e_sucede_na_terceira():
    contadores = {}

    async def acao(request_context, item):
        contadores[item] = contadores.get(item, 0) + 1
        if contadores[item] < 3:
            raise RuntimeError("falha simulada")
        return "ok"

    resultados = await processar_fila_http(object(), ["x"], acao, concorrencia=1)

    assert len(resultados) == 1
    assert resultados[0].sucesso is True
    assert resultados[0].tentativas == 3


@pytest.mark.asyncio
async def test_http_item_falha_round1_mas_sucede_no_round2():
    tentativas_totais = {}

    async def acao(request_context, item):
        tentativas_totais[item] = tentativas_totais.get(item, 0) + 1
        if tentativas_totais[item] < 4:
            raise RuntimeError("falha")
        return "recuperado"

    resultados = await processar_fila_http(object(), ["a", "b"], acao, concorrencia=2, max_tentativas=3)

    assert len(resultados) == 2
    for r in resultados:
        assert r.sucesso is True
        assert r.resultado == "recuperado"


@pytest.mark.asyncio
async def test_http_erro_definitivo_nao_entra_no_round2():
    chamadas = {}

    async def acao(request_context, item):
        chamadas[item] = chamadas.get(item, 0) + 1
        raise RuntimeError("já aberto")

    resultados = await processar_fila_http(
        object(), ["a"], acao, concorrencia=1, max_tentativas=3,
        eh_erro_definitivo=lambda e: "já aberto" in str(e),
    )

    assert len(resultados) == 1
    assert resultados[0].sucesso is False
    assert resultados[0].definitivo is True
    assert resultados[0].tentativas == 1
    assert chamadas["a"] == 1


@pytest.mark.asyncio
async def test_http_item_falha_nas_duas_rodadas_fica_marcado_como_falho():
    async def acao(request_context, item):
        raise RuntimeError("sempre falha")

    resultados = await processar_fila_http(object(), ["z"], acao, concorrencia=1, max_tentativas=2)

    assert len(resultados) == 1
    assert resultados[0].sucesso is False
    assert "sempre falha" in resultados[0].erro


@pytest.mark.asyncio
async def test_http_sessao_caida_pausa_fila_e_preserva_pendentes():
    async def acao(request_context, item):
        if item == 3:
            raise SessaoCaidaError("sessão expirou")
        return "ok"

    with pytest.raises(AguardandoReconexao) as exc_info:
        await processar_fila_http(object(), [1, 2, 3, 4, 5], acao, concorrencia=1)

    erro = exc_info.value
    assert 3 in erro.pendentes
    assert set(erro.pendentes) <= {3, 4, 5}
    assert len(erro.processados) + len(erro.pendentes) == 5


@pytest.mark.asyncio
async def test_http_cancelar_checker_interrompe_processamento_da_fila():
    processados = []

    async def acao(request_context, item):
        processados.append(item)
        return "ok"

    def cancelar_checker():
        return len(processados) >= 2

    with pytest.raises(CancelamentoSolicitado) as exc_info:
        await processar_fila_http(
            object(), [1, 2, 3, 4, 5], acao, concorrencia=1, cancelar_checker=cancelar_checker
        )

    erro = exc_info.value
    assert len(erro.processados) == 2
    assert set(erro.pendentes) == {3, 4, 5}


@pytest.mark.asyncio
async def test_http_fila_vazia_nao_faz_nada():
    async def acao(request_context, item):
        raise AssertionError("não deveria ser chamada")

    resultados = await processar_fila_http(object(), [], acao)
    assert resultados == []


@pytest.mark.asyncio
async def test_http_on_progresso_reporta_contagem_crescente_ate_o_total():
    chamadas = []

    async def acao(request_context, item):
        return item

    resultados = await processar_fila_http(
        object(), ["a", "b", "c"], acao, concorrencia=1,
        on_progresso=lambda concluidos, total: chamadas.append((concluidos, total)),
    )

    assert len(resultados) == 3
    assert chamadas == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.asyncio
async def test_http_concorrencia_real_nunca_excede_o_limite_configurado():
    simultaneas = 0
    pico_simultaneas = 0

    async def acao(request_context, item):
        nonlocal simultaneas, pico_simultaneas
        simultaneas += 1
        pico_simultaneas = max(pico_simultaneas, simultaneas)
        await asyncio.sleep(0.01)
        simultaneas -= 1
        return item

    itens = list(range(30))
    resultados = await processar_fila_http(object(), itens, acao, concorrencia=5)

    assert len(resultados) == 30
    assert pico_simultaneas <= 5


# --- timeout_segundos (achado 2026-08-20: trava indefinida no processo -----
# Node do Playwright, sem timeout nenhum, travou o Painel Operador ~45min) --

@pytest.mark.asyncio
async def test_executar_com_tentativas_sem_timeout_trava_para_sempre_se_acao_nunca_retorna():
    """Documenta o comportamento ANTES do fix (timeout_segundos=None,
    default) -- serve de contraste pro teste seguinte, que prova que
    timeout_segundos resolve. Usa um timeout de teste MENOR que o sleep
    da ação simulada só pra confirmar que, sem timeout_segundos, o
    asyncio.wait_for do PRÓPRIO TESTE é quem teria que interromper (a
    função em si nunca desistiria sozinha)."""
    async def acao_trava(page, item):
        await asyncio.sleep(10)
        return "nunca chega aqui"

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            pu._executar_com_tentativas(object(), "x", acao_trava, max_tentativas=1), timeout=0.05
        )


@pytest.mark.asyncio
async def test_executar_com_tentativas_timeout_segundos_interrompe_acao_travada():
    async def acao_trava(page, item):
        await asyncio.sleep(10)
        return "nunca chega aqui"

    resultado = await asyncio.wait_for(
        pu._executar_com_tentativas(object(), "x", acao_trava, max_tentativas=1, timeout_segundos=0.01),
        timeout=1,
    )

    assert resultado.sucesso is False
    assert "timeout" in resultado.erro


@pytest.mark.asyncio
async def test_executar_com_tentativas_timeout_dobra_a_cada_tentativa(monkeypatch):
    """`acao_trava` usa um `asyncio.Event` que nunca é sinalizado (em vez
    de `asyncio.sleep`) pra travar de propósito sem depender de
    `asyncio.sleep` -- isso permite fazer o BACKOFF entre tentativas
    (`asyncio.sleep(2**tentativa)`, real, não relacionado ao timeout desta
    mudança) instantâneo via monkeypatch, sem acelerar/quebrar a trava
    simulada em si."""
    async def _sleep_instantaneo(segundos):
        return None

    monkeypatch.setattr(pu.asyncio, "sleep", _sleep_instantaneo)

    duracoes_tentativa = []

    async def acao_trava(page, item):
        inicio = time.monotonic()
        try:
            await asyncio.Event().wait()  # nunca sinalizado -- trava até ser cancelado por wait_for
        finally:
            duracoes_tentativa.append(time.monotonic() - inicio)
        return "nunca chega aqui"

    resultado = await asyncio.wait_for(
        pu._executar_com_tentativas(object(), "x", acao_trava, max_tentativas=3, timeout_segundos=0.02),
        timeout=1,
    )

    assert resultado.sucesso is False
    assert len(duracoes_tentativa) == 3
    # 1ª ~0.02s, 2ª ~0.04s, 3ª ~0.08s -- cada uma pelo menos o dobro da anterior
    assert duracoes_tentativa[1] > duracoes_tentativa[0] * 1.5
    assert duracoes_tentativa[2] > duracoes_tentativa[1] * 1.5


@pytest.mark.asyncio
async def test_executar_com_tentativas_timeout_none_preserva_comportamento_atual():
    async def acao_rapida(page, item):
        return "ok"

    resultado = await pu._executar_com_tentativas(object(), "x", acao_rapida, max_tentativas=1)

    assert resultado.sucesso is True
    assert resultado.resultado == "ok"


@pytest.mark.asyncio
async def test_executar_com_tentativas_erro_definitivo_para_na_primeira_tentativa():
    chamadas = {"n": 0}

    async def acao(page, item):
        chamadas["n"] += 1
        raise RuntimeError("já aberto")

    resultado = await pu._executar_com_tentativas(
        object(), "x", acao, max_tentativas=3, eh_erro_definitivo=lambda e: "já aberto" in str(e),
    )

    assert resultado.sucesso is False
    assert resultado.definitivo is True
    assert resultado.tentativas == 1
    assert chamadas["n"] == 1


@pytest.mark.asyncio
async def test_executar_com_tentativas_eh_erro_definitivo_none_preserva_comportamento():
    async def acao(page, item):
        raise RuntimeError("falha técnica")

    resultado = await pu._executar_com_tentativas(object(), "x", acao, max_tentativas=2)

    assert resultado.sucesso is False
    assert resultado.definitivo is False
    assert resultado.tentativas == 2


@pytest.mark.asyncio
async def test_http_item_travado_nao_impede_resto_da_fila_nem_trava_a_funcao():
    """Reproduz o sintoma real de 2026-08-20: 1 item trava pra sempre —
    com timeout_segundos configurado, processar_fila_http ainda assim
    retorna (o item travado vira falha, o resto processa normal),
    em vez de travar o Painel inteiro."""
    async def acao(request_context, item):
        if item == "trava":
            await asyncio.sleep(10)
        return "ok"

    resultados = await asyncio.wait_for(
        processar_fila_http(
            object(), ["trava", "rapido-1", "rapido-2"], acao, concorrencia=3,
            max_tentativas=1, timeout_segundos=0.02,
        ),
        timeout=1,
    )

    por_item = {r.item: r for r in resultados}
    assert por_item["trava"].sucesso is False
    assert "timeout" in por_item["trava"].erro
    assert por_item["rapido-1"].sucesso is True
    assert por_item["rapido-2"].sucesso is True
