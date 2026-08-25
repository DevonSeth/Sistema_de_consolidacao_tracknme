from datetime import date, datetime

from core.constants import (
    COL_RASTREADORES_CHASSI,
    COL_RASTREADORES_CLIENTE,
    COL_RASTREADORES_DATA_INSTALACAO,
    COL_RASTREADORES_IMEI,
    COL_RASTREADORES_MODELO_EQUIPAMENTO,
)
from core.motor_regras_instalacao_remocao import (
    _buscar_equipamento_por_chassi,
    _classificar_instalacao,
    _classificar_remocao,
    _consolidar_por_chassi,
    _extrair_cilindrada,
    _parse_valor_fipe,
    _resolver_origem,
    _titularidade_diverge,
    _veiculo_de_risco,
    atualizar_situacao_sga,
    chassi_ja_instalado,
    classificar_instalacao_remocao,
)


def _registro(chassi="9BWZZZ377VT004251", nome_associado="Fulano de Tal", placa="ABC1234",
              data_contrato="15/03/2026", modelo="Onix", valor_fipe="75000",
              tipo_veiculo="Passeio", servico="Instalação", cpf="12345678900",
              situacao="Ativo", cidade="Recife", bairro="Boa Vista", celular="81988887777"):
    return {
        "Chassi": chassi,
        "Nome Associado": nome_associado,
        "Placa": placa,
        "Data contrato": data_contrato,
        "Modelo": modelo,
        "Valor FIPE": valor_fipe,
        "Tipo Veículo": tipo_veiculo,
        "Serviço": servico,
        "CPF": cpf,
        "Situação": situacao,
        "Cidade": cidade,
        "Bairro": bairro,
        "celular": celular,
    }


def _equipamento(chassi="9BWZZZ377VT004251", cliente="Fulano de Tal", imei="", data_instalacao="",
                  modelo_equipamento=""):
    return {
        f"col_{COL_RASTREADORES_CHASSI}": chassi,
        f"col_{COL_RASTREADORES_CLIENTE}": cliente,
        f"col_{COL_RASTREADORES_IMEI}": imei,
        f"col_{COL_RASTREADORES_DATA_INSTALACAO}": data_instalacao,
        f"col_{COL_RASTREADORES_MODELO_EQUIPAMENTO}": modelo_equipamento,
    }


class TestParseValorFipe:
    def test_numero_puro(self):
        assert _parse_valor_fipe("75000") == 75000.0

    def test_moeda_br_formatada(self):
        assert _parse_valor_fipe("R$ 45.000,00") == 45000.0

    def test_vazio_retorna_none(self):
        assert _parse_valor_fipe("") is None

    def test_invalido_retorna_none(self):
        assert _parse_valor_fipe("não informado") is None


class TestExtrairCilindrada:
    def test_primeiro_numero_de_2_a_4_digitos(self):
        assert _extrair_cilindrada("CG 160 FAN FLEX", {}) == 160

    def test_edicao_especial_ainda_acerta_o_primeiro_numero(self):
        assert _extrair_cilindrada("CG 160 TITAN FLEXONE/ED.ESPECIAL 40 ANOS", {}) == 160

    def test_excecao_manual_tem_prioridade(self):
        assert _extrair_cilindrada("DOMINAR 400 NS", {"DOMINAR 400": 373}) == 373

    def test_sem_numero_retorna_none(self):
        assert _extrair_cilindrada("SEM NUMERO NENHUM", {}) is None


class TestVeiculoDeRisco:
    def test_moto_cilindrada_alta_e_risco(self):
        registro = _registro(tipo_veiculo="Moto", modelo="CB 300F TWISTER", valor_fipe="0")
        assert _veiculo_de_risco(registro, {}) is True

    def test_moto_cilindrada_baixa_nao_e_risco(self):
        registro = _registro(tipo_veiculo="Moto", modelo="CG 160 FAN FLEX", valor_fipe="0")
        assert _veiculo_de_risco(registro, {}) is False

    def test_carro_fipe_alto_e_risco(self):
        registro = _registro(tipo_veiculo="Passeio", modelo="Modelo Qualquer", valor_fipe="95000")
        assert _veiculo_de_risco(registro, {}) is True

    def test_carro_fipe_baixo_mas_modelo_na_lista_e_risco(self):
        registro = _registro(tipo_veiculo="Passeio", modelo="Onix", valor_fipe="45000")
        parametros = {"modelos_alto_risco_furto": "ONIX,GOL,HB20"}
        assert _veiculo_de_risco(registro, parametros) is True

    def test_carro_fipe_baixo_e_fora_da_lista_nao_e_risco(self):
        registro = _registro(tipo_veiculo="Passeio", modelo="Modelo Raro", valor_fipe="30000")
        parametros = {"modelos_alto_risco_furto": "ONIX,GOL,HB20"}
        assert _veiculo_de_risco(registro, parametros) is False

    def test_limiar_cilindrada_customizado_via_parametros(self):
        """Moto de 160cc não é risco no limiar padrão (300), mas passa a
        ser se `limiar_cilindrada_risco_cc` for reduzido pelo Painel Admin."""
        registro = _registro(tipo_veiculo="Moto", modelo="CG 160 FAN FLEX", valor_fipe="0")
        assert _veiculo_de_risco(registro, {"limiar_cilindrada_risco_cc": 150}) is True

    def test_limiar_fipe_customizado_via_parametros(self):
        """Carro de R$45mil não é risco no limiar padrão (80mil), mas
        passa a ser se `limiar_fipe_risco` for reduzido."""
        registro = _registro(tipo_veiculo="Passeio", modelo="Modelo Qualquer", valor_fipe="45000")
        assert _veiculo_de_risco(registro, {"limiar_fipe_risco": 40000}) is True


class TestTitularidadeDiverge:
    def test_nomes_iguais_nao_diverge(self):
        assert _titularidade_diverge("Fulano de Tal", "FULANO DE TAL") is False

    def test_nomes_diferentes_diverge(self):
        assert _titularidade_diverge("Fulano de Tal", "Outra Pessoa") is True

    def test_campo_vazio_nao_diverge(self):
        assert _titularidade_diverge("Fulano de Tal", "") is False


class TestResolverOrigem:
    def test_instalacao(self):
        assert _resolver_origem("Instalação") == "instalacao"

    def test_retirada_vira_remocao(self):
        assert _resolver_origem("Retirada") == "remocao"

    def test_remocao_vira_remocao(self):
        assert _resolver_origem("Remoção") == "remocao"

    def test_vazio_retorna_none(self):
        assert _resolver_origem("") is None


class TestBuscarEquipamentoPorChassi:
    def test_encontra_por_chassi_identico(self):
        equipamentos = [_equipamento(chassi="CHASSI-001")]
        assert _buscar_equipamento_por_chassi("CHASSI-001", equipamentos) is not None

    def test_nao_encontra_retorna_none(self):
        equipamentos = [_equipamento(chassi="CHASSI-001")]
        assert _buscar_equipamento_por_chassi("CHASSI-999", equipamentos) is None


class TestChassiJaInstalado:
    """Detecção automática de "instalação concluída" em "Encaminhar pra
    Puma" (decisão de negócio 2026-08-14) — mesma condição de
    REGRA_INSTALACAO_JA_FEITA, exposta como wrapper booleano público."""

    def test_chassi_presente_retorna_true(self):
        equipamentos = [_equipamento(chassi="CHASSI-001")]
        assert chassi_ja_instalado("CHASSI-001", equipamentos) is True

    def test_chassi_ausente_retorna_false(self):
        equipamentos = [_equipamento(chassi="CHASSI-001")]
        assert chassi_ja_instalado("CHASSI-999", equipamentos) is False


class TestConsolidarPorChassi:
    def test_mantem_a_linha_mais_recente_do_mesmo_chassi(self):
        registros = [
            _registro(chassi="CHASSI-001", servico="Instalação"),
            _registro(chassi="CHASSI-001", servico="Retirada"),
        ]
        resultado = _consolidar_por_chassi(registros)
        assert len(resultado) == 1
        assert resultado[0]["Serviço"] == "Retirada"

    def test_chassis_diferentes_ficam_todos(self):
        registros = [_registro(chassi="CHASSI-001"), _registro(chassi="CHASSI-002")]
        assert len(_consolidar_por_chassi(registros)) == 2


class TestClassificarInstalacao:
    AGORA = datetime(2026, 8, 6, 12, 0, 0)

    def test_prazo_normal(self):
        registro = _registro(data_contrato="01/08/2026", tipo_veiculo="Passeio", valor_fipe="30000")
        assert _classificar_instalacao(registro, None, {}, self.AGORA) == ("REGRA_PRAZO_NORMAL", 5)

    def test_prazo_atraso(self):
        registro = _registro(data_contrato="17/07/2026", tipo_veiculo="Passeio", valor_fipe="30000")
        assert _classificar_instalacao(registro, None, {}, self.AGORA) == ("REGRA_PRAZO_ATRASO", 20)

    def test_prazo_critico(self):
        registro = _registro(data_contrato="01/06/2026", tipo_veiculo="Passeio", valor_fipe="30000")
        codigo, dias = _classificar_instalacao(registro, None, {}, self.AGORA)
        assert codigo == "REGRA_PRAZO_CRITICO"
        assert dias == 66

    def test_risco_isolado_quando_prazo_ainda_normal(self):
        registro = _registro(data_contrato="01/08/2026", tipo_veiculo="Moto", modelo="CB 300F TWISTER")
        assert _classificar_instalacao(registro, None, {}, self.AGORA) == ("REGRA_RISCO", 5)

    def test_prazo_e_risco_quando_os_dois_batem(self):
        registro = _registro(data_contrato="17/07/2026", tipo_veiculo="Moto", modelo="CB 300F TWISTER")
        assert _classificar_instalacao(registro, None, {}, self.AGORA) == ("REGRA_PRAZO_E_RISCO", 20)

    def test_titularidade_quando_chassi_ja_instalado_e_nome_diverge(self):
        registro = _registro(nome_associado="Fulano de Tal")
        equipamento = _equipamento(cliente="Outra Pessoa")
        assert _classificar_instalacao(registro, equipamento, {}, self.AGORA) == ("REGRA_TITULARIDADE", None)

    def test_instalacao_ja_feita_quando_instalado_e_nome_bate(self):
        """Antes desta sessão (2026-08-13) essa combinação retornava
        `None` e a linha sumia sem aviso — decisão do usuário: sinalizar
        numa aba própria em vez de descartar."""
        registro = _registro(nome_associado="Fulano de Tal")
        equipamento = _equipamento(cliente="Fulano de Tal")
        assert _classificar_instalacao(registro, equipamento, {}, self.AGORA) == ("REGRA_INSTALACAO_JA_FEITA", None)

    def test_tier_instalacao_customizado_via_parametros(self):
        """5 dias seria NORMAL na faixa padrão (1-10) — com uma faixa
        customizada onde ATRASO já começa em 3 dias, muda de verdade."""
        registro = _registro(data_contrato="01/08/2026", tipo_veiculo="Passeio", valor_fipe="30000")
        parametros = {"tier_instalacao": "31=CRITICO,11=ATRASO,3=ATRASO_RAPIDO,1=NORMAL"}
        assert _classificar_instalacao(registro, None, parametros, self.AGORA) == ("REGRA_PRAZO_ATRASO_RAPIDO", 5)


class TestClassificarRemocao:
    AGORA = datetime(2026, 8, 6, 12, 0, 0)
    MODELO_PERMITIDO = {"modelos_removiveis": "J16,ST300"}

    def test_sem_situacao_sga_nao_gera_nada(self):
        registro = _registro(servico="Retirada")
        assert _classificar_remocao(registro, None, None, self.AGORA) is None

    def test_sga_ativo_gera_divergencia_sem_dias(self):
        """Bloco B (2026-08-24): antes descartado em silêncio, agora
        sinaliza que a remoção pendente pode estar desatualizada (SGA
        ainda não confirma cancelamento) — sem tier/dias, igual a
        REGRA_INSTALACAO_JA_FEITA."""
        registro = _registro(servico="Retirada")
        situacao_sga = {"status": "ATIVO", "desde": date(2026, 8, 1)}
        assert _classificar_remocao(registro, None, situacao_sga, self.AGORA) == ("REGRA_REMOCAO_SGA_ATIVO", None)

    def test_outro_status_sga_nao_gera_nada(self):
        """Qualquer status que não seja literalmente ATIVO/INATIVO
        continua descartado em silêncio (gating original, sem mudança)."""
        registro = _registro(servico="Retirada")
        situacao_sga = {"status": "NÃO ENCONTRADO", "desde": date(2026, 8, 1)}
        assert _classificar_remocao(registro, None, situacao_sga, self.AGORA) is None

    def test_prazo_sem_equipamento_e_bloqueado_por_falta_de_modelo(self):
        """Bloco B (2026-08-24), decisão confirmada: sem equipamento
        encontrado não há 'Modelo do Equipamento' pra confirmar contra a
        lista permitida — bloqueia igual a um modelo fora da lista, vira
        divergência em vez de tratativa (mesmo com a lista configurada)."""
        registro = _registro(servico="Retirada")
        situacao_sga = {"status": "INATIVO", "desde": date(2026, 8, 1)}
        codigo, dias = _classificar_remocao(registro, None, situacao_sga, self.AGORA, self.MODELO_PERMITIDO)
        assert codigo == "REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO"
        assert dias == 5

    def test_ativa_quando_chassi_encontrado_nome_bate_e_modelo_permitido(self):
        registro = _registro(servico="Retirada", nome_associado="Fulano de Tal")
        equipamento = _equipamento(cliente="Fulano de Tal", modelo_equipamento="J16")
        situacao_sga = {"status": "INATIVO", "desde": date(2026, 8, 1)}
        assert _classificar_remocao(registro, equipamento, situacao_sga, self.AGORA, self.MODELO_PERMITIDO) == (
            "REGRA_REMOCAO_ATIVA_NORMAL", 5
        )

    def test_ativa_bloqueada_quando_modelo_fora_da_lista(self):
        """Bloco B (2026-08-24): equipamento encontrado, sem divergência
        de titularidade, mas o modelo não está em `modelos_removiveis` —
        redireciona pra divergência (não bloqueia a avaliação de
        prazo/titularidade, só o destino final)."""
        registro = _registro(servico="Retirada", nome_associado="Fulano de Tal")
        equipamento = _equipamento(cliente="Fulano de Tal", modelo_equipamento="OUTRO MODELO")
        situacao_sga = {"status": "INATIVO", "desde": date(2026, 8, 1)}
        codigo, dias = _classificar_remocao(registro, equipamento, situacao_sga, self.AGORA, self.MODELO_PERMITIDO)
        assert codigo == "REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO"
        assert dias == 5

    def test_lista_vazia_bloqueia_tudo(self):
        """`modelos_removiveis` não configurado (parametros vazio) —
        decisão fechada: bloqueia tudo até o Admin configurar, mesmo com
        um modelo real de equipamento presente."""
        registro = _registro(servico="Retirada", nome_associado="Fulano de Tal")
        equipamento = _equipamento(cliente="Fulano de Tal", modelo_equipamento="J16")
        situacao_sga = {"status": "INATIVO", "desde": date(2026, 8, 1)}
        codigo, _ = _classificar_remocao(registro, equipamento, situacao_sga, self.AGORA, {})
        assert codigo == "REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO"

    def test_titularidade_quando_chassi_encontrado_e_nome_diverge(self):
        registro = _registro(servico="Retirada", nome_associado="Fulano de Tal")
        equipamento = _equipamento(cliente="Outra Pessoa", modelo_equipamento="J16")
        situacao_sga = {"status": "INATIVO", "desde": date(2026, 7, 10)}
        codigo, dias = _classificar_remocao(registro, equipamento, situacao_sga, self.AGORA, self.MODELO_PERMITIDO)
        assert codigo == "REGRA_REMOCAO_TITULARIDADE_URGENTE"
        assert dias == 27

    def test_titularidade_vence_mesmo_com_modelo_fora_da_lista(self):
        """Decisão confirmada nesta sessão: titularidade tem precedência
        sobre o filtro de modelo — mesmo com o modelo fora da lista
        permitida, o motivo que aparece é titularidade, não 'equipamento
        não permitido'."""
        registro = _registro(servico="Retirada", nome_associado="Fulano de Tal")
        equipamento = _equipamento(cliente="Outra Pessoa", modelo_equipamento="OUTRO MODELO")
        situacao_sga = {"status": "INATIVO", "desde": date(2026, 7, 10)}
        codigo, dias = _classificar_remocao(registro, equipamento, situacao_sga, self.AGORA, self.MODELO_PERMITIDO)
        assert codigo == "REGRA_REMOCAO_TITULARIDADE_URGENTE"
        assert dias == 27

    def test_nivel_urgencia_da_divergencia_segue_os_dias_nao_um_valor_fixo(self):
        """Confirma a decisão de 2026-08-06: dias sempre mandam, mesmo
        quando o chassi ainda está ativo (divergência)."""
        registro = _registro(servico="Retirada", nome_associado="Fulano de Tal")
        equipamento = _equipamento(cliente="Fulano de Tal", modelo_equipamento="J16")
        situacao_sga = {"status": "INATIVO", "desde": date(2026, 7, 10)}
        codigo, dias = _classificar_remocao(registro, equipamento, situacao_sga, self.AGORA, self.MODELO_PERMITIDO)
        assert codigo == "REGRA_REMOCAO_ATIVA_URGENTE"
        assert dias == 27

    def test_tier_remocao_customizado_via_parametros(self):
        """5 dias seria NORMAL na faixa padrão — com uma faixa
        customizada onde URGENTE já começa em 3 dias, muda de verdade
        (confirmado no `dias` retornado, já que sem equipamento o
        código final vira sempre EQUIPAMENTO_NAO_PERMITIDO agora)."""
        registro = _registro(servico="Retirada")
        situacao_sga = {"status": "INATIVO", "desde": date(2026, 8, 1)}
        parametros = {"tier_remocao": "21=URGENTE,11=ALTA,3=URGENTE_RAPIDO,1=NORMAL"}
        codigo, dias = _classificar_remocao(registro, None, situacao_sga, self.AGORA, parametros)
        assert codigo == "REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO"
        assert dias == 5


class TestAtualizarSituacaoSga:
    AGORA = datetime(2026, 8, 6, 12, 0, 0)

    def test_sem_registro_anterior_comeca_a_contagem_agora(self):
        resultado = atualizar_situacao_sga("CHASSI-001", "INATIVO", None, self.AGORA)
        assert resultado == {
            "chassi": "CHASSI-001", "status": "INATIVO", "desde": self.AGORA,
            "atualizado_em": self.AGORA, "encontrado_via": None,
            "cidade": "", "bairro": "",
        }

    def test_encontrado_via_e_repassado_sem_afetar_desde(self):
        resultado = atualizar_situacao_sga("CHASSI-001", "INATIVO", None, self.AGORA, encontrado_via="placa")
        assert resultado["encontrado_via"] == "placa"
        assert resultado["desde"] == self.AGORA

    def test_cidade_bairro_sao_repassados_sem_afetar_desde(self):
        """Bloco C1 (2026-08-24): cidade/bairro agora persistidos no
        checkpoint, mesmo padrão de `encontrado_via` — só repassados."""
        resultado = atualizar_situacao_sga(
            "CHASSI-001", "INATIVO", None, self.AGORA, cidade="Recife", bairro="Boa Vista"
        )
        assert resultado["cidade"] == "Recife"
        assert resultado["bairro"] == "Boa Vista"
        assert resultado["desde"] == self.AGORA

    def test_status_mudou_reinicia_a_contagem(self):
        anterior = {"status": "ATIVO", "desde": datetime(2026, 1, 1)}
        resultado = atualizar_situacao_sga("CHASSI-001", "INATIVO", anterior, self.AGORA)
        assert resultado["desde"] == self.AGORA

    def test_status_igual_mantem_a_contagem_original(self):
        anterior_desde = datetime(2026, 7, 1)
        anterior = {"status": "INATIVO", "desde": anterior_desde}
        resultado = atualizar_situacao_sga("CHASSI-001", "INATIVO", anterior, self.AGORA)
        assert resultado["desde"] == anterior_desde
        assert resultado["atualizado_em"] == self.AGORA


class TestClassificarInstalacaoRemocaoIntegracao:
    """Cobre o fluxo completo: consolidação por chassi, pelo menos um
    caso de Instalação e um de Remoção, e o gating por SGA."""

    AGORA = datetime(2026, 8, 6, 12, 0, 0)
    PARAMETROS = {"modelos_alto_risco_furto": "ONIX,GOL,HB20", "modelos_removiveis": "J16"}
    TEMPLATES = {
        "REGRA_PRAZO_NORMAL": {"template_acao": "Aguardar.", "template_observacao": "Pendente há {dias} dias.", "nivel_urgencia": 1},
        "REGRA_PRAZO_E_RISCO": {"template_acao": "Agendar já.", "template_observacao": "Risco + {dias} dias.", "nivel_urgencia": 5},
        "REGRA_TITULARIDADE": {"template_acao": "Corrigir cadastro.", "template_observacao": "{nome_associado} != {cliente_cadastro}.", "nivel_urgencia": 2},
        "REGRA_INSTALACAO_JA_FEITA": {
            "template_acao": "Remover ou atualizar a linha em Instalação-Remoção.",
            "template_observacao": "Chassi já consta em Rastreadores Ativos (instalado), mas ainda está em Instalação-Remoção.",
        },
        "REGRA_REMOCAO_PRAZO_URGENTE": {"template_acao": "Agendar retirada urgente.", "template_observacao": "INATIVO há {dias} dias.", "nivel_urgencia": 5},
        "REGRA_REMOCAO_ATIVA_NORMAL": {"template_acao": "Verificar divergência.", "template_observacao": "Ainda ativo, {dias} dias INATIVO.", "nivel_urgencia": 1},
        "REGRA_REMOCAO_SGA_ATIVO": {"template_acao": "Confirmar no SGA.", "template_observacao": "SGA ainda confirma ATIVO."},
        "REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO": {"template_acao": "Revisar lista de modelos.", "template_observacao": "Modelo fora da lista permitida."},
    }

    def test_fluxo_completo(self):
        registros = [
            # duplicado de propósito - a linha de Retirada (mais recente) deve prevalecer
            _registro(chassi="CHASSI-DUP", servico="Instalação"),
            _registro(chassi="CHASSI-DUP", servico="Retirada", nome_associado="Fulano de Tal"),
            # instalação pendente, risco (moto grande) + prazo em atraso -> REGRA_PRAZO_E_RISCO
            _registro(chassi="CHASSI-RISCO", data_contrato="17/07/2026", tipo_veiculo="Moto",
                      modelo="CB 300F TWISTER", servico="Instalação"),
            # instalação já feita, nome diverge -> REGRA_TITULARIDADE (agora vai pra divergência)
            _registro(chassi="CHASSI-TITULAR", nome_associado="Fulano de Tal", servico="Instalação"),
            # instalação normal, sem risco -> REGRA_PRAZO_NORMAL
            _registro(chassi="CHASSI-NORMAL", data_contrato="01/08/2026", tipo_veiculo="Passeio",
                      modelo="Modelo Sem Risco", valor_fipe="30000", servico="Instalação"),
            # instalação já feita, nome bate -> REGRA_INSTALACAO_JA_FEITA (divergência)
            _registro(chassi="CHASSI-JAFEITA", nome_associado="Fulano de Tal", servico="Instalação"),
            # remoção, SGA ainda ATIVO -> REGRA_REMOCAO_SGA_ATIVO (divergência de remoção)
            _registro(chassi="CHASSI-SGA-ATIVO", servico="Retirada"),
            # remoção, equipamento encontrado mas modelo fora da lista -> divergência de remoção
            _registro(chassi="CHASSI-MODELO-FORA", servico="Retirada"),
        ]
        equipamentos = [
            _equipamento(chassi="CHASSI-TITULAR", cliente="Outra Pessoa"),
            _equipamento(chassi="CHASSI-DUP", cliente="Fulano de Tal", modelo_equipamento="J16"),
            _equipamento(chassi="CHASSI-JAFEITA", cliente="Fulano de Tal", imei="123456789012345",
                         data_instalacao="10/08/2026"),
            _equipamento(chassi="CHASSI-MODELO-FORA", cliente="Fulano de Tal", modelo_equipamento="ST300"),
        ]
        situacoes_sga = {
            "CHASSI-DUP": {"status": "INATIVO", "desde": date(2026, 8, 1)},
            "CHASSI-SGA-ATIVO": {"status": "ATIVO", "desde": date(2026, 8, 1)},
            "CHASSI-MODELO-FORA": {"status": "INATIVO", "desde": date(2026, 8, 1)},
        }

        resultado, divergencias_instalacao, divergencias_remocao = classificar_instalacao_remocao(
            registros, equipamentos, situacoes_sga, self.PARAMETROS, self.TEMPLATES, agora=self.AGORA
        )
        por_chassi = {linha["chassi"]: linha for linha in resultado}

        assert len(resultado) == 3
        assert por_chassi["CHASSI-DUP"]["codigo_regra"] == "REGRA_REMOCAO_ATIVA_NORMAL"
        assert por_chassi["CHASSI-RISCO"]["codigo_regra"] == "REGRA_PRAZO_E_RISCO"
        assert por_chassi["CHASSI-NORMAL"]["codigo_regra"] == "REGRA_PRAZO_NORMAL"
        assert por_chassi["CHASSI-NORMAL"]["nivel_urgencia"] == 1

        # REGRA_TITULARIDADE/REGRA_INSTALACAO_JA_FEITA nunca viram
        # tratativa — só aparecem na lista de divergência de Instalação.
        assert "CHASSI-TITULAR" not in por_chassi
        assert "CHASSI-JAFEITA" not in por_chassi
        assert len(divergencias_instalacao) == 2
        por_chassi_div_instalacao = {linha["chassi"]: linha for linha in divergencias_instalacao}

        divergencia_titular = por_chassi_div_instalacao["CHASSI-TITULAR"]
        assert divergencia_titular["motivo"] == "Titularidade divergente"
        assert "Fulano de Tal" in divergencia_titular["observacao"]

        divergencia_jafeita = por_chassi_div_instalacao["CHASSI-JAFEITA"]
        assert divergencia_jafeita["motivo"] == "Instalação já concluída"
        assert divergencia_jafeita["placa"] == "ABC1234"
        assert divergencia_jafeita["cliente_cadastro"] == "Fulano de Tal"
        assert divergencia_jafeita["cliente_rastreadores"] == "Fulano de Tal"
        assert divergencia_jafeita["data_contrato"] == "15/03/2026"
        assert divergencia_jafeita["data_instalacao"] == "10/08/2026"
        assert divergencia_jafeita["imei"] == "123456789012345"
        assert divergencia_jafeita["observacao"] == (
            "Chassi já consta em Rastreadores Ativos (instalado), mas ainda está em Instalação-Remoção."
        )
        assert divergencia_jafeita["acao"] == "Remover ou atualizar a linha em Instalação-Remoção."
        assert divergencia_jafeita["cpf"] == "12345678900"
        assert divergencia_jafeita["situacao"] == "Ativo"

        # Remoção: nem SGA-ATIVO nem modelo fora da lista viram tratativa.
        assert "CHASSI-SGA-ATIVO" not in por_chassi
        assert "CHASSI-MODELO-FORA" not in por_chassi
        assert len(divergencias_remocao) == 2
        por_chassi_div_remocao = {linha["chassi"]: linha for linha in divergencias_remocao}

        divergencia_sga_ativo = por_chassi_div_remocao["CHASSI-SGA-ATIVO"]
        assert divergencia_sga_ativo["motivo"] == "SGA confirma veículo ainda ativo"
        assert divergencia_sga_ativo["status_sga"] == "ATIVO"

        divergencia_modelo_fora = por_chassi_div_remocao["CHASSI-MODELO-FORA"]
        assert divergencia_modelo_fora["motivo"] == "Modelo de equipamento fora da lista permitida para remoção"
        assert divergencia_modelo_fora["modelo_equipamento"] == "ST300"
        assert divergencia_modelo_fora["status_sga"] == "INATIVO"

        # CHASSI-DUP tem entrada conhecida em situacoes_sga -> "sga" reflete o status vivo.
        assert por_chassi["CHASSI-DUP"]["sga"] == "INATIVO"

        # Campos novos pra tratativa/chave_unica completa — vêm direto da
        # própria linha de Instalação-Remoção (não do SGA, decisão fechada).
        normal = por_chassi["CHASSI-NORMAL"]
        assert normal["cpf"] == "12345678900"
        assert normal["situacao"] == "Ativo"
        assert normal["data_contrato"] == "01/08/2026"
        assert normal["telefone"] == "+5581988887777"
        assert normal["cidade"] == "Recife"
        assert normal["bairro"] == "Boa Vista"
        assert normal["tipo_identificador"] == "chassi"
        assert normal["identificador"] == "CHASSI-NORMAL"
        assert normal["sga"] == ""  # SGA não consultado pra esse chassi
        assert normal["modelo"] == "Modelo Sem Risco"

        # CHASSI-DUP tem entrada conhecida em situacoes_sga -> "sga" reflete o status vivo.
        assert por_chassi["CHASSI-DUP"]["sga"] == "INATIVO"

    def test_telefone_vazio_quando_celular_nao_informado(self):
        registros = [_registro(chassi="CHASSI-SEMFONE", celular="", servico="Instalação")]

        resultado, divergencias_instalacao, divergencias_remocao = classificar_instalacao_remocao(
            registros, [], {}, self.PARAMETROS, self.TEMPLATES, agora=self.AGORA
        )

        assert resultado[0]["telefone"] == ""
        assert divergencias_instalacao == []
        assert divergencias_remocao == []

    def test_chassi_nao_encontrado_nao_gera_divergencia(self):
        """Chassi ainda não instalado (equipamento não encontrado) segue
        a cascata normal de prazo/risco — nunca cai em
        REGRA_INSTALACAO_JA_FEITA, que exige o chassi já estar em
        Rastreadores Ativos."""
        registros = [_registro(chassi="CHASSI-PENDENTE", data_contrato="01/08/2026",
                                tipo_veiculo="Passeio", modelo="Modelo Sem Risco",
                                valor_fipe="30000", servico="Instalação")]

        resultado, divergencias_instalacao, divergencias_remocao = classificar_instalacao_remocao(
            registros, [], {}, self.PARAMETROS, self.TEMPLATES, agora=self.AGORA
        )

        assert len(resultado) == 1
        assert resultado[0]["codigo_regra"] == "REGRA_PRAZO_NORMAL"
        assert divergencias_instalacao == []
        assert divergencias_remocao == []
