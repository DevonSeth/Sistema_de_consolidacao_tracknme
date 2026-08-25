# Regras de negócio — Sistema de Pendências Puma

Documento vivo, atualizado conforme as regras vão sendo fechadas com o
usuário. Cobre o motor de classificação de incidentes de manutenção
(`core/motor_regras.py`, Passo 7 — **implementado**, ver seção final). O
modelo de regras de Instalação/Remoção é uma fase futura separada e ainda
não está aqui.

## Fontes de dados

- **Incidentes** (aba, 15 colunas): `ID, Marca, Placa, Modelo do
  equipamento, Imei, Iccid, Linha, Operadora, Data do incidente, Evento,
  Data do retorno, Operador, Status, Situação, Cliente`.
- **Rastreadores Ativos** (aba, 28 colunas, só equipamento ATIVO — lida
  por posição, ver `COL_RASTREADORES_*` em `google_sheets_client.py`):
  campos relevantes — `Imei` (3), `Data de Comunicação` (6), `Data de
  Posição` (7), `Cliente` (8), `Placa` (10), `Status` (16), `Chassi` (27).
  **"Comunicou ou não" usa exclusivamente `Data de Posição`** — um
  equipamento pode comunicar (col. 6) sem nunca dar posição (col. 7).
- **Instalação-Remoção** (aba, 22 colunas): inclui `Chassi`, `Serviço`
  (texto livre tipo "Instalação"/"Retirada", normalizado pelo
  administrador antes de chegar no sistema), `Situação` (status de
  pagamento/associado via SGA — não indica se o serviço foi executado).
- **system_parameters** (Supabase): `tempo_limiar_inatividade_horas = 48`,
  `normalizar_placas`, `placas_genericas`, `horario_corte_disparo =
  '17:30'`, `fuso_horario = 'America/Recife'`.
- **Data de corte 30/09/2025**: qualquer `Data de Posição` anterior a essa
  data é tratada como "nunca comunicou" (equivalente a campo vazio).
  Constante fixa em `core/constants.py` (data histórica de migração, não
  deve mudar de novo — não é `system_parameter`).

## Grupo 1 — abrir incidente automaticamente (scan independente)

Não é uma regra da cascata abaixo — é um scan separado sobre "Rastreadores
Ativos": equipamento ativo **sem** incidente aberto vinculado, com `Data
de Posição` há mais de 48h (ou anterior ao corte de 30/09/2025) → **abre
incidente automaticamente**. Ação: **Sistema**.

## Cascata sobre incidentes já existentes

Ordem de avaliação: prioridade menor primeiro, primeira que bater "ganha".
Cada incidente recebe **uma única classificação** (a primeira regra que
bater) com sua própria urgência — não há combinação/soma de urgência entre
regras (ver decisão sobre `REGRA_ALERTA_CLIENTE` abaixo).

### Passo 0 — dedup silencioso (roda antes de tudo)

| codigo_regra | Prioridade | O que é | Ação |
|---|---|---|---|
| `REGRA_4` | 40 | Incidente duplicado — mantém o mais recente | Sistema, silencioso (não vira linha) |
| `REGRA_4_TIMESTAMP` | 40 | Duplicado com mesmo timestamp exato | Sistema, silencioso (não vira linha) |

### Cascata principal

| codigo_regra | Prioridade | Urgência (1-5) | O que é | Ação | Responsável |
|---|---|---|---|---|---|
| `REGRA_SEM_PLACA` | 5 | **1** | Placa inválida/placeholder no incidente | Só reporta, sem correção automática | — |
| `REGRA_1` | 10 | **5** | Sem comunicar desde antes do incidente, ou nunca comunicou | Esteira de disparo (WhatsApp) → ligação → encaminhar Puma | Sistema dispara, humano decide/liga |
| `REGRA_2` | 20 | **1** | Voltou a comunicar dentro de 48h | Conclui incidente automaticamente | Sistema |
| `REGRA_3` | 30 | **3** | Comunicou após o incidente, mas recaiu (+48h de novo) | Reabre o ciclo do zero (mesma esteira de REGRA_1) | Sistema dispara, humano na ligação |
| `REGRA_5_1` | 50 | **3** | IMEI diverge do cadastro — troca confirmada, equipamento novo comunicando bem | Ajustar vínculo no cadastro | Humano |
| `REGRA_5_1_SEM_COMUNICACAO` | 50 | **5** | IMEI diverge E o equipamento cadastrado nunca comunicou | Investigar (ligar + conferir cadastro) | Humano |
| `REGRA_5_1_RECAIU` **(nova)** | 50 | **4** | Troca confirmada, mas o equipamento novo TAMBÉM já recaiu (+48h sem comunicar de novo) | Vai pra esteira de disparo como REGRA_1/REGRA_3 | Sistema dispara, humano na ligação |
| `REGRA_5_2` | 55 | **2** | IMEI existe no cadastro mas sem vínculo de placa | Ajustar vínculo no cadastro | Humano |
| `REGRA_5_3` | 60 | **5** | Nem placa nem IMEI do incidente batem com o cadastro atual | Verifica se o chassi está na lista de retirada (Instalação-Remoção, `Serviço` = retirada/remoção) — se sim, **conclui o incidente automaticamente**; se não, investigação manual | Sistema (caso retirada) / Humano (caso contrário) |
| `REGRA_5_4` | 65 | **3** | IMEI do incidente está ativo para OUTRA placa no cadastro | Ajustar vínculo de Placa/IMEI no cadastro | Humano |
| `REGRA_6_1` **(nova)** | 70 | **2** | Iccid/Linha do incidente diverge da linha ativa no cadastro (nova série 6_x, separada dos 5_x que são só IMEI) | Verificar com a operadora e ajustar | Humano (raro de acontecer) |

### Precedência REGRA_1/2/3 vs. família REGRA_5_1 (decisão fechada na implementação)

Um incidente pode satisfazer ao mesmo tempo a condição de `REGRA_1`
(equipamento sem comunicar) e a de `REGRA_5_1_SEM_COMUNICACAO` (IMEI
diverge e o equipamento cadastrado nunca comunicou) — ex: equipamento da
placa nunca teve `Data de Posição`, e o IMEI do incidente também diverge
do cadastro. **A família REGRA_5_1 vence** — é checada antes da cascata
genérica REGRA_1/2/3, mesmo a prioridade numérica de `REGRA_1` (10) sendo
menor que a de `REGRA_5_1` (50): divergência de IMEI é um sinal mais
específico (possível troca de equipamento) que não deve ficar mascarado
pela regra genérica de comunicação.

Exceção: se o IMEI diverge mas a posição do cadastro é antiga (não
posterior à `Data do incidente`) e não vazia, a divergência não é
"relevante" o suficiente pra ativar a família REGRA_5_1 — o incidente cai
de volta na cascata genérica (`REGRA_1`/`2`/`3`) normalmente. Implementado
em `core.motor_regras._imei_diverge_com_troca_relevante`.

### `REGRA_ALERTA_CLIENTE` — resolução independente (não é mais aditiva)

Como a ação virou 100% automática (fecha o incidente antigo e, se
aplicável, abre um novo já com o nome do cliente atual), deixou de ser uma
regra "somada" a outra na mesma linha (revisão da decisão 17 original).
Cada incidente recebe só essa classificação OU outra da cascata, nunca as
duas combinadas — por isso não existe fórmula de combinação de urgência.

| codigo_regra | Prioridade | Urgência (1-5) | O que é | Ação | Responsável |
|---|---|---|---|---|---|
| `REGRA_ALERTA_CLIENTE` | 200 | **2** | Nome do cliente do incidente diverge do cadastro (ex: troca de titularidade) | Fecha o incidente antigo; se o equipamento estiver +48h sem comunicar, abre um novo incidente na mesma placa (já vem com o nome do cliente atual) | Sistema |

### SGA — terceira passada, depois de aberto o Grupo 1 (fechada em 2026-08-07)

Diferente das regras acima (que rodam numa cascata só, sem SGA), estas
duas rodam numa **segunda passada separada**, em `core.motor_regras.
aplicar_situacoes_sga`, depois que o SGA é consultado ao vivo (Fase D do
orchestrator — ver `_handoff/HANDOFF.md`). Têm prioridade sobre **qualquer**
classificação da cascata acima, mesmo uma já resolvida automaticamente
(ex: `REGRA_2`).

| codigo_regra | Urgência (1-5) | O que é | Ação | Responsável |
|---|---|---|---|---|
| `REGRA_SGA_INATIVO` | **1** | SGA retornou qualquer status diferente de `ATIVO` | Fecha o incidente automaticamente | Sistema |
| `REGRA_SGA_NAO_ENCONTRADO` | **2** | SGA não encontrou o chassi cadastrado | Verificar cadastro do veículo/associado | Humano |

**2026-08-25**: `REGRA_SGA_INATIVO` passou a também alimentar a aba
"Análise de Divergência - Manutenção" (código `REGRA_MANUTENCAO_
DIVERGENCIA_SGA`, só identifica a linha de divergência — não muda o
fechamento automático em si). Motivação: se o SGA diz que o associado
não está mais ativo mas o equipamento segue comunicando, ele
"teoricamente deveria estar fora da lista de Rastreadores Ativos" — dá
visibilidade de equipamento que devia ter sido removido fisicamente e
não foi. `REGRA_SGA_NAO_ENCONTRADO` não entra nessa regra nova.

**Porquê** (ensinado pelo usuário): só veículo ativo na cooperativa
precisa de tratamento de manutenção — se ele reativar e voltar a
precisar de contato, o sistema recaptura isso sozinho nos critérios que
já existem (o SGA é consultado de novo a cada execução, nunca fica
"esquecido" fechado). `NÃO ENCONTRADO` não fecha sozinho porque pode ser
erro de cadastro, não ausência real do veículo — por isso vira tratativa
humana em vez de fechamento automático.

Textos finais (mesmo padrão enxuto das demais regras) — as 2 primeiras
linhas **já rodadas e confirmadas em produção** (2026-08-07, ver SQL em
`_handoff/HANDOFF.md`); a 3ª (`REGRA_MANUTENCAO_DIVERGENCIA_SGA`,
2026-08-25) tem código+testes prontos, mas **ainda não foi inserida em
`rule_templates`** nem a worksheet real foi criada — ação separada, com
aprovação explícita:

| codigo_regra | observação (`template_observacao`) | ação (`template_acao`) |
|---|---|---|
| `REGRA_SGA_INATIVO` | SGA retornou '{status_sga}' — associado não está mais ativo. | Nenhuma ação necessária — encerrado por status do SGA. |
| `REGRA_SGA_NAO_ENCONTRADO` | SGA não encontrou esse chassi. | Verificar cadastro do veículo/associado. |
| `REGRA_MANUTENCAO_DIVERGENCIA_SGA` | Status SGA {status_sga}, mas o equipamento segue comunicando | Cancelar contrato manualmente |

## Regra nova avaliada e rejeitada

- **Reincidência recorrente** (mesma placa/equipamento com N incidentes em
  M dias) — rejeitada. Veículos passam por períodos parados normalmente
  na operação, não é sinal relevante.

## `REGRA_5_3` — nota sobre "lista de retirada"

Não existe hoje nenhum campo que confirme que uma retirada foi de fato
**executada** fisicamente — a presença da linha em "Instalação-Remoção"
com `Serviço` = retirada/remoção significa "precisa ser feito", não "foi
feito" (não existe campo equivalente a `Data de Instalação`/`Instalado
por`, que já existe pra confirmar instalação em "Rastreadores Ativos").

Decisão prática: a simples presença da linha já basta pra **fechar o
incidente de manutenção** — não é uma confirmação de execução física, é só
uma decisão de não manter "manutenção" e "retirada" duplicados em
acompanhamento pro mesmo veículo.

**Pendência real (adiada pra fase de Instalação/Remoção)**: criar um jeito
da equipe sinalizar que a retirada foi executada de verdade — proposta em
aberto: campo novo `Data de Retirada`/`Retirado por` em
Instalação-Remoção, espelhando o padrão que já existe pra instalação. Pro
lado da instalação, a confirmação deve cruzar a presença do chassi em
"Rastreadores Ativos" (equipamento ativo pra aquele chassi) além dos
campos de data já existentes.

## Textos finais — inserir em `rule_templates` (produção)

Convenção de texto (vale pra essas e qualquer regra futura): **enxuto**,
só a informação necessária, no mesmo estilo direto das 12 regras
originais (uma frase curta pra observação, uma instrução curta pra ação).

| codigo_regra | prioridade | observação (`template_observacao`) | ação (`template_acao`) |
|---|---|---|---|
| `REGRA_5_1_RECAIU` | 50 | IMEI diverge do cadastro ({imei_cadastro}) — equipamento novo recaiu, sem comunicar de novo há mais de {tempo_limiar_inatividade_horas}h. | Seguir esteira de disparo (WhatsApp → ligação → Puma). |
| `REGRA_6_1` | 70 | Iccid/Linha diverge do cadastro para o IMEI {imei_incidente}. | Verificar com a operadora e corrigir o cadastro. |

**Status**: RESOLVIDO — script SQL rodado pelo usuário no SQL Editor do
Supabase (INSERT das 2 regras + coluna `nivel_urgencia` + `codigo_regra` em
`tratativas`). Confirmado ao vivo por leitura direta em produção
(2026-08-07): os 2 códigos existem em `rule_templates` com
`nivel_urgencia` preenchido, e `tratativas.codigo_regra` existe. `core.
motor_regras` já usa isso normalmente.

## Coluna "Nível de Urgência" no relatório final

Nome da coluna: **"Nível de Urgência"** (consistente com o estilo já usado
em `Ação Sugerida`/`Observação do Sistema`). Valor numérico 1-5, **sem
reordenar as linhas**. Formatação condicional por cor: 1-2 verde, 3
amarelo, 4 laranja, 5 vermelho — **implementada e confirmada ao vivo em
2026-08-07** (`integrations/google_sheets_client.configurar_
formatacao_condicional()`, setup único via request bruto da API do
Sheets, já aplicado nas 3 abas operacionais reais).

Decisão fechada: a coluna aparece nas **3 abas operacionais**
(`Tratativas`, `Pendente de Ligação`, `Encaminhar pra Puma`), logo depois
de `Observação do Sistema` — já adicionada em `CABECALHO_TRATATIVAS`/
`CABECALHO_PENDENTE_LIGACAO`/`CABECALHO_ENCAMINHAR_PUMA` em
`integrations/google_sheets_client.py`, e populada de verdade pelo
`orchestrator/pipeline.py` a cada execução.

## Rastreabilidade de `tratativas` — decisão fechada

`tratativas` vai ganhar uma coluna `codigo_regra` (texto) — guarda qual
regra gerou a linha (decisão B, entre denormalizar `nivel_urgencia` direto
ou guardar `codigo_regra` e derivar a urgência via consulta em
`rule_templates` no momento de montar o relatório). Resolve de quebra uma
lacuna que não tinha sido percebida antes: hoje não existe nenhum jeito de
saber depois qual regra classificou uma tratativa — só o texto já
renderizado (`acao_sugerida`/`observacao_sistema`). `core.motor_regras` já
produz esse campo (`_montar_linha_resultado` já inclui `codigo_regra` na
saída); a coluna já existe em produção (mesmo script SQL acima) e
`orchestrator/pipeline.py::etapa_publicar_fila_operacional` já grava esse
valor ao persistir a tratativa.

## RESOLVIDO — como "Incidentes"/"Rastreadores Ativos" são alimentadas

Vêm do próprio Track N' Me: `integrations/tracknme_bot.py::baixar_
relatorios()` baixa os xlsx, e `integrations/google_sheets_client.
py::carregar_administrador()` limpa e reescreve as 2 abas a partir deles
— chamado por `orchestrator/pipeline.py::etapa_baixar_relatorios`/
`etapa_ler_planilha_gestor`. Validado ao vivo em produção (2026-08-06/07,
1698 linhas de Incidentes e 12241 de Rastreadores Ativos escritas sem
chunking). "Instalação-Remoção" continua 100% humana, isso não muda.

## Passo 7 — implementado

`core/motor_regras.py` está completo: `classificar_incidentes()` e
`buscar_chassi_por_placa()`, com uma função pura testável por regra (todas
as 14 regras + Grupo 1 + dedup + `REGRA_ALERTA_CLIENTE`). Assinatura final
(ganhou um parâmetro em relação ao stub original, pra suportar a checagem
de "lista de retirada" do `REGRA_5_3`):

```python
def classificar_incidentes(
    incidentes: list[dict],
    equipamentos: list[dict],
    instalacao_remocao: list[dict],
    parametros: dict,
    templates: dict,
    agora: datetime | None = None,  # opcional, default datetime.now() — só pra teste determinístico
) -> dict:
```

`tests/test_motor_regras.py` cobre cada função isoladamente (dado
sintético) e tem um teste de integração (`TestClassificarIncidentesIntegracao`)
rodando o fluxo completo — dedup, `REGRA_ALERTA_CLIENTE` (com e sem
reabertura), a cascata principal e o scan do Grupo 1 juntos, com pelo
menos um incidente de cada grupo/regra.

Refactor de arquitetura feito junto (necessário — `core/` não pode
importar de `integrations/`): as constantes `COL_RASTREADORES_*` e a nova
`DATA_CORTE_POSICAO_VALIDA` foram movidas para `core/constants.py`;
`integrations/google_sheets_client.py` agora importa de lá.

**Pendente antes de integrar ao `orchestrator/pipeline.py`** (mutação em
produção, não é trabalho de código):
- Inserir `REGRA_5_1_RECAIU` e `REGRA_6_1` em `rule_templates` (textos já
  fechados acima).
- Adicionar a coluna `nivel_urgencia` em `rule_templates` e preencher os
  12 valores já fechados (ver `projeto_classificacao_urgencia` na memória
  persistente) — `buscar_rule_templates()` também precisa passar a
  selecionar essa coluna.

## Fora de escopo por agora (adiado pelo usuário)

- Modelo de regras de Instalação/Remoção (fase separada, depois do motor
  de manutenção).
- Regras de disparo/supressão de contato (ex: veículo na oficina, sem
  movimentação — não faz sentido tentar contato) — relacionado a
  `core/escalonamento.py` (`elegivel_para_disparo`).
