from core.normalizacao import (
    normalizar_placa,
    normalizar_telefone_e164,
    validar_formato_placa,
)


class TestNormalizarTelefoneE164:
    def test_celular_ja_com_9_digitos_e_ddd(self):
        resultado = normalizar_telefone_e164("(81) 98765-4321")
        assert resultado == {"e164": "+5581987654321", "valido": True, "motivo_invalido": None}

    def test_celular_antigo_sem_9_digito_ganha_o_9(self):
        resultado = normalizar_telefone_e164("81 8765-4321")
        assert resultado["valido"] is True
        assert resultado["e164"] == "+5581987654321"

    def test_fixo_de_8_digitos_nao_ganha_9(self):
        resultado = normalizar_telefone_e164("81 3765-4321")
        assert resultado["valido"] is True
        assert resultado["e164"] == "+558137654321"

    def test_com_codigo_do_pais_e_formatacao(self):
        resultado = normalizar_telefone_e164("+55 (81) 98765-4321")
        assert resultado["valido"] is True
        assert resultado["e164"] == "+5581987654321"

    def test_com_prefixo_zero_e_ddd_que_tambem_e_prefixo_de_pais(self):
        # DDD 55 (noroeste do RS) não pode ser confundido com código de país
        resultado = normalizar_telefone_e164("055987654321")
        assert resultado["valido"] is True
        assert resultado["e164"] == "+5555987654321"

    def test_telefone_vazio_e_invalido(self):
        resultado = normalizar_telefone_e164("")
        assert resultado["valido"] is False
        assert resultado["e164"] is None
        assert "vazio" in resultado["motivo_invalido"]

    def test_telefone_muito_curto_e_invalido(self):
        resultado = normalizar_telefone_e164("123")
        assert resultado["valido"] is False
        assert resultado["e164"] is None

    def test_numero_de_9_digitos_que_nao_comeca_com_9_e_invalido(self):
        resultado = normalizar_telefone_e164("81187654321")
        assert resultado["valido"] is False


class TestNormalizarPlaca:
    def test_remove_espaco_e_traco_e_deixa_maiusculo(self):
        assert normalizar_placa("abc-1234", []) == "ABC1234"
        assert normalizar_placa(" abc 1234 ", []) == "ABC1234"

    def test_placeholder_conhecido_vira_none(self):
        genericas = ["CHASSI", "SEM PLACA", "0000000"]
        assert normalizar_placa("SEM PLACA", genericas) is None
        assert normalizar_placa("sem-placa", genericas) is None
        assert normalizar_placa("chassi", genericas) is None

    def test_placa_valida_nao_confundida_com_placeholder(self):
        genericas = ["CHASSI", "SEM PLACA", "0000000"]
        assert normalizar_placa("ABC1D23", genericas) == "ABC1D23"

    def test_placa_vazia_ou_none_vira_none(self):
        assert normalizar_placa("", []) is None
        assert normalizar_placa(None, []) is None


class TestValidarFormatoPlaca:
    def test_formato_antigo(self):
        assert validar_formato_placa("ABC1234") is True

    def test_formato_mercosul(self):
        assert validar_formato_placa("ABC1D23") is True

    def test_aceita_minuscula(self):
        assert validar_formato_placa("abc1234") is True

    def test_formato_invalido(self):
        assert validar_formato_placa("ABC123") is False
        assert validar_formato_placa("ABCD123") is False

    def test_vazio_ou_none(self):
        assert validar_formato_placa("") is False
        assert validar_formato_placa(None) is False
