from datetime import date, datetime, timezone

from core.escalonamento import (
    deve_escalar_para_ligacao,
    dia_permite_disparo,
    dias_uteis_entre,
    elegivel_para_disparo,
    passou_do_horario_corte,
    resultado_ligacao,
)


def _tratativa(selecionado=True, tentativas=0, status="aguardando_resposta",
                situacao_manual="", ultimo_disparo=None):
    return {
        "selecionado": selecionado,
        "tentativas": tentativas,
        "status": status,
        "situacao_manual": situacao_manual,
        "ultimo_disparo": ultimo_disparo,
    }


class TestElegivelParaDisparo:
    AGORA = datetime(2026, 8, 6, 12, 0, 0)

    def test_caso_normal_e_elegivel(self):
        assert elegivel_para_disparo(_tratativa(), self.AGORA) is True

    def test_nao_selecionado_nao_e_elegivel(self):
        assert elegivel_para_disparo(_tratativa(selecionado=False), self.AGORA) is False

    def test_tres_tentativas_nao_e_elegivel(self):
        assert elegivel_para_disparo(_tratativa(tentativas=3), self.AGORA) is False

    def test_ja_disparou_hoje_nao_e_elegivel(self):
        tratativa = _tratativa(ultimo_disparo=datetime(2026, 8, 6, 9, 0, 0))
        assert elegivel_para_disparo(tratativa, self.AGORA) is False

    def test_ja_disparou_hoje_aceita_string_iso(self):
        tratativa = _tratativa(ultimo_disparo="2026-08-06T09:00:00")
        assert elegivel_para_disparo(tratativa, self.AGORA) is False

    def test_disparou_ontem_continua_elegivel(self):
        tratativa = _tratativa(ultimo_disparo=datetime(2026, 8, 5, 9, 0, 0))
        assert elegivel_para_disparo(tratativa, self.AGORA) is True

    def test_bloqueado_por_sga_nao_e_elegivel(self):
        assert elegivel_para_disparo(_tratativa(status="bloqueado_sga"), self.AGORA) is False

    def test_situacao_manual_definida_nao_e_elegivel(self):
        assert elegivel_para_disparo(_tratativa(situacao_manual="Agendado"), self.AGORA) is False

    def test_limite_tentativas_customizado(self):
        """Com `limite_tentativas=5` (system_parameters.
        limite_tentativas_disparo customizado), 3 tentativas ainda é
        elegível — só o default (3) barra nesse ponto."""
        assert elegivel_para_disparo(_tratativa(tentativas=3), self.AGORA, limite_tentativas=5) is True
        assert elegivel_para_disparo(_tratativa(tentativas=5), self.AGORA, limite_tentativas=5) is False


class TestPassouDoHorarioCorte:
    def test_antes_do_corte_local_naive(self):
        agora = datetime(2026, 8, 6, 17, 29, 0)
        assert passou_do_horario_corte(agora, "17:30", "America/Recife") is False

    def test_exatamente_no_corte_local_naive(self):
        agora = datetime(2026, 8, 6, 17, 30, 0)
        assert passou_do_horario_corte(agora, "17:30", "America/Recife") is True

    def test_depois_do_corte_local_naive(self):
        agora = datetime(2026, 8, 6, 17, 31, 0)
        assert passou_do_horario_corte(agora, "17:30", "America/Recife") is True

    def test_converte_de_utc_pra_recife(self):
        # Recife é UTC-3 (sem horário de verão desde 2019): 20:29 UTC = 17:29 Recife
        agora_utc = datetime(2026, 8, 6, 20, 29, 0, tzinfo=timezone.utc)
        assert passou_do_horario_corte(agora_utc, "17:30", "America/Recife") is False

        agora_utc = datetime(2026, 8, 6, 20, 30, 0, tzinfo=timezone.utc)
        assert passou_do_horario_corte(agora_utc, "17:30", "America/Recife") is True


class TestDiaPermiteDisparo:
    # date(2024, 1, 1) é uma segunda-feira confirmada — âncora conhecida.
    SEGUNDA = date(2024, 1, 1)
    SEXTA = date(2024, 1, 5)
    SABADO = date(2024, 1, 6)
    DOMINGO = date(2024, 1, 7)

    def test_dia_util_normal_permite(self):
        assert dia_permite_disparo(self.SEGUNDA, feriados=set()) is True
        assert dia_permite_disparo(self.SEXTA, feriados=set()) is True

    def test_sabado_nao_permite(self):
        assert dia_permite_disparo(self.SABADO, feriados=set()) is False

    def test_domingo_nao_permite(self):
        assert dia_permite_disparo(self.DOMINGO, feriados=set()) is False

    def test_feriado_em_dia_util_nao_permite(self):
        assert dia_permite_disparo(self.SEGUNDA, feriados={self.SEGUNDA}) is False

    def test_excecao_ignora_fim_de_semana_e_feriado(self):
        assert dia_permite_disparo(self.SABADO, feriados=set(), permitir_excecao=True) is True
        assert dia_permite_disparo(self.SEGUNDA, feriados={self.SEGUNDA}, permitir_excecao=True) is True


class TestDeveEscalarParaLigacao:
    def test_tres_tentativas_sem_resposta_escala(self):
        assert deve_escalar_para_ligacao(_tratativa(tentativas=3)) is True

    def test_menos_de_tres_tentativas_nao_escala(self):
        assert deve_escalar_para_ligacao(_tratativa(tentativas=2)) is False

    def test_ja_resolvido_manualmente_nao_escala(self):
        tratativa = _tratativa(tentativas=3, situacao_manual="Cancelado")
        assert deve_escalar_para_ligacao(tratativa) is False

    def test_status_diferente_de_aguardando_resposta_nao_escala(self):
        tratativa = _tratativa(tentativas=3, status="respondido")
        assert deve_escalar_para_ligacao(tratativa) is False

    def test_limite_tentativas_customizado(self):
        """Mesmo `limite_tentativas` de `elegivel_para_disparo` — as duas
        funções precisam usar o mesmo valor pra nunca divergir."""
        assert deve_escalar_para_ligacao(_tratativa(tentativas=3), limite_tentativas=5) is False
        assert deve_escalar_para_ligacao(_tratativa(tentativas=5), limite_tentativas=5) is True


class TestDiasUteisEntre:
    # Mesmas âncoras de TestDiaPermiteDisparo: date(2024, 1, 1) é segunda.
    SEGUNDA = date(2024, 1, 1)
    TERCA = date(2024, 1, 2)
    QUARTA = date(2024, 1, 3)
    SEXTA = date(2024, 1, 5)
    PROXIMA_SEGUNDA = date(2024, 1, 8)

    def test_fim_igual_inicio_e_zero(self):
        assert dias_uteis_entre(self.SEGUNDA, self.SEGUNDA, feriados=set()) == 0

    def test_fim_antes_do_inicio_e_zero(self):
        assert dias_uteis_entre(self.SEXTA, self.SEGUNDA, feriados=set()) == 0

    def test_um_dia_util_normal(self):
        assert dias_uteis_entre(self.SEGUNDA, self.TERCA, feriados=set()) == 1

    def test_fim_de_semana_nao_conta(self):
        # sexta -> próxima segunda: só a segunda conta, sábado/domingo não.
        assert dias_uteis_entre(self.SEXTA, self.PROXIMA_SEGUNDA, feriados=set()) == 1

    def test_feriado_nao_conta(self):
        # segunda -> quarta, com terça como feriado: só a quarta conta.
        assert dias_uteis_entre(self.SEGUNDA, self.QUARTA, feriados={self.TERCA}) == 1


class TestResultadoLigacao:
    def test_conseguiu_agendar_finaliza(self):
        assert resultado_ligacao(True) == "finalizado"

    def test_nao_conseguiu_agendar_encaminha_puma(self):
        assert resultado_ligacao(False) == "encaminhado_puma"
