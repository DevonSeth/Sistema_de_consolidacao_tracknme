import pytest

from integrations import sga_bot


def _resultado(status, cidade="", bairro=""):
    return {"status": status, "cidade": cidade, "bairro": bairro}


class TestConsultarSituacaoFallbackPlaca:
    """Achado 2026-08-16: `core.motor_regras._resolver_chassi` às vezes usa
    o IMEI ou a própria placa como identificador (quando o veículo não bate
    em Rastreadores Ativos), e a busca por Chassi no SGA nunca acha esses
    casos — mesmo quando o SGA acharia buscando por Placa (a mesma tela tem
    os 2 campos). Como não existe harness de fake `Page` do Playwright
    nesse projeto (nenhum bot é testado no nível de DOM), esses testes
    cobrem só o CONTROLE DE FLUXO de `consultar_situacao`, monkeypatchando
    o helper interno `_buscar_por_identificador`."""

    @pytest.mark.asyncio
    async def test_chassi_encontrado_nao_tenta_placa(self, monkeypatch):
        chamadas = []

        async def _buscar_fake(page, valor, seletor_filtro, seletor_ancora):
            chamadas.append(seletor_filtro)
            return _resultado("ATIVO")

        monkeypatch.setattr(sga_bot, "_buscar_por_identificador", _buscar_fake)

        resultado = await sga_bot.consultar_situacao(None, "CHASSI-REAL-001", placa="ABC1234")

        assert chamadas == [sga_bot.SELETOR_CAMPO_CHASSI_FILTRO]
        assert resultado["status"] == "ATIVO"
        assert resultado["encontrado_via"] == sga_bot.ENCONTRADO_VIA_CHASSI

    @pytest.mark.asyncio
    async def test_chassi_nao_encontrado_com_placa_valida_tenta_placa_e_retorna_esse_resultado(self, monkeypatch):
        async def _buscar_fake(page, valor, seletor_filtro, seletor_ancora):
            if seletor_filtro == sga_bot.SELETOR_CAMPO_CHASSI_FILTRO:
                return _resultado(sga_bot.STATUS_NAO_ENCONTRADO)
            return _resultado("ATIVO", cidade="Recife", bairro="Boa Vista")

        monkeypatch.setattr(sga_bot, "_buscar_por_identificador", _buscar_fake)

        resultado = await sga_bot.consultar_situacao(None, "862667082144174", placa="OYR7F55")

        assert resultado["status"] == "ATIVO"
        assert resultado["cidade"] == "Recife"
        assert resultado["bairro"] == "Boa Vista"
        assert resultado["encontrado_via"] == sga_bot.ENCONTRADO_VIA_PLACA

    @pytest.mark.asyncio
    async def test_chassi_e_placa_nao_encontrados_mantem_nao_encontrado(self, monkeypatch):
        chamadas = []

        async def _buscar_fake(page, valor, seletor_filtro, seletor_ancora):
            chamadas.append(seletor_filtro)
            return _resultado(sga_bot.STATUS_NAO_ENCONTRADO)

        monkeypatch.setattr(sga_bot, "_buscar_por_identificador", _buscar_fake)

        resultado = await sga_bot.consultar_situacao(None, "862667082144174", placa="OYR7F55")

        assert chamadas == [sga_bot.SELETOR_CAMPO_CHASSI_FILTRO, sga_bot.SELETOR_CAMPO_PLACA_FILTRO]
        assert resultado["status"] == sga_bot.STATUS_NAO_ENCONTRADO
        assert resultado["encontrado_via"] == sga_bot.ENCONTRADO_VIA_CHASSI

    @pytest.mark.asyncio
    async def test_chassi_nao_encontrado_sem_placa_informada_nao_tenta_segunda_busca(self, monkeypatch):
        chamadas = []

        async def _buscar_fake(page, valor, seletor_filtro, seletor_ancora):
            chamadas.append(seletor_filtro)
            return _resultado(sga_bot.STATUS_NAO_ENCONTRADO)

        monkeypatch.setattr(sga_bot, "_buscar_por_identificador", _buscar_fake)

        resultado = await sga_bot.consultar_situacao(None, "862667082144174", placa=None)

        assert chamadas == [sga_bot.SELETOR_CAMPO_CHASSI_FILTRO]
        assert resultado["status"] == sga_bot.STATUS_NAO_ENCONTRADO
        assert resultado["encontrado_via"] == sga_bot.ENCONTRADO_VIA_CHASSI
