import pytest

from core.constants import TIPO_IDENTIFICADOR_CHASSI, TIPO_IDENTIFICADOR_PLACA
from integrations import sga_bot


def _resultado(status, cidade="", bairro=""):
    return {"status": status, "cidade": cidade, "bairro": bairro}


class TestConsultarSituacaoBuscaDeterministica:
    """Achado 2026-08-16: os campos Chassi e Placa da tela do SGA são
    INDEPENDENTES -- cada um só aceita o tipo certo de valor. Quem chama
    (`orchestrator.pipeline._alvos_consulta_sga`) já decide ANTES de
    consultar qual campo usar (chassi confirmado via cadastro ou placa
    real/válida) -- `consultar_situacao` nunca adivinha nem tenta os 2,
    só busca no campo indicado. Como não existe harness de fake `Page` do
    Playwright nesse projeto, esses testes cobrem só o CONTROLE DE FLUXO,
    monkeypatchando o helper interno `_buscar_por_identificador`."""

    @pytest.mark.asyncio
    async def test_tipo_chassi_busca_so_no_campo_chassi(self, monkeypatch):
        chamadas = []

        async def _buscar_fake(page, valor, seletor_filtro, seletor_ancora):
            chamadas.append((valor, seletor_filtro, seletor_ancora))
            return _resultado("ATIVO")

        monkeypatch.setattr(sga_bot, "_buscar_por_identificador", _buscar_fake)

        resultado = await sga_bot.consultar_situacao(None, TIPO_IDENTIFICADOR_CHASSI, "9BWZZZ377VT004251")

        assert chamadas == [
            ("9BWZZZ377VT004251", sga_bot.SELETOR_CAMPO_CHASSI_FILTRO, sga_bot.SELETOR_CAMPO_CHASSI_ANCORA)
        ]
        assert resultado["status"] == "ATIVO"
        assert resultado["encontrado_via"] == TIPO_IDENTIFICADOR_CHASSI

    @pytest.mark.asyncio
    async def test_tipo_placa_busca_so_no_campo_placa(self, monkeypatch):
        chamadas = []

        async def _buscar_fake(page, valor, seletor_filtro, seletor_ancora):
            chamadas.append((valor, seletor_filtro, seletor_ancora))
            return _resultado("ATIVO", cidade="Recife", bairro="Boa Vista")

        monkeypatch.setattr(sga_bot, "_buscar_por_identificador", _buscar_fake)

        resultado = await sga_bot.consultar_situacao(None, TIPO_IDENTIFICADOR_PLACA, "OYR7F55")

        assert chamadas == [("OYR7F55", sga_bot.SELETOR_CAMPO_PLACA_FILTRO, sga_bot.SELETOR_CAMPO_PLACA_ANCORA)]
        assert resultado["status"] == "ATIVO"
        assert resultado["cidade"] == "Recife"
        assert resultado["bairro"] == "Boa Vista"
        assert resultado["encontrado_via"] == TIPO_IDENTIFICADOR_PLACA

    @pytest.mark.asyncio
    async def test_chassi_confirmado_nao_encontrado_nao_tenta_placa(self, monkeypatch):
        # Decisão do usuário (2026-08-16): chassi confirmado + "NÃO
        # ENCONTRADO" no SGA já é resultado de negócio válido -- nunca
        # tenta a Placa depois (isso reintroduziria a mistura de campos
        # que causou o achado original).
        chamadas = []

        async def _buscar_fake(page, valor, seletor_filtro, seletor_ancora):
            chamadas.append(seletor_filtro)
            return _resultado(sga_bot.STATUS_NAO_ENCONTRADO)

        monkeypatch.setattr(sga_bot, "_buscar_por_identificador", _buscar_fake)

        resultado = await sga_bot.consultar_situacao(None, TIPO_IDENTIFICADOR_CHASSI, "9BWZZZ377VT004251")

        assert chamadas == [sga_bot.SELETOR_CAMPO_CHASSI_FILTRO]
        assert resultado["status"] == sga_bot.STATUS_NAO_ENCONTRADO
        assert resultado["encontrado_via"] == TIPO_IDENTIFICADOR_CHASSI
