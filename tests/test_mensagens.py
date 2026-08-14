import pytest

from core import mensagens
from core.constants import ORIGEM_INSTALACAO, ORIGEM_MANUTENCAO, ORIGEM_REMOCAO


class TestResolverPlacaParaMensagem:
    def test_placa_valida_usa_a_propria_placa(self):
        assert mensagens.resolver_placa_para_mensagem("ABC1234", "CG 160 FAN") == "ABC1234"

    def test_placa_mercosul_valida_usa_a_propria_placa(self):
        assert mensagens.resolver_placa_para_mensagem("ABC1D23", "") == "ABC1D23"

    def test_placa_vazia_usa_texto_fixo(self):
        assert mensagens.resolver_placa_para_mensagem("", "CG 160 FAN") == "placa não cadastrada"

    def test_placa_none_usa_texto_fixo(self):
        assert mensagens.resolver_placa_para_mensagem(None, "CG 160 FAN") == "placa não cadastrada"

    def test_placa_comecando_com_sga_usa_modelo(self):
        assert mensagens.resolver_placa_para_mensagem("SGA0612", "CG 160 FAN") == "CG 160 FAN"

    def test_placa_fora_do_padrao_usa_modelo(self):
        assert mensagens.resolver_placa_para_mensagem("SEM PLACA", "TIGUAN 2.0 TSI") == "TIGUAN 2.0 TSI"

    def test_placa_fictícia_sem_modelo_usa_texto_fixo(self):
        assert mensagens.resolver_placa_para_mensagem("SGA0612", "") == "placa não cadastrada"

    def test_placa_fictícia_com_modelo_none_usa_texto_fixo(self):
        assert mensagens.resolver_placa_para_mensagem("SGA0612", None) == "placa não cadastrada"


class TestNomeTemplateLogico:
    @pytest.mark.parametrize("origem", [ORIGEM_INSTALACAO, ORIGEM_MANUTENCAO])
    @pytest.mark.parametrize("atendimento", ["base", "rota", "acao"])
    def test_familia_instalacao_manutencao(self, origem, atendimento):
        assert mensagens.nome_template_logico(origem, atendimento, 0) == f"instalacao_manutencao_{atendimento}_normal"

    @pytest.mark.parametrize("atendimento", ["base", "rota", "acao"])
    def test_familia_remocao(self, atendimento):
        assert mensagens.nome_template_logico(ORIGEM_REMOCAO, atendimento, 0) == f"remocao_{atendimento}_normal"

    @pytest.mark.parametrize("tentativas,tom", [(0, "normal"), (1, "normal"), (2, "ultima"), (3, "ultima")])
    def test_fronteira_tom_normal_ultima(self, tentativas, tom):
        assert mensagens.nome_template_logico(ORIGEM_REMOCAO, "base", tentativas) == f"remocao_base_{tom}"


class TestVariaveisTemplate:
    def _tratativa(self, **overrides):
        base = {"origem": ORIGEM_MANUTENCAO, "cliente": "Fulano de Tal", "placa": "ABC1234", "modelo": ""}
        base.update(overrides)
        return base

    def test_instalacao_manutencao_base_normal_quatro_variaveis(self):
        base = {"endereco": "Rua Exemplo, 123"}
        resultado = mensagens.variaveis_template(
            "instalacao_manutencao_base_normal", self._tratativa(origem=ORIGEM_MANUTENCAO), base, None
        )
        assert resultado == ["Fulano de Tal", "manutenção", "ABC1234", "Rua Exemplo, 123"]

    def test_instalacao_manutencao_base_ultima_so_tres_variaveis(self):
        base = {"endereco": "Rua Exemplo, 123"}
        resultado = mensagens.variaveis_template(
            "instalacao_manutencao_base_ultima", self._tratativa(origem=ORIGEM_INSTALACAO), base, None
        )
        assert resultado == ["Fulano de Tal", "instalação", "ABC1234"]

    def test_instalacao_manutencao_rota_normal(self):
        resultado = mensagens.variaveis_template(
            "instalacao_manutencao_rota_normal", self._tratativa(), None, None
        )
        assert resultado == ["Fulano de Tal", "manutenção", "ABC1234"]

    def test_instalacao_manutencao_acao_normal_cinco_variaveis(self):
        ponto_acao = {"data": "2026-08-20", "endereco": "Praça Exemplo, s/n"}
        resultado = mensagens.variaveis_template(
            "instalacao_manutencao_acao_normal", self._tratativa(), None, ponto_acao
        )
        assert resultado == ["Fulano de Tal", "manutenção", "ABC1234", "20/08/2026", "Praça Exemplo, s/n"]

    def test_remocao_base_normal_sem_tipo_servico(self):
        base = {"endereco": "Rua Exemplo, 123"}
        resultado = mensagens.variaveis_template(
            "remocao_base_normal", self._tratativa(origem=ORIGEM_REMOCAO), base, None
        )
        assert resultado == ["Fulano de Tal", "ABC1234", "Rua Exemplo, 123"]

    def test_remocao_rota_ultima_duas_variaveis(self):
        resultado = mensagens.variaveis_template(
            "remocao_rota_ultima", self._tratativa(origem=ORIGEM_REMOCAO), None, None
        )
        assert resultado == ["Fulano de Tal", "ABC1234"]

    def test_remocao_acao_ultima_quatro_variaveis(self):
        ponto_acao = {"data": "2026-09-01", "endereco": "Av. Exemplo, 456"}
        resultado = mensagens.variaveis_template(
            "remocao_acao_ultima", self._tratativa(origem=ORIGEM_REMOCAO), None, ponto_acao
        )
        assert resultado == ["Fulano de Tal", "ABC1234", "01/09/2026", "Av. Exemplo, 456"]

    def test_placa_fictícia_usa_modelo_dentro_da_montagem(self):
        resultado = mensagens.variaveis_template(
            "instalacao_manutencao_rota_normal",
            self._tratativa(placa="SGA0612", modelo="CG 160 FAN"),
            None,
            None,
        )
        assert resultado == ["Fulano de Tal", "manutenção", "CG 160 FAN"]

    def test_slot_desconhecido_levanta_value_error(self):
        with pytest.raises(KeyError):
            mensagens.variaveis_template("nome_inexistente", self._tratativa(), None, None)
