import asyncio

import pytest

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
