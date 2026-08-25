# Regras de negócio — Instalação/Remoção

Documento vivo, atualizado conforme as regras vão sendo fechadas com o
usuário — mesmo espírito de `docs/regras_negocio.md` (Manutenção), mas
para a origem `instalacao`/`remocao`. Em construção: várias regras já
fechadas (ver seções "Regras de Instalação"/"Regras de Remoção"), ainda
tem mais por vir.

**Atualização 2026-08-11**: os valores numéricos citados abaixo
(300cc, R$ 80.000, as faixas de dias de `REGRA_PRAZO`/`REGRA_REMOÇÃO_
PRAZO`) são os **defaults** — deixaram de estar hardcoded no Python e
passaram a ser editáveis via `system_parameters`
(`limiar_cilindrada_risco_cc`, `limiar_fipe_risco`, `tier_instalacao`,
`tier_remocao`) — ver `_handoff/HANDOFF.md`, seção "Fase 3, Passo 1
concluído". Nenhuma regra de negócio mudou, só passou a ser
configurável sem editar código. A tela pra editar isso no Painel Admin
ainda não existe (Fase 3, Passos 3-4 do plano de arquitetura).

## Fonte de dados

- **Instalação-Remoção** (aba da planilha Administrador, 22 colunas, 100%
  humana — sistema só lê, nunca escreve): `Situação, Data contrato, Nome
  Associado, CPF, celular, Celular Aux, Telefone Comercial, Logradouro,
  Número, Bairro, Cidade, Estado, Placa, Modelo, Ano Modelo, Valor FIPE,
  Tipo Veículo, Voluntário, Cooperativa, Chassi, Marca, Serviço`.
  - `Serviço`: texto livre tipo "Instalação"/"Retirada"
    ("remoção"), normalizado pelo administrador antes de chegar no
    sistema — já usado hoje em `core.motor_regras._chassi_na_lista_retirada`
    (procura substring "retir"/"remo") pra fechar incidentes de
    Manutenção quando o veículo está saindo da frota.
  - `Situação`: status de pagamento/associado via SGA — **não indica se o
    serviço (instalação/retirada) foi executado de fato**. Cuidado: não
    confundir com "situação de execução do serviço", que ainda não tem
    fonte conhecida (ver "Perguntas em aberto" abaixo).

## Já decidido antes desta fase (não redecidir, só reaproveitar)

- **Dedup** (`core/dedup.py`, já implementado e testado): chave única =
  `cpf + chassi + situacao + data_contrato`, hash sha256 truncado em 16
  caracteres. `gerar_chave_unica("instalacao"|"remocao", dados)`.
- **Identificador do veículo: CHASSI** (confirmado pelo usuário nesta
  fase — mesmo princípio já usado em Manutenção:
  `core/motor_regras.py` — "CHASSI é o identificador mais confiável do
  VEÍCULO, dado imutável"). **Placa é só pra facilitar visualização** pro
  atendente — nunca usada como chave de agrupamento/identidade, porque
  placa ausente, formato Mercosul vs. antigo geram atrito desnecessário.
  Aqui o chassi já vem direto na própria linha (coluna `Chassi`), não
  precisa buscar em Rastreadores Ativos como acontece com os incidentes
  de Manutenção.
- **Normalização de telefone**: `core.normalizacao.normalizar_telefone_e164`
  (E.164, regra do 9º dígito) — já implementado, reaproveitar direto.
  **Prioridade de telefone confirmada**: coluna `celular` é sempre a
  prioridade, **sem fallback** — se vier vazio, trata como "sem telefone"
  mesmo que `Celular Aux`/`Telefone Comercial` estejam preenchidos.
- **Instalação executada de fato**: chassi encontrado em "Rastreadores
  Ativos" — ver seção "Alimentação da planilha" abaixo pro quadro
  completo (inclui a fonte SGA também).

## Arquitetura (implementada em 2026-08-06)

Mesmo padrão que já funcionou e foi validado em `core/motor_regras.py`:
`core/motor_regras_instalacao_remocao.py`, funções puras por regra, uma
cascata por if/elif explícito (a ordem no código é a precedência real,
não um número de prioridade solto), saída alimentada por
`rule_templates` (nunca texto hardcoded), cada regra com
`nivel_urgencia`. Testado com fixtures de dict simples, sem I/O —
`tests/test_motor_regras_instalacao_remocao.py`, mesmo rigor de
`tests/test_motor_regras.py`. Diferença proposital em relação a
Manutenção: a função pública (`classificar_instalacao_remocao`) retorna
uma lista simples de tratativas, não um dict de grupos — não existe
aqui nenhum caso de "sistema resolve automaticamente".

## Alimentação da planilha "Instalação-Remoção" (explicado pelo usuário, 2026-08-06)

- O administrador recebe as pendências e **insere linhas na aba — nunca
  apaga as anteriores**. A aba é, na prática, um log que só cresce, não
  uma tabela de estado atual mantida à mão.
- **Consolidação por chassi: os dados mais recentes prevalecem.** Quando
  o mesmo chassi aparece em mais de uma linha (histórico de
  Instalação/Remoção acumulado), o sistema usa a linha **mais recente**
  como estado atual — as linhas antigas do mesmo chassi são descartadas
  do processamento (só ficam como histórico bruto na planilha).
- **Duas fontes de confirmação, independentes, em paralelo** (não uma
  substituindo a outra):
  - **SGA (Hinova)**, consultado ao vivo por
    `integrations.sga_bot.consultar_situacao(chassi)` (já implementado e
    validado) — devolve a situação do veículo/associado na cooperativa
    (`ATIVO`/`INATIVO`/outros valores possíveis, texto livre do dropdown
    do Hinova). É o gatilho de negócio: virou `INATIVO` → motivo real
    pra existir uma pendência de retirada.
  - **Rastreadores Ativos (Track N' Me)** — confirma o lado técnico
    (equipamento comunicando). **Instalação executada** = chassi
    encontrado em Rastreadores Ativos (confirmado). **Remoção
    executada** = **não tem nenhum sinal automático hoje** — o
    atendimento confirma manualmente (ver seção de controles manuais
    abaixo).

## Instalação e Remoção: mesmo processo, regras documentadas separadas

Uma cascata técnica só — reage ao valor de `Serviço`, não duas funções
separadas (confirmado pelo usuário). Mas dentro dessa cascata vão existir
regras pequenas que só se aplicam a uma das origens (ex: urgência por
`Data contrato` vale só pra Instalação, não pra Remoção) — por isso as
regras abaixo ficam documentadas em seções separadas por origem, mesmo
sendo implementadas na mesma função/cascata.

## Confiabilidade do relatório Track N' Me pra status ≠ Ativo (confirmado)

Usuário rejeitou explicitamente a ideia de ampliar o filtro de download
(`tracknme_bot.baixar_relatorios`) pra trazer também status
cancelado/descartado: **não há confiança na integridade do relatório do
Track N' Me pra equipamentos com situação diferente de "Ativo"**. Não
mexer em `tracknme_bot.py` nessa direção. `Data de Cancelamento`/`Data de
Descarte` continuam fora de uso.

## Regras de Instalação

### Estrutura combinada de urgência (confirmado)

Duas dimensões distintas de urgência que, combinadas, indicam risco maior
de perda do veículo — **3 regras separadas**, mesmo padrão de precedência
já usado em Manutenção (ex: família `REGRA_5_1`): regra de prazo isolada,
regra de risco isolada, e uma regra composta (prioridade mais alta,
`nivel_urgencia` maior) quando as duas condições batem ao mesmo tempo.
Não é uma fórmula somando pontuações — é cascata, primeira que bate
vence, mesma arquitetura de `core/motor_regras.py`.

### REGRA_PRAZO — urgência por `Data contrato` (fechada)

Dias corridos entre `Data contrato` e a data atual (`agora`):

| Faixa | Nome | Nível de urgência |
|---|---|---|
| 1–10 dias | Normal | **1** |
| 11–30 dias | Em atraso | **3** |
| 31+ dias | Crítico | **4** |

(nível 5 reservado pra `REGRA_PRAZO_E_RISCO`, quando prazo crítico coincide
com veículo de alto risco — ver regra de risco abaixo, ainda em pesquisa)

### REGRA_RISCO — veículo de alto risco (fechada, critério; nível de urgência pendente)

Pesquisa feita em 2026-08-05, grounded na frota real
(`downloads/rastreadores_ativos.xlsx`, 12241 ativos: 9133 Carro / 3069
Moto / 33 Caminhão — nota: há motos mal categorizadas como "Carro" nos
dados do Track N' Me, problema de qualidade de dado deles, fora do nosso
controle).

**Critério final (um "veículo de risco" cobre os dois tipos, mesma
regra)**:

- **Moto**: cilindrada **300cc ou mais**. Descartamos a ideia inicial de
  cruzar com ranking nacional de roubo/furto — quase toda a frota de moto
  real (CG 160, NXR 160 Bros) já lidera esses rankings só por volume de
  venda, então usar ranking sozinho marcaria a frota inteira. Cilindrada
  alta filtra bem melhor (só ~160 de 3069 motos na frota real: XRE 300,
  CB 300F Twister).
- **Carro**: **Valor FIPE > R$ 80.000** OU **modelo está entre os mais
  furtados do Brasil** (ranking nacional, independe do valor) — os dois
  critérios contam, qualquer um dos dois já classifica como risco.
  Confirmado pelo usuário mesmo sabendo que isso marca a maioria da frota
  de carro real (Onix, Gol, HB20, Mobi, Kwid, Argo estão todos no
  ranking nacional) — tolerância diferente da moto, decisão explícita.

**Ranking nacional de roubo/furto, moto, 1º semestre 2025**: 1º CG 160
(4794 ocorrências), 2º CG 150 (681), 3º Fazer 250 (649), 4º XTZ 250 (622),
5º XRE 300 (445), 6º PCX 150 (418), 7º CBX 250 Twister (405), 8º CG 125
(320), 9º NXR 160 Bros (318), 10º NMax 160 (268). NXR 160 Bros tem
destaque regional específico no Norte/Nordeste.

**Ranking nacional de roubo/furto, carro, jan-mai/2025**: VW Gol (1669),
Hyundai HB20 (1397), Chevrolet Onix (1322), Fiat Uno (1232), Ford Ka
(1212), Chevrolet Corsa (1134), Fiat Argo/Strada/Mobi/VW Fox (653-819).

**Nível de urgência: 4** — veículo de risco (moto ou carro) já nasce com
prioridade alta no sistema, mesmo com prazo do contrato ainda normal.
Mesmo patamar de `REGRA_PRAZO` Crítico isolado; só quando os dois batem
juntos (`REGRA_PRAZO_E_RISCO`) chega no nível máximo (5).

**Implementação — extração de cilindrada do texto do `Modelo` (fechado,
2026-08-06)**: usuário confirmou que os modelos sempre trazem a
cilindrada embutida no nome (ex: "CG 160 FAN FLEX", "DOMINAR 400 NS").
Abordagem: extrair por regex o **primeiro número de 2-4 dígitos** que
aparece no texto de `Modelo`, tratar como cilindrada. Risco conhecido:
edições especiais podem ter um número mais cedo/tarde no texto que não é
cilindrada (ex: real na frota: "CG 160 TITAN FLEXONE/ED.ESPECIAL 40
ANOS" — o "40" vem depois do "160" real, então "primeiro número" ainda
funciona nesse caso, mas vale testar contra a lista real de modelos antes
de confiar cegamente). Mitigação: manter uma **lista de exceções manual**
(mesmo mecanismo de `system_parameters` abaixo) pros poucos casos que a
extração automática errar, alimentada conforme forem aparecendo.

**Implementação — lista "modelos mais furtados" (fechado, 2026-08-06)**:
fica em `system_parameters` (Supabase), não fixa no código — ranking de
roubo muda com o tempo. Nome proposto: `modelos_alto_risco_furto`,
formato CSV de substrings de modelo (mesmo padrão de `placas_genericas`),
ex: `"CG 160,CG 150,FAZER 250,XTZ 250,XRE 300,PCX 150,CBX 250,CG
125,NXR 160,BROS,NMAX 160,ONIX,GOL,HB20,UNO,KA,CORSA,ARGO,STRADA,MOBI,
FOX"`. Verificação: normaliza os dois lados (maiúsculo, sem espaço extra)
e checa se algum item do parâmetro aparece como substring do `Modelo` da
linha — mesma lógica de substring já usada em
`core.motor_regras._chassi_na_lista_retirada` pro campo `Serviço`.

### REGRA_PRAZO_E_RISCO — composta (fechada)

Dispara quando **REGRA_RISCO bate (moto 300cc+, ou carro conforme
critério acima) E o prazo já está em "Em atraso" ou "Crítico" (11+
dias)** — não precisa esperar chegar em "Crítico" sozinho.

- **Nível de urgência: 5** (a mais alta, reservada exatamente pra esse
  cruzamento).

### REGRA_TITULARIDADE — troca de titularidade (fechada, 2026-08-06; destino mudou no Bloco B, 2026-08-24)

Quando o chassi da linha **já é encontrado** em "Rastreadores Ativos"
(instalação confirmada como executada) **e** o nome do cliente diverge
entre as duas fontes — `Nome Associado` (aba Instalação-Remoção) vs.
`Cliente` (aba Rastreadores Ativos, mesmo chassi) — sinaliza troca de
titularidade.

Não compete com `REGRA_PRAZO`/`REGRA_RISCO`/`REGRA_PRAZO_E_RISCO` —
essas só valem **enquanto a instalação ainda está pendente** (chassi
não encontrado); `REGRA_TITULARIDADE` só dispara depois que a
instalação já foi confirmada. Não há sobreposição/precedência a
resolver entre elas.

**Bloco B (2026-08-24)**: deixou de virar tratativa/entrar em
"Tratativas" — vai pra aba "Análise de Divergência - Instalação" (mesmo
destino de `REGRA_INSTALACAO_JA_FEITA`, motivo "Titularidade
divergente"), lado a lado com o `codigo_regra` original. Ação continua
puramente manual (o atendente verifica e corrige o cadastro), só o
destino/apresentação mudou — deixou de competir na esteira de
prioridade de atendimento por ser mais aviso administrativo que
atendimento ao associado. **Sem nível de urgência** a partir de agora
(não compete mais na esteira; o nível 2 antigo deixou de se aplicar).

### REGRA_INSTALACAO_JA_FEITA — instalação esquecida no cadastro (fechada, 2026-08-13)

Quando o chassi da linha **já é encontrado** em "Rastreadores Ativos"
(instalação confirmada como executada) **e** o nome do cliente **NÃO**
diverge entre as duas fontes — mutuamente exclusiva com
`REGRA_TITULARIDADE`, que cobre o caso em que o nome diverge. Antes de
2026-08-13 essa combinação era descartada em silêncio (nenhuma
tratativa, nenhum aviso) — a linha desaparecia do radar sem ninguém
saber que o administrador esqueceu de tirá-la de "Instalação-Remoção".

Decisão de negócio (2026-08-13): isso é **limpeza de cadastro**, não
atendimento ao associado — por isso:

- **Não gera tratativa** e **não entra na aba "Tratativas"** — entra
  numa aba própria, "Análise de Divergência - Instalação" (ver
  `docs/planilha_operacional.md`), reescrita do zero a cada ciclo. A
  linha some sozinha quando o chassi deixar de aparecer em
  "Instalação-Remoção" (o administrador finalmente atualiza/remove a
  linha de lá).
- **Sem nível de urgência** — não compete na esteira normal de
  prioridade (não afeta o associado).
- Texto (enxuto, via `rule_templates`):
  - `template_observacao`: "Chassi já consta em Rastreadores Ativos
    (instalado), mas ainda está em Instalação-Remoção."
  - `template_acao`: "Remover ou atualizar a linha em
    Instalação-Remoção."

## Regras de Remoção

### REGRA_PRAZO (Remoção) — urgência por tempo desde que ficou INATIVO (fechada)

Diferente de Instalação: **não usa `Data contrato`**. O que orienta a
eficiência de uma retirada é o tempo desde o **cancelamento** — o
momento em que o veículo virou `INATIVO` no SGA.

| Faixa | Nome | Nível de urgência |
|---|---|---|
| 1–10 dias | Normal | **1** |
| 11–20 dias | Alta | **3** |
| 21–30 dias | Urgente | **5** |

Depois de 30 dias, **congela em "Urgente" (nível 5) pra sempre** —
decisão do usuário: dentro desse prazo a pendência já devia ter passado
pelas 3 tentativas de contato, ligação e encaminhamento pra Puma, não faz
sentido escalar além disso.

**Fonte da data "ficou INATIVO"**: não é um campo da planilha — é
**detectada pelo próprio sistema**, na primeira vez que
`sga_bot.consultar_situacao(chassi)` retornar `INATIVO` pra aquele
chassi. Se o veículo reativar (`SGA` volta a mostrar diferente de
`INATIVO`) e depois cair pra `INATIVO` de novo, conta como um ciclo novo
(reinicia em 0 dias) — é isso que "cancela e depois cai de novo"
significa na prática.

**Persistência (decisão técnica, 2026-08-06)**: não dá pra usar
`tratativas.created_at`/`chave_unica` pra isso — a chave de dedup inclui
`situacao` (o texto da planilha, coisa diferente do status vivo do SGA),
então mudanças nela quebrariam a contagem. Proposta: **tabela nova e
pequena no Supabase**, dedicada só a isso:

```
tabela situacao_veiculo_sga
    chassi (text, pk)
    status (text)        -- último status do SGA visto (ex: "ATIVO", "INATIVO")
    desde (timestamptz)   -- quando ESSE status começou (só muda quando status muda)
    atualizado_em (timestamptz)  -- última vez que consultamos, sempre atualiza
```

Lógica: a cada execução, consulta o SGA ao vivo pro chassi; se o status
voltou diferente do que está salvo (ou não existe linha ainda), grava
`status=novo, desde=agora`; se voltou igual, só atualiza
`atualizado_em`. `dias_desde_inativo = hoje - desde`, quando
`status == "INATIVO"`. Desacoplada de `tratativas` de propósito — pode
ser reaproveitada por qualquer regra futura que precise de "há quanto
tempo isso é verdade", não só Remoção. Implementado como
`core.motor_regras_instalacao_remocao.atualizar_situacao_sga` (função
pura, sem I/O — o orchestrator lê/grava no Supabase antes/depois de
chamar).

**Gating (2026-08-06)**: o SGA é o gatilho de negócio pra remoção
existir de fato (já estava dito acima, "Fonte de dados") — na prática
isso significa que **nenhuma tratativa de remoção é gerada** enquanto o
SGA não tiver confirmado `INATIVO` pra aquele chassi pelo menos uma vez
(sem registro em `situacao_veiculo_sga`, ou status atual diferente de
`INATIVO`). A pendência existe na planilha "Instalação-Remoção", mas só
"nasce" pro sistema depois dessa confirmação.

### REGRA_REMOÇÃO_ATIVA — Remoção com equipamento ainda ativo (fechada)

Quando `Serviço` indica remoção/retirada E o chassi da linha **é
encontrado** em "Rastreadores Ativos" (o simples fato de aparecer nessa
aba já confirma que o equipamento está ativo no Track N' Me, por causa do
filtro de download) → sinalizar ao atendimento que o veículo consta na
lista de retirada mas o equipamento continua ativo na Track N' Me.
Discrepância a resolver manualmente, não uma confirmação de execução.

**Correção de 2026-08-06 (mudou o que estava escrito antes)**: o nível
de urgência **não é mais fixo em 1** — o usuário confirmou (com exemplo
concreto: 25 dias `INATIVO` + rastreador ainda ativo → nível 5, não 1)
que **os dias desde `INATIVO` sempre decidem o número final**, igual à
tabela de `REGRA_PRAZO` (Remoção) abaixo. A divergência (rastreador
ainda ativo) só muda **qual código/texto** é usado — nunca o nível.
Isso gerou 3 variantes (`REGRA_REMOÇÃO_ATIVA_NORMAL`/`_ALTA`/`_URGENTE`,
mesmos limiares de dias de `REGRA_PRAZO`), não mais um código único de
nível 1. Ver tabela final consolidada abaixo.

Caso o chassi **não seja encontrado** em Rastreadores Ativos (situação
esperada pra uma remoção): **também gera tratativa** — o usuário
confirmou que o sistema deve cobrar confirmação mesmo assim, escalando
pelos dias (é exatamente o papel de `REGRA_PRAZO` abaixo, que já cobria
esse caso; a suposição anterior de "nenhuma tratativa automática" nesse
cenário estava incompleta, corrigido aqui).

**Remoção executada de fato**: sem sinal automático confirmado até agora
(ver "Alimentação da planilha" acima) — resolvido via controle manual do
atendente, ver seção abaixo.

### REGRA_REMOÇÃO_TITULARIDADE — divergência de remoção + troca de titularidade (fechada, composta, 2026-08-06)

Dispara quando as duas condições de `REGRA_REMOÇÃO_ATIVA` batem
(`Serviço` indica remoção **e** chassi encontrado em "Rastreadores
Ativos") **e**, além disso, o nome do cliente diverge entre
`Nome Associado` (Instalação-Remoção) e `Cliente` (Rastreadores Ativos,
mesmo chassi). Vence sobre `REGRA_REMOÇÃO_ATIVA` isolada nesse caso
(mesma cascata "mais específica vence" de `REGRA_PRAZO_E_RISCO`) —
gera **uma tratativa só**, nunca duas.

Motivo de ser uma tratativa só (explicado pelo usuário): se houve troca
de titularidade, a remoção pendente foi solicitada pro proprietário
**antigo** — mas se o rastreador está ativo no nome de outra pessoa, a
informação pode não ter sido atualizada. Juntar numa tratativa só evita
que o atendente cobre uma retirada de quem já não é mais o dono do
veículo — o atendente verifica a titularidade antes de agir.

**Mesma correção de 2026-08-06** que `REGRA_REMOÇÃO_ATIVA`: o nível
também vem sempre dos dias desde `INATIVO` (3 variantes `_NORMAL`/
`_ALTA`/`_URGENTE`), não um nível 2 fixo.

**Bloco B (2026-08-24)**: deixou de virar tratativa/entrar em
"Tratativas" — vai pra aba nova "Análise de Divergência - Remoção"
(motivo "Titularidade divergente"). Tem precedência sobre o filtro de
modelo abaixo (decisão fechada): se titularidade diverge, o motivo que
aparece é sempre titularidade, mesmo que o modelo do equipamento também
não esteja na lista permitida. **Sem nível de urgência** a partir de
agora (não compete mais na esteira).

### REGRA_REMOCAO_SGA_ATIVO — SGA ainda confirma ATIVO (Bloco B, 2026-08-24)

Antes, qualquer status do SGA diferente de `INATIVO` (incluindo `ATIVO`)
fazia `_classificar_remocao` descartar a linha em silêncio — nenhuma
tratativa, nenhum aviso, a pendência de remoção simplesmente não
"nascia" ainda. Achado da revisão do motor de regras: isso escondia do
atendente uma remoção pendente cujo veículo o SGA ainda confirma
`ATIVO` de verdade (não é ausência de dado, é confirmação positiva do
contrário do que a remoção pressupõe).

Agora, especificamente quando o status é **literalmente `ATIVO`**
(`STATUS_SGA_ATIVO`, não qualquer não-`INATIVO`), gera uma linha na aba
"Análise de Divergência - Remoção" em vez de descartar — sinaliza que a
remoção pendente pode estar desatualizada e pede confirmação manual no
SGA antes de prosseguir. Qualquer outro status (nem `ATIVO` nem
`INATIVO`) continua descartado em silêncio, sem mudança.

- **Sem nível de urgência** — divergência pura, mesmo espírito de
  `REGRA_INSTALACAO_JA_FEITA`, não compete na esteira de prioridade.

### REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO — modelo de equipamento fora da lista permitida (Bloco B, 2026-08-24)

Achado crítico ao desenhar esta regra: existem **2 colunas "Modelo"**
em "Rastreadores Ativos" — a posição 13 (`Modelo do VEÍCULO`, ex. "CG
160 FAN FLEX", já usada pelo critério de risco de Instalação) e a
posição 2 (`Modelo do EQUIPAMENTO/rastreador`, ex. "J16"), que nenhuma
regra usava até aqui. Esta regra é a primeira a ler a posição 2.

Uma remoção só vira tratativa (`REGRA_REMOCAO_PRAZO_*`/`REGRA_REMOCAO_
ATIVA_*`) se o modelo do equipamento estiver numa lista permitida,
configurável no Admin (`system_parameters.modelos_removiveis`, CSV de
substrings — mesmo formato de `modelos_alto_risco_furto`, mas lista
própria, exclusiva de Remoção). O filtro roda **depois** da
classificação normal (prazo/titularidade) — redireciona o destino final,
nunca bloqueia essa avaliação.

Decisões fechadas com o usuário sobre os casos-limite:
- **Lista vazia/não configurada**: bloqueia tudo até o Admin configurar
  — nenhuma remoção vira tratativa nesse meio-tempo, todas caem aqui.
- **Chassi não encontrado em Rastreadores Ativos** (`REGRA_REMOCAO_
  PRAZO_*`, situação normal/esperada de remoção): não há equipamento pra
  ler o modelo, então também bloqueia — sem confirmação do modelo, vai
  pra divergência do mesmo jeito que um modelo fora da lista. Na
  prática, `REGRA_REMOCAO_PRAZO_*` nunca chega a virar tratativa de
  fato enquanto essa lista não cobrir "sem equipamento" de alguma forma
  — é o comportamento pretendido, não um bug.
- **Titularidade divergente ao mesmo tempo**: titularidade sempre vence
  (ver `REGRA_REMOCAO_TITULARIDADE` acima).

- **Sem nível de urgência** — divergência pura, mesmo espírito das
  demais regras desta família.

## Tabela final consolidada — 18 códigos de `rule_templates` (atualizado 2026-08-24, Bloco B)

Implementados em `core/motor_regras_instalacao_remocao.py`. A cascata em
código só decide o **sufixo de tier** por dias — o nível sempre vem da
linha correspondente em `rule_templates`, nunca hardcoded.

**Instalação** (dispara enquanto chassi NÃO encontrado em Rastreadores
Ativos, exceto `REGRA_TITULARIDADE`/`REGRA_INSTALACAO_JA_FEITA`):

| codigo_regra | nível |
|---|---|
| `REGRA_PRAZO_NORMAL` | 1 |
| `REGRA_PRAZO_ATRASO` | 3 |
| `REGRA_PRAZO_CRITICO` | 4 |
| `REGRA_RISCO` | 4 |
| `REGRA_PRAZO_E_RISCO` | 5 |
| `REGRA_TITULARIDADE` (chassi já encontrado + nome diverge) | — (Bloco B, 2026-08-24: aba "Análise de Divergência - Instalação", não compete mais na esteira) |
| `REGRA_INSTALACAO_JA_FEITA` (chassi já encontrado + nome bate) | — (aba própria, não compete na esteira de urgência — ver seção acima) |

**Remoção** (só depois do gating por SGA `INATIVO`/`ATIVO`, ver seção
acima; `REGRA_REMOCAO_PRAZO_*`/`REGRA_REMOCAO_ATIVA_*` só chegam a virar
tratativa se o modelo do equipamento passar no filtro de
`modelos_removiveis`, ver `REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO`
abaixo):

| codigo_regra | nível |
|---|---|
| `REGRA_REMOCAO_PRAZO_NORMAL` | 1 |
| `REGRA_REMOCAO_PRAZO_ALTA` | 3 |
| `REGRA_REMOCAO_PRAZO_URGENTE` | 5 |
| `REGRA_REMOCAO_ATIVA_NORMAL` | 1 |
| `REGRA_REMOCAO_ATIVA_ALTA` | 3 |
| `REGRA_REMOCAO_ATIVA_URGENTE` | 5 |
| `REGRA_REMOCAO_TITULARIDADE_NORMAL` | — (Bloco B, 2026-08-24: aba "Análise de Divergência - Remoção") |
| `REGRA_REMOCAO_TITULARIDADE_ALTA` | — (idem) |
| `REGRA_REMOCAO_TITULARIDADE_URGENTE` | — (idem) |
| `REGRA_REMOCAO_SGA_ATIVO` | — (Bloco B, 2026-08-24: aba própria, não compete na esteira) |
| `REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO` | — (idem) |

## Regras comuns (aplicam a Instalação e Remoção)

(nenhuma ainda)

## Painel operacional — controles manuais (checkboxes)

Movido pra `docs/planilha_operacional.md` (2026-08-06) — a estrutura da
planilha Operacional passou a ter documento próprio, porque vale pras 3
origens (`instalacao`/`remocao`/`manutencao`), não é específica desta
fase. Ver esse arquivo pro desenho completo dos checkboxes e pra uma
proposta de redesenho de `Tratativas` que pode **substituir** um dos
checkboxes abaixo ("Retirada confirmada") — ainda em aberto, não fechado.
