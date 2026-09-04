"""
Cliente de leitura/escrita das planilhas (via `gspread`, autenticado por
service account — caminho do .json e IDs das planilhas vêm de
`config.manager`, nunca hardcoded).

Cabeçalhos confirmados por introspecção direta das planilhas reais:

    Planilha ADMINISTRADOR:

        'Instalação-Remoção' (22 colunas, 100% humana — sistema só lê):
            Situação, Data contrato, Nome Associado, CPF, celular,
            Celular Aux, Telefone Comercial, Logradouro, Número, Bairro,
            Cidade, Estado, Placa, Modelo, Ano Modelo, Valor FIPE,
            Tipo Veículo, Voluntário, Cooperativa, Chassi, Marca, Serviço

        'Incidentes' (15 colunas) e 'Rastreadores Ativos' (28 colunas) —
        antes preenchidas por um humano que copiava/colava o export do
        Track N' Me; o sistema agora também pode escrever aqui via
        `carregar_administrador()` (decisão: reduzir intervenção manual —
        ver `ABAS_ADMINISTRADOR_ESCRITAS`). 'Instalação-Remoção' continua
        exclusivamente humana.

        'Rastreadores Ativos' tem "Modelo" duplicado de propósito nas
        posições 2 e 13, "Marca" duplicado nas posições 12 e 15. Isso
        quebra `gspread.get_all_records()` (que exige cabeçalho sem
        duplicatas), então esta aba é sempre lida E escrita por POSIÇÃO —
        ver `ABAS_LER_POR_POSICAO`, `ler_aba`, `carregar_administrador` e as
        constantes `COL_RASTREADORES_*`. Ordem real (1-indexed):
            1 ID, 2 Modelo, 3 Imei, 4 Número, 5 Data de Criação,
            6 Data de Comunicação, 7 Data de Posição, 8 Cliente, 9 Celular,
            10 Placa do veículo, 11 Tipo, 12 Marca, 13 Modelo,
            14 Ano Fabr./Ano Modelo, 15 Marca, 16 Status, 17 Criado por,
            18 Data de Instalação, 19 Instalado por, 20 Local de Instalação,
            21 Operadora, 22 Data de Cancelamento, 23 Data de Descarte,
            24 Data de Manutenção, 25 Data de Retorno, 26 Data de Reativação,
            27 Chassi, 28 ICCID

    Planilha OPERACIONAL - PENDENCIAS - PUMA (sistema lê e escreve, sempre
    reescrevendo a aba inteira do zero — nunca edita célula a célula):

        'Tratativas' (33 colunas): ver `CABECALHO_TRATATIVAS`
        'Pendente de Ligação' (23 colunas): ver `CABECALHO_PENDENTE_LIGACAO`
        'Encaminhar pra Puma' (17 colunas): ver `CABECALHO_ENCAMINHAR_PUMA`
        'Alertas' (8 colunas, Fase F.2/F.5): ver `CABECALHO_ALERTAS` — fila
        de exceção (retorno tardio do associado / agendamento confirmado
        sem data), alimentada pelo webhook do Newmo + `etapa_processar_
        alertas`.
        'Análise de Divergência - Instalação' (10 colunas): ver
        `CABECALHO_ANALISE_DIVERGENCIA_INSTALACAO` — chassi já instalado
        (encontrado em Rastreadores Ativos, sem divergência de
        titularidade) mas ainda esquecido na aba Instalação-Remoção
        (`REGRA_INSTALACAO_JA_FEITA`). Mecânica, sem estado editável pelo
        atendente, alimentada por `etapa_publicar_fila_operacional`.

        5 abas ao todo — 'Alertas' já foi criada na planilha real
        (2026-08-11, via `Spreadsheet.add_worksheet`); 'Análise de
        Divergência - Instalação' ainda precisa ser criada manualmente
        (mesmo caminho) antes do primeiro `reescrever_aba`.

    Estrutura de `Tratativas`/`Pendente de Ligação` revisada em
    2026-08-06 (ver `docs/planilha_operacional.md`): `Situação Original`
    removida (nunca foi implementada; `SGA` já cobre esse papel, com
    dado ao vivo). `Tratativas` ganhou `Técnico` (dropdown nativo do
    Google Sheets, mantido direto na planilha, fora do Supabase),
    `Discrepância revisada` e `Telefone corrigido` (checkboxes
    desenhados em 2026-08-05, implementados agora junto com o motor de
    regras de Instalação/Remoção que os torna necessários) e
    `Finalizado` (checkbox — substitui o antigo "Retirada confirmada";
    fonte única de verdade sobre conclusão, tira a linha de `Tratativas`
    no ciclo seguinte). Nenhuma dessas colunas foi aplicada na planilha
    real ainda — só na constante de código; `reescrever_aba()` sempre
    limpa e reescreve a aba inteira a partir daqui, então a planilha
    real só reflete isso quando o orchestrator rodar de verdade.

    `Pendente de Ligação` ganhou `Finalizado` (checkbox, 2026-08-07,
    Fase F.4) — mesmo princípio de `Tratativas`: saída manual pra
    quando o atendimento por telefone termina por um caminho que a
    lógica automática (`Retornou?`/`Conseguiu Agendar?`) não cobre (ex:
    associado resolveu por outro canal), e também o único jeito de tirar
    a linha da aba depois de "Conseguiu Agendar? = Sim" (isso só marca
    um agendamento futuro, não uma conclusão).

    `Tratativas` e `Pendente de Ligação` ganharam `Atendimento`/`Base`/
    `Ponto de Ação` (2026-08-10, Passo 3 de F.1 — ver
    `docs/templates_whatsapp.md`) — decide qual template de WhatsApp
    disparar. `Tratativas` também ganhou `Retorno do Associado` (só
    aqui — os botões de discrepância só existem nos templates de 1º
    contato). Nenhuma dessas colunas tem lógica de leitura/gravação no
    orchestrator ainda (fica pro Passo 6) — só a estrutura e a validação
    de dropdown (`configurar_validacao_atendimento`), preparadas agora.
"""

from functools import lru_cache

import gspread
from gspread.utils import (
    ValidationConditionType,
    ValueInputOption,
    a1_range_to_grid_range,
    convert_hex_to_colors_dict,
    rowcol_to_a1,
)

from config import manager
from integrations.retry_utils import retry_erro_transitorio_windows
# Posições de coluna de 'Rastreadores Ativos' moram em core.constants (não
# aqui) porque core/motor_regras.py também precisa delas e `core/` nunca
# importa de `integrations/` — este módulo só reexporta.
from core.constants import (  # noqa: F401 (reexportado para quem já importava daqui)
    COL_RASTREADORES_CELULAR,
    COL_RASTREADORES_CHASSI,
    COL_RASTREADORES_CLIENTE,
    COL_RASTREADORES_DATA_POSICAO,
    COL_RASTREADORES_ICCID,
    COL_RASTREADORES_ID,
    COL_RASTREADORES_IMEI,
    COL_RASTREADORES_NUMERO,
    COL_RASTREADORES_PLACA,
    COL_RASTREADORES_STATUS,
)

NOME_PLANILHA_ADMINISTRADOR = "Administrador"
NOME_PLANILHA_OPERACIONAL = "Operacional - Pendencias - Puma"

ABAS_LER_POR_POSICAO = {"Rastreadores Ativos"}

# Abas da planilha Administrador que o sistema pode escrever (via
# `carregar_administrador`) — 'Instalação-Remoção' fica de fora de propósito,
# continua 100% humana.
ABAS_ADMINISTRADOR_ESCRITAS = {"Incidentes", "Rastreadores Ativos"}

CABECALHO_INCIDENTES = [
    "ID", "Marca", "Placa", "Modelo do equipamento", "Imei", "Iccid", "Linha",
    "Operadora", "Data do incidente", "Evento", "Data do retorno", "Operador",
    "Status", "Situação", "Cliente",
]

CABECALHO_RASTREADORES_ATIVOS = [
    "ID", "Modelo", "Imei", "Número", "Data de Criação", "Data de Comunicação",
    "Data de Posição", "Cliente", "Celular", "Placa do veículo", "Tipo",
    "Marca", "Modelo", "Ano Fabr./Ano Modelo", "Marca", "Status",
    "Criado por", "Data de Instalação", "Instalado por", "Local de Instalação",
    "Operadora", "Data de Cancelamento", "Data de Descarte",
    "Data de Manutenção", "Data de Retorno", "Data de Reativação",
    "Chassi", "ICCID",
]

_CABECALHOS_ADMINISTRADOR = {
    "Incidentes": CABECALHO_INCIDENTES,
    "Rastreadores Ativos": CABECALHO_RASTREADORES_ATIVOS,
}

CABECALHO_TRATATIVAS = [
    "ID (hash)", "Tipo Serviço", "Identificador", "Chassi", "Placa", "Cliente",
    "Telefone", "Cidade", "Bairro", "Data Contrato / Data Incidente", "SGA",
    "Ação Sugerida", "Observação do Sistema", "Nível de Urgência",
    "Dias sem contato",
    "Discrepância revisada",
    "Atendimento", "Base", "Ponto de Ação",
    "Selecionado", "Status Contato", "Telefone corrigido", "Status Sistema",
    "Tentativa 1", "Tentativa 2", "Tentativa 3", "Resposta", "Data Resposta",
    "Retorno do Associado",
    "Situação Manual", "Data Agendada", "Técnico", "Observação Manual",
    "Finalizado",
]

CABECALHO_PENDENTE_LIGACAO = [
    "ID (hash)", "Tipo Serviço", "Identificador", "Chassi", "Cliente",
    "Telefone", "Cidade", "Bairro", "Data Contrato / Data Incidente", "SGA",
    "Ação Sugerida", "Observação do Sistema", "Nível de Urgência",
    "Dias sem contato",
    "Atendimento", "Base", "Ponto de Ação",
    "Histórico de Tentativas (msg)", "Data Contato", "Retornou?",
    "Conseguiu Agendar?", "Observação", "Finalizado",
]

CABECALHO_ENCAMINHAR_PUMA = [
    "ID (hash)", "Tipo Serviço", "Identificador", "Chassi", "Cliente",
    "Telefone", "Cidade", "Bairro", "Data Contrato / Data Incidente",
    "Ação Sugerida", "Observação do Sistema", "Nível de Urgência",
    "Dias sem contato",
    "Data Encaminhamento", "Motivo", "Status", "Observação Puma",
]

CABECALHO_ALERTAS = [
    "ID (hash)", "Cliente", "Telefone", "Onde está", "Tipo de Alerta",
    "Contexto", "Ação", "Data Agendada",
]

CABECALHO_ANALISE_DIVERGENCIA_INSTALACAO = [
    "ID (hash)", "Chassi", "Placa",
    "Cliente (Instalação-Remoção cadastro)", "Cliente (Rastreadores Ativos)",
    "Data Contrato", "Data de Instalação", "IMEI", "Motivo", "Observação", "Ação",
]

# Bloco B (2026-08-24) — mesmo espírito da de Instalação, mas pra
# divergências de Remoção (SGA ainda ATIVO, modelo de equipamento fora
# da lista permitida, titularidade divergente).
CABECALHO_ANALISE_DIVERGENCIA_REMOCAO = [
    "ID (hash)", "Chassi", "Placa",
    "Cliente cadastro", "Cliente Rastreadores Ativos",
    "Modelo do Equipamento", "Status SGA", "Motivo", "Observação", "Ação",
]

# Manutenção (2026-08-25) — mesmo espírito das duas acima, mas pro caso
# em que o SGA diverge de ATIVO (INATIVO/INADIMPLENTE/CANCELADO/etc) mas
# o equipamento segue comunicando -- REGRA_SGA_INATIVO continua fechando
# o incidente automaticamente (comportamento inalterado); esta aba só dá
# visibilidade. Só existe 1 causa possível, por isso sem "Motivo".
CABECALHO_ANALISE_DIVERGENCIA_MANUTENCAO = [
    "ID (hash)", "Chassi", "Placa", "Cliente", "Evento",
    "Status SGA", "Observação", "Ação",
]

_CABECALHOS_OPERACIONAL = {
    "Tratativas": CABECALHO_TRATATIVAS,
    "Pendente de Ligação": CABECALHO_PENDENTE_LIGACAO,
    "Encaminhar pra Puma": CABECALHO_ENCAMINHAR_PUMA,
    "Alertas": CABECALHO_ALERTAS,
    "Análise de Divergência - Instalação": CABECALHO_ANALISE_DIVERGENCIA_INSTALACAO,
    "Análise de Divergência - Remoção": CABECALHO_ANALISE_DIVERGENCIA_REMOCAO,
    "Análise de Divergência - Manutenção": CABECALHO_ANALISE_DIVERGENCIA_MANUTENCAO,
}


@lru_cache(maxsize=1)
def _cliente() -> gspread.Client:
    """Cliente gspread, criado uma única vez (singleton). Se a config for
    resalva com um caminho de credencial novo em runtime, chamar
    `_cliente.cache_clear()` antes da próxima chamada.
    """
    cfg = manager.carregar_config()["google_sheets"]
    return gspread.service_account(filename=cfg["credenciais_path"])


def _id_planilha(planilha: str) -> str:
    cfg = manager.carregar_config()["google_sheets"]
    if planilha == NOME_PLANILHA_ADMINISTRADOR:
        return cfg["planilha_administrador_id"]
    if planilha == NOME_PLANILHA_OPERACIONAL:
        return cfg["planilha_operacional_id"]
    raise ValueError(f"Planilha desconhecida: {planilha!r}")


def _spreadsheet(planilha: str) -> gspread.Spreadsheet:
    return _cliente().open_by_key(_id_planilha(planilha))


def _worksheet(planilha: str, aba: str) -> gspread.Worksheet:
    return _spreadsheet(planilha).worksheet(aba)


@retry_erro_transitorio_windows()
def ler_aba(planilha: str, aba: str) -> list[dict]:
    """Lê uma aba inteira.

    Para 'Rastreadores Ativos' (cabeçalho com "Modelo"/"Marca" duplicados),
    retorna um dict por linha com chaves posicionais `col_1`..`col_28` (ver
    constantes `COL_RASTREADORES_*` para as posições relevantes) — nunca por
    nome de coluna, que perderia uma das duas ocorrências.

    Para as demais abas, um dict por linha com o cabeçalho como chave
    (`gspread.get_all_records`). `numericise_ignore=["all"]` desliga a
    "numericização" automática do gspread (célula com cara de número vira
    `int`/`float` em vez de manter texto) — descoberto ao vivo (2026-08-05):
    sem isso, `Imei`/`Iccid`/`Linha`/`ID` de 'Incidentes' viravam `int`, e
    `core.motor_regras` chama `.strip()` neles em vários pontos (assumindo
    string, como todos os testes já fixam). O resto do sistema só deve
    receber texto puro do Sheets, nunca tipo inferido.

    Nas 3 abas da planilha Operacional, descarta linhas com "ID (hash)"
    vazio — achado ao vivo (2026-08-07): aplicar validação de dado
    (checkbox/dropdown) numa faixa de linhas maior do que o conteúdo real
    faz o Google Sheets tratar as células com checkbox como "com valor"
    (`FALSE` implícito) mesmo nunca tendo sido escritas, então
    `get_all_records()` passa a devolver uma linha "fantasma" pra cada
    linha vazia dentro da faixa de validação. Toda linha real das 3 abas
    sempre tem "ID (hash)" preenchido (é a chave de dedup), então esse
    filtro nunca descarta dado de verdade.

    `expected_headers` (só os nomes não-vazios do cabeçalho real) evita
    que colunas extras sem nome — achado ao vivo (2026-09-04): humano
    colou/adicionou colunas em branco à direita de 'Instalação-Remoção',
    virando várias colunas com cabeçalho `''` — sejam contadas como
    "cabeçalho duplicado" pelo `gspread` (que só valida unicidade quando
    `expected_headers` é `None`). Os valores dessas colunas em branco
    ainda colapsam numa chave `''` no dict resultante, mas nada no
    sistema lê essa chave.
    """
    ws = _worksheet(planilha, aba)
    if aba in ABAS_LER_POR_POSICAO:
        linhas = ws.get_all_values()[1:]
        return [
            {f"col_{i + 1}": valor for i, valor in enumerate(linha)}
            for linha in linhas
        ]
    cabecalho = ws.row_values(1)
    registros = ws.get_all_records(
        numericise_ignore=["all"], expected_headers=[h for h in cabecalho if h]
    )
    if planilha == NOME_PLANILHA_OPERACIONAL:
        registros = [r for r in registros if (r.get("ID (hash)") or "").strip()]
    return registros


@retry_erro_transitorio_windows()
def reescrever_aba(planilha: str, aba: str, linhas: list[dict]) -> None:
    """Limpa a aba inteira e reescreve do zero (nunca edita célula a
    célula) — só permitido na planilha Operacional; o Administrador é
    somente leitura pelo sistema.

    **Reaplica a validação de checkbox logo depois de escrever** em
    "Tratativas"/"Pendente de Ligação" (achado 2026-08-25: confirmado 2x
    ao vivo que os checkboxes BOOLEAN somem depois do ciclo `clear()`+
    `update()` desta função — a documentação da API do Sheets diz que
    nem `values.clear` nem `values.update` deveriam tocar validação, mas
    o comportamento real observado é esse. Em vez de perseguir mais a
    causa exata do lado da API, a aba fica auto-curativa: toda escrita
    real já deixa o checkbox funcionando de novo, sem depender de rerun
    manual de `configurar_checkboxes_tratativas`/`configurar_checkbox_
    finalizado_pendente_ligacao`). Os dropdowns (Situação Manual/
    Atendimento/Base/Ponto de Ação) não foram afetados nesse achado e
    continuam fora daqui — `configurar_validacao_atendimento` depende de
    `nomes_bases`/`nomes_pontos_acao` externos (Supabase), que este
    módulo não conhece.
    """
    if planilha != NOME_PLANILHA_OPERACIONAL:
        raise ValueError(
            f"Só a planilha {NOME_PLANILHA_OPERACIONAL!r} pode ser escrita pelo sistema"
        )
    cabecalho = _CABECALHOS_OPERACIONAL.get(aba)
    if cabecalho is None:
        raise ValueError(f"Aba desconhecida em {planilha!r}: {aba!r}")

    corpo = [[linha.get(coluna, "") for coluna in cabecalho] for linha in linhas]
    ws = _worksheet(planilha, aba)
    ws.clear()
    ws.update(values=[cabecalho] + corpo, value_input_option=ValueInputOption.user_entered)

    if aba == "Tratativas":
        configurar_checkboxes_tratativas()
    elif aba == "Pendente de Ligação":
        configurar_checkbox_finalizado_pendente_ligacao()


_COR_VERDE = convert_hex_to_colors_dict("#B7E1CD")
_COR_AMARELO = convert_hex_to_colors_dict("#FFF2CC")
_COR_LARANJA = convert_hex_to_colors_dict("#FCE4B2")
_COR_VERMELHO = convert_hex_to_colors_dict("#F4C7C3")

# "Encaminhar pra Puma" é só acompanhamento (regra fechada 2026-08-07,
# ver docs/planilha_operacional.md) — sem limite/destaque de "Dias sem
# contato" lá, só nessas duas.
_ABAS_COM_DIAS_SEM_CONTATO_DESTACADO = {"Tratativas", "Pendente de Ligação"}

# "Alertas" (adicionada em 2026-08-11, depois desta função existir) não
# tem coluna "Nível de Urgência" nem "Dias sem contato" — é uma fila de
# exceção, não uma fila priorizável — por isso fica de fora da cor por
# urgência (bug real encontrado ao escrever teste pra esta função: sem
# essa exclusão, `cabecalho.index("Nível de Urgência")` levanta
# `ValueError` pra "Alertas").
_ABAS_COM_NIVEL_URGENCIA = {"Tratativas", "Pendente de Ligação", "Encaminhar pra Puma"}



# Limite de linhas das regras de formatação condicional — achado ao vivo
# testando numa aba descartável (2026-08-07): omitir `endRowIndex` NÃO
# significa "sem limite pra sempre" — a API resolve pro tamanho atual da
# aba no momento em que a regra é criada (confirmado lendo de volta:
# regra criada numa aba de 6 linhas voltou com `endRowIndex: 6`), então
# linhas novas escritas depois por `reescrever_aba` ficariam de fora.
# Precisa de um valor explícito e generoso. Diferente da validação de
# checkbox/dropdown (que só tolera 300 por causa do bug de "linha
# fantasma" em `ler_aba`), formatação condicional não cria valor
# nenhum — só afeta renderização — então um limite bem maior aqui é
# seguro.
_LINHA_LIMITE_FORMATACAO_CONDICIONAL = 10_000


def _regra_formatacao_condicional(sheet_id: int, col_idx: int, tipo: str, valores: list[dict], cor: dict) -> dict:
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": 1,  # pula o cabeçalho (linha 1)
                    "endRowIndex": _LINHA_LIMITE_FORMATACAO_CONDICIONAL,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                }],
                "booleanRule": {
                    "condition": {"type": tipo, "values": valores},
                    "format": {"backgroundColor": cor},
                },
            }
        }
    }


@retry_erro_transitorio_windows()
def configurar_formatacao_condicional(limiar_dias_sem_contato: int = 7) -> None:
    """Configura formatação condicional de cor nas 3 abas operacionais —
    SETUP ÚNICO, não faz parte do pipeline recorrente (mesma natureza da
    configuração de checkbox/dropdown já feita ao vivo antes: roda uma
    vez, não é chamada em todo ciclo de execução do orchestrator).

    `gspread` 6.2.1 não tem nenhum método nativo pra "conditional format
    rules" (só `add_validation`, que é validação de dado, e
    `format`/`batch_format`, que é formatação fixa) — por isso as regras
    são montadas como request bruto da API REST do Sheets
    (`AddConditionalFormatRuleRequest`) via `Spreadsheet.batch_update`.

    Regras (decisões já fechadas — ver `docs/regras_negocio.md`/
    `docs/pop_operador.md` e `docs/planilha_operacional.md`):
    - "Nível de Urgência" (3 abas): 1-2 verde, 3 amarelo, 4 laranja, 5
      vermelho.
    - "Dias sem contato" (só Tratativas/Pendente de Ligação): a coluna
      guarda texto ("10 dias sem contato"), então a condição usa uma
      fórmula customizada que extrai o número via `REGEXEXTRACT`
      (envolvida em `IFERROR` — nunca colore por engano nem quebra numa
      célula vazia/sem match) e destaca vermelho quando >=
      `limiar_dias_sem_contato` (default 7, editável via
      `system_parameters.limiar_dias_sem_contato` — quem chama decide o
      valor, esta função continua sem acessar o Supabase diretamente).

    **Mudar `limiar_dias_sem_contato` só tem efeito rodando esta função
    de novo** — a formatação já aplicada numa execução anterior fica
    fixa na planilha até alguém rodar de novo (e, por não ser
    idempotente, isso duplica a regra a menos que a antiga seja
    apagada manualmente na UI do Sheets primeiro — mesma ressalva de
    sempre, só reforçada aqui porque agora existe motivo real pra rodar
    de novo).

    NÃO é idempotente — não apaga regras já existentes antes de criar as
    novas (decisão: menos código sem cobertura de teste automatizado,
    mesmo padrão de baixa cerimônia já usado no setup de checkbox/
    dropdown). Rodar de novo sem apagar manualmente as regras antigas na
    UI do Google Sheets primeiro duplica as regras.

    **Redimensiona a aba antes de criar as regras** — achado ao vivo
    testando numa aba descartável (2026-08-07): a API sempre recorta
    `endRowIndex` pro tamanho ATUAL do grid da aba no momento da
    criação da regra, mesmo pedindo um valor maior
    (`_LINHA_LIMITE_FORMATACAO_CONDICIONAL`). As 3 abas reais hoje têm só
    301 linhas (mesmo limite da validação de checkbox/dropdown já
    configurada) — sem redimensionar primeiro, a formatação condicional
    nunca cobriria linha nenhuma escrita depois de `reescrever_aba`
    crescer a aba além disso. Aumentar `row_count` sozinho (sem também
    aplicar validação na faixa nova) não recria o bug de "linha
    fantasma" da validação — esse bug era especificamente do checkbox
    tornar célula vazia em `FALSE` implícito, não de a aba só ter mais
    linhas em branco.
    """
    sh = _spreadsheet(NOME_PLANILHA_OPERACIONAL)
    for aba, cabecalho in _CABECALHOS_OPERACIONAL.items():
        if aba not in _ABAS_COM_NIVEL_URGENCIA and aba not in _ABAS_COM_DIAS_SEM_CONTATO_DESTACADO:
            continue
        ws = sh.worksheet(aba)
        if ws.row_count < _LINHA_LIMITE_FORMATACAO_CONDICIONAL:
            ws.resize(rows=_LINHA_LIMITE_FORMATACAO_CONDICIONAL)
        requests = []
        if aba in _ABAS_COM_NIVEL_URGENCIA:
            col_urgencia = cabecalho.index("Nível de Urgência")
            requests += [
                _regra_formatacao_condicional(
                    ws.id, col_urgencia, "NUMBER_BETWEEN",
                    [{"userEnteredValue": "1"}, {"userEnteredValue": "2"}], _COR_VERDE,
                ),
                _regra_formatacao_condicional(
                    ws.id, col_urgencia, "NUMBER_EQ", [{"userEnteredValue": "3"}], _COR_AMARELO,
                ),
                _regra_formatacao_condicional(
                    ws.id, col_urgencia, "NUMBER_EQ", [{"userEnteredValue": "4"}], _COR_LARANJA,
                ),
                _regra_formatacao_condicional(
                    ws.id, col_urgencia, "NUMBER_EQ", [{"userEnteredValue": "5"}], _COR_VERMELHO,
                ),
            ]
        if aba in _ABAS_COM_DIAS_SEM_CONTATO_DESTACADO:
            col_dias = cabecalho.index("Dias sem contato")
            referencia_linha_2 = rowcol_to_a1(2, col_dias + 1)
            # Locale da planilha é pt_BR -> separador de argumento de
            # fórmula via API é ";", não "," (achado ao vivo testando
            # numa aba descartável: REGEXMATCH/REGEXEXTRACT/FIND com ","
            # sempre voltavam "Invalid ConditionValue.userEnteredValue"
            # da API, mesmo com sintaxe correta — só o separador errado).
            formula = f'=IFERROR(VALUE(REGEXEXTRACT(${referencia_linha_2};"\\d+"))>={limiar_dias_sem_contato};FALSE)'
            requests.append(_regra_formatacao_condicional(
                ws.id, col_dias, "CUSTOM_FORMULA", [{"userEnteredValue": formula}], _COR_VERMELHO,
            ))
        sh.batch_update({"requests": requests})


_LINHA_LIMITE_VALIDACAO = 10_000  # achado 2026-08-21: o teto antigo (300)
# datava de quando o volume real era muito menor -- com a fila real girando
# ~1.900 itens, >80% da aba ficava sem checkbox/dropdown (aparecia como
# texto "FALSE" cru). O motivo histórico do teto baixo (evitar "linha
# fantasma", ver `ler_aba`) já não se aplica: `ler_aba` descarta toda linha
# sem "ID (hash)" preenchido, o que já neutraliza esse efeito colateral
# independente de quão larga for a faixa de validação. Mesmo valor de
# `_LINHA_LIMITE_FORMATACAO_CONDICIONAL` (folga generosa e já aceita nesta
# planilha), mantido como constante separada por serem conceitos diferentes.


def _garantir_linhas_minimas(ws: gspread.Worksheet, minimo: int) -> None:
    """Redimensiona a aba ANTES de aplicar validação/formatação numa faixa
    maior que o grid atual -- achado 2026-08-07 (ver `configurar_
    formatacao_condicional`): a API sempre recorta o range pedido pro
    tamanho ATUAL da aba no momento da chamada, mesmo pedindo um valor
    maior."""
    if ws.row_count < minimo:
        ws.resize(rows=minimo)


@retry_erro_transitorio_windows()
def limpar_validacoes_aba(aba: str) -> None:
    """Remove QUALQUER validação de dado (checkbox/dropdown) de toda a
    faixa de dados da aba — SETUP, mesma natureza dos `configurar_*`.

    Achado ao vivo 2026-08-14: validação do Sheets fica presa à LETRA da
    coluna, não ao nome — toda vez que uma coluna nova é inserida no meio
    de `CABECALHO_TRATATIVAS`/`CABECALHO_PENDENTE_LIGACAO`/`CABECALHO_
    ENCAMINHAR_PUMA` (ex: "Discrepância revisada", 2026-08-13), as
    colunas depois dela deslizam pra letras diferentes, mas a validação
    configurada antes continua na letra antiga — passa a "vazar" pra
    dentro de outra coluna que por coincidência caiu naquela letra depois
    (ex: dropdown sobrando em "Status Sistema"/"Tentativa 2", que nunca
    tiveram validação nenhuma configurada em código). Rodar isto ANTES de
    `configurar_checkboxes_tratativas`/`configurar_checkbox_finalizado_
    pendente_ligacao`/`configurar_validacao_atendimento`/`configurar_
    validacao_alertas` garante que sobra só a validação que o código
    intencionalmente recria, nunca lixo de um layout de cabeçalho
    anterior.
    """
    cabecalho = _CABECALHOS_OPERACIONAL[aba]
    ws = _worksheet(NOME_PLANILHA_OPERACIONAL, aba)
    _garantir_linhas_minimas(ws, _LINHA_LIMITE_VALIDACAO)
    ultima_letra = _coluna_letra(len(cabecalho))
    grid = a1_range_to_grid_range(f"A2:{ultima_letra}{_LINHA_LIMITE_VALIDACAO}", ws.id)
    ws.client.batch_update(ws.spreadsheet_id, {"requests": [{"setDataValidation": {"range": grid}}]})

_ATENDIMENTO_VALORES = ["Base", "Em rota", "Ação"]

# Abas onde `Atendimento` existe e é editável (ver docs/templates_whatsapp.md
# seção 3) — "Encaminhar pra Puma" fica de fora, não tem esteira de mensagem.
_ABAS_COM_ATENDIMENTO = {"Tratativas", "Pendente de Ligação"}


def _coluna_letra(indice_1based: int) -> str:
    """Índice de coluna (1 = A, 27 = AA, ...) pra letra A1."""
    letras = ""
    n = indice_1based
    while n > 0:
        n, resto = divmod(n - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


@retry_erro_transitorio_windows()
def configurar_validacao_atendimento(
    nomes_bases: list[str], nomes_pontos_acao: list[str]
) -> None:
    """Configura os dropdowns de `Atendimento`/`Base`/`Ponto de Ação` em
    `Tratativas` e `Pendente de Ligação` — SETUP, não faz parte do
    pipeline recorrente (mesma natureza de `configurar_formatacao_
    condicional`). Precisa rodar de novo sempre que a lista de bases/
    pontos de ação mudar (cadastro novo, ativado ou desativado) — hoje
    isso é manual (sem CRUD ainda, ver `docs/templates_whatsapp.md`
    seção 4); quando o `ui/` existir, o CRUD deve chamar essa função a
    cada alteração.

    `nomes_bases`/`nomes_pontos_acao` vêm de fora (quem chama busca as
    linhas ativas de `bases`/`pontos_acao` no Supabase) — este módulo
    não conhece o Supabase diretamente, mesmo princípio de isolamento já
    usado nas outras integrações.

    Faixa de linhas limitada a `_LINHA_LIMITE_VALIDACAO`, mesmo valor e
    mesmo motivo já usado nos outros dropdowns/checkboxes dessas 2 abas (ver comentário de
    `_LINHA_LIMITE_VALIDACAO`).

    Achado ao vivo 2026-08-14/15 (auditoria de validações): `showCustomUi`
    do gspread vem `False` por padrão — sem passar `True`, a validação
    valia (célula aceita/rejeita certo) mas o Sheets nunca desenhava a
    setinha/lista suspensa de verdade na célula. Mesmo bug corrigido em
    `configurar_validacao_alertas`/`configurar_validacao_situacao_manual`/
    `configurar_validacao_retornou_conseguiu_agendar`/`configurar_
    validacao_status_puma`.
    """
    for aba in _ABAS_COM_ATENDIMENTO:
        cabecalho = _CABECALHOS_OPERACIONAL[aba]
        ws = _worksheet(NOME_PLANILHA_OPERACIONAL, aba)
        _garantir_linhas_minimas(ws, _LINHA_LIMITE_VALIDACAO)
        for coluna, valores in (
            ("Atendimento", _ATENDIMENTO_VALORES),
            ("Base", nomes_bases),
            ("Ponto de Ação", nomes_pontos_acao),
        ):
            letra = _coluna_letra(cabecalho.index(coluna) + 1)
            intervalo = f"{letra}2:{letra}{_LINHA_LIMITE_VALIDACAO}"
            ws.add_validation(
                intervalo, ValidationConditionType.one_of_list, valores,
                strict=False, showCustomUi=True,
            )


def _configurar_checkbox(aba: str, coluna: str) -> None:
    """`showCustomUi=True` é obrigatório aqui pelo mesmo motivo já achado
    pros dropdowns (2026-08-14/15, ver `configurar_validacao_atendimento`):
    é esse campo que faz o Sheets desenhar o widget de checkbox de
    verdade na célula, não só validar o valor por baixo."""
    cabecalho = _CABECALHOS_OPERACIONAL[aba]
    ws = _worksheet(NOME_PLANILHA_OPERACIONAL, aba)
    _garantir_linhas_minimas(ws, _LINHA_LIMITE_VALIDACAO)
    letra = _coluna_letra(cabecalho.index(coluna) + 1)
    intervalo = f"{letra}2:{letra}{_LINHA_LIMITE_VALIDACAO}"
    ws.add_validation(intervalo, ValidationConditionType.boolean, [], strict=True, showCustomUi=True)


@retry_erro_transitorio_windows()
def configurar_checkbox_finalizado_pendente_ligacao() -> None:
    """Configura a validação BOOLEAN (checkbox) da coluna "Finalizado" na
    aba "Pendente de Ligação" — SETUP, mesma natureza de
    `configurar_validacao_atendimento`/`configurar_formatacao_condicional`
    (roda uma vez, não faz parte do pipeline recorrente).

    Achado ao vivo 2026-08-13: essa coluna nunca teve a validação
    configurada — aparecia como texto literal `"False"` em vez de uma
    caixa de seleção de verdade. Mesma faixa/limite de linhas
    (`_LINHA_LIMITE_VALIDACAO`) já usado nos outros dropdowns/checkboxes
    dessa planilha.
    """
    _configurar_checkbox("Pendente de Ligação", "Finalizado")


_COLUNAS_CHECKBOX_TRATATIVAS = [
    "Discrepância revisada", "Selecionado", "Telefone corrigido", "Finalizado",
]


@retry_erro_transitorio_windows()
def configurar_checkboxes_tratativas() -> None:
    """Configura a validação BOOLEAN (checkbox) das 4 colunas booleanas de
    "Tratativas" — SETUP, mesma natureza de
    `configurar_checkbox_finalizado_pendente_ligacao`.

    Achado ao vivo 2026-08-13 (mesma sessão): nenhuma delas tinha a
    validação configurada de fato — apareciam como texto `"FALSE"`
    (às vezes em itálico, formatação herdada) em vez de caixa de seleção
    de verdade, apesar da docstring do módulo mencionar esse checkbox
    como "adicionado em 2026-08-07" (nunca foi versionado em código, e
    parece nunca ter sido aplicado de fato em produção).
    """
    for coluna in _COLUNAS_CHECKBOX_TRATATIVAS:
        _configurar_checkbox("Tratativas", coluna)


_SITUACAO_MANUAL_VALORES = ["Agendado", "Cancelado", "Solicitação operacional"]


@retry_erro_transitorio_windows()
def configurar_validacao_situacao_manual() -> None:
    """Configura o dropdown da coluna "Situação Manual" em "Tratativas" —
    SETUP, mesma natureza de `configurar_validacao_atendimento`/
    `configurar_validacao_alertas` (roda uma vez, não faz parte do
    pipeline recorrente).

    Achado ao vivo 2026-08-14 (auditoria de validações): documentada
    desde 2026-08-07 (`docs/planilha_operacional.md`), mas nunca tinha
    sido implementada de fato — a coluna sempre existiu como texto livre,
    sem nenhuma validação configurada na planilha real. `showCustomUi=True`
    é obrigatório aqui — sem isso a validação vale mas não aparece como
    dropdown de verdade na célula (achado ao vivo, mesma auditoria).
    """
    cabecalho = _CABECALHOS_OPERACIONAL["Tratativas"]
    ws = _worksheet(NOME_PLANILHA_OPERACIONAL, "Tratativas")
    _garantir_linhas_minimas(ws, _LINHA_LIMITE_VALIDACAO)
    letra = _coluna_letra(cabecalho.index("Situação Manual") + 1)
    intervalo = f"{letra}2:{letra}{_LINHA_LIMITE_VALIDACAO}"
    ws.add_validation(
        intervalo, ValidationConditionType.one_of_list, _SITUACAO_MANUAL_VALORES,
        strict=False, showCustomUi=True,
    )


_RETORNOU_CONSEGUIU_AGENDAR_VALORES = ["Sim", "Não"]


@retry_erro_transitorio_windows()
def configurar_validacao_retornou_conseguiu_agendar() -> None:
    """Configura os dropdowns de `Retornou?`/`Conseguiu Agendar?` em
    `Pendente de Ligação` — SETUP, mesma natureza de
    `configurar_validacao_situacao_manual`.

    Achado ao vivo 2026-08-15 (auditoria de validações): `docs/planilha_
    operacional.md` documenta essas 2 colunas como configuradas ao vivo
    desde 2026-08-07, mas a planilha real está sem validação nenhuma —
    nunca existiu uma função `configurar_*` versionada em código pra
    recriá-las, então o `limpar_validacoes_aba()` da sessão anterior (que
    limpa a faixa inteira antes de recriar só o que tem função de
    recriação) as apagou e elas nunca voltaram. `strict=False` de
    propósito — célula vazia é um 3º estado com significado próprio
    (`docs/planilha_operacional.md`), não pode ser bloqueada.
    """
    cabecalho = _CABECALHOS_OPERACIONAL["Pendente de Ligação"]
    ws = _worksheet(NOME_PLANILHA_OPERACIONAL, "Pendente de Ligação")
    _garantir_linhas_minimas(ws, _LINHA_LIMITE_VALIDACAO)
    for coluna in ("Retornou?", "Conseguiu Agendar?"):
        letra = _coluna_letra(cabecalho.index(coluna) + 1)
        intervalo = f"{letra}2:{letra}{_LINHA_LIMITE_VALIDACAO}"
        ws.add_validation(
            intervalo, ValidationConditionType.one_of_list,
            _RETORNOU_CONSEGUIU_AGENDAR_VALORES, strict=False, showCustomUi=True,
        )


_STATUS_PUMA_VALORES = ["aguardando_acao", "em_andamento", "concluido"]


@retry_erro_transitorio_windows()
def configurar_validacao_status_puma() -> None:
    """Configura o dropdown da coluna `Status` em `Encaminhar pra Puma` —
    SETUP, mesma natureza de `configurar_validacao_situacao_manual`.

    Gap conhecido desde 2026-08-14 (`docs/planilha_operacional.md`,
    "Correção de validação/cabeçalho"): nunca existiu função pra essa
    coluna. Valores internos exatos (`aguardando_acao`/`em_andamento`/
    `concluido`) — mesmo texto que `Status Sistema` já mostra em
    `Tratativas`, decisão de manter consistência em vez de traduzir.
    """
    cabecalho = _CABECALHOS_OPERACIONAL["Encaminhar pra Puma"]
    ws = _worksheet(NOME_PLANILHA_OPERACIONAL, "Encaminhar pra Puma")
    _garantir_linhas_minimas(ws, _LINHA_LIMITE_VALIDACAO)
    letra = _coluna_letra(cabecalho.index("Status") + 1)
    intervalo = f"{letra}2:{letra}{_LINHA_LIMITE_VALIDACAO}"
    ws.add_validation(
        intervalo, ValidationConditionType.one_of_list, _STATUS_PUMA_VALORES,
        strict=False, showCustomUi=True,
    )


_ABAS_CABECALHO_FORMATADO = [
    "Tratativas", "Pendente de Ligação", "Encaminhar pra Puma",
    "Alertas", "Análise de Divergência - Instalação", "Análise de Divergência - Remoção",
    "Análise de Divergência - Manutenção",
]
_COR_CABECALHO_FUNDO_SISTEMA = convert_hex_to_colors_dict("#0A1B5E")
_COR_CABECALHO_FUNDO_EQUIPE = convert_hex_to_colors_dict("#0046B0")
_COR_CABECALHO_TEXTO = convert_hex_to_colors_dict("#FFFFFF")

# Colunas que alguém digita/marca manualmente (nosso atendente, ou o time
# da Puma em "Encaminhar pra Puma") — o resto é sempre escrito do zero
# pelo sistema a cada ciclo (`reescrever_aba`). Fonte de verdade: os
# campos que cada etapa efetivamente LÊ de volta da aba antes de
# regravar (`_sincronizar_atendente_da_aba` pra Tratativas, `_resultado_
# ligacao_da_linha`/`_checkbox_sheets(linha.get("Finalizado"))` pra
# Pendente de Ligação, `_puma_concluido` pra Encaminhar pra Puma,
# `_resolver_acao_alerta`/`Data Agendada` pra Alertas) — não é uma lista
# arbitrária. "Análise de Divergência - Instalação" é 100% relatório
# mecânico (`_linha_divergencia_para_aba`, "sem estado de atendente"),
# por isso não tem nenhuma coluna de equipe.
_COLUNAS_EQUIPE_POR_ABA: dict[str, set[str]] = {
    "Tratativas": {
        "Discrepância revisada", "Atendimento", "Base", "Ponto de Ação",
        "Selecionado", "Telefone corrigido", "Situação Manual", "Data Agendada",
        "Técnico", "Observação Manual", "Finalizado",
    },
    "Pendente de Ligação": {
        "Atendimento", "Base", "Ponto de Ação",
        "Data Contato", "Retornou?", "Conseguiu Agendar?", "Observação", "Finalizado",
    },
    "Encaminhar pra Puma": {"Status", "Observação Puma"},
    "Alertas": {"Ação", "Data Agendada"},
    "Análise de Divergência - Instalação": set(),
    "Análise de Divergência - Remoção": set(),
    "Análise de Divergência - Manutenção": set(),
}


def _runs_por_cor(cabecalho: list[str], colunas_equipe: set[str]) -> list[tuple[int, int, bool]]:
    """Agrupa colunas CONSECUTIVAS da mesma origem (equipe/sistema) num
    único range — bem menos chamadas de API do que 1 por coluna. Devolve
    `(indice_inicio_0based, indice_fim_exclusivo, eh_equipe)`."""
    runs = []
    inicio = 0
    eh_equipe_atual = cabecalho[0] in colunas_equipe
    for i in range(1, len(cabecalho) + 1):
        eh_equipe = i < len(cabecalho) and cabecalho[i] in colunas_equipe
        if i == len(cabecalho) or eh_equipe != eh_equipe_atual:
            runs.append((inicio, i, eh_equipe_atual))
            inicio = i
            eh_equipe_atual = eh_equipe
    return runs


@retry_erro_transitorio_windows()
def configurar_formatacao_cabecalho() -> None:
    """Uniformiza a cor do cabeçalho (linha 1) das 5 abas operacionais —
    SETUP ÚNICO, mesma natureza de `configurar_formatacao_condicional`/
    `formatar_colunas_identificador_texto` (roda uma vez, não faz parte
    do pipeline recorrente).

    Achado ao vivo 2026-08-13 (print do usuário): os cabeçalhos de
    `Tratativas`/`Pendente de Ligação` tinham cor inconsistente (algumas
    colunas com fundo colorido, a maioria sem cor nenhuma) — resquício de
    formatação manual antiga, nunca um padrão definido em código.

    Achado ao vivo 2026-08-14: `Alertas` usava um azul diferente do
    padrão (resquício manual) e `Análise de Divergência - Instalação`
    não tinha formatação nenhuma — as 2 entraram na lista.

    2026-08-14: 2 cores por origem da coluna, não mais 1 cor por aba —
    fundo azul-marinho (sistema) + accent da marca (equipe, `#0046B0`,
    ver `_COLUNAS_EQUIPE_POR_ABA`), texto branco em negrito nos dois
    casos. Par validado com o skill `dataviz` (CVD ΔE 17.4, visão normal
    ΔE 17.8 — bem acima do piso de 8/15).
    """
    for aba in _ABAS_CABECALHO_FORMATADO:
        cabecalho = _CABECALHOS_OPERACIONAL[aba]
        colunas_equipe = _COLUNAS_EQUIPE_POR_ABA.get(aba, set())
        ws = _worksheet(NOME_PLANILHA_OPERACIONAL, aba)
        for inicio, fim, eh_equipe in _runs_por_cor(cabecalho, colunas_equipe):
            letra_inicio = _coluna_letra(inicio + 1)
            letra_fim = _coluna_letra(fim)
            cor = _COR_CABECALHO_FUNDO_EQUIPE if eh_equipe else _COR_CABECALHO_FUNDO_SISTEMA
            ws.format(f"{letra_inicio}1:{letra_fim}1", {
                "backgroundColor": cor,
                "textFormat": {"bold": True, "foregroundColor": _COR_CABECALHO_TEXTO},
            })


_ACAO_ALERTA_VALORES = ["Confirma conclusão", "Foi engano, ignorar"]


@retry_erro_transitorio_windows()
def configurar_validacao_alertas() -> None:
    """Configura o dropdown da coluna "Ação" na aba "Alertas" — SETUP,
    mesma natureza de `configurar_validacao_atendimento`/
    `configurar_formatacao_condicional` (roda uma vez, não faz parte do
    pipeline recorrente). Mesma faixa/limite de linhas
    (`_LINHA_LIMITE_VALIDACAO`) já usado nos outros dropdowns dessa
    planilha.
    """
    cabecalho = _CABECALHOS_OPERACIONAL["Alertas"]
    ws = _worksheet(NOME_PLANILHA_OPERACIONAL, "Alertas")
    _garantir_linhas_minimas(ws, _LINHA_LIMITE_VALIDACAO)
    letra = _coluna_letra(cabecalho.index("Ação") + 1)
    intervalo = f"{letra}2:{letra}{_LINHA_LIMITE_VALIDACAO}"
    ws.add_validation(
        intervalo, ValidationConditionType.one_of_list, _ACAO_ALERTA_VALORES,
        strict=False, showCustomUi=True,
    )


# Rastreadores Ativos já tem 12241 linhas de dado real hoje — folga
# generosa pro crescimento. Só formatação (numberFormat), sem o bug de
# "linha fantasma" de checkbox/dropdown (não cria valor nenhum, mesmo
# raciocínio de `_LINHA_LIMITE_FORMATACAO_CONDICIONAL`).
_LINHA_LIMITE_TEXTO_ADMINISTRADOR = 50_000

_COLUNAS_TEXTO_INCIDENTES = ["ID", "Imei", "Iccid", "Linha"]

# Mesmas posições (1-indexed) que `core.constants.COL_RASTREADORES_*` já usa
# pra ler/escrever 'Rastreadores Ativos' por posição (aba sem cabeçalho
# único por nome — ver docstring do módulo).
_COLUNAS_TEXTO_RASTREADORES = [
    COL_RASTREADORES_ID,
    COL_RASTREADORES_IMEI,
    COL_RASTREADORES_NUMERO,
    COL_RASTREADORES_CELULAR,
    COL_RASTREADORES_ICCID,
]


@retry_erro_transitorio_windows()
def formatar_colunas_identificador_texto() -> None:
    """Formata como "Plain text" as colunas que guardam identificador "cara
    de número" — SETUP ÚNICO, mesma natureza de `configurar_formatacao_
    condicional`/`configurar_validacao_atendimento` (roda 1 vez, não faz
    parte do pipeline recorrente).

    **Achado ao vivo (2026-08-12, testado numa aba descartável antes de
    tocar produção)**: o Google Sheets, ao receber uma string "cara de
    número" com muitos dígitos via `value_input_option=USER_ENTERED` (o
    que `carregar_administrador`/`reescrever_aba` já usam), pode converter
    a célula pra tipo NÚMERO e exibi-la em notação científica
    (`6,73138E+15`) — mesmo o valor interno ficando intacto (confirmado
    lendo com `value_render_option=UNFORMATTED_VALUE`: nenhuma perda de
    dígito em nenhum teste, até 30 dígitos). O problema real não é perda
    de precisão na escrita — é (1) o operador não conseguir ler o
    identificador de verdade na tela, e (2) qualquer leitura futura via
    `ler_aba()` (que usa o texto FORMATADO, não o bruto) devolver essa
    string cortada como se fosse o valor real. Formatar a coluna como
    texto ANTES de escrever evita a conversão de tipo por completo, nos
    dois lados — confirmado 100% dos casos testados (13 a 30 dígitos).

    **Só formatar depois que o valor já existe como número NÃO corrige**
    (confirmado ao vivo) — a célula continua tipada como número
    internamente, só a máscara muda, e a notação científica nem some.
    Depois de rodar esta função contra produção pela primeira vez, é
    preciso reescrever os valores (`carregar_administrador` de novo pra
    'Incidentes'/'Rastreadores Ativos') pra que o dado já gravado errado
    seja corrigido — só formatar a planilha não basta.

    `ws.clear()` preserva formatação de coluna (confirmado ao vivo) — por
    isso este setup não precisa ser refeito a cada ciclo do orchestrator,
    mesmo `carregar_administrador`/`reescrever_aba` chamando `clear()`
    antes de cada escrita.

    Colunas cobertas (identificador puro, nunca usado em conta aritmética):
    - Administrador/Incidentes: `ID`, `Imei`, `Iccid`, `Linha`.
    - Administrador/Rastreadores Ativos (por posição): `ID`, `Imei`,
      `Número` (linha do chip), `Celular`, `ICCID`.
    - Operacional (`Tratativas`/`Pendente de Ligação`/`Encaminhar pra
      Puma`/`Alertas`): `Telefone` — nenhuma tem dado real ainda (só a
      linha placeholder), preventivo pra quando a Fase 4 alimentar de
      verdade.
    - `Instalação-Remoção` fica de fora de propósito: é 100% preenchida
      por humano, este módulo nunca escreve lá.

    Redimensiona cada aba antes de formatar (mesmo motivo/padrão de
    `configurar_formatacao_condicional`: a API não expande a aba sozinha
    pra cobrir um range maior que o `row_count` atual, confirmado ao vivo
    numa aba descartável) — garante folga pra crescimento futuro sem
    precisar rodar esta função de novo a cada carga.
    """
    sh_admin = _spreadsheet(NOME_PLANILHA_ADMINISTRADOR)

    ws_incidentes = sh_admin.worksheet("Incidentes")
    if ws_incidentes.row_count < _LINHA_LIMITE_TEXTO_ADMINISTRADOR:
        ws_incidentes.resize(rows=_LINHA_LIMITE_TEXTO_ADMINISTRADOR)
    for coluna in _COLUNAS_TEXTO_INCIDENTES:
        letra = _coluna_letra(CABECALHO_INCIDENTES.index(coluna) + 1)
        ws_incidentes.format(
            f"{letra}2:{letra}{_LINHA_LIMITE_TEXTO_ADMINISTRADOR}",
            {"numberFormat": {"type": "TEXT"}},
        )

    ws_rastreadores = sh_admin.worksheet("Rastreadores Ativos")
    if ws_rastreadores.row_count < _LINHA_LIMITE_TEXTO_ADMINISTRADOR:
        ws_rastreadores.resize(rows=_LINHA_LIMITE_TEXTO_ADMINISTRADOR)
    for posicao in _COLUNAS_TEXTO_RASTREADORES:
        letra = _coluna_letra(posicao)
        ws_rastreadores.format(
            f"{letra}2:{letra}{_LINHA_LIMITE_TEXTO_ADMINISTRADOR}",
            {"numberFormat": {"type": "TEXT"}},
        )

    sh_operacional = _spreadsheet(NOME_PLANILHA_OPERACIONAL)
    for aba, cabecalho in _CABECALHOS_OPERACIONAL.items():
        if "Telefone" not in cabecalho:
            continue
        ws = sh_operacional.worksheet(aba)
        if ws.row_count < _LINHA_LIMITE_FORMATACAO_CONDICIONAL:
            ws.resize(rows=_LINHA_LIMITE_FORMATACAO_CONDICIONAL)
        letra = _coluna_letra(cabecalho.index("Telefone") + 1)
        ws.format(
            f"{letra}2:{letra}{_LINHA_LIMITE_FORMATACAO_CONDICIONAL}",
            {"numberFormat": {"type": "TEXT"}},
        )


@retry_erro_transitorio_windows()
def carregar_administrador(aba: str, linhas: list[list]) -> None:
    """Limpa e reescreve 'Incidentes' ou 'Rastreadores Ativos' na planilha
    Administrador a partir dos dados já baixados do Track N' Me (ver
    `integrations.tracknme_bot.baixar_relatorios` + `ler_linhas_xlsx`).

    Recebe `linhas` posicional (`list[list]`, sem cabeçalho) em vez de
    `list[dict]` — ao contrário de `reescrever_aba` — porque 'Rastreadores
    Ativos' tem "Modelo"/"Marca" duplicados no cabeçalho, o que inviabiliza
    mapear por nome de coluna (mesmo motivo que já obriga `ler_aba` a usar
    posição pra essa aba).

    'Instalação-Remoção' fica de fora de propósito: continua 100% humana.
    """
    cabecalho = _CABECALHOS_ADMINISTRADOR.get(aba)
    if aba not in ABAS_ADMINISTRADOR_ESCRITAS or cabecalho is None:
        raise ValueError(
            f"Aba {aba!r} não pode ser escrita pelo sistema na planilha "
            f"{NOME_PLANILHA_ADMINISTRADOR!r}"
        )

    ws = _worksheet(NOME_PLANILHA_ADMINISTRADOR, aba)
    ws.clear()
    ws.update(values=[cabecalho] + linhas, value_input_option=ValueInputOption.user_entered)
