# Planilha Operacional — estrutura das abas

Documento vivo. Cobre a estrutura das 3 abas da planilha "Operacional -
Pendencias - Puma" (`Tratativas`, `Pendente de Ligação`, `Encaminhar pra
Puma`) — separado de `docs/regras_negocio.md`/
`docs/regras_negocio_instalacao_remocao.md` porque essa estrutura vale
pras 3 origens (`instalacao`/`remocao`/`manutencao`), não é regra de
classificação de uma origem só.

**Nada neste documento foi implementado ainda** — é só a proposta,
levantada em 2026-08-06, discutida mas não fechada em código.

## Estrutura atual em produção (`integrations/google_sheets_client.py`)

```
CABECALHO_TRATATIVAS = [
    "ID (hash)", "Tipo Serviço", "Identificador", "Chassi", "Cliente",
    "Telefone", "Cidade", "Bairro", "Data Contrato / Data Incidente", "SGA",
    "Situação Original", "Ação Sugerida", "Observação do Sistema",
    "Nível de Urgência",
    "Selecionado", "Status Contato", "Status Sistema", "Tentativa 1",
    "Tentativa 2", "Tentativa 3", "Resposta", "Data Resposta",
    "Situação Manual", "Data Agendada", "Observação Manual",
]

CABECALHO_PENDENTE_LIGACAO = [
    "ID (hash)", "Tipo Serviço", "Identificador", "Chassi", "Cliente",
    "Telefone", "Cidade", "Bairro", "Data Contrato / Data Incidente", "SGA",
    "Situação Original", "Ação Sugerida", "Observação do Sistema",
    "Nível de Urgência",
    "Histórico de Tentativas (msg)", "Data Contato", "Retornou?",
    "Conseguiu Agendar?", "Observação",
]

CABECALHO_ENCAMINHAR_PUMA = [
    "ID (hash)", "Tipo Serviço", "Identificador", "Chassi", "Cliente",
    "Telefone", "Cidade", "Bairro", "Data Contrato / Data Incidente",
    "Ação Sugerida", "Observação do Sistema", "Nível de Urgência",
    "Data Encaminhamento", "Motivo", "Status", "Observação Puma",
]
```

## Estrutura final de Tratativas — decidida (2026-08-06)

As 6 perguntas abaixo foram todas respondidas pelo usuário nesta sessão.
Cabeçalho final (substitui a proposta original do início desta seção):

```
ID (hash)                      -- auto-explicativo
Tipo Serviço                   -- Instalação, Manutenção, Remoção
Identificador                  -- Placa
Chassi
Cliente
Telefone
Cidade
Bairro
Data Contrato / Data Incidente
SGA                            -- situação de pagamento/associado (live, via SGA)
Ação Sugerida
Observação do Sistema
Nível de Urgência              -- MANTIDA (decisão de 2026-08-05 não mudou)
Selecionado                    -- checkbox, atendente marca pra disparar mensagem
Status Contato
Status Sistema
Tentativa 1                    -- data do 1º disparo, preenchido pelo sistema
Tentativa 2                    -- data do 2º disparo, preenchido pelo sistema
Tentativa 3                    -- data do 3º contato, preenchido pelo sistema
Resposta                       -- dropdown sim/não, preenchido pelo sistema via webhook
Data Resposta
Situação Manual                -- Agendado, Cancelado, Solicitação operacional
                                   (SEM "Finalizado" — ver decisão 4 abaixo)
Data Agendada                  -- resgatado do template — detalhar quando integrar Newmo
Técnico          [NOVO]        -- dropdown NATIVO do Google Sheets (data validation),
                                   lista de nomes mantida direto na planilha, fora do
                                   Supabase/system_parameters
Observação Manual
Finalizado       [NOVO]        -- checkbox, substitui "Retirada confirmada". Fonte ÚNICA
                                   de verdade pra "concluído" (independe do valor de
                                   Situação Manual). Humano marca (ex: Remoção, que o
                                   sistema não enxerga) OU o próprio bot marca quando
                                   resolve sozinho. Quando marcado, a linha sai de
                                   Tratativas no próximo ciclo (o sistema exclui da
                                   regravação — ver nota de implementação abaixo)
```

`Situação Original` **removida** das 3 abas (decisão 1) — nunca foi
ligada ao motor de regras (confirmado lendo `core.motor_regras.
_montar_linha_resultado`, que não produz essa chave), e o papel que
faria (situação vinda da fonte) já é coberto por `SGA`, que traz a
situação mais atualizada via consulta live ao SGA.

## Decisões (perguntas respondidas em 2026-08-06)

1. **`Situação Original`**: removida das 3 abas. Nunca foi implementada
   de fato (nenhuma linha de código a preenche); `SGA` já cobre o papel
   de "situação vinda da fonte", e mantendo os dois lado a lado criaria
   a ambiguidade que se queria evitar (razão do próprio usuário).
2. **`Nível de Urgência`**: mantém — decisão de 2026-08-05 ("aparece nas
   3 abas operacionais") não muda, só não tinha sido mencionada na
   descrição da proposta.
3. **`Finalizado` (checkbox) substitui `Retirada confirmada`** — não
   coexistem. Vale pra qualquer `Tipo Serviço`.
4. **Ambiguidade de nome resolvida removendo, não renomeando**: o valor
   `"Finalizado"` sai da lista de `Situação Manual` (fica só `Agendado`,
   `Cancelado`, `Solicitação operacional`). O checkbox `Finalizado` é a
   única fonte de verdade sobre conclusão — independe do que estiver
   selecionado em `Situação Manual`.
5. **`Técnico`**: dropdown nativo do Google Sheets (data validation),
   não vai pro Supabase — o atendente/admin edita a lista de nomes
   direto na planilha, sem precisar de deploy/código.
6. **Não propaga pras outras 2 abas.** `Técnico` é irrelevante em
   `Pendente de Ligação`/`Encaminhar pra Puma`. `Finalizado` também não
   precisa propagar — ele não é só uma flag visual, é um filtro de
   saída: uma linha marcada `Finalizado` sai da lista de `Tratativas` no
   próximo ciclo (ver nota de implementação abaixo), então não tem
   sentido replicar esse controle nas outras abas.

**Nota de implementação (pra quando `orchestrator/pipeline.py` for
escrito, ainda não vale agora)**: ao regravar `Tratativas`, o pipeline
precisa excluir da regravação qualquer linha já marcada `Finalizado` na
versão anterior da aba (ler antes de sobrescrever, filtrar). Ainda não
implementado — só decisão registrada.

**Schema do Supabase já ajustado (2026-08-06, confirmado ao vivo)**:
`tratativas.tecnico` (text) criada — sem ela, o valor de `Técnico`
escolhido pelo atendente se perderia a cada `reescrever_aba`.
`tratativas.situacao_original` removida — nunca foi preenchida por
nenhum código (coluna órfã desde sempre) e o papel que faria já é
coberto por `SGA` (situação vinda do SGA, ao vivo). `CABECALHO_TRATATIVAS`
em `integrations/google_sheets_client.py` ainda não foi atualizado —
fica pro lote de implementação (junto com `Técnico`/`Finalizado` e o
resto de Instalação/Remoção), sem risco: a planilha real de hoje só tem
linha de exemplo, e `reescrever_aba()` reescreve a aba inteira a partir
da constante de cabeçalho quando o pipeline rodar de verdade.

## `Finalizado` em "Pendente de Ligação" — adicionada 2026-08-07 (Fase F.4)

`CABECALHO_PENDENTE_LIGACAO` ganhou uma coluna `Finalizado` (checkbox,
mesmo princípio da de `Tratativas`) — 18 → 19 colunas. Resolve 2 casos
que a leitura automática de `Retornou?`/`Conseguiu Agendar?` não cobre:

- **Vence qualquer combinação dos outros dois campos** — cobre desfecho
  fora do padrão (ex: associado resolveu por outro canal, desistiu do
  serviço). Marca a tratativa `finalizado` direto, sem passar por
  `registrar_ligacao` (não é uma ligação de verdade).
- **É o único jeito de tirar a linha da aba depois de `Conseguiu
  Agendar? = Sim`** — esse valor só indica um agendamento futuro
  (ex: técnico vai visitar em tal data), não uma conclusão. A linha
  fica na aba como lembrete até o atendente confirmar que o agendado
  realmente aconteceu.

Regra completa de leitura (`Retornou?`/`Conseguiu Agendar?` são dropdown
de texto Sim/Não, não checkbox): `Finalizado` marcado vence sempre;
senão, `Retornou? = Não` é o único caminho automático pra "Encaminhar
pra Puma"; `Retornou? = Sim` + `Conseguiu Agendar? = Sim` registra a
ligação mas não sai da aba; qualquer outra combinação (`Retornou?`
vazio, ou `Sim` + `Não`/vazio) deixa a linha como está.

## Limpeza de "Encaminhar pra Puma" — adicionada 2026-08-07 (Fase F.4)

Não ganhou coluna nova — reaproveita o campo `Status` que já existia na
aba. A cada execução da Fase F.4, o sistema remove da planilha as
linhas com `Status = concluido` — e, desde 2026-08-14, sincroniza
`Status`/`Observação Puma` de volta pro Supabase todo ciclo
(`supabase_client.sincronizar_status_puma`/`sincronizar_observacao_
puma`, único ponto de leitura dessas 2 colunas).

**Quem preenche `Status`/`Observação Puma` — decisão de negócio fechada
2026-08-14** (ver `docs/pop_operador.md` seção 9 pro detalhe completo):
manutenção/instalação são reconhecidas concluídas automaticamente pelo
próprio sistema (`orchestrator.pipeline._puma_concluido_
automaticamente`, reaproveita a mesma detecção de `REGRA_2`/`REGRA_5_3`/
`REGRA_INSTALACAO_JA_FEITA`) — remoção, ou qualquer caso que o sistema
não confirme sozinho, continua manual (atendimento ou time da Puma).

## Controles manuais (checkboxes) — estado atualizado

Ver `docs/regras_negocio_instalacao_remocao.md` pro contexto de cada um
— resumo aqui, pra ficar tudo num lugar só sobre estrutura de planilha:

1. ~~"Retirada confirmada"~~ — **substituída por `Finalizado`** (decisão
   3 acima). Não existe mais como controle separado.
2. **"Discrepância revisada"** (Tratativas, linhas de
   `REGRA_REMOÇÃO_ATIVA`) — **implementado 2026-08-07**: quando marcado,
   suprime a exibição da linha em `Tratativas` enquanto a condição
   continuar batendo (`orchestrator.pipeline.etapa_publicar_fila_
   operacional`).
3. **`Retornou?` / `Conseguiu Agendar?`** (Pendente de Ligação) —
   continuam sendo lidos como texto (Sim/Não), nunca vira booleano de
   verdade, porque o "vazio" é um terceiro estado com significado
   próprio (ainda não trabalhado) que um checkbox não representa.
4. **"Telefone corrigido"** (Tratativas, linhas com
   `status_contato = contato_invalido`) — **implementado 2026-08-10**
   (Passo 6 de F.1): quando marcado, `orchestrator.pipeline.
   _sincronizar_atendente_da_aba` limpa `tratativas.status_contato`
   (volta a `null`), reabrindo a elegibilidade de disparo. Some sozinho
   no ciclo seguinte (mesmo mecanismo de sempre, `_linha_para_aba`
   grava `False` de novo) — é um "botão de ação", não um estado
   persistente.
5. **`Finalizado`** (Tratativas **e**, desde 2026-08-07, Pendente de
   Ligação) — **implementado nas duas abas**. Único jeito de tirar uma
   linha manualmente quando a lógica automática não cobre o desfecho.

`Técnico` (dropdown nativo do Google Sheets, Tratativas) também já
implementado — ver Fase E.

**Widgets de verdade configurados na planilha real, 2026-08-07** (antes
disso, essas colunas eram só texto solto "TRUE"/"FALSE"/"Sim"/"Não" sem
nenhum controle de UI — o valor era certo, mas a célula não tinha
nenhuma caixa de seleção/lista de fato): via `gspread.Worksheet.
add_validation` (Google Sheets API `setDataValidation`), aplicado uma
vez (sobrevive a `ws.clear()`, que só limpa valor, nunca formatação/
validação — confirmado na doc da API):

- **Checkbox** (`ValidationConditionType.boolean`): `Discrepância
  revisada`, `Selecionado`, `Telefone corrigido`, `Finalizado` em
  `Tratativas`; `Finalizado` em `Pendente de Ligação`.
- **Lista suspensa** (`ValidationConditionType.one_of_list`,
  `strict=False` pra não bloquear célula vazia): `Situação Manual`
  (Tratativas); `Retornou?`/`Conseguiu Agendar?` (Pendente de Ligação);
  `Status` (Encaminhar pra Puma, com os valores internos exatos
  `aguardando_acao`/`em_andamento`/`concluido` — mesmo texto que
  `Status Sistema` já mostra em `Tratativas`, decisão de manter
  consistência em vez de traduzir).
- `Técnico` fica de fora de propósito — é o admin/atendente quem
  mantém a lista de nomes direto na planilha.

**Achado ao vivo durante essa configuração — corrigido**: aplicar
validação numa faixa de linhas maior que o conteúdo real (ex: linha 2 a
1000) faz o Google Sheets tratar toda célula de checkbox dentro dessa
faixa como "com valor" (`FALSE` implícito), mesmo nunca tendo sido
escrita — `ler_aba` passava a devolver uma linha "fantasma" (tudo vazio)
pra cada linha em branco dentro da faixa. Corrigido em duas frentes:
`integrations/google_sheets_client.ler_aba` agora descarta qualquer
linha com `"ID (hash)"` vazio nas 3 abas operacionais (toda linha real
sempre tem isso preenchido); e a faixa de validação foi reduzida pra um
tamanho razoável (300 linhas) em vez de 1000, evitando o problema na
origem e sem poluir a planilha com centenas de checkboxes desmarcados
visíveis ao rolar. 2 testes de regressão em
`tests/test_google_sheets_client.py`.

## `Atendimento`/`Base`/`Ponto de Ação`/`Retorno do Associado` — adicionadas 2026-08-10 (Passo 3 de F.1)

Estrutura completa da lógica de negócio em `docs/templates_whatsapp.md`
(seção 3) — aqui só o resumo de planilha:

- **`Atendimento`** (dropdown `Base`/`Em rota`/`Ação`) — `Tratativas` e
  `Pendente de Ligação` (29 → 33 colunas em Tratativas; 20 → 23 em
  Pendente de Ligação). Decide qual template de WhatsApp disparar.
- **`Base`** e **`Ponto de Ação`** (dropdown, uma coluna pra cada) —
  mesmas 2 abas, imediatamente depois de `Atendimento`. Só uma das duas
  é preenchida por vez (a que combina com o valor de `Atendimento`).
  Lista de opções vem das linhas ativas de `bases`/`pontos_acao`
  (Supabase, tabelas novas do Passo 2) — sem CRUD ainda, então a lista é
  fixa até alguém rodar `configurar_validacao_atendimento()` de novo
  manualmente (ver docstring da função).
- **`Retorno do Associado`** — só `Tratativas` (os botões de discrepância
  só existem nos templates de 1º contato). Sem dropdown — é preenchida
  automaticamente pelo webhook (Fase F.2), não digitada pelo atendente.

**Implementado**: `integrations/google_sheets_client.
configurar_validacao_atendimento(nomes_bases, nomes_pontos_acao)` —
mesma família de função "setup, não recorrente" de
`configurar_formatacao_condicional`. **Testado numa aba descartável da
planilha Operacional real** (`"TESTE validacao atendimento (apagar
depois de revisar)"`) antes de considerar pronto — confirmado lendo de
volta a metadata da API (`fetch_sheet_metadata`): as 3 colunas caem
exatamente nas posições certas (`P`/`Q`/`R` no cabeçalho de
`Tratativas`), com os valores certos. Aba de teste já apagada (usuário
revisou e confirmou).

**Ainda NÃO rodada contra as abas reais `Tratativas`/`Pendente de
Ligação`** — de propósito: o cabeçalho real dessas abas na planilha de
produção ainda é o antigo (29/20 colunas), porque `reescrever_aba` só
escreve o cabeçalho novo quando o orchestrator rodar de verdade com o
`CABECALHO_*` atualizado. **Ordem correta**: só rodar depois que
`etapa_publicar_fila_operacional`/`etapa_escalonar_ligacao` reescreverem
a aba real com o cabeçalho de 33/23 colunas pela primeira vez.

**Lógica funcional implementada (2026-08-10, Passo 6 de F.1)**:
`orchestrator.pipeline._sincronizar_atendente_da_aba` já lê `Atendimento`/
`Base`/`Ponto de Ação` da aba e sincroniza pro Supabase — `Atendimento`
vira o código interno (`base`/`rota`/`acao`, `core.constants.
ATENDIMENTO_*`) e `Base`/`Ponto de Ação` (nome escolhido no dropdown)
viram o uuid real (`base_id`/`ponto_acao_id`) via um mapa nome→id
buscado 1x por execução (`supabase_client.buscar_bases_ativas`/
`buscar_pontos_acao_ativos`). `_linha_para_aba` escreve os 3 de volta
(preserva a escolha do atendente entre ciclos) e também `Retorno do
Associado` (sempre fresco do Supabase, mesmo padrão de `Status
Contato`). `orchestrator.pipeline.etapa_disparo_mensagens` usa
`atendimento`/`base_id`/`ponto_acao_id` pra decidir qual template de
WhatsApp disparar e montar as variáveis (`core.mensagens`) — só falta a
Meta aprovar os 2 templates restantes pra rodar de ponta a ponta contra
produção.

## Indicador "Dias sem contato" — regra fechada em 2026-08-07, AINDA NÃO IMPLEMENTADO

Objetivo: destacar visualmente quando uma pendência ficou tempo demais
sem nenhuma ação da equipe, nas 3 abas operacionais — evita que uma
linha fique "esquecida" sem que ninguém perceba (ideia original do
usuário, registrada antes como item cru em `docs/pop_operador.md`,
agora com a regra completa).

**Cálculo**: dias úteis (mesma definição já usada em
`core.escalonamento.dia_permite_disparo` — segunda a sexta, exceto
feriado em Recife via biblioteca `holidays`) desde o **último contato**
até agora. "Contato" é qualquer ação nossa: entrada da linha na
pendência, disparo de mensagem (tentativa 1/2/3) ou ligação registrada
— cada um desses eventos **reseta o contador**, não é uma soma
acumulada desde o início.

**Marco zero (entrada na pendência)**: `tratativas.created_at` (coluna
já existe, sem schema novo) — o momento em que o sistema criou a
tratativa no Supabase, **não** `data_incidente`/`data_contrato` (data
do evento original, que pode ser bem mais antiga e geraria alerta
artificial sem relação com demora real da equipe). Recomendação técnica
do Claude, aceita pelo usuário nesta sessão.

**Janelas cobertas** (todas usam o mesmo contador único, só o ponto de
reset muda):
1. Criação da tratativa → 1º disparo de mensagem.
2. Entre um disparo de mensagem e o próximo (1→2, 2→3).
3/4. Entre a 3ª tentativa de mensagem e a ligação acontecer, e — se a
   ligação resultar em `Retornou=Sim`+`Conseguiu Agendar=Sim` (linha
   permanece em "Pendente de Ligação" como compromisso futuro) — entre
   essa ligação e a resolução final (`Finalizado` ou nova ligação).
   Confirmado com o usuário: **não são fases distintas** com regras
   diferentes, é o mesmo princípio ("entre cada contato, seja mensagem
   ou ligação") aplicado nesse trecho da esteira.
5. **"Encaminhar pra Puma" é exceção**: o contador continua existindo
   só pra acompanhamento (dias desde o encaminhamento,
   `puma_encaminhamentos.data_encaminhamento`), **sem limite/destaque**
   — o tempo de retorno não depende da equipe.

**Limite**: 7 dias úteis entre contatos consecutivos (itens 1-4). Ao
ultrapassar, **só destaca visualmente** — sem nenhuma ação automática
associada (não escala, não dispara nada sozinho).

**Exibição**: coluna nova nas 3 abas, texto direto ("10 dias sem
contato").

**Status**: IMPLEMENTADO e confirmado ao vivo em 2026-08-07 —
`orchestrator/pipeline.py` calcula e escreve a coluna a cada execução
(inclusive recalculando linhas que já estavam na aba, não só as
novas); `integrations/google_sheets_client.configurar_formatacao_
condicional()` (setup único, não faz parte do pipeline recorrente)
aplicou o destaque vermelho (≥ 7 dias úteis, só Tratativas/Pendente de
Ligação) direto na planilha real via `AddConditionalFormatRuleRequest`
bruto da API do Sheets (`gspread` não tem suporte nativo a isso). Duas
pegadinhas reais da API encontradas e corrigidas no caminho: (1) a
planilha tem locale `pt_BR`, então fórmula customizada via API precisa
de `;` como separador de argumento, não `,`; (2) `endRowIndex` omitido
não significa "sem limite pra sempre" — a API recorta pro tamanho
atual do grid no momento da criação da regra, por isso a função
redimensiona a aba pra 10.000 linhas antes de criar as regras.

## Aba nova "Alertas" — CONCLUÍDA em código 2026-08-11 (Fase C do webhook)

Fila de exceção que complementa as 3 abas operacionais — não substitui
`Finalizado`/`Status=concluido` (que já resolvem o caminho previsível
sozinhas). Só entra aqui o que o sistema **detecta mas não sabe agir
sozinho**, alimentada pelo webhook do Newmo (Fase F.2 — Edge Function
`newmo-webhook`, já escrita e validada ao vivo). Decisão de escopo
fundamentada em pesquisa de padrão de humano-no-loop (fontes: Moxo
"Human in the Loop RPA", TrustLayer "Human-in-the-Loop: A Practical
Automation Framework") — a recomendação estabelecida é híbrida:
automação resolve o previsível sozinha, só o incerto vai pra uma fila
de exceção única com contexto completo por item.

Hoje, 2 casos alimentam esta aba:
1. **Retorno tardio do associado** — o webhook grava `retorno_
   associado` numa tratativa que já saiu de `Tratativas` (foi pra
   ligação ou pra Puma), então o sinal não tem lugar pra aparecer sem
   fragmentar em mais colunas nas outras abas.
2. **Agendamento confirmado sem data preenchida** — associado confirma
   por WhatsApp (`situacao_manual = 'Agendado'`), mas a data combinada
   só o atendente sabe, e o sistema não tem como preencher sozinho.

**Cabeçalho** (`CABECALHO_ALERTAS`, `integrations/google_sheets_
client.py`):

```
ID (hash)        -- mesma chave das outras 3 abas
Cliente
Telefone
Onde está         -- label derivado do status atual da tratativa:
                     Pendente de Ligação / Encaminhar pra Puma / Finalizado
Tipo de Alerta    -- texto fixo por caso ("Retorno tardio" / "Agendado sem data")
Contexto          -- resposta literal do associado, ou "Confirmado via WhatsApp"
Ação              -- dropdown: vazio / "Confirma conclusão" / "Foi engano, ignorar"
                     (só relevante pro caso de retorno tardio)
Data Agendada     -- mesmo campo/formato de Tratativas
                     (só relevante pro caso de agendado sem data)
```

**Mecânica de resolução** (`etapa_processar_alertas`, `orchestrator/
pipeline.py`) — `Pendente de Ligação`/`Encaminhar pra Puma` só se
limpam pelo que está escrito NA PRÓPRIA ABA (`Finalizado`/
`Status=concluido`), não por reconsulta de status ao Supabase — por
isso resolver um alerta toca a aba de origem diretamente:
- `"Foi engano, ignorar"` → só limpa `retorno_associado`.
- `"Confirma conclusão"` + tratativa em `aguardando_ligacao` → marca
  `status=finalizado` e remove a linha correspondente de "Pendente de
  Ligação".
- `"Confirma conclusão"` + tratativa em `encaminhado_puma` → escreve
  `Status=concluido` na linha correspondente de "Encaminhar pra Puma"
  (a limpeza automática de `etapa_processar_resultado_ligacao` remove
  a linha no ciclo seguinte — reaproveita 100% o mecanismo existente,
  não é imediato). `tratativas.status` não é alterado nesse caso.
- `"Confirma conclusão"` + outro status → só marca `status=finalizado`.
- Em todos os casos de "Confirma conclusão", também limpa
  `retorno_associado`.
- `Data Agendada` preenchida (sem "Ação") → grava `data_agendada` na
  tratativa.

Roda como **F.5** (`etapa_processar_alertas`), depois de F.3/F.4 no
ciclo — precisa ver o `status` mais recente que essas etapas produzem.
Plano completo em `C:\Users\Devon Local\.claude\plans\vast-swinging-
rabbit.md` e na seção "F.2 (webhook Newmo/Zapio) + aba 'Alertas'" de
`_handoff/HANDOFF.md`. **Pendente**: criar a worksheet "Alertas" na
planilha real + rodar `configurar_validacao_alertas()` uma vez +
validar ao vivo (ver HANDOFF pro estado atual desses passos).

## Aba nova "Análise de Divergência - Instalação" — CONCLUÍDA em código 2026-08-13

Aba isolada, mesmo espírito de "Alertas" (fila separada da esteira
normal), mas pra um problema diferente: **limpeza de cadastro
administrativo**, não atendimento ao associado. Cobre o caso em que
uma linha de "Instalação-Remoção" (planilha Administrador, 100%
humana) já foi fisicamente instalada — o chassi já aparece em
"Rastreadores Ativos", sem divergência de titularidade — mas ninguém
tirou a linha de lá. Antes de 2026-08-13, o sistema já fazia esse
cruzamento (`core/motor_regras_instalacao_remocao.py`) mas descartava
o resultado em silêncio; agora sinaliza numa aba própria.

Decisões de negócio (ver `docs/regras_negocio_instalacao_remocao.md`,
seção `REGRA_INSTALACAO_JA_FEITA`): não entra em "Tratativas" (não é
atendimento), sem nível de urgência (não compete na esteira normal),
reescrita do zero a cada ciclo — a linha some sozinha quando o
administrador atualizar/remover a linha de origem.

**Cabeçalho** (`CABECALHO_ANALISE_DIVERGENCIA_INSTALACAO`,
`integrations/google_sheets_client.py`):

```
ID (hash)                                -- mesma fórmula de hash de Instalação (cpf+chassi+situacao+data_contrato)
Chassi
Placa
Cliente (Instalação-Remoção cadastro)    -- "Nome Associado" da aba Instalação-Remoção
Cliente (Rastreadores Ativos)            -- "Cliente" da aba Rastreadores Ativos, mesmo chassi (conferência visual)
Data Contrato
Data de Instalação                       -- da aba Rastreadores Ativos (quando o rastreador foi de fato instalado)
IMEI                                     -- da aba Rastreadores Ativos
Motivo                                   -- Bloco B (2026-08-24): "Instalação já concluída" ou "Titularidade divergente"
Observação
Ação
```

Alimentada por `etapa_publicar_fila_operacional` (Fase E) — mecânica,
sem upsert em `tratativas` (não são tratativas) e sem sincronizar
nenhum campo de atendente antes de reescrever (não há estado editável
a preservar, diferente de "Tratativas"). **Pendente**: criar a
worksheet "Análise de Divergência - Instalação" na planilha real antes
do primeiro `reescrever_aba` (mesmo processo já usado pra "Alertas").

**Bloco B (2026-08-24)**: `REGRA_TITULARIDADE` (troca de titularidade,
chassi já instalado) passou a alimentar esta aba também, ao lado de
`REGRA_INSTALACAO_JA_FEITA` — deixou de virar tratativa/entrar em
"Tratativas". A coluna "Motivo" existe justamente pra diferenciar os
dois casos numa aba compartilhada.

## Aba nova "Análise de Divergência - Remoção" — Bloco B, 2026-08-24

Mesmo espírito da aba de Instalação acima (relatório mecânico, sem fila
de atendimento, reescrita do zero a cada ciclo), mas pra 3 situações de
Remoção que não devem virar tratativa de atendimento: SGA ainda
confirma `ATIVO` (`REGRA_REMOCAO_SGA_ATIVO`), modelo do equipamento fora
da lista permitida (`REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO`,
`system_parameters.modelos_removiveis`) e titularidade divergente
(`REGRA_REMOCAO_TITULARIDADE_*` — deixou de entrar em "Tratativas",
mesma mudança de `REGRA_TITULARIDADE` acima). Ver
`docs/regras_negocio_instalacao_remocao.md` pra critério completo de
cada regra.

**Cabeçalho** (`CABECALHO_ANALISE_DIVERGENCIA_REMOCAO`,
`integrations/google_sheets_client.py`):

```
ID (hash)                                -- mesma fórmula de hash de Remoção (cpf+chassi+situacao+data_contrato)
Chassi
Placa
Cliente cadastro                         -- "Nome Associado" da aba Instalação-Remoção
Cliente Rastreadores Ativos              -- "Cliente" da aba Rastreadores Ativos, mesmo chassi (conferência visual)
Modelo do Equipamento                    -- coluna B de Rastreadores Ativos (rastreador, não o veículo)
Status SGA                               -- status vivo consultado (ATIVO/INATIVO)
Motivo                                   -- qual das 3 situações gerou a linha
Observação
Ação
```

Alimentada por `etapa_publicar_fila_operacional` (Fase E), mesmo padrão
mecânico da aba de Instalação. **Pendente**: criar a worksheet "Análise
de Divergência - Remoção" na planilha real antes do primeiro
`reescrever_aba` (mesmo processo já usado pra "Alertas"/"Análise de
Divergência - Instalação") — sem isso a Fase E quebra tentando escrever
numa aba inexistente.

## Correção de validação/cabeçalho — achado e resolvido 2026-08-14

Achado ao vivo (print do usuário): a coluna "Tentativa 2" (Tratativas)
tinha um dropdown, sem sentido pra uma coluna que só guarda timestamp de
disparo. Investigação revelou um **padrão sistêmico, não um bug
isolado**: a validação de dado (checkbox/dropdown) do Google Sheets fica
presa à LETRA da coluna, não ao nome. Toda vez que uma coluna nova é
inserida no meio de um cabeçalho (`CABECALHO_TRATATIVAS`/etc., em
`integrations/google_sheets_client.py`), as colunas depois dela deslizam
pra letras diferentes, mas a validação configurada antes continua presa
à letra antiga — "vaza" pra dentro de outra coluna que passou a ocupar
aquela letra depois. Confirmado via `fetch_sheet_metadata` (raio-x de
formatação + validação de cada coluna das 5 abas):

- **Tratativas**: dropdown indevido em "Status Sistema"/"Tentativa 2"
  (colunas 100% escritas pelo sistema, nunca deveriam ter validação);
  faltava o dropdown documentado em "Atendimento"/"Base"/"Ponto de Ação"
  (2026-08-10, nunca chegou a ser aplicado de fato, ou foi perdido por
  esse mesmo mecanismo de drift).
- **Encaminhar pra Puma**: dropdown indevido em "Data Encaminhamento"/
  "Motivo"; a coluna "Status" (que tem um conjunto fixo de valores reais
  — `aguardando_acao`/`em_andamento`/`concluido`) estava sem validação
  nenhuma.
- **Alertas**: usava um tom de azul diferente do padrão (`_ABAS_
  CABECALHO_FORMATADO` nunca incluiu essa aba); "Análise de Divergência -
  Instalação" não tinha formatação de cabeçalho nenhuma (mesmo motivo).

**Correção**: `integrations/google_sheets_client.limpar_validacoes_aba(aba)`
(novo) limpa QUALQUER validação da faixa de dados inteira antes de
`configurar_checkboxes_tratativas`/`configurar_checkbox_finalizado_
pendente_ligacao`/`configurar_validacao_atendimento`/`configurar_
validacao_alertas` recriarem só a validação intencional a partir do
cabeçalho ATUAL — elimina qualquer resquício de um layout de cabeçalho
anterior. Rodado ao vivo 2026-08-14 (confirmado lendo a metadata de
volta): nenhuma das 5 abas ficou com validação fora do esperado. **Não
foi criado um dropdown novo pra "Status"** em "Encaminhar pra Puma" —
ficou fora de escopo desta correção (não existe `configurar_*` pra essa
coluna ainda; considerar como melhoria futura, não bug).

**Achado à parte — RESOLVIDO 2026-08-14, mesma sessão** (decisão do
usuário: implementar o wiring que faltava, não remover as colunas): em
"Pendente de Ligação", as colunas "Atendimento"/"Base"/"Ponto de Ação"
existiam e tinham o dropdown correto, mas nunca eram preenchidas
automaticamente nem lidas de volta por nenhuma etapa. Agora:
`_linha_pendente_ligacao` (`orchestrator/pipeline.py`) as popula ao
escalar (herda o que já estava decidido em Tratativas, convertendo
`atendimento`/`base_id`/`ponto_acao_id` pros nomes exibidos no
dropdown), e `etapa_processar_resultado_ligacao` as sincroniza de volta
pro Supabase todo ciclo (mesma filosofia incondicional de
`_sincronizar_atendente_da_aba`, a aba é a fonte de verdade enquanto a
pendência está na fila de ligação). Ver seção "Sessão 2026-08-14 (chat
novo #15, continuação)" do `_handoff/HANDOFF.md` pro detalhe completo.

**Cor de cabeçalho por origem da coluna** (item novo, mesma sessão): até
aqui todo cabeçalho usava 1 cor só (navy `#0A1B5E`) pra aba inteira.
Agora usa 2, pra ajudar a equipe a distinguir visualmente o que ela
precisa preencher do que é só informativo:

- **Navy `#0A1B5E`** — coluna escrita pelo sistema a cada ciclo (nunca
  editar).
- **Accent da marca `#0046B0`** — coluna que alguém preenche manualmente
  (nosso atendente, ou o time da Puma em "Encaminhar pra Puma"). Fonte de
  verdade de quais colunas são essas: `_COLUNAS_EQUIPE_POR_ABA` em
  `integrations/google_sheets_client.py`, derivada dos campos que cada
  etapa efetivamente LÊ de volta da aba (não uma lista arbitrária). Par
  de cores validado com o skill `dataviz`
  (`scripts/validate_palette.js`): CVD ΔE 17.4, visão normal ΔE 17.8 —
  bem acima do piso de 8/15.

`configurar_formatacao_cabecalho()` agora aplica as 2 cores por coluna
(agrupando colunas consecutivas da mesma origem num único range, via
`_runs_por_cor`) em vez de 1 cor só pra aba inteira, e passou a cobrir
as 5 abas (antes só 3 — "Alertas"/"Análise de Divergência - Instalação"
ficaram de fora por engano).

## Auditoria de validações (dropdowns/checkboxes) — 2026-08-15

Usuário revisou a planilha real e reportou "Situação Manual"/
"Atendimento"/"Base"/"Ponto de Ação"/"Status" (Puma) sem dropdown. Nova
auditoria (`_handoff/auditoria_validacoes_planilha_operacional.py`, só
leitura, `fetch_sheet_metadata` nas 5 abas) confirmou 3 causas
diferentes, misturadas:

1. **Bug sistêmico de código, achado novo**: `gspread.Worksheet.
   add_validation` tem um parâmetro `showCustomUi` que **vem `False` por
   padrão** — sem passar `True`, uma validação `ONE_OF_LIST` continua
   validando o valor digitado (aceita/rejeita certo), mas o Google Sheets
   nunca desenha a setinha/lista suspensa de verdade na célula. Nenhuma
   chamada de `add_validation` no código passava esse parâmetro — isso
   inclui `configurar_validacao_atendimento` (Atendimento/Base/Ponto de
   Ação, já configurado desde 2026-08-10/14) e `configurar_validacao_
   alertas` (Ação), que pareciam "prontos" pela metadata mas nunca
   mostravam a UI de dropdown de verdade. **Corrigido nas 2 funções
   existentes + em toda função nova** (`showCustomUi=True` em todo
   `add_validation` de `ValidationConditionType.one_of_list`).
   Checkboxes (`ValidationConditionType.boolean`) não são afetados —
   sempre renderizam como caixa de seleção independente desse parâmetro,
   já confirmado ao vivo antes (2026-08-07).
2. **"Situação Manual" (Tratativas) nunca tinha sido implementada** —
   documentada desde 2026-08-07, mas sem nenhuma função `configurar_*`
   correspondente. `configurar_validacao_situacao_manual()` novo
   (`Agendado`/`Cancelado`/`Solicitação operacional`, `strict=False`).
3. **"Retornou?"/"Conseguiu Agendar?" (Pendente de Ligação), achado
   novo**: documentadas como configuradas ao vivo desde 2026-08-07, mas
   sem função versionada em código pra recriá-las — o `limpar_
   validacoes_aba()` da auditoria anterior (2026-08-14) limpa a faixa
   inteira antes de recriar só o que tem função de recriação, então
   essas 2 colunas foram limpas e nunca voltaram. `configurar_validacao_
   retornou_conseguiu_agendar()` novo (`Sim`/`Não`, `strict=False`).
4. **"Status" (Encaminhar pra Puma)** — gap já conhecido desde
   2026-08-14 (ver seção acima), agora fechado: `configurar_validacao_
   status_puma()` novo (`aguardando_acao`/`em_andamento`/`concluido`,
   `strict=False`).

**"Técnico" (Tratativas)** também apareceu sem validação na auditoria —
mas esse dropdown é intencionalmente gerenciado à mão pelo admin/
atendente direto na planilha (fora do código, ver seção acima), não uma
correção de código. Provavelmente foi arrastado pelo mesmo `limpar_
validacoes_aba()` de 2026-08-14 (que limpa a faixa inteira, sem exceção)
— fica por conta do usuário recriar manualmente na planilha quando
quiser.

As 5 funções (2 corrigidas + 3 novas) foram rodadas ao vivo contra a
planilha real e reconfirmadas lendo a metadata de volta
(`showCustomUi=True` em todos os 10 dropdowns das 4 abas) — usuário
confirmou visualmente que a setinha aparece em todas. 3 testes novos em
`tests/test_google_sheets_client.py` (602 → 605).
