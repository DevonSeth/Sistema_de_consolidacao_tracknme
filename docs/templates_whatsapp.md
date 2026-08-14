# Templates de WhatsApp (Newmo/Zapio) — Fase F.1

Documento vivo, mesmo espírito de `docs/regras_negocio.md`/`docs/planilha_operacional.md`. Cobre a lógica de qual template disparar (`etapa_disparo_mensagens`, implementada) e o catálogo de textos submetidos pra aprovação da Meta. Construído junto com o usuário, regra por regra, antes de qualquer código.

**Status (2026-08-11, atualizado): F.1 e F.2 100% completas. Os 14 templates estão aprovados pela Meta, e os 14/14 foram validados ao vivo de ponta a ponta** (disparo real → resposta real no WhatsApp → webhook grava certo no Supabase) — inclui `pendencia_remocao_acao_confirmar` (`1921`) e a recriação `pendencia_remocao_rota_agendar` (`1923`, substitui o antigo `pendencia_remocao_rota_normal`/`1900` que recusava disparo com `cod=10` mesmo aprovado no painel — recriar com nome novo resolveu, mesma estratégia usada no `1921`). **`config["newmo"]["templates"]` foi populado nesta sessão** (12 chaves, os templates automáticos — os 2 de confirmação de agendamento continuam manuais, ver seção 6) — **o disparo automático de WhatsApp está liberado pra produção a partir de agora**. Ver `_handoff/HANDOFF.md` pro estado completo, mais detalhado e atualizado com mais frequência que este documento.

## 1. Contexto e objetivo

Cada pendência (Manutenção, Instalação ou Remoção) publicada em `Tratativas` passa por até 3 tentativas de contato via WhatsApp antes de escalar pra ligação. A pergunta central desta fase: **qual template disparar em cada tentativa**, de um jeito que maximize a chance do associado responder, sempre dentro da categoria **Utility** (mais barata, e a única que se aplica — não é uma mensagem de marketing).

## 2. Categoria Utility — princípios que todo template segue

Utility exige que a mensagem seja sobre uma **transação/relação já existente**, nunca uma oferta nova. Nosso caso se encaixa bem (sempre é sobre um serviço já contratado/equipamento já cedido), mas o texto precisa reforçar isso:

- **Sempre sobre uma pendência real e específica daquela conta** — nunca um disparo genérico. Por isso toda mensagem é personalizada (nome, placa).
- **Sem tom de campanha**: sem "aproveite", sem oferta, sem emoji em excesso, sem urgência artificial.
- **"Última chamada" comunica consequência real, sem ameaça** — factual, nunca alarmista (ver histórico de revisão de tom na seção 5.2).
- **Introdução sempre presente**: toda mensagem se identifica ("Somos a Viver de Rastreamento, responsável pelo setor de Rastreamento da Puma Proteção Veícular") — reforça que é comunicação de conta, não spam, e ajuda a manter a classificação.

## 3. Colunas novas na planilha Operacional

### `Atendimento` (dropdown: `Base` / `Em rota` / `Ação`)

- Existe em `Tratativas` **e** `Pendente de Ligação`.
- Em `Tratativas`: **obrigatória antes do disparo** — sem ela, o sistema não sabe qual template usar, não dispara. Preenchida pelo atendente, com base no conhecimento da geografia do associado (decisão 100% manual, sem cálculo automático).
- **Trava durante as 3 tentativas de WhatsApp** — não muda no meio da esteira de mensagens.
- Em `Pendente de Ligação`: **editável de novo** — na ligação o atendente pode descobrir que o tipo certo é outro (ex: descobriu que o associado não pode ir à base, é caso de rota).
- Quando `Atendimento = Base`, o atendente também escolhe **qual base específica** (dropdown, alimentado pelo cadastro de Bases — ver seção 4). Quando `Atendimento = Ação`, escolhe **qual ponto de ação**. Nome exato dessas colunas de seleção ainda não fechado — ver seção 10.

### `Retorno do Associado` (só `Tratativas`)

Preenchida **automaticamente pelo webhook** quando o associado clica no botão de discrepância (ver seção 5.4/6). Valores possíveis (texto curto e fixo por caso):

- `"Instalado — associado confirma, revisar"` (família Instalação/Manutenção)
- `"Retirado — associado confirma, revisar"` (família Remoção)

**Correção 2026-08-10**: Instalação/Manutenção tinha 2 casos de discrepância (`"sem acesso ao app"` e o acima) — o botão `"Sem acesso ao app"` foi removido (ver seção 5.4, correção de botões), então só resta 1 caso, igual à Remoção. `"Instalado — sem acesso ao app"` não existe mais como valor possível.

**Efeito**: enquanto preenchida, a esteira automática de disparo **pausa** pra aquela pendência (mesmo espírito de `Situação Manual` — não sai da aba, só para de mandar mais mensagem). O sistema **nunca corrige o cadastro sozinho** — só entrega o dado; um humano valida e decide (corrigir manualmente, marcar `Finalizado`, ou liberar a esteira de novo).

Só existe em `Tratativas` porque os botões de discrepância só aparecem nos templates de 1º contato (WhatsApp automático) — `Pendente de Ligação` já é fase de ligação humana, sem esse mecanismo.

## 4. Cadastros futuros — Bases e Pontos de Ação

**Ainda não existem em lugar nenhum do sistema** — hoje as bases só existem informalmente (a empresa sabe onde ficam, mas não há registro). Precisam ser criados como parte do painel de controle (`ui/`), que será construído logo depois desta fase (F.1 → `ui/`, ordem já combinada).

- **Bases**: nome, endereço, ativo/inativo. CRUD (adicionar/remover/ativar/desativar) pelo atendente, direto no painel.
- **Pontos de Ação**: nome/local, endereço, **data editável** (não nasce com data fixa — o atendente define/atualiza a data sempre que agenda uma nova ocorrência ali), ativo/inativo. Mesmo CRUD.

**Nota de escopo**: isso amplia o que `ui/` tinha sido desenhado como ("só roda/monitora etapas do pipeline", ver `_handoff/HANDOFF.md`) — passa a incluir também esses 2 cadastros. Atualizar o HANDOFF quando chegarmos na fase do painel.

## 5. Catálogo — templates de 1º contato (automáticos, 12 templates)

### 5.1 Estrutura

**2 famílias de origem** × **3 atendimentos** × **2 tons** = 12.

- **Famílias de origem**: `instalacao_manutencao` (corpo compartilhado, variável de tipo de serviço) / `remocao` (corpo próprio — a alegação "protege contra roubo/furto" não se aplica a uma retirada, usa o argumento do **comodato** em vez disso).
- **Atendimentos**: `base` / `rota` / `acao` — decidem o CTA final e os botões de agendamento.
- **Tons**: `normal` (tentativas 1 e 2, mesmo template reaproveitado) / `ultima` (tentativa 3, tom mais urgente, sempre orientado a benefício — nunca ameaça).

**Não foi possível mesclar o "tom" numa variável só** (economizaria 6 templates) — a política do WhatsApp trata variável como substituição de dado, não como troca de intenção/urgência da mensagem; usar variável pra isso arrisca rejeição/perda de categoria na aprovação.

### 5.2 Histórico de revisão de tom (registro, não repetir o erro)

A primeira versão da "última chamada" de Remoção usava linguagem de consequência ("encaminhado para tratamento formal", "posse indevida") — o usuário considerou agressivo demais. Reescrito 2x até chegar num tom que comunica urgência **através do benefício de resolver rápido** ("seu nome fica livre de qualquer pendência"), nunca através de ameaça. Esse princípio vale pra qualquer template futuro desta família.

### 5.3 Variáveis por combinação

| Família | Atendimento | Variáveis (nesta ordem) |
|---|---|---|
| Instalação/Manutenção | Base | `{{1}}` nome, `{{2}}` tipo de serviço, `{{3}}` placa, `{{4}}` endereço da base — **exceção: tom "última chamada" tem só `{{1}}`/`{{2}}`/`{{3}}`, sem endereço (ver seção 10 item 5 — mudança pra sair da classificação Marketing)** |
| Instalação/Manutenção | Rota | `{{1}}` nome, `{{2}}` tipo de serviço, `{{3}}` placa |
| Instalação/Manutenção | Ação | `{{1}}` nome, `{{2}}` tipo de serviço, `{{3}}` placa, `{{4}}` data do ponto de ação, `{{5}}` endereço do ponto de ação |
| Remoção | Base | `{{1}}` nome, `{{2}}` placa, `{{3}}` endereço da base |
| Remoção | Rota | `{{1}}` nome, `{{2}}` placa |
| Remoção | Ação | `{{1}}` nome, `{{2}}` placa, `{{3}}` data do ponto de ação, `{{4}}` endereço do ponto de ação |

**Placa**: quando ausente, ou quando for uma "placa fictícia" (regra fechada, ver seção 10), usa `Modelo` do veículo no lugar.

Rota não recebe variável de endereço no 1º contato — o técnico vai até o endereço já conhecido do associado; a confirmação do endereço certo acontece no template de agendamento (seção 6), como prova por escrito.

### 5.4 Botões (após restrição de 3 botões por template — ver seção 9)

| Família | Atendimento | Botões |
|---|---|---|
| Instalação/Manutenção | Base (tom normal) | `Quero agendar` / `Não consigo ir até a base` / `Já foi realizado` |
| Instalação/Manutenção | Base (tom última chamada) | `Quero agendar` / `Não vou conseguir` / `Já foi realizado` — **exceção, ver seção 5.5**: botão de escape sem "base", tentativa de sair da classificação `Marketing` |
| Instalação/Manutenção | Rota | `Agendar visita` / `Já foi realizado` |
| Instalação/Manutenção | Ação | `Confirmar presença` / `Não vou conseguir` / `Já foi realizado` |
| Remoção | Base | `Quero agendar` / `Não posso ir à base` / `Já foi realizado` |
| Remoção | Rota | `Agendar visita` / `Já foi realizado` |
| Remoção | Ação | `Confirmar presença` / `Não vou conseguir` / `Já foi realizado` |

**Correção 2026-08-10** (revertendo uma decisão anterior desta mesma sessão): Instalação/Manutenção tinha perdido o botão de escape (logística) pra caber 2 casos de discrepância — o usuário corrigiu isso ao revisar o catálogo já pronto: `"Sem acesso ao app"` **não deve existir**. Instalação/Manutenção agora tem a mesma estrutura de botões da Remoção, atendimento por atendimento (Base/Ação com escape + "Já foi realizado"; Rota só com "Já foi realizado", sem escape) — só 1 caso de discrepância nas duas famílias agora (ver seção 3, `Retorno do Associado`).

Clicar em `Já foi realizado` grava `Retorno do Associado` (seção 3) e pausa a esteira — nunca fecha/corrige nada sozinho.

**Textos de botão, histórico de ajustes**:
- "Já foi realizado" (16 caracteres) — nasceu como "Corrigir cadastro" (18 caracteres), passou por "Já foi realizado - Corrigir cadastro" (37 caracteres, rejeitado por ser longo demais) até fechar nesse texto — fala na voz do associado que clica o botão, não na ação interna do sistema.
- "Não consigo ir até a base" (~25 caracteres) e "Não vou conseguir" (17 caracteres) — textos dos botões de escape de Instalação/Manutenção, espelhando os já usados em Remoção. **Indício favorável sobre o limite de caracteres** (item 1 da seção 10, ainda sem confirmação oficial): os 14 templates foram submetidos à Meta com esses textos, incluindo o de 25 caracteres, sem nenhuma rejeição por tamanho de botão até agora — sugere que o limite real é maior que os ~20 caracteres assumidos por precaução, mas não é uma confirmação oficial (a Meta pode rejeitar por outro motivo depois, ou o limite pode não ser sobre tamanho de texto).

### 5.5 Textos — família Instalação/Manutenção

**Tom normal (tentativas 1 e 2):**

> Olá, {{1}}! Somos a Viver de Rastreamento, responsável pelo setor de Rastreamento da Puma Proteção Veícular.
>
> Identificamos que existe o serviço de {{2}} pendente para o veículo placa {{3}}, que possui o *Rastreador Obrigatório* para garantir a proteção contra Roubo/Furto.

- **+ Base** (`pendencia_instalacao_manutencao_base_normal`): " Você pode resolver isso na nossa base: {{4}}." → *"Para agendar, selecione uma opção abaixo:"*
- **+ Rota** (`pendencia_instalacao_manutencao_rota_normal`): (sem adição) → *"Para agendar a visita do técnico, selecione uma opção abaixo:"*
- **+ Ação** (`pendencia_instalacao_manutencao_acao_normal`): " Estaremos atendendo na sua região no dia {{4}}, no endereço {{5}}." → *"Para confirmar presença, selecione uma opção abaixo:"*

**Tom última chamada (tentativa 3) — Ação e Rota:**

> Olá, {{1}}! Somos a Viver de Rastreamento, responsável pelo setor de Rastreamento da Puma Proteção Veícular.
>
> Esta é nossa última tentativa de contato sobre o serviço de {{2}} pendente para o veículo placa {{3}}, que possui o *Rastreador Obrigatório* para garantir sua proteção contra Roubo/Furto.

- **+ Ação** (`pendencia_instalacao_manutencao_acao_ultima`): " Estaremos atendendo na sua região no dia {{4}}, no endereço {{5}}."
- **+ Rota** (`pendencia_instalacao_manutencao_rota_ultima`): (sem adição)

> Queremos ajudar a resolver isso de forma rápida e simples — assim sua proteção fica garantida sem nenhuma pendência.

(mesma linha de CTA + botões do tom normal, por atendimento)

**Tom última chamada (tentativa 3) — Base, texto próprio (5ª tentativa, 2026-08-10)**:
as 4 anteriores (`pendencia_instalacao_manutencao_base_ultima`, `pendencia_instalacao_e_manutencao_base_ultima`,
`pendencia_instalacao_manutencao_base_final`, `pendencia_instalacao_manutencao_base_ultimo_aviso`) caíram
como `Marketing` na Meta — a 4ª já sem nenhuma menção de endereço, texto estruturalmente idêntico ao de
Rota (que passou), então o problema não é o CTA nem o endereço. Confirmado com o usuário: a categoria
"Utilidade" está selecionada corretamente antes de cada envio, e cada template é criado do zero (não é
herança de categoria de uma cópia). Pesquisa direta na documentação oficial da Meta (ver seção 10 item 5)
apontou a causa mais provável: **qualquer frase que referencie um benefício do produto, mesmo
indiretamente, é tratada como conteúdo de Marketing — e "mistura" derruba o template inteiro, mesmo que o
resto seja 100% transacional.** As frases "para garantir sua proteção contra Roubo/Furto" (intro) e "assim
sua proteção fica garantida" (fechamento) — compartilhadas com Ação/Rota, que passaram — são exatamente
esse tipo de conteúdo; a classificação por ML não é 100% determinística, então é plausível que só nessa
combinação específica o sinal acumulado cruze o limite. **Por isso, só o texto de Base (não Ação/Rota, que
já estão aprovados) fica com texto próprio, sem nenhuma linguagem de benefício**:

> Olá, {{1}}! Somos a Viver de Rastreamento, responsável pelo setor de Rastreamento da Puma Proteção Veícular.
>
> Esta é nossa última tentativa de contato sobre o serviço de {{2}} pendente para o veículo placa {{3}}.
>
> Para agendar, selecione uma opção abaixo:

- **+ Base** (`pendencia_instalacao_manutencao_ultimo_aviso` — nome novo, sem "base", pra eliminar mais uma
  variável possível): variáveis `{{1}}`/`{{2}}`/`{{3}}` (sem endereço, mesmo padrão da 4ª tentativa).
  **Botões também mudam nesta variação**: `Quero agendar` / `Não vou conseguir` / `Já foi realizado` — o
  botão de escape deixa de mencionar "base" (era `Não consigo ir até a base`), reaproveitando o texto já
  aprovado em Ação (`Não vou conseguir`) em vez de inventar um novo.

### 5.6 Textos — família Remoção

**Tom normal (tentativas 1 e 2) — aprovado pelo usuário:**

> Olá, {{1}}! Somos a Viver de Rastreamento, responsável pelo setor de Rastreamento da Puma Proteção Veícular.
>
> Identificamos que o equipamento de rastreamento instalado no veículo placa {{2}} está em regime de *comodato* — ou seja, é um bem da Puma, cedido a você apenas enquanto durou o vínculo com a cooperativa. Como esse vínculo foi encerrado, é necessário agendar a retirada do equipamento o quanto antes, para regularizar sua situação e evitar problemas contratuais decorrentes da posse de um bem que não é seu.

- **+ Base** (`pendencia_remocao_base_normal`): " Você pode comparecer à nossa base: {{3}}." → *"Para agendar, selecione uma opção abaixo:"*
- **+ Rota** (`pendencia_remocao_rota_agendar` — **recriado 2026-08-11 no lugar do antigo `pendencia_remocao_rota_normal`/`1900`, aprovado e validado ao vivo, ver seção 10 item 5**): (sem adição) → *"Para agendar a visita do técnico, selecione uma opção abaixo:"*
- **+ Ação** (`pendencia_remocao_acao_confirmar` — **nome renomeado em 2026-08-11**: o original (`pendencia_remocao_acao_normal`) ficou travado "pendente" de revisão da Meta por muito tempo, mesmo templates criados depois já aprovados — recriado com nome novo pra tentar sair de uma fila trancada, mesmo conteúdo, motivo exato não confirmado): " Estaremos atendendo na sua região no dia {{3}}, no endereço {{4}}." → *"Para confirmar presença, selecione uma opção abaixo:"*

**Tom última chamada (tentativa 3) — aprovado pelo usuário após 2 revisões de tom:**

> Olá, {{1}}! Somos a Viver de Rastreamento, responsável pelo setor de Rastreamento da Puma Proteção Veícular.
>
> Esta é nossa última tentativa de contato sobre a retirada do equipamento de rastreamento do veículo placa {{2}}, cedido em regime de *comodato* durante seu vínculo com a cooperativa.

- **+ Base** (`pendencia_remocao_base_ultima`): " Você pode comparecer à nossa base: {{3}}."
- **+ Ação** (`pendencia_remocao_acao_ultima_tentativa` — **nome renomeado em 2026-08-10**: o nome original (`pendencia_remocao_acao_ultima`) foi rejeitado/colidiu no cadastro do Newmo, motivo exato não confirmado): " Estaremos atendendo na sua região no dia {{3}}, no endereço {{4}}."
- **+ Rota** (`pendencia_remocao_rota_ultima`): (sem adição)

> Queremos ajudar a resolver isso de forma rápida e simples, sem burocracia — assim seu nome fica livre de qualquer pendência relacionada ao equipamento. Vamos agendar agora?

(mesma linha de CTA + botões do tom normal, por atendimento)

**Nota**: a cláusula de endereço/data (Base/Ação) e a intro na "última chamada" foram adicionadas nesta consolidação — não estavam explícitas nas primeiras versões aprovadas em chat. Confirmar com o usuário antes de considerar fechado.

## 6. Catálogo — confirmação de agendamento (manual, 2 templates)

Disparado **manualmente pelo atendente**, no meio da conversa humana (depois que uma data foi combinada) — não é automático pelo pipeline. Sem introdução (já está em conversa). O sistema sabe que esse template específico foi enviado; quando o associado confirma pelo botão, o **webhook** grava a confirmação (`Data Agendada`, status) na planilha/Supabase.

**2 templates**: um cobre Base+Ação (ambos têm um endereço fixo pra ir), outro cobre Rota (técnico vai até o associado).

Variáveis: `{{1}}` nome, `{{2}}` placa, `{{3}}` data, `{{4}}` endereço — o endereço aparece **mesmo no de Rota**, como prova escrita de que o endereço passado está correto.

**Base + Ação** (`confirmacao_agendamento_base_acao`):

> Confirmando, {{1}}: seu agendamento referente ao veículo placa {{2}} ficou marcado para o dia {{3}}, no endereço {{4}}.
>
> Podemos confirmar?

Botões: `Confirmar` / `Mudar a data`

**Rota** (`confirmacao_agendamento_rota`):

> Confirmando, {{1}}: nosso técnico irá até você no dia {{3}}, no endereço {{4}}, para o serviço no veículo placa {{2}}.
>
> Podemos confirmar?

Botões: `Confirmar` / `Mudar a data`

(botões encurtados pra caber no limite de caracteres — ver seção 10)

## 7. Convenção de nomes (nomes reais, já cadastrados no Newmo)

```
pendencia_instalacao_manutencao_base_normal
pendencia_instalacao_manutencao_ultimo_aviso       (5ª tentativa, ver seção 5.5/10 — texto próprio sem linguagem de benefício, nome sem "base")
pendencia_instalacao_manutencao_rota_normal / _ultima
pendencia_instalacao_manutencao_acao_normal / _ultima
pendencia_remocao_base_normal / _ultima
pendencia_remocao_rota_agendar (recriado 2026-08-11, aprovado e validado ao vivo — era pendencia_remocao_rota_normal) / _ultima
pendencia_remocao_acao_confirmar                   (renomeado 2026-08-11, ver seção 5.6 — era pendencia_remocao_acao_normal)
pendencia_remocao_acao_ultima_tentativa            (renomeado, ver seção 5.6)
confirmacao_agendamento_base_acao
confirmacao_agendamento_rota
```

14 templates no total, todos já submetidos ao Newmo/Meta (2026-08-10) com esses nomes exatos — não são mais só uma sugestão de código, são os nomes reais cadastrados. **Nomes colocados inline em cada template do catálogo (seções 5.5, 5.6 e 6)** — mesmo princípio de antes, pra deixar claro qual nome mapeia pra qual texto na hora de usar `template.codigo` (`integrations/newmo_client.py`). O `template.codigo` numérico de cada um é a coluna "SEQUÊNCIA" do painel do Newmo — não existe endpoint na API pra consultar isso, só o próprio painel (ver seção 9).

## 9. Ciclo de vida do atendimento no Newmo — quando finalizar

**Achado ao vivo em 2026-08-10** (testando `enviar_template` contra vários templates já aprovados, pro mesmo número de teste): existe um código de retorno da Newmo não documentado no Swagger original — `cod=13`, `"Já existe um atendimento pendente ou em andamento para este contato"`. Enquanto o atendimento aberto por um envio anterior não é finalizado (`POST /atendimento/finalizar`), **qualquer novo envio de template pro mesmo contato falha** — mesmo com `ignorarSePossuiAtendimento=True` (o motivo exato desse flag não bloquear isso não foi confirmado com o suporte da Newmo). Isso afeta diretamente a esteira de 3 tentativas: sem finalizar entre uma tentativa e outra, a 2ª/3ª tentativa da mesma pendência falharia.

**Regra fechada com o usuário**: finalizar o atendimento **uma vez por dia, no fim do ciclo diário** (não imediatamente após cada envio) — não uma etapa por disparo. Isso permite que o atendimento fique aberto durante o dia pra correlacionar a resposta do associado (via webhook, F.2) e só fecha à noite quem realmente não vai receber mais WhatsApp automático:

- **Finalizar** quando a tratativa ainda estaria elegível pra um novo disparo automático (mesmos critérios de `core.escalonamento.elegivel_para_disparo`, exceto a checagem de horário/já-disparou-hoje, que é sobre *quando* e não se aplica aqui): `tentativas < 3`, `status` ainda em `pendente`/`aguardando_resposta` (sem nenhuma resposta), sem `Situação Manual` preenchida, sem `Retorno do Associado` pendente de revisão.
- **Não finalizar** (não é proibido, só não é urgente — nenhum desses 3 casos vai receber WhatsApp automático de novo):
  - **Respondeu** (`status=respondido`, via webhook — qualquer resposta, botão ou texto livre, já move o status e já tira da elegibilidade de disparo, então não precisa distinguir qual botão foi clicado).
  - **Situação Manual preenchida** (ex: efetivamente `Agendado`).
  - **Já escalou pra ligação** (`tentativas=3`, foi pra `Pendente de Ligação`/F.3) — o canal virou ligação, não WhatsApp.

**Implementado**: `finalizar_atendimento()` existe em `integrations/newmo_client.py` (validado ao vivo em 2026-08-10, ver `_handoff/HANDOFF.md`) e a rotina noturna que decide quem finalizar é `orchestrator.pipeline.etapa_finalizar_atendimentos_diarios` (Passo 6.4 de F.1, implementada — critério desta seção aplicado literalmente em `_deve_finalizar_atendimento`). Só cobertura unitária até agora — ainda não validada ao vivo contra produção (mesma situação do resto de F.1, bloqueada pelos 2 templates pendentes de aprovação da Meta).

**Confirmação de agendamento (seção 6) — decisão de escopo**: por enquanto é enviada **direto na tela de chat do painel do Newmo pelo atendente**, sem passar pela nossa API — não corre risco do `cod=13` (o atendimento já está aberto e em uso pelo próprio atendente humano). O usuário mencionou a ideia futura de um agente de atendimento automatizado que enviaria isso por conta própria (fora do escopo atual, só registrado aqui pra não se perder).

## 10. Status — todas as decisões de negócio fechadas

O catálogo deste documento está **completo e fechado com o usuário** (sessão de 2026-08-10): os 14 modelos, a lógica de botões, a coluna `Retorno do Associado`, e a regra de placa fictícia. Restam só itens técnicos de implementação (não decisões de negócio):

1. **Limite de caracteres do botão de resposta rápida** — os textos da seção 5.4/6 já foram encurtados assumindo um limite curto (memória do Claude: algo perto de 20 caracteres — **ainda sem confirmação oficial**). **Indício favorável em 2026-08-10**: os 14 templates foram submetidos com um botão de 25 caracteres ("Não consigo ir até a base") sem nenhuma rejeição por tamanho até agora — sugere que o limite real é maior, mas não é uma confirmação oficial do WhatsApp Business Manager.
2. **RESOLVIDO (2026-08-10, Passo 2 de F.1)** — nomes das colunas de seleção de Base/Ponto de Ação específicos: `tratativas.base_id` e `tratativas.ponto_acao_id` (uuid, nullable, cada uma com FK real pra `bases.id`/`pontos_acao.id`) — só uma preenchida por vez, dependendo do valor de `atendimento`. Escolhido em vez de uma coluna genérica única porque o banco garante de verdade que o id aponta pra tabela certa (constraint de FK), não só a aplicação confiando que está certo. Ver SQL completo em `_handoff/HANDOFF.md`, seção do Passo 2.
3. **Regra de identificação de "placas fictícias"** — **RESOLVIDO** (confirmado pelo usuário 2026-08-10). Regra: uma placa é fictícia quando **começa com `SGA`** (ex: `SGA0612`, `SGA17076`) **ou** quando **não bate com o padrão antigo (`LLLNNNN`) nem o Mercosul (`LLLNLNN`)**, depois de normalizada (sem espaço/hífen/ponto, maiúscula). A checagem de "começa com SGA" precisa vir **antes** da checagem de padrão — confirmado ao vivo contra a planilha real (aba "Rastreadores Ativos", coluna `Placa`, 12.241 linhas): existem placas `SGA` de 4 dígitos (ex: `SGA0612`) que colam por acaso no formato do padrão antigo (3 letras + 4 números) e passariam batido só com a checagem de padrão.

   **Achado ao investigar** (não é regra nova, só confirma que a regra acima já cobre o caso): 156 das 215 placas fora do padrão são o **próprio chassi copiado** no campo de placa (112 idênticas, 43 truncadas, 1 abreviada) — não precisa de lógica extra pra detectar isso, a checagem de padrão já classifica como fictícia sem precisar comparar com a coluna de chassi. Existe hoje um parâmetro `placas_genericas` em `system_parameters` (Supabase) — é uma lista de strings exatas (`CHASSI,SEM PLACA,0000000`), mecanismo diferente (lista fixa vs. os 2 padrões daqui, que exigem checagem de formato) — os dois convivem, não se substituem.
4. **Cláusulas de endereço/data e intro na última chamada** (nota da seção 5.6) — adicionadas na consolidação de 2026-08-10, sem objeção do usuário depois de revisar o artifact publicado com esse conteúdo — considerar confirmado.
5. **Aprovação da Meta — CONCLUÍDA, 14/14 templates aprovados e
   validados ao vivo de ponta a ponta (2026-08-11)**.
   `pendencia_instalacao_manutencao_acao_ultima` (faltava por
   completo) criado, submetido e aprovado. `pendencia_remocao_
   acao_normal` ficou travado "pendente" por muito tempo (mesmo
   templates criados depois já aprovados) — recriado como
   `pendencia_remocao_acao_confirmar` (sequência `1921`); **aprovado
   pela Meta e validado ao vivo** (disparo real → resposta real "Já
   foi realizado" → `retorno_associado` gravado certo → atendimento
   finalizado → tratativa de teste limpa).

   `pendencia_remocao_rota_normal` (`1900`), já `Aprovado` no painel,
   recusou um disparo real com `cod=10 "O template não foi aprovado
   pela Meta"` — hipótese: editar o Setor do template no painel pode
   ter resincronizado/reaberto revisão internamente no Newmo sem
   atualizar o status mostrado na tela; motivo exato não confirmado.
   **Decisão**: em vez de investigar mais o motivo exato, recriado do
   zero com nome novo — mesma estratégia que resolveu a fila trancada
   do `1921`. Recriado como **`pendencia_remocao_rota_agendar`**
   (sequência `1923`), mesmo conteúdo — **aprovado pela Meta e
   validado ao vivo** (disparo real → resposta real "Já foi
   realizado" → `retorno_associado = "Retirado — associado confirma,
   revisar"` → atendimento finalizado → tratativa de teste limpa). O
   `pendencia_remocao_rota_normal`/`1900` antigo foi excluído do
   painel do Newmo pelo usuário (mesmo destino dos templates
   anteriores que ficaram travados).

   **`config["newmo"]["templates"]` populado (2026-08-11)** — os 12
   templates automáticos (nome lógico interno, ver `core/mensagens.
   py::nome_template_logico`, → sequência real no Newmo):

   | nome lógico | sequência |
   |---|---|
   | `instalacao_manutencao_base_normal` | `1893` |
   | `instalacao_manutencao_base_ultima` | `1920` (`..._ultimo_aviso`) |
   | `instalacao_manutencao_rota_normal` | `1895` |
   | `instalacao_manutencao_rota_ultima` | `1896` |
   | `instalacao_manutencao_acao_normal` | `1897` |
   | `instalacao_manutencao_acao_ultima` | `1909` |
   | `remocao_base_normal` | `1898` |
   | `remocao_base_ultima` | `1899` |
   | `remocao_rota_normal` | `1923` (`..._rota_agendar`) |
   | `remocao_rota_ultima` | `1901` |
   | `remocao_acao_normal` | `1921` (`..._acao_confirmar`) |
   | `remocao_acao_ultima` | `1904` (`..._ultima_tentativa`) |

   Os 2 templates de confirmação de agendamento (`confirmacao_
   agendamento_base_acao`/`1905`, `confirmacao_agendamento_rota`/
   `1906`) ficam de fora de propósito — são enviados manualmente pelo
   atendente direto no painel do Newmo (seção 6/9), não por
   `enviar_template`. **A partir de agora, `etapa_disparo_mensagens`
   dispara mensagens de verdade pra associados reais quando o
   orchestrator rodar** — deixou de ser código pronto-mas-inerte.

   **Achado — a "última chamada + Base" de Instalação/Manutenção caiu
   como `Marketing` QUATRO vezes seguidas**, com 4 conteúdos diferentes:
   1. `pendencia_instalacao_manutencao_base_ultima` — *"Você pode
      resolver isso na nossa base: {{4}}."*
   2. `pendencia_instalacao_e_manutencao_base_ultima` (recriado, mesmo
      texto) — mesmo resultado.
   3. `pendencia_instalacao_manutencao_base_final` (texto reescrito,
      tirando o tom de convite) — *"A base mais próxima para
      regularizar essa pendência fica em {{4}}."* — caiu de novo.
   4. `pendencia_instalacao_manutencao_base_ultimo_aviso` (sem nenhuma
      menção de endereço, estruturalmente idêntico ao texto de Rota, que
      passou) — **caiu como `Marketing` de novo**, o que descarta
      qualquer teoria baseada só no corpo da mensagem (não havia mais
      diferença de conteúdo relevante entre esse texto e o de Rota).

   **Duas causas alternativas descartadas com o usuário**: categoria
   errada na hora de submeter (`Utilidade` confirmado selecionado nas 4
   tentativas) e herança de categoria de um template duplicado (usuário
   confirmou que cria cada template do zero, nunca duplicando).

   **Pesquisa direta na documentação oficial da Meta** (`developers.
   facebook.com/documentation/business-messaging/whatsapp/templates/
   template-categorization`) e em artigos de suporte de provedores
   (WANotifier, MSG91, Spur, InsiderOne) — achado que nenhuma das 4
   tentativas tinha testado: **qualquer template que misture conteúdo de
   Utility com conteúdo de Marketing é classificado inteiro como
   Marketing — uma única frase promocional derruba o template inteiro**,
   e frases que "referenciam um benefício pro usuário, mesmo que
   indiretamente" contam como esse tipo de conteúdo. As frases
   compartilhadas com Ação/Rota (que passaram) — *"para garantir sua
   proteção contra Roubo/Furto"* (intro) e *"assim sua proteção fica
   garantida"* (fechamento) — são exatamente esse tipo de linguagem de
   benefício. A classificação por ML não é 100% determinística (mesmo a
   documentação de terceiros descreve inconsistência em casos
   legitimamente transacionais) — plausível que só nessa combinação
   específica o sinal acumulado cruze o limite, mesmo a frase sendo
   idêntica em templates-irmãos que passaram.

   **Decisão (2026-08-10, depois da 4ª rejeição, usuário optou por
   corrigir em vez de abrir chamado de suporte): dar um texto PRÓPRIO
   pra "última chamada + Base"**, sem nenhuma linguagem de benefício —
   diferente de Ação/Rota, que mantêm o texto original (já aprovados,
   não mexer). Ver texto novo completo na seção 5.5. Também: nome novo
   sem "base" (`pendencia_instalacao_manutencao_ultimo_aviso`) e botão de
   escape sem "base" (`Não vou conseguir`, reaproveitando o texto já
   aprovado em Ação em vez de inventar um novo) — elimina mais 2
   variáveis possíveis de uma vez, já que testar uma mudança por vez
   (4 tentativas, cada uma custando um ciclo de revisão da Meta) não
   estava convergindo.

   `pendencia_instalacao_e_manutencao_base_ultima` (sequência `1908`),
   `pendencia_instalacao_manutencao_base_final` e `pendencia_instalacao_
   manutencao_base_ultimo_aviso` (as 3 tentativas anteriores) foram
   **excluídas do painel do Newmo pelo usuário** — nenhuma chegou a
   entrar em `config["newmo"]["templates"]`, exclusão não afeta código
   nenhum.

   **Status na época (histórico — ver início da seção 10 e desta seção 5
   pro estado atual real)**: 12/14 aprovados corretamente como
   `Utilidade`, 2 pendentes de revisão (`pendencia_remocao_acao_normal`
   e o novo `pendencia_instalacao_manutencao_ultimo_aviso`), 0 com
   categoria errada, 0 faltando criar. Se esta 5ª tentativa também
   caísse como `Marketing`, a hipótese de "linguagem de benefício"
   também não seria a causa raiz completa — nesse caso o próximo passo
   recomendado seria abrir chamado com o suporte do Newmo/Meta. **Não
   foi necessário**: `pendencia_instalacao_manutencao_ultimo_aviso`
   foi aprovado corretamente como `Utilidade` — a hipótese se confirmou.

   **Pronto pra colar no Newmo/Meta**:
   ```
   Nome: pendencia_instalacao_manutencao_ultimo_aviso
   Categoria: Utilidade
   Idioma: Português (BR)

   Corpo:
   Olá, {{1}}! Somos a Viver de Rastreamento, responsável pelo setor de Rastreamento da Puma Proteção Veícular.

   Esta é nossa última tentativa de contato sobre o serviço de {{2}} pendente para o veículo placa {{3}}.

   Para agendar, selecione uma opção abaixo:

   Exemplos de variável:
   {{1}} -> João da Silva
   {{2}} -> manutenção
   {{3}} -> ABC1234

   Botões (resposta rápida):
   Quero agendar
   Não vou conseguir
   Já foi realizado
   ```

   Não bloqueia o código: `integrations/newmo_client.py` (Passo 4, já
   implementado e **validado ao vivo com 13 templates reais, em 2
   números de teste diferentes**, ver `_handoff/HANDOFF.md`) e a lógica
   de seleção de template/`etapa_disparo_mensagens` (Passos 5-7, já em
   andamento — ver `_handoff/HANDOFF.md`) não precisam de todos os 14
   códigos reais pra serem escritas/testadas (unitário, com código
   mockado) — só o envio desse template específico depende do código
   real, uma vez aprovado. **Nota já aplicada em `core/mensagens.py`**:
   essa combinação (Instalação/Manutenção, Base, última chamada) passa
   só 3 variáveis, não 4 como sua contraparte "normal" — tratado como
   exceção explícita na tabela de variáveis por template, não como regra
   genérica.

**Implementado e validado ao vivo**: `integrations/newmo_client.py`
(Passo 4 de F.1, 2026-08-10) — `enviar_template`/`listar_canais`/
`listar_setores`, com retry automático pra falha temporária da própria
Newmo. Ver `_handoff/HANDOFF.md` pro detalhe completo (inclui o achado
do `cod=13`/ciclo de vida do atendimento, seção 9 acima).

**Fase F.1 completa**: Passos 5 (`core/mensagens.py`), 6.1-6.3
(`etapa_disparo_mensagens`) e 6.4 (`etapa_finalizar_atendimentos_diarios`,
rotina noturna, critério da seção 9 acima) todos implementados e
testados (370 testes) — ver `_handoff/HANDOFF.md`. Próximo passo do
projeto é F.2 (Edge Function do webhook, Supabase).
