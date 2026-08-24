import hashlib

import pytest

from core.constants import ORIGEM_INSTALACAO, ORIGEM_MANUTENCAO, ORIGEM_REMOCAO
from core.dedup import gerar_chave_unica


def _hash_esperado(*partes: str) -> str:
    return hashlib.sha256("|".join(partes).encode()).hexdigest()[:16]


class TestGerarChaveUnicaManutencao:
    def test_exemplo_confirmado_com_o_cliente(self):
        chave = gerar_chave_unica(
            ORIGEM_MANUTENCAO,
            {"placa": "XYZ9A87", "evento": "Sem comunicação"},
        )
        esperado = hashlib.sha256("manutencao|XYZ9A87|Sem comunicação".encode()).hexdigest()[:16]
        assert chave == esperado

    def test_tem_16_caracteres_hex(self):
        chave = gerar_chave_unica(ORIGEM_MANUTENCAO, {"placa": "ABC1234", "evento": "x"})
        assert len(chave) == 16
        int(chave, 16)  # não deve levantar ValueError

    def test_placa_e_normalizada_antes_de_entrar_na_chave(self):
        chave_com_traco = gerar_chave_unica(
            ORIGEM_MANUTENCAO, {"placa": "xyz-9a87", "evento": "Sem comunicação"}
        )
        chave_limpa = gerar_chave_unica(
            ORIGEM_MANUTENCAO, {"placa": "XYZ9A87", "evento": "Sem comunicação"}
        )
        assert chave_com_traco == chave_limpa

    def test_campo_faltando_levanta_key_error(self):
        with pytest.raises(KeyError):
            gerar_chave_unica(ORIGEM_MANUTENCAO, {"placa": "ABC1234"})

    def test_data_incidente_nao_afeta_mais_a_chave(self):
        """Achado 2026-08-24 (Bloco H): o Track N'Me atualiza `data_incidente`
        periodicamente enquanto o incidente segue aberto — incluir esse campo
        na chave mintava uma chave nova pro mesmo veículo/evento a cada
        atualização, deixando a tratativa anterior órfã pra sempre. `dados`
        pode trazer `data_incidente` (o resto do pipeline usa esse campo pra
        exibição), mas ele não deve influenciar o hash."""
        chave_1 = gerar_chave_unica(
            ORIGEM_MANUTENCAO,
            {"placa": "XYZ9A87", "evento": "Sem comunicação", "data_incidente": "2026-07-26 10:00:00"},
        )
        chave_2 = gerar_chave_unica(
            ORIGEM_MANUTENCAO,
            {"placa": "XYZ9A87", "evento": "Sem comunicação", "data_incidente": "2026-08-24 06:19:02"},
        )
        assert chave_1 == chave_2


class TestGerarChaveUnicaInstalacaoRemocao:
    def test_bate_com_concatenacao_manual_normalizada(self):
        chave = gerar_chave_unica(
            ORIGEM_INSTALACAO,
            {
                "cpf": "123.456.789-00",
                "chassi": "9bwzzz377vt004251",
                "situacao": " Pendente ",
                "data_contrato": "15/03/2026",
            },
        )
        esperado = _hash_esperado(
            ORIGEM_INSTALACAO, "12345678900", "9BWZZZ377VT004251", "pendente", "15/03/2026"
        )
        assert chave == esperado

    def test_origens_diferentes_geram_chaves_diferentes_mesmo_com_mesmos_dados(self):
        dados = {
            "cpf": "12345678900",
            "chassi": "ABC123",
            "situacao": "pendente",
            "data_contrato": "01/01/2026",
        }
        chave_instalacao = gerar_chave_unica(ORIGEM_INSTALACAO, dados)
        chave_remocao = gerar_chave_unica(ORIGEM_REMOCAO, dados)
        assert chave_instalacao != chave_remocao

    def test_cpf_com_ou_sem_pontuacao_gera_a_mesma_chave(self):
        base = {"chassi": "ABC123", "situacao": "pendente", "data_contrato": "01/01/2026"}
        chave_formatado = gerar_chave_unica(
            ORIGEM_INSTALACAO, {**base, "cpf": "123.456.789-00"}
        )
        chave_so_digitos = gerar_chave_unica(ORIGEM_INSTALACAO, {**base, "cpf": "12345678900"})
        assert chave_formatado == chave_so_digitos

    def test_situacao_case_insensitive(self):
        base = {"cpf": "12345678900", "chassi": "ABC123", "data_contrato": "01/01/2026"}
        chave_maiuscula = gerar_chave_unica(ORIGEM_REMOCAO, {**base, "situacao": "PENDENTE"})
        chave_minuscula = gerar_chave_unica(ORIGEM_REMOCAO, {**base, "situacao": "pendente"})
        assert chave_maiuscula == chave_minuscula


def test_origem_desconhecida_levanta_value_error():
    with pytest.raises(ValueError):
        gerar_chave_unica("origem_que_nao_existe", {})
