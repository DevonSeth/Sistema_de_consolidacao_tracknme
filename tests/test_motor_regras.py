from datetime import datetime

from core.constants import (
    COL_RASTREADORES_CELULAR,
    COL_RASTREADORES_CHASSI,
    COL_RASTREADORES_CLIENTE,
    COL_RASTREADORES_DATA_POSICAO,
    COL_RASTREADORES_ICCID,
    COL_RASTREADORES_IMEI,
    COL_RASTREADORES_MODELO,
    COL_RASTREADORES_NUMERO,
    COL_RASTREADORES_PLACA,
    COL_RASTREADORES_STATUS,
)
from core.motor_regras import (
    _buscar_equipamento_por_imei,
    _buscar_equipamento_por_placa,
    _classificar_incidente_existente,
    _esta_sem_comunicar,
    _filtrar_duplicados,
    _montar_linha_resultado,
    _regra_1,
    _regra_2,
    _regra_3,
    _regra_5_1,
    _regra_5_1_recaiu,
    _regra_5_1_sem_comunicacao,
    _regra_5_2,
    _regra_5_3,
    _regra_5_4,
    _regra_6_1,
    _regra_sem_placa,
    _resolver_chassi,
    _scan_grupo_1,
    _tem_incidente_aberto,
    _verificar_alerta_cliente,
    aplicar_situacoes_sga,
    buscar_chassi_por_placa,
    classificar_incidentes,
    manutencao_concluida_para_placa,
)


def _equipamento(placa="ABC1234", imei="123456789012345", chassi="9BWZZZ377VT004251",
                  cliente="Fulano de Tal", data_posicao="05/08/2026 12:00:00",
                  iccid="8955000000000000000", linha="81999999999", status="Ativo",
                  celular="81988887777", modelo=""):
    return {
        f"col_{COL_RASTREADORES_PLACA}": placa,
        f"col_{COL_RASTREADORES_IMEI}": imei,
        f"col_{COL_RASTREADORES_CHASSI}": chassi,
        f"col_{COL_RASTREADORES_CLIENTE}": cliente,
        f"col_{COL_RASTREADORES_DATA_POSICAO}": data_posicao,
        f"col_{COL_RASTREADORES_ICCID}": iccid,
        f"col_{COL_RASTREADORES_NUMERO}": linha,
        f"col_{COL_RASTREADORES_STATUS}": status,
        f"col_{COL_RASTREADORES_CELULAR}": celular,
        f"col_{COL_RASTREADORES_MODELO}": modelo,
    }


class TestBuscarEquipamentoPorPlaca:
    def test_encontra_quando_placa_bate_apos_normalizacao(self):
        equipamentos = [_equipamento(placa="ABC1234")]
        assert _buscar_equipamento_por_placa("abc-1234", equipamentos) == equipamentos[0]

    def test_retorna_none_quando_placa_nao_bate_com_nenhum_equipamento(self):
        equipamentos = [_equipamento(placa="ABC1234")]
        assert _buscar_equipamento_por_placa("XYZ9999", equipamentos) is None

    def test_retorna_none_quando_placa_informada_e_vazia(self):
        equipamentos = [_equipamento(placa="ABC1234")]
        assert _buscar_equipamento_por_placa("", equipamentos) is None

    def test_nao_bate_com_equipamento_de_placa_vazia(self):
        equipamentos = [_equipamento(placa="")]
        assert _buscar_equipamento_por_placa("", equipamentos) is None


class TestBuscarEquipamentoPorImei:
    def test_encontra_quando_imei_bate(self):
        equipamentos = [_equipamento(imei="123456789012345")]
        assert _buscar_equipamento_por_imei("123456789012345", equipamentos) == equipamentos[0]

    def test_retorna_none_quando_imei_nao_bate(self):
        equipamentos = [_equipamento(imei="123456789012345")]
        assert _buscar_equipamento_por_imei("999999999999999", equipamentos) is None

    def test_retorna_none_quando_imei_informado_e_vazio(self):
        equipamentos = [_equipamento(imei="123456789012345")]
        assert _buscar_equipamento_por_imei("", equipamentos) is None


class TestBuscarChassiPorPlaca:
    def test_retorna_chassi_do_equipamento_com_placa_identica(self):
        equipamentos = [_equipamento(placa="ABC1234", chassi="9BWZZZ377VT004251")]
        assert buscar_chassi_por_placa("ABC1234", equipamentos) == "9BWZZZ377VT004251"

    def test_retorna_none_quando_placa_nao_encontrada(self):
        equipamentos = [_equipamento(placa="ABC1234")]
        assert buscar_chassi_por_placa("ZZZ0000", equipamentos) is None


class TestResolverChassi:
    def test_resolve_por_placa_quando_bate_no_cadastro(self):
        equipamentos = [_equipamento(placa="ABC1234", chassi="CHASSI-POR-PLACA")]
        incidente = {"Placa": "ABC1234", "Imei": "000000000000000"}
        assert _resolver_chassi(incidente, equipamentos, {}) == "CHASSI-POR-PLACA"

    def test_cai_pro_imei_quando_placa_do_incidente_nao_bate_no_cadastro(self):
        equipamentos = [_equipamento(placa="ZZZ0000", imei="123456789012345", chassi="CHASSI-POR-IMEI")]
        incidente = {"Placa": "ABC1234", "Imei": "123456789012345"}
        assert _resolver_chassi(incidente, equipamentos, {}) == "CHASSI-POR-IMEI"

    def test_usa_imei_do_incidente_quando_nada_bate_no_cadastro(self):
        equipamentos = [_equipamento(placa="ZZZ0000", imei="999999999999999")]
        incidente = {"Placa": "ABC1234", "Imei": "123456789012345"}
        assert _resolver_chassi(incidente, equipamentos, {}) == "123456789012345"

    def test_usa_placa_normalizada_como_ultimo_recurso(self):
        incidente = {"Placa": "abc-1234", "Imei": ""}
        assert _resolver_chassi(incidente, [], {}) == "ABC1234"

    def test_retorna_none_quando_nao_ha_identificador_nenhum(self):
        incidente = {"Placa": "", "Imei": ""}
        assert _resolver_chassi(incidente, [], {}) is None

    def test_placa_generica_nao_conta_como_pseudo_chassi(self):
        """Achado 2026-08-07: sem passar a lista real de placas_genericas,
        um placeholder de placa (ex: "SEM PLACA") normalizava pra um
        pseudo-chassi truthy, fundindo (no dedup) incidentes de veículos
        DIFERENTES que só compartilham o mesmo placeholder."""
        parametros = {"placas_genericas": "SEM PLACA,CHASSI,0000000"}
        incidente = {"Placa": "SEM PLACA", "Imei": ""}
        assert _resolver_chassi(incidente, [], parametros) is None


class TestEstaSemComunicar:
    def test_posicao_vazia_e_sem_comunicar(self):
        agora = datetime(2026, 8, 5, 12, 0, 0)
        assert _esta_sem_comunicar("", agora, limiar_horas=48) is True

    def test_posicao_ilegivel_e_sem_comunicar(self):
        agora = datetime(2026, 8, 5, 12, 0, 0)
        assert _esta_sem_comunicar("data-invalida", agora, limiar_horas=48) is True

    def test_posicao_anterior_ao_corte_de_migracao_e_sem_comunicar(self):
        agora = datetime(2026, 8, 5, 12, 0, 0)
        assert _esta_sem_comunicar("15/09/2025 10:00:00", agora, limiar_horas=48) is True

    def test_posicao_recente_dentro_do_limiar_esta_comunicando(self):
        agora = datetime(2026, 8, 5, 12, 0, 0)
        assert _esta_sem_comunicar("04/08/2026 12:00:00", agora, limiar_horas=48) is False

    def test_posicao_mais_antiga_que_o_limiar_e_sem_comunicar(self):
        agora = datetime(2026, 8, 5, 12, 0, 0)
        assert _esta_sem_comunicar("01/08/2026 00:00:00", agora, limiar_horas=48) is True

    def test_posicao_sem_hora_e_aceita(self):
        agora = datetime(2026, 8, 5, 12, 0, 0)
        assert _esta_sem_comunicar("05/08/2026", agora, limiar_horas=48) is False


class TestFiltrarDuplicados:
    def test_mantem_o_incidente_mais_recente_do_mesmo_chassi(self):
        equipamentos = [_equipamento(placa="ABC1234", chassi="CHASSI-1")]
        antigo = {"Placa": "ABC1234", "Imei": "", "Data do incidente": "01/08/2026 10:00:00"}
        recente = {"Placa": "ABC1234", "Imei": "", "Data do incidente": "03/08/2026 10:00:00"}
        resultado = _filtrar_duplicados([antigo, recente], equipamentos, {})
        assert resultado == [recente]

    def test_nao_mexe_em_incidentes_de_chassis_diferentes(self):
        equipamentos = [
            _equipamento(placa="ABC1234", chassi="CHASSI-1"),
            _equipamento(placa="XYZ9999", chassi="CHASSI-2"),
        ]
        incidente_a = {"Placa": "ABC1234", "Imei": "", "Data do incidente": "01/08/2026 10:00:00"}
        incidente_b = {"Placa": "XYZ9999", "Imei": "", "Data do incidente": "02/08/2026 10:00:00"}
        resultado = _filtrar_duplicados([incidente_a, incidente_b], equipamentos, {})
        assert incidente_a in resultado and incidente_b in resultado
        assert len(resultado) == 2

    def test_mantem_apenas_um_quando_timestamps_sao_identicos(self):
        equipamentos = [_equipamento(placa="ABC1234", chassi="CHASSI-1")]
        primeiro = {"Placa": "ABC1234", "Imei": "", "Data do incidente": "01/08/2026 10:00:00"}
        duplicata_mesmo_horario = {"Placa": "ABC1234", "Imei": "", "Data do incidente": "01/08/2026 10:00:00"}
        resultado = _filtrar_duplicados([primeiro, duplicata_mesmo_horario], equipamentos, {})
        assert resultado == [primeiro]

    def test_incidente_sem_identificador_nenhum_nao_e_agrupado(self):
        sem_identificador_1 = {"Placa": "", "Imei": "", "Data do incidente": "01/08/2026 10:00:00"}
        sem_identificador_2 = {"Placa": "", "Imei": "", "Data do incidente": "02/08/2026 10:00:00"}
        resultado = _filtrar_duplicados([sem_identificador_1, sem_identificador_2], [], {})
        assert len(resultado) == 2

    def test_incidentes_com_mesma_placa_generica_nao_sao_fundidos(self):
        """Regressão do achado 2026-08-07 (ver TestResolverChassi) — dois
        incidentes de veículos diferentes, ambos com o mesmo placeholder
        de placa, não podem ser tratados como duplicata um do outro."""
        parametros = {"placas_genericas": "SEM PLACA"}
        a = {"Placa": "SEM PLACA", "Imei": "", "Data do incidente": "01/08/2026 10:00:00"}
        b = {"Placa": "SEM PLACA", "Imei": "", "Data do incidente": "02/08/2026 10:00:00"}
        resultado = _filtrar_duplicados([a, b], [], parametros)
        assert len(resultado) == 2


class TestVerificarAlertaCliente:
    AGORA = datetime(2026, 8, 5, 12, 0, 0)

    def test_sem_divergencia_retorna_none(self):
        equipamentos = [_equipamento(placa="ABC1234", cliente="Fulano de Tal")]
        incidente = {"Placa": "ABC1234", "Imei": "", "Cliente": "Fulano de Tal"}
        assert _verificar_alerta_cliente(incidente, equipamentos, self.AGORA, 48) is None

    def test_comparacao_e_case_insensitive(self):
        equipamentos = [_equipamento(placa="ABC1234", cliente="FULANO DE TAL")]
        incidente = {"Placa": "ABC1234", "Imei": "", "Cliente": "fulano de tal"}
        assert _verificar_alerta_cliente(incidente, equipamentos, self.AGORA, 48) is None

    def test_equipamento_nao_encontrado_retorna_none(self):
        incidente = {"Placa": "ZZZ0000", "Imei": "", "Cliente": "Fulano de Tal"}
        assert _verificar_alerta_cliente(incidente, [], self.AGORA, 48) is None

    def test_divergencia_comunicando_bem_so_fecha(self):
        equipamentos = [_equipamento(placa="ABC1234", cliente="Cliente Novo",
                                      data_posicao="05/08/2026 11:00:00")]
        incidente = {"Placa": "ABC1234", "Imei": "", "Cliente": "Cliente Antigo"}
        resultado = _verificar_alerta_cliente(incidente, equipamentos, self.AGORA, 48)
        assert resultado == {
            "codigo_regra": "REGRA_ALERTA_CLIENTE",
            "fechar_incidente": True,
            "abrir_novo_incidente": False,
            "cliente_atual": "Cliente Novo",
        }

    def test_divergencia_sem_comunicar_fecha_e_sinaliza_reabertura(self):
        equipamentos = [_equipamento(placa="ABC1234", cliente="Cliente Novo",
                                      data_posicao="01/08/2026 00:00:00")]
        incidente = {"Placa": "ABC1234", "Imei": "", "Cliente": "Cliente Antigo"}
        resultado = _verificar_alerta_cliente(incidente, equipamentos, self.AGORA, 48)
        assert resultado == {
            "codigo_regra": "REGRA_ALERTA_CLIENTE",
            "fechar_incidente": True,
            "abrir_novo_incidente": True,
            "cliente_atual": "Cliente Novo",
        }

    def test_cliente_vazio_nao_diverge(self):
        equipamentos = [_equipamento(placa="ABC1234", cliente="")]
        incidente = {"Placa": "ABC1234", "Imei": "", "Cliente": "Fulano de Tal"}
        assert _verificar_alerta_cliente(incidente, equipamentos, self.AGORA, 48) is None


class TestRegraSemPlaca:
    def test_placa_generica_bate(self):
        incidente = {"Placa": "SEM PLACA"}
        parametros = {"placas_genericas": "CHASSI,SEM PLACA,0000000"}
        assert _regra_sem_placa(incidente, parametros) is True

    def test_placa_vazia_bate(self):
        incidente = {"Placa": ""}
        assert _regra_sem_placa(incidente, {"placas_genericas": ""}) is True

    def test_placa_valida_nao_bate(self):
        incidente = {"Placa": "ABC1234"}
        assert _regra_sem_placa(incidente, {"placas_genericas": ""}) is False


class TestRegra51SemComunicacao:
    def test_bate_quando_imei_diverge_e_nunca_comunicou(self):
        equipamento = _equipamento(imei="999999999999999", data_posicao="")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_5_1_sem_comunicacao(incidente, equipamento) is True

    def test_nao_bate_quando_imei_nao_diverge(self):
        equipamento = _equipamento(imei="123456789012345", data_posicao="")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_5_1_sem_comunicacao(incidente, equipamento) is False

    def test_nao_bate_quando_equipamento_tem_posicao(self):
        equipamento = _equipamento(imei="999999999999999", data_posicao="01/08/2026 10:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_5_1_sem_comunicacao(incidente, equipamento) is False

    def test_nao_bate_quando_equipamento_e_none(self):
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_5_1_sem_comunicacao(incidente, None) is False


class TestRegra51:
    AGORA = datetime(2026, 8, 5, 12, 0, 0)

    def test_bate_quando_troca_confirmada_e_comunicando_bem(self):
        equipamento = _equipamento(imei="999999999999999", data_posicao="05/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_5_1(incidente, equipamento, self.AGORA, 48) is True

    def test_nao_bate_quando_esta_sem_comunicar(self):
        equipamento = _equipamento(imei="999999999999999", data_posicao="01/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/07/2026 10:00:00"}
        assert _regra_5_1(incidente, equipamento, self.AGORA, 48) is False

    def test_nao_bate_quando_posicao_nao_e_posterior_ao_incidente(self):
        equipamento = _equipamento(imei="999999999999999", data_posicao="01/08/2026 09:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_5_1(incidente, equipamento, self.AGORA, 48) is False

    def test_nao_bate_quando_imei_nao_diverge(self):
        equipamento = _equipamento(imei="123456789012345", data_posicao="05/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_5_1(incidente, equipamento, self.AGORA, 48) is False


class TestRegra51Recaiu:
    AGORA = datetime(2026, 8, 5, 12, 0, 0)

    def test_bate_quando_troca_confirmada_mas_recaiu(self):
        equipamento = _equipamento(imei="999999999999999", data_posicao="01/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/07/2026 10:00:00"}
        assert _regra_5_1_recaiu(incidente, equipamento, self.AGORA, 48) is True

    def test_nao_bate_quando_comunicando_bem(self):
        equipamento = _equipamento(imei="999999999999999", data_posicao="05/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_5_1_recaiu(incidente, equipamento, self.AGORA, 48) is False


class TestRegra2:
    AGORA = datetime(2026, 8, 5, 12, 0, 0)

    def test_bate_quando_comunicou_apos_incidente_e_esta_fresco(self):
        equipamento = _equipamento(imei="123456789012345", data_posicao="05/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_2(incidente, equipamento, self.AGORA, 48) is True

    def test_nao_bate_quando_imei_diverge(self):
        equipamento = _equipamento(imei="999999999999999", data_posicao="05/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_2(incidente, equipamento, self.AGORA, 48) is False

    def test_nao_bate_quando_nao_comunicou_apos_incidente(self):
        equipamento = _equipamento(imei="123456789012345", data_posicao="01/08/2026 09:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_2(incidente, equipamento, self.AGORA, 48) is False


class TestRegra1:
    AGORA = datetime(2026, 8, 5, 12, 0, 0)

    def test_bate_quando_nunca_comunicou(self):
        equipamento = _equipamento(imei="123456789012345", data_posicao="")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_1(incidente, equipamento, self.AGORA, 48) is True

    def test_bate_quando_posicao_anterior_ao_incidente(self):
        equipamento = _equipamento(imei="123456789012345", data_posicao="01/08/2026 09:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_1(incidente, equipamento, self.AGORA, 48) is True

    def test_nao_bate_quando_imei_diverge(self):
        equipamento = _equipamento(imei="999999999999999", data_posicao="")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_1(incidente, equipamento, self.AGORA, 48) is False

    def test_nao_bate_quando_comunicou_apos_incidente(self):
        equipamento = _equipamento(imei="123456789012345", data_posicao="05/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_1(incidente, equipamento, self.AGORA, 48) is False

    def test_nao_bate_quando_equipamento_e_none(self):
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_1(incidente, None, self.AGORA, 48) is False

    def test_bate_quando_imei_diverge_mas_posicao_nao_e_relevante(self):
        """Cobre o gap corrigido: IMEI diverge, mas a posição do cadastro é
        antiga (não posterior ao incidente) — não é uma troca "relevante"
        o suficiente pra família REGRA_5_1, então cai de volta em REGRA_1.
        """
        equipamento = _equipamento(imei="999999999999999", data_posicao="01/07/2026 09:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_1(incidente, equipamento, self.AGORA, 48) is True


class TestRegra3:
    AGORA = datetime(2026, 8, 5, 12, 0, 0)

    def test_bate_quando_recaiu_apos_comunicar(self):
        equipamento = _equipamento(imei="123456789012345", data_posicao="01/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/07/2026 10:00:00"}
        assert _regra_3(incidente, equipamento, self.AGORA, 48) is True

    def test_nao_bate_quando_ainda_fresco(self):
        equipamento = _equipamento(imei="123456789012345", data_posicao="05/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/08/2026 10:00:00"}
        assert _regra_3(incidente, equipamento, self.AGORA, 48) is False

    def test_nao_bate_quando_imei_diverge(self):
        equipamento = _equipamento(imei="999999999999999", data_posicao="01/08/2026 11:00:00")
        incidente = {"Imei": "123456789012345", "Placa": "ABC1234", "Data do incidente": "01/07/2026 10:00:00"}
        assert _regra_3(incidente, equipamento, self.AGORA, 48) is False


class TestRegra52:
    def test_bate_quando_imei_encontrado_sem_placa_vinculada(self):
        equipamentos = [_equipamento(imei="123456789012345", placa="")]
        incidente = {"Imei": "123456789012345"}
        assert _regra_5_2(incidente, equipamentos) is True

    def test_nao_bate_quando_placa_vinculada(self):
        equipamentos = [_equipamento(imei="123456789012345", placa="ABC1234")]
        incidente = {"Imei": "123456789012345"}
        assert _regra_5_2(incidente, equipamentos) is False

    def test_nao_bate_quando_imei_nao_encontrado(self):
        equipamentos = [_equipamento(imei="999999999999999", placa="")]
        incidente = {"Imei": "123456789012345"}
        assert _regra_5_2(incidente, equipamentos) is False


class TestRegra53:
    def test_bate_quando_nem_placa_nem_imei_encontrados(self):
        equipamentos = [_equipamento(placa="ZZZ0000", imei="999999999999999")]
        incidente = {"Placa": "ABC1234", "Imei": "123456789012345"}
        assert _regra_5_3(incidente, equipamentos) is True

    def test_nao_bate_quando_placa_encontrada(self):
        equipamentos = [_equipamento(placa="ABC1234", imei="999999999999999")]
        incidente = {"Placa": "ABC1234", "Imei": "123456789012345"}
        assert _regra_5_3(incidente, equipamentos) is False

    def test_nao_bate_quando_imei_encontrado(self):
        equipamentos = [_equipamento(placa="ZZZ0000", imei="123456789012345")]
        incidente = {"Placa": "ABC1234", "Imei": "123456789012345"}
        assert _regra_5_3(incidente, equipamentos) is False


class TestRegra54:
    def test_bate_quando_imei_vinculado_a_outra_placa(self):
        equipamentos = [_equipamento(imei="123456789012345", placa="ZZZ9999")]
        incidente = {"Placa": "ABC1234", "Imei": "123456789012345"}
        assert _regra_5_4(incidente, equipamentos) is True

    def test_nao_bate_quando_mesma_placa(self):
        equipamentos = [_equipamento(imei="123456789012345", placa="ABC1234")]
        incidente = {"Placa": "ABC1234", "Imei": "123456789012345"}
        assert _regra_5_4(incidente, equipamentos) is False

    def test_nao_bate_quando_sem_vinculo_de_placa(self):
        equipamentos = [_equipamento(imei="123456789012345", placa="")]
        incidente = {"Placa": "ABC1234", "Imei": "123456789012345"}
        assert _regra_5_4(incidente, equipamentos) is False


class TestRegra61:
    def test_bate_quando_iccid_diverge(self):
        equipamentos = [_equipamento(imei="123456789012345", iccid="8955000000000000001")]
        incidente = {"Imei": "123456789012345", "Iccid": "8955000000000000002", "Linha": ""}
        assert _regra_6_1(incidente, equipamentos) is True

    def test_bate_quando_linha_diverge(self):
        equipamentos = [_equipamento(imei="123456789012345", linha="81999999999")]
        incidente = {"Imei": "123456789012345", "Iccid": "", "Linha": "81988888888"}
        assert _regra_6_1(incidente, equipamentos) is True

    def test_nao_bate_quando_tudo_igual(self):
        equipamentos = [_equipamento(imei="123456789012345", iccid="X", linha="Y")]
        incidente = {"Imei": "123456789012345", "Iccid": "X", "Linha": "Y"}
        assert _regra_6_1(incidente, equipamentos) is False

    def test_nao_bate_quando_imei_nao_encontrado(self):
        equipamentos = [_equipamento(imei="999999999999999")]
        incidente = {"Imei": "123456789012345", "Iccid": "X", "Linha": "Y"}
        assert _regra_6_1(incidente, equipamentos) is False


class TestClassificarIncidenteExistente:
    AGORA = datetime(2026, 8, 5, 12, 0, 0)
    PARAMETROS = {"tempo_limiar_inatividade_horas": 48, "placas_genericas": "CHASSI,SEM PLACA,0000000"}

    def test_regra_sem_placa_tem_precedencia_maxima(self):
        incidente = {"Placa": "SEM PLACA", "Imei": "123456789012345", "Data do incidente": "01/08/2026 10:00:00"}
        assert _classificar_incidente_existente(incidente, [], self.PARAMETROS, self.AGORA) == "REGRA_SEM_PLACA"

    def test_imei_divergente_vence_sobre_regra_1(self):
        """Ambiguidade resolvida: quando o equipamento da placa nunca
        comunicou E o IMEI diverge do cadastro, REGRA_5_1_SEM_COMUNICACAO
        vence sobre REGRA_1, mesmo REGRA_1 tendo prioridade numérica menor.
        """
        equipamentos = [_equipamento(placa="ABC1234", imei="999999999999999", data_posicao="")]
        incidente = {
            "Placa": "ABC1234", "Imei": "123456789012345",
            "Data do incidente": "01/08/2026 10:00:00", "Iccid": "", "Linha": "",
        }
        resultado = _classificar_incidente_existente(incidente, equipamentos, self.PARAMETROS, self.AGORA)
        assert resultado == "REGRA_5_1_SEM_COMUNICACAO"

    def test_regra_1_bate_quando_imei_nao_diverge(self):
        equipamentos = [_equipamento(placa="ABC1234", imei="123456789012345", data_posicao="")]
        incidente = {
            "Placa": "ABC1234", "Imei": "123456789012345",
            "Data do incidente": "01/08/2026 10:00:00", "Iccid": "", "Linha": "",
        }
        resultado = _classificar_incidente_existente(incidente, equipamentos, self.PARAMETROS, self.AGORA)
        assert resultado == "REGRA_1"

    def test_cai_no_regra_5_3_quando_nada_bate(self):
        incidente = {
            "Placa": "ABC1234", "Imei": "123456789012345",
            "Data do incidente": "01/08/2026 10:00:00", "Iccid": "", "Linha": "",
        }
        resultado = _classificar_incidente_existente(incidente, [], self.PARAMETROS, self.AGORA)
        assert resultado == "REGRA_5_3"

    def test_regra_2_bate_quando_comunicou_bem_e_imei_nao_diverge(self):
        equipamentos = [_equipamento(placa="ABC1234", imei="123456789012345",
                                      data_posicao="05/08/2026 11:00:00")]
        incidente = {
            "Placa": "ABC1234", "Imei": "123456789012345",
            "Data do incidente": "01/08/2026 10:00:00", "Iccid": "", "Linha": "",
        }
        resultado = _classificar_incidente_existente(incidente, equipamentos, self.PARAMETROS, self.AGORA)
        assert resultado == "REGRA_2"

    def test_imei_divergente_com_posicao_antiga_cai_no_regra_1(self):
        """Cobre o gap corrigido na cascata completa: IMEI diverge, mas a
        posição não é posterior ao incidente — não ativa a família
        REGRA_5_1, cai em REGRA_1 (não fica sem classificação nenhuma).
        """
        equipamentos = [_equipamento(placa="ABC1234", imei="999999999999999",
                                      data_posicao="01/07/2026 09:00:00")]
        incidente = {
            "Placa": "ABC1234", "Imei": "123456789012345",
            "Data do incidente": "01/08/2026 10:00:00", "Iccid": "", "Linha": "",
        }
        resultado = _classificar_incidente_existente(incidente, equipamentos, self.PARAMETROS, self.AGORA)
        assert resultado == "REGRA_1"


class TestTemIncidenteAberto:
    def test_bate_por_placa_identica(self):
        equipamento = _equipamento(placa="ABC1234", imei="000000000000000")
        incidentes = [{"Placa": "ABC1234", "Imei": "", "Status": "Aberto"}]
        assert _tem_incidente_aberto(equipamento, incidentes) is True

    def test_bate_por_imei_identico(self):
        equipamento = _equipamento(placa="ZZZ0000", imei="123456789012345")
        incidentes = [{"Placa": "", "Imei": "123456789012345", "Status": "Aberto"}]
        assert _tem_incidente_aberto(equipamento, incidentes) is True

    def test_incidente_resolvido_nao_conta_como_aberto(self):
        equipamento = _equipamento(placa="ABC1234")
        incidentes = [{"Placa": "ABC1234", "Imei": "", "Status": "Resolvido"}]
        assert _tem_incidente_aberto(equipamento, incidentes) is False

    def test_sem_incidente_nenhum(self):
        equipamento = _equipamento(placa="ABC1234")
        assert _tem_incidente_aberto(equipamento, []) is False


class TestManutencaoConcluidaParaPlaca:
    """Detecção automática de "manutenção concluída" em "Encaminhar pra
    Puma" (decisão de negócio 2026-08-14) — reaproveita a mesma
    normalização/filtro de `_tem_incidente_aberto`, isolada de um
    `equipamento` específico. Framing positivo de propósito: "não dá pra
    confirmar" (placa vazia/genérica) sempre retorna `False`, nunca
    `True` por omissão."""

    PARAMETROS = {"placas_genericas": "SEM PLACA,0000000"}

    def test_incidente_ainda_aberto_com_placa_igual_retorna_false(self):
        incidentes = [{"Placa": "ABC1234", "Status": "Aberto"}]
        assert manutencao_concluida_para_placa("ABC1234", incidentes, self.PARAMETROS) is False

    def test_incidente_com_status_diferente_de_aberto_retorna_true(self):
        incidentes = [{"Placa": "ABC1234", "Status": "Resolvido"}]
        assert manutencao_concluida_para_placa("ABC1234", incidentes, self.PARAMETROS) is True

    def test_placa_nao_bate_com_nenhum_incidente_retorna_true(self):
        incidentes = [{"Placa": "ZZZ9999", "Status": "Aberto"}]
        assert manutencao_concluida_para_placa("ABC1234", incidentes, self.PARAMETROS) is True

    def test_sem_incidente_nenhum_retorna_true(self):
        assert manutencao_concluida_para_placa("ABC1234", [], self.PARAMETROS) is True

    def test_placa_generica_nao_conclui_sozinho(self):
        incidentes = [{"Placa": "ZZZ9999", "Status": "Aberto"}]
        assert manutencao_concluida_para_placa("SEM PLACA", incidentes, self.PARAMETROS) is False

    def test_placa_vazia_nao_conclui_sozinho(self):
        assert manutencao_concluida_para_placa("", [{"Placa": "ABC1234", "Status": "Aberto"}], self.PARAMETROS) is False


class TestScanGrupo1:
    PARAMETROS = {"tempo_limiar_inatividade_horas": 48}
    AGORA = datetime(2026, 8, 5, 12, 0, 0)

    def test_equipamento_ativo_sem_incidente_e_sem_comunicar_entra(self):
        equipamento = _equipamento(placa="ABC1234", status="Ativo", data_posicao="01/08/2026 00:00:00")
        resultado = _scan_grupo_1([equipamento], [], self.PARAMETROS, self.AGORA)
        assert resultado == [equipamento]

    def test_equipamento_com_incidente_aberto_e_ignorado(self):
        equipamento = _equipamento(placa="ABC1234", status="Ativo", data_posicao="01/08/2026 00:00:00")
        incidentes = [{"Placa": "ABC1234", "Imei": "", "Status": "Aberto"}]
        assert _scan_grupo_1([equipamento], incidentes, self.PARAMETROS, self.AGORA) == []

    def test_equipamento_comunicando_bem_e_ignorado(self):
        equipamento = _equipamento(placa="ABC1234", status="Ativo", data_posicao="05/08/2026 11:00:00")
        assert _scan_grupo_1([equipamento], [], self.PARAMETROS, self.AGORA) == []

    def test_equipamento_inativo_e_ignorado(self):
        equipamento = _equipamento(placa="ABC1234", status="Inativo", data_posicao="01/08/2026 00:00:00")
        assert _scan_grupo_1([equipamento], [], self.PARAMETROS, self.AGORA) == []

    def test_incidente_resolvido_nao_impede_entrada(self):
        equipamento = _equipamento(placa="ABC1234", status="Ativo", data_posicao="01/08/2026 00:00:00")
        incidentes = [{"Placa": "ABC1234", "Imei": "", "Status": "Resolvido"}]
        assert _scan_grupo_1([equipamento], incidentes, self.PARAMETROS, self.AGORA) == [equipamento]


class TestClassificarIncidentesIntegracao:
    """Cobre o fluxo completo end-to-end com dado sintético: pelo menos um
    incidente de cada grupo, dedup, REGRA_ALERTA_CLIENTE (com e sem
    reabertura) e o scan do Grupo 1 — tudo junto, como
    `orchestrator/pipeline.py` vai chamar de verdade.
    """
    AGORA = datetime(2026, 8, 5, 12, 0, 0)
    PARAMETROS = {"tempo_limiar_inatividade_horas": 48, "placas_genericas": "CHASSI,SEM PLACA,0000000"}
    TEMPLATES = {
        "REGRA_2": {"template_acao": "", "template_observacao": "Voltou a comunicar dentro do limiar."},
        "REGRA_1": {"template_acao": "Seguir esteira de disparo.", "template_observacao": "Sem comunicação."},
        "REGRA_3": {"template_acao": "Seguir esteira de disparo.", "template_observacao": "Recaiu."},
        "REGRA_SEM_PLACA": {"template_acao": "TODO: definir ação", "template_observacao": "Placa inválida."},
        "REGRA_5_1": {"template_acao": "Ajustar vínculo no cadastro.",
                      "template_observacao": "IMEI do cadastro ({imei_cadastro}) diverge — possível troca."},
        "REGRA_5_3": {"template_acao": "Investigar manualmente.", "template_observacao": "Nada encontrado no cadastro."},
        "REGRA_ALERTA_CLIENTE": {"template_acao": "",
                                  "template_observacao": "Cliente do incidente ({cliente_incidente}) diverge do cadastro ({cliente_equipamentos})."},
    }

    def _cenario(self):
        equipamentos = [
            _equipamento(placa="AAA1111", imei="100000000000001", chassi="CHASSI-001",
                         cliente="Cliente A", data_posicao="05/08/2026 11:00:00"),
            _equipamento(placa="BBB2222", imei="100000000000002", chassi="CHASSI-002",
                         cliente="Cliente B", data_posicao=""),
            _equipamento(placa="CCC3333", imei="100000000000003", chassi="CHASSI-003",
                         cliente="Cliente C", data_posicao="01/07/2026 11:00:00"),
            _equipamento(placa="EEE5555", imei="100000000000099", chassi="CHASSI-005",
                         cliente="Cliente E", data_posicao="05/08/2026 10:00:00"),
            _equipamento(placa="GGG7777", imei="100000000000007", chassi="CHASSI-007",
                         cliente="Cliente Novo G", data_posicao="05/08/2026 09:00:00"),
            _equipamento(placa="HHH8888", imei="100000000000008", chassi="CHASSI-008",
                         cliente="Cliente Novo H", data_posicao="01/07/2026 00:00:00"),
            _equipamento(placa="III9999", imei="100000000000009", chassi="CHASSI-009",
                         cliente="Cliente I", data_posicao="01/07/2026 00:00:00"),
        ]
        incidentes = [
            # AAA1111: duplicata (dedup deve manter só o ID 2, mais recente) -> REGRA_2
            {"ID": "1", "Placa": "AAA1111", "Imei": "100000000000001", "Cliente": "Cliente A",
             "Status": "Aberto", "Data do incidente": "01/08/2026 08:00:00"},
            {"ID": "2", "Placa": "AAA1111", "Imei": "100000000000001", "Cliente": "Cliente A",
             "Status": "Aberto", "Data do incidente": "01/08/2026 10:00:00"},
            # BBB2222 -> REGRA_1 (nunca comunicou)
            {"ID": "3", "Placa": "BBB2222", "Imei": "100000000000002", "Cliente": "Cliente B",
             "Status": "Aberto", "Data do incidente": "01/08/2026 10:00:00"},
            # CCC3333 -> REGRA_3 (comunicou depois do incidente, mas recaiu)
            {"ID": "4", "Placa": "CCC3333", "Imei": "100000000000003", "Cliente": "Cliente C",
             "Status": "Aberto", "Data do incidente": "01/07/2026 08:00:00"},
            # placa inválida -> REGRA_SEM_PLACA
            {"ID": "5", "Placa": "SEM PLACA", "Imei": "", "Cliente": "Cliente D",
             "Status": "Aberto", "Data do incidente": "01/08/2026 10:00:00"},
            # EEE5555 -> REGRA_5_1 (imei diverge, troca confirmada, comunicando bem)
            {"ID": "6", "Placa": "EEE5555", "Imei": "999999999999999", "Cliente": "Cliente E",
             "Status": "Aberto", "Data do incidente": "01/08/2026 08:00:00"},
            # FFF6666: não encontrado em nenhum cadastro -> REGRA_5_3 + lista de retirada -> fecha sozinho
            {"ID": "7", "Placa": "FFF6666", "Imei": "100000000000066", "Cliente": "Cliente F",
             "Status": "Aberto", "Data do incidente": "01/08/2026 10:00:00"},
            # GGG7777 -> REGRA_ALERTA_CLIENTE, comunicando bem -> só fecha
            {"ID": "8", "Placa": "GGG7777", "Imei": "100000000000007", "Cliente": "Cliente Antigo G",
             "Status": "Aberto", "Data do incidente": "01/08/2026 08:00:00"},
            # HHH8888 -> REGRA_ALERTA_CLIENTE, sem comunicar -> fecha e reabre
            {"ID": "9", "Placa": "HHH8888", "Imei": "100000000000008", "Cliente": "Cliente Antigo H",
             "Status": "Aberto", "Data do incidente": "01/07/2026 00:00:00"},
        ]
        instalacao_remocao = [{"Chassi": "100000000000066", "Serviço": "Retirada"}]
        return equipamentos, incidentes, instalacao_remocao

    def test_fluxo_completo(self):
        equipamentos, incidentes, instalacao_remocao = self._cenario()
        resultado = classificar_incidentes(
            incidentes, equipamentos, instalacao_remocao, self.PARAMETROS, self.TEMPLATES, agora=self.AGORA
        )

        por_placa_grupo2 = {linha["placa"]: linha for linha in resultado["grupo_2_concluir"]}
        por_placa_grupo3 = {linha["placa"]: linha for linha in resultado["grupo_3_tratativa_humana"]}

        assert len(resultado["grupo_2_concluir"]) == 4
        assert len(resultado["grupo_3_tratativa_humana"]) == 4
        assert len(resultado["grupo_1_abrir"]) == 2

        # Grupo 2 (sistema conclui sozinho)
        assert por_placa_grupo2["AAA1111"]["id"] == "2"  # dedup manteve o mais recente
        assert por_placa_grupo2["AAA1111"]["codigo_regra"] == "REGRA_2"
        assert por_placa_grupo2["FFF6666"]["codigo_regra"] == "REGRA_5_3"
        assert por_placa_grupo2["GGG7777"]["codigo_regra"] == "REGRA_ALERTA_CLIENTE"
        assert por_placa_grupo2["GGG7777"]["cliente"] == "Cliente Novo G"
        assert por_placa_grupo2["HHH8888"]["codigo_regra"] == "REGRA_ALERTA_CLIENTE"
        assert por_placa_grupo2["HHH8888"]["cliente"] == "Cliente Novo H"

        # Grupo 3 (tratativa humana)
        assert por_placa_grupo3["BBB2222"]["codigo_regra"] == "REGRA_1"
        assert por_placa_grupo3["CCC3333"]["codigo_regra"] == "REGRA_3"
        assert por_placa_grupo3["SEM PLACA"]["codigo_regra"] == "REGRA_SEM_PLACA"
        assert por_placa_grupo3["EEE5555"]["codigo_regra"] == "REGRA_5_1"
        assert "100000000000099" in por_placa_grupo3["EEE5555"]["observacao_sistema"]

        # Campos novos pra tratativa (chave_unica/dedup + identidade + telefone) —
        # BBB2222 tem equipamento encontrado (por placa, mesmo sem comunicar).
        assert por_placa_grupo3["BBB2222"]["data_incidente"] == "01/08/2026 10:00:00"
        assert por_placa_grupo3["BBB2222"]["tipo_identificador"] == "chassi"
        assert por_placa_grupo3["BBB2222"]["identificador"] == "CHASSI-002"
        assert por_placa_grupo3["BBB2222"]["telefone"] == "+5581988887777"
        # SEM PLACA não bate com nenhum equipamento e é placa genérica
        # (está em PARAMETROS["placas_genericas"]) -> sem chassi resolvível,
        # cai pro identificador de placa; sem telefone (equipamento é None).
        assert por_placa_grupo3["SEM PLACA"]["tipo_identificador"] == "placa"
        assert por_placa_grupo3["SEM PLACA"]["identificador"] == "SEM PLACA"
        assert por_placa_grupo3["SEM PLACA"]["telefone"] == ""

        # Grupo 1 (abrir automaticamente): reabertura do ALERTA_CLIENTE + scan do Grupo 1
        chassis_grupo1 = {linha["chassi"] for linha in resultado["grupo_1_abrir"]}
        assert chassis_grupo1 == {"CHASSI-008", "CHASSI-009"}


class TestMontarLinhaResultado:
    """Campos novos de `_montar_linha_resultado`, testados direto (função
    privada, mesmo padrão já usado neste arquivo pra outras funções
    internas) pra cobrir os dois ramos de identidade sem precisar montar
    um cenário completo de `classificar_incidentes`."""

    TEMPLATES = {
        "REGRA_1": {"template_acao": "Seguir esteira.", "template_observacao": "Sem comunicação.", "nivel_urgencia": 5},
    }
    PARAMETROS = {"tempo_limiar_inatividade_horas": 48}

    def test_identificador_usa_chassi_quando_disponivel(self):
        incidente = {
            "ID": "1", "Placa": "AAA1111",
            "Data do incidente": "01/08/2026 10:00:00", "Evento": "Sem comunicação",
        }
        linha = _montar_linha_resultado(incidente, "REGRA_1", None, "CHASSI-XYZ", self.TEMPLATES, self.PARAMETROS)

        assert linha["tipo_identificador"] == "chassi"
        assert linha["identificador"] == "CHASSI-XYZ"
        assert linha["data_incidente"] == "01/08/2026 10:00:00"
        assert linha["evento"] == "Sem comunicação"
        assert linha["telefone"] == ""  # sem equipamento encontrado

    def test_identificador_cai_pra_placa_quando_sem_chassi(self):
        incidente = {"ID": "2", "Placa": "BBB2222"}
        linha = _montar_linha_resultado(incidente, "REGRA_1", None, None, self.TEMPLATES, self.PARAMETROS)

        assert linha["tipo_identificador"] == "placa"
        assert linha["identificador"] == "BBB2222"
        assert linha["data_incidente"] == ""
        assert linha["evento"] == ""

    def test_telefone_vem_do_equipamento_quando_encontrado(self):
        incidente = {"ID": "3", "Placa": "CCC3333"}
        equipamento = _equipamento(celular="81988887777")
        linha = _montar_linha_resultado(incidente, "REGRA_1", equipamento, "CHASSI-CCC", self.TEMPLATES, self.PARAMETROS)

        assert linha["telefone"] == "+5581988887777"

    def test_modelo_vem_do_equipamento_quando_encontrado(self):
        incidente = {"ID": "4", "Placa": "DDD4444"}
        equipamento = _equipamento(modelo="CG 160 FAN FLEX")
        linha = _montar_linha_resultado(incidente, "REGRA_1", equipamento, "CHASSI-DDD", self.TEMPLATES, self.PARAMETROS)

        assert linha["modelo"] == "CG 160 FAN FLEX"

    def test_modelo_vazio_quando_equipamento_nao_encontrado(self):
        incidente = {"ID": "5", "Placa": "EEE5555"}
        linha = _montar_linha_resultado(incidente, "REGRA_1", None, None, self.TEMPLATES, self.PARAMETROS)

        assert linha["modelo"] == ""


class TestAplicarSituacoesSga:
    """Segunda passada, depois de `classificar_incidentes` — decisão do
    usuário (2026-08-07): SGA diferente de ATIVO sempre fecha o incidente
    de Manutenção (REGRA_SGA_INATIVO); SGA não encontrado vira tratativa
    própria (REGRA_SGA_NAO_ENCONTRADO) — os dois têm prioridade sobre
    qualquer classificação da cascata original."""

    TEMPLATES = {
        "REGRA_SGA_INATIVO": {
            "template_acao": "Nenhuma ação necessária — encerrado por status do SGA.",
            "template_observacao": "SGA retornou '{status_sga}' — associado não está mais ativo.",
            "nivel_urgencia": 1,
        },
        "REGRA_SGA_NAO_ENCONTRADO": {
            "template_acao": "Verificar cadastro do veículo/associado.",
            "template_observacao": "SGA não encontrou esse chassi.",
            "nivel_urgencia": 2,
        },
    }

    def _linha(self, chassi, codigo_regra="REGRA_1", **extra):
        base = {
            "id": "1", "placa": "ABC1234", "chassi": chassi, "imei": "123",
            "cliente": "Fulano", "codigo_regra": codigo_regra,
            "acao_sugerida": "Seguir esteira.", "observacao_sistema": "Sem comunicação.",
            "nivel_urgencia": 5,
        }
        base.update(extra)
        return base

    def test_status_ativo_mantem_classificacao_original_mas_ganha_sga_cidade_bairro(self):
        linha_g3 = self._linha("CHASSI-001")
        resultado = {"grupo_1_abrir": [], "grupo_2_concluir": [], "grupo_3_tratativa_humana": [linha_g3]}

        final = aplicar_situacoes_sga(
            resultado, {"CHASSI-001": {"status": "ATIVO", "cidade": "Recife", "bairro": "Boa Vista"}}, self.TEMPLATES
        )

        assert final["grupo_2_concluir"] == []
        assert len(final["grupo_3_tratativa_humana"]) == 1
        enriquecida = final["grupo_3_tratativa_humana"][0]
        assert enriquecida["codigo_regra"] == linha_g3["codigo_regra"]
        assert enriquecida["sga"] == "ATIVO"
        assert enriquecida["cidade"] == "Recife"
        assert enriquecida["bairro"] == "Boa Vista"

    def test_status_inativo_forca_fechamento_mesmo_vindo_do_grupo_3(self):
        linha_g3 = self._linha("CHASSI-002", codigo_regra="REGRA_1")
        resultado = {"grupo_1_abrir": [], "grupo_2_concluir": [], "grupo_3_tratativa_humana": [linha_g3]}

        final = aplicar_situacoes_sga(
            resultado, {"CHASSI-002": {"status": "INATIVO", "cidade": "Olinda", "bairro": "Rio Doce"}}, self.TEMPLATES
        )

        assert final["grupo_3_tratativa_humana"] == []
        assert len(final["grupo_2_concluir"]) == 1
        sobrescrita = final["grupo_2_concluir"][0]
        assert sobrescrita["codigo_regra"] == "REGRA_SGA_INATIVO"
        assert sobrescrita["nivel_urgencia"] == 1
        assert "INATIVO" in sobrescrita["observacao_sistema"]
        # identidade do veículo preservada
        assert sobrescrita["chassi"] == "CHASSI-002"
        assert sobrescrita["placa"] == "ABC1234"
        # enriquecimento do SGA preservado na sobrescrita
        assert sobrescrita["sga"] == "INATIVO"
        assert sobrescrita["cidade"] == "Olinda"
        assert sobrescrita["bairro"] == "Rio Doce"

    def test_status_inativo_sobrescreve_mesmo_ja_estando_no_grupo_2(self):
        linha_g2 = self._linha("CHASSI-003", codigo_regra="REGRA_2")
        resultado = {"grupo_1_abrir": [], "grupo_2_concluir": [linha_g2], "grupo_3_tratativa_humana": []}

        final = aplicar_situacoes_sga(resultado, {"CHASSI-003": {"status": "SUSPENSO"}}, self.TEMPLATES)

        assert len(final["grupo_2_concluir"]) == 1
        assert final["grupo_2_concluir"][0]["codigo_regra"] == "REGRA_SGA_INATIVO"

    def test_nao_encontrado_vira_tratativa_propria_mesmo_vindo_do_grupo_2(self):
        linha_g2 = self._linha("CHASSI-004", codigo_regra="REGRA_2")
        resultado = {"grupo_1_abrir": [], "grupo_2_concluir": [linha_g2], "grupo_3_tratativa_humana": []}

        final = aplicar_situacoes_sga(resultado, {"CHASSI-004": {"status": "NÃO ENCONTRADO"}}, self.TEMPLATES)

        assert final["grupo_2_concluir"] == []
        assert len(final["grupo_3_tratativa_humana"]) == 1
        sobrescrita = final["grupo_3_tratativa_humana"][0]
        assert sobrescrita["codigo_regra"] == "REGRA_SGA_NAO_ENCONTRADO"
        assert sobrescrita["nivel_urgencia"] == 2

    def test_chassi_sem_entrada_em_situacoes_sga_mantem_original(self):
        linha_g3 = self._linha("CHASSI-005")
        resultado = {"grupo_1_abrir": [], "grupo_2_concluir": [], "grupo_3_tratativa_humana": [linha_g3]}

        final = aplicar_situacoes_sga(resultado, {}, self.TEMPLATES)

        assert final["grupo_3_tratativa_humana"] == [linha_g3]

    def test_grupo_1_abrir_nunca_e_afetado(self):
        candidato_abertura = {"placa": "XYZ9999", "chassi": "CHASSI-006", "imei": "999", "cliente": "Ciclano"}
        resultado = {
            "grupo_1_abrir": [candidato_abertura],
            "grupo_2_concluir": [],
            "grupo_3_tratativa_humana": [],
        }

        final = aplicar_situacoes_sga(resultado, {"CHASSI-006": {"status": "INATIVO"}}, self.TEMPLATES)

        assert final["grupo_1_abrir"] == [candidato_abertura]
