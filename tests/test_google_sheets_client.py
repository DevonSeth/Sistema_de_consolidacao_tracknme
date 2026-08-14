import pytest

from integrations import google_sheets_client as sheets


class WorksheetFalso:
    """Substitui um gspread.Worksheet real — só registra as chamadas."""

    def __init__(self):
        self.limpou = False
        self.valores_escritos = None

    def clear(self):
        self.limpou = True

    def update(self, values, value_input_option=None):
        self.valores_escritos = values


def test_carregar_administrador_recusa_instalacao_remocao(monkeypatch):
    chamou_worksheet = False

    def _worksheet_fake(planilha, aba):
        nonlocal chamou_worksheet
        chamou_worksheet = True
        return WorksheetFalso()

    monkeypatch.setattr(sheets, "_worksheet", _worksheet_fake)

    with pytest.raises(ValueError):
        sheets.carregar_administrador("Instalação-Remoção", [["x"]])

    assert chamou_worksheet is False


def test_carregar_administrador_recusa_aba_desconhecida(monkeypatch):
    monkeypatch.setattr(
        sheets, "_worksheet", lambda planilha, aba: WorksheetFalso()
    )

    with pytest.raises(ValueError):
        sheets.carregar_administrador("Aba Que Não Existe", [["x"]])


def test_carregar_administrador_incidentes_limpa_e_reescreve(monkeypatch):
    ws_falso = WorksheetFalso()
    planilhas_recebidas = []

    def _worksheet_fake(planilha, aba):
        planilhas_recebidas.append((planilha, aba))
        return ws_falso

    monkeypatch.setattr(sheets, "_worksheet", _worksheet_fake)

    linhas = [["1", "PUMA NORDESTE", "ABC1234"]]
    sheets.carregar_administrador("Incidentes", linhas)

    assert planilhas_recebidas == [(sheets.NOME_PLANILHA_ADMINISTRADOR, "Incidentes")]
    assert ws_falso.limpou is True
    assert ws_falso.valores_escritos == [sheets.CABECALHO_INCIDENTES] + linhas


def test_carregar_administrador_rastreadores_ativos_limpa_e_reescreve(monkeypatch):
    ws_falso = WorksheetFalso()
    monkeypatch.setattr(sheets, "_worksheet", lambda planilha, aba: ws_falso)

    linhas = [["1"] + [""] * 27]
    sheets.carregar_administrador("Rastreadores Ativos", linhas)

    assert ws_falso.limpou is True
    assert ws_falso.valores_escritos == [sheets.CABECALHO_RASTREADORES_ATIVOS] + linhas


class WorksheetComRegistros(WorksheetFalso):
    def __init__(self, registros):
        super().__init__()
        self._registros = registros

    def get_all_records(self, numericise_ignore=None):
        return self._registros


def test_ler_aba_descarta_linha_fantasma_sem_id_hash(monkeypatch):
    """Checkbox/dropdown aplicado numa faixa maior que o conteúdo real faz
    o Sheets devolver linha "fantasma" (todo campo vazio, só o checkbox
    aparecendo como FALSE) — só acontece na planilha Operacional, onde
    toda linha real tem "ID (hash)" preenchido."""
    registros = [
        {"ID (hash)": "chave-1", "Cliente": "Cliente Real", "Finalizado": "FALSE"},
        {"ID (hash)": "", "Cliente": "", "Finalizado": "FALSE"},
        {"ID (hash)": "   ", "Cliente": "", "Finalizado": "FALSE"},
    ]
    monkeypatch.setattr(sheets, "_worksheet", lambda planilha, aba: WorksheetComRegistros(registros))

    linhas = sheets.ler_aba(sheets.NOME_PLANILHA_OPERACIONAL, "Tratativas")

    assert linhas == [{"ID (hash)": "chave-1", "Cliente": "Cliente Real", "Finalizado": "FALSE"}]


def test_ler_aba_nao_filtra_fora_da_planilha_operacional(monkeypatch):
    """O filtro por "ID (hash)" é específico da planilha Operacional —
    outras abas (ex: Administrador) não têm essa coluna."""
    registros = [{"Placa": "ABC1234", "Cliente": "Alguém"}]
    monkeypatch.setattr(sheets, "_worksheet", lambda planilha, aba: WorksheetComRegistros(registros))

    linhas = sheets.ler_aba(sheets.NOME_PLANILHA_ADMINISTRADOR, "Incidentes")

    assert linhas == registros


def test_cabecalho_tratativas_tem_as_colunas_de_atendimento():
    assert len(sheets.CABECALHO_TRATATIVAS) == 33
    for coluna in ("Atendimento", "Base", "Ponto de Ação", "Retorno do Associado"):
        assert coluna in sheets.CABECALHO_TRATATIVAS


def test_cabecalho_pendente_ligacao_tem_atendimento_mas_nao_retorno_associado():
    assert len(sheets.CABECALHO_PENDENTE_LIGACAO) == 23
    for coluna in ("Atendimento", "Base", "Ponto de Ação"):
        assert coluna in sheets.CABECALHO_PENDENTE_LIGACAO
    assert "Retorno do Associado" not in sheets.CABECALHO_PENDENTE_LIGACAO


@pytest.mark.parametrize(
    "indice,letra",
    [(1, "A"), (2, "B"), (26, "Z"), (27, "AA"), (52, "AZ"), (53, "BA")],
)
def test_coluna_letra(indice, letra):
    assert sheets._coluna_letra(indice) == letra


class WorksheetComValidacao(WorksheetFalso):
    def __init__(self):
        super().__init__()
        self.validacoes = []

    def add_validation(self, range, condition_type, values, strict=False):
        self.validacoes.append((range, condition_type, values, strict))


def _validacao_esperada(cabecalho, coluna, valores):
    letra = sheets._coluna_letra(cabecalho.index(coluna) + 1)
    return (f"{letra}2:{letra}300", sheets.ValidationConditionType.one_of_list, valores, False)


def test_configurar_validacao_atendimento_aplica_nas_2_abas(monkeypatch):
    fakes = {
        "Tratativas": WorksheetComValidacao(),
        "Pendente de Ligação": WorksheetComValidacao(),
    }
    monkeypatch.setattr(sheets, "_worksheet", lambda planilha, aba: fakes[aba])

    sheets.configurar_validacao_atendimento(["Base Afogados"], ["Ponto Centro"])

    for aba, cabecalho in (
        ("Tratativas", sheets.CABECALHO_TRATATIVAS),
        ("Pendente de Ligação", sheets.CABECALHO_PENDENTE_LIGACAO),
    ):
        assert fakes[aba].validacoes == [
            _validacao_esperada(cabecalho, "Atendimento", sheets._ATENDIMENTO_VALORES),
            _validacao_esperada(cabecalho, "Base", ["Base Afogados"]),
            _validacao_esperada(cabecalho, "Ponto de Ação", ["Ponto Centro"]),
        ]


def test_configurar_checkbox_finalizado_pendente_ligacao(monkeypatch):
    fake = WorksheetComValidacao()
    monkeypatch.setattr(sheets, "_worksheet", lambda planilha, aba: fake)

    sheets.configurar_checkbox_finalizado_pendente_ligacao()

    letra = sheets._coluna_letra(sheets.CABECALHO_PENDENTE_LIGACAO.index("Finalizado") + 1)
    assert fake.validacoes == [(f"{letra}2:{letra}300", sheets.ValidationConditionType.boolean, [], True)]


class WorksheetComFormato(WorksheetFalso):
    def __init__(self):
        super().__init__()
        self.formats = []

    def format(self, range_name, body):
        self.formats.append((range_name, body))


def _formato(equipe: bool) -> dict:
    cor = sheets._COR_CABECALHO_FUNDO_EQUIPE if equipe else sheets._COR_CABECALHO_FUNDO_SISTEMA
    return {"backgroundColor": cor, "textFormat": {"bold": True, "foregroundColor": sheets._COR_CABECALHO_TEXTO}}


def test_runs_por_cor_agrupa_colunas_consecutivas_da_mesma_origem():
    cabecalho = ["A", "B", "C", "D", "E"]
    equipe = {"B", "C", "E"}

    assert sheets._runs_por_cor(cabecalho, equipe) == [
        (0, 1, False),  # A
        (1, 3, True),   # B, C
        (3, 4, False),  # D
        (4, 5, True),   # E
    ]


def test_runs_por_cor_sem_nenhuma_coluna_de_equipe_devolve_1_run():
    assert sheets._runs_por_cor(["A", "B", "C"], set()) == [(0, 3, False)]


def test_configurar_formatacao_cabecalho_aplica_nas_5_abas(monkeypatch):
    fakes = {aba: WorksheetComFormato() for aba in sheets._ABAS_CABECALHO_FORMATADO}
    monkeypatch.setattr(sheets, "_worksheet", lambda planilha, aba: fakes[aba])

    sheets.configurar_formatacao_cabecalho()

    assert set(fakes) == {
        "Tratativas", "Pendente de Ligação", "Encaminhar pra Puma",
        "Alertas", "Análise de Divergência - Instalação",
    }
    # Tratativas: interseção mais complexa (equipe intercalada com sistema)
    # conferida coluna a coluna, não só via `_runs_por_cor` (evita teste
    # circular que só reafirmaria a própria implementação).
    assert fakes["Tratativas"].formats == [
        ("A1:N1", _formato(False)),   # ID (hash) .. Dias sem contato
        ("O1:S1", _formato(True)),    # Discrepância revisada .. Selecionado
        ("T1:T1", _formato(False)),   # Status Contato
        ("U1:U1", _formato(True)),    # Telefone corrigido
        ("V1:AB1", _formato(False)),  # Status Sistema .. Retorno do Associado
        ("AC1:AG1", _formato(True)),  # Situação Manual .. Finalizado
    ]
    # Alertas: 2 runs (equipe é um bloco contíguo no fim)
    assert fakes["Alertas"].formats == [
        ("A1:F1", _formato(False)),
        ("G1:H1", _formato(True)),
    ]
    # Análise de Divergência - Instalação: 100% sistema, 1 run só
    assert fakes["Análise de Divergência - Instalação"].formats == [
        ("A1:J1", _formato(False)),
    ]


class WorksheetComValidacaoELimpeza(WorksheetComValidacao):
    """Também registra chamadas de `client.batch_update` (usado por
    `limpar_validacoes_aba`, que não passa por `add_validation`)."""

    def __init__(self, sheet_id=42):
        super().__init__()
        self.id = sheet_id
        self.spreadsheet_id = "planilha-fake"
        self.client = self
        self.batch_updates = []

    def batch_update(self, spreadsheet_id, body):
        self.batch_updates.append((spreadsheet_id, body))


def test_limpar_validacoes_aba_limpa_a_faixa_inteira_da_aba(monkeypatch):
    fake = WorksheetComValidacaoELimpeza()
    monkeypatch.setattr(sheets, "_worksheet", lambda planilha, aba: fake)

    sheets.limpar_validacoes_aba("Alertas")

    assert len(fake.batch_updates) == 1
    spreadsheet_id, body = fake.batch_updates[0]
    assert spreadsheet_id == "planilha-fake"
    grid_range = body["requests"][0]["setDataValidation"]["range"]
    assert grid_range["sheetId"] == 42
    assert grid_range["startRowIndex"] == 1  # linha 2 (pula cabeçalho)
    assert grid_range["startColumnIndex"] == 0
    assert grid_range["endColumnIndex"] == len(sheets.CABECALHO_ALERTAS)
    # sem "rule" no request = limpa a validação existente (API do Sheets)
    assert "rule" not in body["requests"][0]["setDataValidation"]


def test_configurar_checkboxes_tratativas_aplica_nas_4_colunas(monkeypatch):
    fake = WorksheetComValidacao()
    monkeypatch.setattr(sheets, "_worksheet", lambda planilha, aba: fake)

    sheets.configurar_checkboxes_tratativas()

    esperado = [
        (
            f"{sheets._coluna_letra(sheets.CABECALHO_TRATATIVAS.index(coluna) + 1)}2:"
            f"{sheets._coluna_letra(sheets.CABECALHO_TRATATIVAS.index(coluna) + 1)}300",
            sheets.ValidationConditionType.boolean,
            [],
            True,
        )
        for coluna in sheets._COLUNAS_CHECKBOX_TRATATIVAS
    ]
    assert fake.validacoes == esperado


class WorksheetFormatacao:
    def __init__(self):
        self.row_count = sheets._LINHA_LIMITE_FORMATACAO_CONDICIONAL
        self.id = 111
        self.resized = None

    def resize(self, rows):
        self.resized = rows
        self.row_count = rows


class SpreadsheetFalso:
    def __init__(self, worksheets):
        self._worksheets = worksheets
        self.batches = []

    def worksheet(self, aba):
        return self._worksheets[aba]

    def batch_update(self, body):
        self.batches.append(body)


def test_configurar_formatacao_condicional_usa_limiar_customizado(monkeypatch):
    """`limiar_dias_sem_contato` (default 7) substitui o valor fixo na
    fórmula customizada de "Dias sem contato" — editável via
    `system_parameters.limiar_dias_sem_contato` a partir de agora."""
    worksheets = {aba: WorksheetFormatacao() for aba in sheets._CABECALHOS_OPERACIONAL}
    spreadsheet_falso = SpreadsheetFalso(worksheets)
    monkeypatch.setattr(sheets, "_spreadsheet", lambda planilha: spreadsheet_falso)

    sheets.configurar_formatacao_condicional(limiar_dias_sem_contato=10)

    batch_tratativas = spreadsheet_falso.batches[0]
    formulas = [
        r["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
        for r in batch_tratativas["requests"]
        if r["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["type"] == "CUSTOM_FORMULA"
    ]
    assert len(formulas) == 1
    assert ">=10;" in formulas[0]
    assert ">=7;" not in formulas[0]


def test_configurar_formatacao_condicional_default_continua_7(monkeypatch):
    worksheets = {aba: WorksheetFormatacao() for aba in sheets._CABECALHOS_OPERACIONAL}
    spreadsheet_falso = SpreadsheetFalso(worksheets)
    monkeypatch.setattr(sheets, "_spreadsheet", lambda planilha: spreadsheet_falso)

    sheets.configurar_formatacao_condicional()

    batch_tratativas = spreadsheet_falso.batches[0]
    formulas = [
        r["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
        for r in batch_tratativas["requests"]
        if r["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["type"] == "CUSTOM_FORMULA"
    ]
    assert ">=7;" in formulas[0]


class WorksheetTexto:
    """Fake de worksheet pra `formatar_colunas_identificador_texto` — só
    registra `resize`/`format` (mesmo padrão de `WorksheetFormatacao`, sem
    herdar dela porque essa é específica de conditional format rule)."""

    def __init__(self, row_count):
        self.row_count = row_count
        self.resized = None
        self.formats = []

    def resize(self, rows):
        self.resized = rows
        self.row_count = rows

    def format(self, range_name, body):
        self.formats.append((range_name, body))


def _mock_spreadsheets_texto(monkeypatch, ws_admin, ws_operacional):
    sh_admin = SpreadsheetFalso(ws_admin)
    sh_operacional = SpreadsheetFalso(ws_operacional)

    def _spreadsheet_fake(planilha):
        if planilha == sheets.NOME_PLANILHA_ADMINISTRADOR:
            return sh_admin
        if planilha == sheets.NOME_PLANILHA_OPERACIONAL:
            return sh_operacional
        raise AssertionError(f"planilha inesperada: {planilha!r}")

    monkeypatch.setattr(sheets, "_spreadsheet", _spreadsheet_fake)


def test_formatar_colunas_identificador_texto_incidentes_redimensiona_e_formata(monkeypatch):
    ws_incidentes = WorksheetTexto(row_count=1698)
    ws_rastreadores = WorksheetTexto(row_count=12242)
    ws_oper = {aba: WorksheetTexto(row_count=10_000) for aba in sheets._CABECALHOS_OPERACIONAL}
    _mock_spreadsheets_texto(
        monkeypatch,
        {"Incidentes": ws_incidentes, "Rastreadores Ativos": ws_rastreadores},
        ws_oper,
    )

    sheets.formatar_colunas_identificador_texto()

    assert ws_incidentes.resized == sheets._LINHA_LIMITE_TEXTO_ADMINISTRADOR
    limite = sheets._LINHA_LIMITE_TEXTO_ADMINISTRADOR
    esperado = {
        f"{sheets._coluna_letra(sheets.CABECALHO_INCIDENTES.index(c) + 1)}2:"
        f"{sheets._coluna_letra(sheets.CABECALHO_INCIDENTES.index(c) + 1)}{limite}"
        for c in ("ID", "Imei", "Iccid", "Linha")
    }
    assert {r for r, _ in ws_incidentes.formats} == esperado
    assert all(body == {"numberFormat": {"type": "TEXT"}} for _, body in ws_incidentes.formats)


def test_formatar_colunas_identificador_texto_rastreadores_ativos_usa_posicao(monkeypatch):
    ws_incidentes = WorksheetTexto(row_count=1698)
    ws_rastreadores = WorksheetTexto(row_count=12242)
    ws_oper = {aba: WorksheetTexto(row_count=10_000) for aba in sheets._CABECALHOS_OPERACIONAL}
    _mock_spreadsheets_texto(
        monkeypatch,
        {"Incidentes": ws_incidentes, "Rastreadores Ativos": ws_rastreadores},
        ws_oper,
    )

    sheets.formatar_colunas_identificador_texto()

    assert ws_rastreadores.resized == sheets._LINHA_LIMITE_TEXTO_ADMINISTRADOR
    limite = sheets._LINHA_LIMITE_TEXTO_ADMINISTRADOR
    esperado = {
        f"{sheets._coluna_letra(p)}2:{sheets._coluna_letra(p)}{limite}"
        for p in (
            sheets.COL_RASTREADORES_ID,
            sheets.COL_RASTREADORES_IMEI,
            sheets.COL_RASTREADORES_NUMERO,
            sheets.COL_RASTREADORES_CELULAR,
            sheets.COL_RASTREADORES_ICCID,
        )
    }
    assert {r for r, _ in ws_rastreadores.formats} == esperado


def test_formatar_colunas_identificador_texto_nao_redimensiona_se_ja_grande(monkeypatch):
    limite = sheets._LINHA_LIMITE_TEXTO_ADMINISTRADOR
    ws_incidentes = WorksheetTexto(row_count=limite)
    ws_rastreadores = WorksheetTexto(row_count=limite)
    ws_oper = {aba: WorksheetTexto(row_count=sheets._LINHA_LIMITE_FORMATACAO_CONDICIONAL) for aba in sheets._CABECALHOS_OPERACIONAL}
    _mock_spreadsheets_texto(
        monkeypatch,
        {"Incidentes": ws_incidentes, "Rastreadores Ativos": ws_rastreadores},
        ws_oper,
    )

    sheets.formatar_colunas_identificador_texto()

    assert ws_incidentes.resized is None
    assert ws_rastreadores.resized is None
    assert all(ws.resized is None for ws in ws_oper.values())


def test_formatar_colunas_identificador_texto_operacional_so_abas_com_telefone(monkeypatch):
    ws_incidentes = WorksheetTexto(row_count=1698)
    ws_rastreadores = WorksheetTexto(row_count=12242)
    ws_oper = {aba: WorksheetTexto(row_count=300) for aba in sheets._CABECALHOS_OPERACIONAL}
    _mock_spreadsheets_texto(
        monkeypatch,
        {"Incidentes": ws_incidentes, "Rastreadores Ativos": ws_rastreadores},
        ws_oper,
    )

    sheets.formatar_colunas_identificador_texto()

    limite = sheets._LINHA_LIMITE_FORMATACAO_CONDICIONAL
    assert set(ws_oper) == {
        "Tratativas", "Pendente de Ligação", "Encaminhar pra Puma", "Alertas",
        "Análise de Divergência - Instalação",
    }
    for aba, cabecalho in sheets._CABECALHOS_OPERACIONAL.items():
        ws = ws_oper[aba]
        if "Telefone" not in cabecalho:
            # "Análise de Divergência - Instalação" não tem "Telefone" —
            # nunca deve ser tocada por esta função (mesma lógica de
            # `Instalação-Remoção`, que fica de fora de propósito).
            assert ws.resized is None
            assert ws.formats == []
            continue
        assert ws.resized == limite
        letra = sheets._coluna_letra(cabecalho.index("Telefone") + 1)
        assert ws.formats == [(f"{letra}2:{letra}{limite}", {"numberFormat": {"type": "TEXT"}})]
