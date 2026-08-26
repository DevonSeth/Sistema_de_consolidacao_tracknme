# POP — Operador / Atendente

Documento vivo, reescrito com detalhe completo em 2026-08-07 (a versão
anterior era um rascunho inicial de 2026-08-06) e expandido no mesmo dia
com a visão de sistema completo e um exemplo de ponta a ponta. Objetivo:
qualquer pessoa que nunca usou o sistema consegue operar como atendente
só lendo este documento — nenhum passo fica implícito, mesmo o que
pareça óbvio pra quem já conhece o projeto.

**Versão resumida dentro do próprio app** (2026-08-15): o Painel
Operador ganhou uma aba "Manual" (`ui/web/index.html`, seção
`#manual-planilha` e vizinhas) com uma versão curada deste conteúdo —
visão geral, rotina diária, como alimentar cada aba e avisos
importantes — pra consulta rápida sem precisar abrir este arquivo.
Este documento continua sendo a fonte completa/oficial; se o
comportamento do sistema mudar, atualize aqui primeiro e depois
reflita a mudança na aba Manual.

## 1. Visão geral do papel

O atendente cuida do **contato com o associado** depois que o sistema já
identificou uma pendência (manutenção, instalação ou remoção) e já
calculou a urgência dela sozinho. Em resumo, o trabalho tem 3 frentes:

1. **Priorizar e disparar** o primeiro contato (WhatsApp) pras
   pendências que valem a pena trabalhar agora.
2. **Ligar** pras que não responderam a 3 mensagens.
3. **Registrar o resultado** de cada ligação e, quando não der certo,
   deixar a pendência pronta pra virar um caso da Puma.

O sistema decide sozinho quando uma pendência nasce, qual a urgência
dela e quando ela deve avançar pra próxima etapa (ligação, Puma) — o
atendente decide **quem contatar primeiro** e **registra o que
aconteceu** em cada contato.

## 2. Como o sistema funciona por dentro, de ponta a ponta

Você não precisa fazer nada do que está descrito aqui — é só pra você
entender **de onde vem** cada pendência que aparece em `Tratativas`, e
confiar no sistema em vez de duvidar de um dado que parecer estranho
("por que essa urgência é essa?", "de onde saiu esse telefone?").

Todo dia (execução automática — hoje disparada manualmente; no futuro,
por um painel de controle), o sistema faz, nesta ordem:

1. **Baixa dois relatórios do Track N' Me** (o sistema que monitora os
   rastreadores de verdade) — "Incidentes" (problemas de comunicação de
   equipamentos já instalados) e "Rastreadores Ativos" (todo
   equipamento funcionando agora) — e escreve isso na planilha
   "Administrador".
2. **Classifica cada incidente de manutenção** com um motor de 14
   regras — decide se é um problema real (precisa contato) ou um
   alarme falso/já resolvido (fecha sozinho, **nunca chega até você**).
   Ex: se o equipamento voltou a comunicar sozinho, a pendência nem
   aparece em `Tratativas`.
3. **Lê a aba `Instalação-Remoção`** (preenchida pelo administrador —
   ver `docs/pop_administrador.md`) e consulta o **SGA** (situação do
   associado na cooperativa, ao vivo) pra cada chassi — decide se uma
   instalação/remoção já é uma pendência real, e há quanto tempo. Uma
   remoção, por exemplo, só existe de verdade pro sistema depois que o
   SGA confirmar que o veículo ficou `INATIVO` — antes disso, mesmo já
   cadastrada pelo administrador, ela ainda não aparece pra ninguém
   contatar.
4. **Calcula a urgência** (1 a 5) de cada pendência — manutenção,
   instalação e remoção juntas — combinando tempo sem contato/prazo,
   risco do veículo e eventuais divergências, e monta a fila única.
5. **Publica essa fila em `Tratativas`** — a partir daqui, a esteira de
   contato que **você** opera (WhatsApp → ligação → Puma) roda em cima
   dela. É onde este documento passa a valer de fato (seção 7 em
   diante).

Em resumo: **tudo que chega em `Tratativas` já passou por uma triagem
automática** — se uma linha está ali, é porque o sistema decidiu que
ela precisa mesmo de contato humano. Se algo parecer estranho (ex:
urgência que não faz sentido, dado desatualizado), o motivo geralmente
está a montante (Track N' Me, cadastro do administrador, ou SGA) — não
é algo pra corrigir direto na planilha Operacional. Ver o exemplo
completo com prazos reais na seção 11.

## 3. Status atual do sistema (leia antes de tudo)

Todo o fluxo abaixo já está **funcionando de verdade em produção**
(atualizado 2026-08-26 — os 14/14 templates foram aprovados pela Meta e
o disparo automático foi liberado em 2026-08-11):

| Parte do fluxo | Status |
|---|---|
| Cálculo de urgência, abertura/fechamento automático de incidente, publicação em `Tratativas` | **Funcionando** |
| Disparo de mensagem por WhatsApp (Newmo) | **Funcionando** |
| Escalonamento pra ligação depois de 3 tentativas sem resposta | **Funcionando** |
| Registrar resultado da ligação e encaminhar pra Puma | **Funcionando** |
| O associado responder o WhatsApp por um botão (grava `Retorno do Associado`) e a aba `Alertas` (seção 13) | **Funcionando** |

**Na prática, hoje**: a aba `Tratativas` já reflete a fila real de
pendências, priorizada por urgência, e as colunas `Atendimento`/`Base`/
`Ponto de Ação` (seção 7) **precisam** ser preenchidas antes do 1º
disparo — não há mais nenhuma parte do fluxo aguardando aprovação
externa ou configuração pendente.

## 4. Ferramentas (duas, com papéis diferentes)

1. **Google Sheets, planilha "Operacional - Pendencias - Puma"** (abas
   `Tratativas`, `Pendente de Ligação`, `Encaminhar pra Puma`, `Alertas`
   — ver seções 7 a 9 e 13 — e mais 3 abas só de leitura, **`Análise de
   Divergência - Instalação`/`- Remoção`/`- Manutenção`**, escritas
   sozinhas pelo sistema pra sinalizar cadastro desatualizado ou
   equipamento que ainda comunica mas já deveria ter sido removido —
   você não preenche nada nelas, detalhe de cada uma em
   `docs/pop_administrador.md`) — é aqui que o trabalho de atendimento
   acontece de fato: revisar prioridades, marcar quem contatar,
   registrar resultado de ligação.
2. **Painel Operador (app instalado, pywebview)** — painel de controle
   pra rodar/monitorar as etapas automáticas do sistema (baixar
   relatórios, rodar motor de regras, disparar mensagens etc.). O menu
   lateral tem 3 itens: **"Fases da Automação"** (rodar/acompanhar as
   etapas, ver seção 6), **"Dashboard"** (gráficos e métricas de apoio
   pra priorizar o dia) e **"Manual"** (versão resumida deste mesmo
   documento, embutida no app, pra consulta rápida sem precisar abrir
   este arquivo — ver nota no topo deste documento). **Não é onde o
   trabalho de revisão de pendências acontece** — isso é sempre na
   planilha.

## 5. Conceitos fundamentais (glossário rápido)

- **Chassi vs. Placa**: o sistema identifica um veículo pelo **chassi**,
  nunca pela placa (placa é só exibição — pode ficar vazia, mudar de
  formato, ter erro de digitação). A coluna `Identificador` mostra o
  chassi quando disponível, ou a placa como alternativa só pra exibição
  quando o chassi não é conhecido pra aquela origem.
- **"ID (hash)"**: um código curto (ex.: `5d447453facd74e9`) que
  identifica a pendência de forma única — é a chave que o sistema usa
  internamente pra saber que a linha de hoje em `Tratativas` é "a mesma"
  pendência que apareceu ontem (mesmo que os outros dados tenham mudado
  um pouco). **Nunca edite essa coluna.**
- **Nível de Urgência**: número de **1 (mais baixa) a 5 (mais crítica)**,
  calculado automaticamente pelo sistema a partir da regra de negócio
  que gerou a pendência (tempo sem comunicar, prazo de contrato, risco
  do veículo, etc.). **Sempre revise a fila do maior nível pro menor.**
  **Destaque de cor implementado em 2026-08-07**: a célula fica verde
  (1-2), amarela (3), laranja (4) ou vermelha (5) automaticamente
  (formatação condicional configurada direto na planilha, nas 3 abas
  operacionais). A ordenação/filtro por essa coluna continua manual se
  quiser reordenar as linhas (ver aviso na seção 10 sobre isso não ser
  permanente) — a cor é só um destaque visual, não reordena nada.
- **Dias sem contato**: texto (ex.: "10 dias sem contato") — dias úteis
  desde o último contato com o associado (entrada da pendência, disparo
  de mensagem, ligação ou encaminhamento, dependendo de onde a linha
  está). Em `Tratativas`/`Pendente de Ligação`, a célula fica vermelha
  quando passa de 7 dias úteis (destaque visual, sem nenhuma ação
  automática associada). Em `Encaminhar pra Puma` não tem destaque — é
  só acompanhamento, porque o tempo de retorno não depende da equipe.
- **Esteira de contato**: a sequência **WhatsApp (até 3 tentativas) →
  ligação (1 tentativa) → Puma**. Cada pendência anda por essa esteira
  sozinha, exceto quando o atendente intervém manualmente (`Situação
  Manual`, `Finalizado`).
- **`Ação Sugerida` / `Observação do Sistema`**: texto pronto que o
  sistema já escreve pra cada pendência, explicando o que aconteceu e o
  que fazer. Sempre leia antes de agir — geralmente já responde "por que
  essa linha está aqui".
- **SGA**: sistema da cooperativa (Hinova) que informa se o associado
  está `ATIVO`/`INATIVO`/outro status. A coluna `SGA` na aba
  `Tratativas` mostra esse valor, consultado ao vivo.

## 6. Rotina diária

1. Abrir o Painel Operador (aba "Fases da Automação") e rodar a rotina
   do dia — todas as etapas de uma vez, ou retomar de onde parou se o
   dia anterior foi interrompido.
2. Logar manualmente no SGA quando o aplicativo solicitar (captcha
   impede login automático) — só precisa logar uma vez por sessão; o
   sistema consulta a fila inteira sozinho depois disso.
3. Confirmar reconexão manual se o Track N' Me ou o SGA caírem no meio
   do processamento (o aplicativo sinaliza e pausa a fila até a
   confirmação).
4. Abrir a planilha Operacional e revisar a fila em `Tratativas` —
   ordene/filtre por `Nível de Urgência` (do maior pro menor) antes de
   começar a trabalhar (a cor da célula já ajuda a enxergar isso de
   cara — ver seção 5).
5. Marcar `Selecionado` (checkbox) nas linhas que devem receber disparo
   de WhatsApp nesse ciclo. **Esse checkbox não se desmarca sozinho
   depois de disparar** — fica marcado até você desmarcar (proposital:
   dá controle manual sobre o ritmo, por exemplo pra alinhar com
   roteirização de técnico por região). A trava contra disparo duplicado
   no mesmo dia é automática, não depende de desmarcar nada.
6. Atribuir um **`Técnico`** (dropdown, lista de nomes mantida direto na
   planilha) quando fizer sentido pra pendência.
7. **Preencher `Atendimento`** (`Base`/`Em rota`/`Ação`) em toda linha
   marcada `Selecionado` — **obrigatório antes do 1º disparo de
   WhatsApp**, com base no seu conhecimento de onde/como esse associado
   vai resolver a pendência (decisão 100% sua, o sistema não calcula
   isso sozinho). Sem essa coluna preenchida, a linha fica parada, sem
   disparar nada, mesmo com `Selecionado` marcado. Quando escolher
   `Base`, escolha também **qual base** na coluna `Base` (dropdown);
   quando escolher `Ação`, escolha o **ponto de ação** correspondente
   na coluna `Ponto de Ação`. Evite trocar o valor de `Atendimento` no
   meio das 3 tentativas de WhatsApp da mesma pendência — o sistema não
   te impede, mas o associado pode receber mensagens inconsistentes
   entre uma tentativa e outra.
8. Se o sistema aponta uma discrepância de remoção (veículo consta pra
   retirada mas ainda ativo no Track N' Me) que você já está resolvendo
   por fora, marque **`Discrepância revisada`** — isso faz a linha
   sumir de `Tratativas` enquanto você resolve, **e ela some sozinha
   de novo se o problema real for corrigido** (não precisa desmarcar
   manualmente depois — mas se marcar e o problema continuar, ela
   continua escondida até você desmarcar ou o dado mudar).
9. Se o WhatsApp de um associado voltar como número inválido
   (`Status Contato = contato_invalido`), corrija o telefone no
   cadastro de origem e marque **`Telefone corrigido`** — isso libera a
   pendência pra tentar disparar de novo no próximo ciclo. O checkbox
   volta sozinho pra desmarcado depois (é um "botão de ação", não um
   estado permanente) — isso é esperado, não é bug.
10. Se resolver uma pendência por um caminho que o sistema não enxerga
    sozinho (ex: confirmar que uma remoção foi executada de fato — não
    existe hoje nenhuma confirmação automática pra isso), marque
    **`Finalizado`**. É a única forma de tirar uma linha manualmente de
    `Tratativas` — **`Situação Manual` sozinho NÃO tira a linha da aba**
    (ver aviso importante na seção 10).
11. Acompanhar `Pendente de Ligação` — fazer as ligações escaladas
    (depois de 3 tentativas de WhatsApp sem resposta) e registrar o
    resultado ali mesmo (ver seção 8 pra regra completa de como
    preencher). Se descobrir na ligação que o `Atendimento` certo é
    outro (ex: associado não pode ir à base, é caso de rota), pode
    corrigir ali mesmo — nesta aba a coluna continua editável.
12. Acompanhar `Encaminhar pra Puma` — pendências que chegaram até aqui
    já passaram por toda a esteira sem sucesso. Ver seção 9.

**Dica**: se você só mudou marcações do atendente (`Selecionado`,
`Atendimento`, `Base`, `Ponto de Ação` etc.) na aba `Tratativas` sem
que mais nada tenha mudado (nenhum incidente novo, nenhuma consulta
nova ao SGA), não precisa rodar a rotina inteira de novo. Existe uma
etapa avulsa no Painel Operador, **"Sincronizar seleção do
atendente"**, que só grava essas marcações e libera o próximo disparo,
sem reprocessar a classificação/SGA. É rápida — não reprocessa a
esteira inteira, só o que você marcou — e não trava a tela por muito
tempo, mesmo com volume alto de pendências.

## 7. Aba `Tratativas` — cada coluna

| Coluna | Quem preenche | O que significa |
|---|---|---|
| `ID (hash)` | Sistema | Identificador único da pendência. Nunca editar. |
| `Tipo Serviço` | Sistema | `Manutenção`, `Instalação` ou `Remoção`. |
| `Identificador` | Sistema | Chassi (ou placa, se chassi não disponível) — só exibição. |
| `Chassi` | Sistema | Chassi de verdade, sempre que existir. |
| `Cliente`, `Telefone`, `Cidade`, `Bairro` | Sistema | Dados de contato/localização. |
| `Data Contrato / Data Incidente` | Sistema | Data de referência da pendência (data do contrato pra instalação/remoção, data do incidente pra manutenção). |
| `SGA` | Sistema | Situação do associado na cooperativa, consultada ao vivo. |
| `Ação Sugerida` / `Observação do Sistema` | Sistema | O que fazer e por quê — sempre leia primeiro. |
| `Nível de Urgência` | Sistema | 1 (baixa) a 5 (crítica), com cor automática (ver seção 5). |
| `Dias sem contato` | Sistema | Dias úteis desde o último contato, com destaque vermelho ≥ 7 (ver seção 5). |
| `Discrepância revisada` | **Atendente** (checkbox) | Ver passo 8 da rotina diária. Só tem efeito em linhas de discrepância de remoção (`REGRA_REMOÇÃO_ATIVA`) — em qualquer outra linha, marcar não faz nada. |
| `Atendimento` | **Atendente** (dropdown: `Base`, `Em rota`, `Ação`) | Ver passo 7 da rotina diária. **Obrigatório antes do 1º disparo** — decide qual mensagem de WhatsApp o associado recebe. |
| `Base` | **Atendente** (dropdown, lista de bases cadastradas) | Só preencha quando `Atendimento = Base`. |
| `Ponto de Ação` | **Atendente** (dropdown, lista de pontos de ação cadastrados) | Só preencha quando `Atendimento = Ação`. |
| `Selecionado` | **Atendente** (checkbox) | Marca a linha pra receber disparo de WhatsApp no próximo ciclo. Não reseta sozinho. |
| `Status Contato` | Sistema (via Newmo) | `contato_invalido` quando o WhatsApp recusa o número (telefone não cadastrado) — a pendência para de tentar disparar até você corrigir o telefone e marcar `Telefone corrigido`. Vazio no caso normal. |
| `Telefone corrigido` | **Atendente** (checkbox) | Ver passo 9 da rotina diária. Corrija o telefone no cadastro de origem, depois marque aqui pra liberar um novo disparo. Volta sozinho pra desmarcado no ciclo seguinte (é uma ação, não um estado) — esperado, não é bug. |
| `Status Sistema` | Sistema | Status interno do ciclo de disparo (`pendente`, `aguardando_resposta`, `aguardando_ligacao`, `encaminhado_puma`, `finalizado`). Puramente informativo. |
| `Tentativa 1`/`2`/`3` | Sistema | Data/hora de cada disparo de WhatsApp. |
| `Resposta` / `Data Resposta` | Sistema (via webhook, quando existir) | Se e quando o associado respondeu. |
| `Retorno do Associado` | Sistema (via webhook, quando existir) | Preenchido automaticamente quando o associado clica no botão "Já foi realizado" (avisa que a instalação/remoção já aconteceu, mas o sistema ainda não sabia). Enquanto preenchida, pausa a esteira automática — confira o cadastro, corrija se for o caso, e marque `Finalizado` ou libere de novo. Vazio até a Fase F.2 existir. |
| `Situação Manual` | **Atendente** (dropdown: `Agendado`, `Cancelado`, `Solicitação operacional`) | Ver aviso importante na seção 10 — **preencher isso pausa a esteira automática, mas não tira a linha da aba.** |
| `Data Agendada` | **Atendente** (texto, `dd/mm/aaaa`) | Data combinada com o associado, se houver. |
| `Técnico` | **Atendente** (dropdown nativo, lista mantida na própria planilha) | Não é sincronizado com o Supabase — é só uma anotação de planilha. |
| `Observação Manual` | **Atendente** (texto livre) | Qualquer anotação relevante. |
| `Finalizado` | **Atendente** (checkbox) | **A única coisa que tira a linha de `Tratativas` no ciclo seguinte.** Marque sempre que a pendência estiver de fato resolvida. |

**Uma linha some de `Tratativas` sozinha, sem precisar marcar
`Finalizado`, quando ela avança automaticamente pra `Pendente de
Ligação` ou `Encaminhar pra Puma`** (a esteira "levou" ela pra outra
aba) — nesse caso vá acompanhar a pendência na aba nova, não em
`Tratativas`.

## 8. Aba `Pendente de Ligação` — cada coluna e a regra de leitura

Uma linha chega aqui **sozinha**, sem ação do atendente, quando uma
pendência teve 3 tentativas de WhatsApp sem resposta.

| Coluna | Quem preenche | O que significa |
|---|---|---|
| `ID (hash)`, `Tipo Serviço`, `Identificador`, `Chassi`, `Cliente`, `Telefone`, `Cidade`, `Bairro`, `Data Contrato / Data Incidente`, `SGA`, `Ação Sugerida`, `Observação do Sistema`, `Nível de Urgência`, `Dias sem contato` | Sistema | Mesmo significado de `Tratativas` — uma "foto" da pendência no momento em que ela escalou (`Dias sem contato` continua sendo recalculado a cada execução enquanto a linha ficar aqui). |
| `Atendimento`, `Base`, `Ponto de Ação` | **Atendente** (dropdown) | Mesmo significado de `Tratativas`, mas **editável de novo aqui** — na ligação você pode descobrir que o tipo certo é outro (ex: associado não pode ir à base, é caso de rota). |
| `Histórico de Tentativas (msg)` | Sistema | Datas das 3 tentativas de WhatsApp, separadas por vírgula. Ainda não mostra o texto da mensagem em si (depende da Fase F.2, que ainda não existe). |
| `Data Contato` | **Atendente** (texto, `dd/mm/aaaa`) | Data em que você fez a ligação. |
| `Retornou?` | **Atendente** (dropdown: `Sim`, `Não`) | O associado atendeu/retornou a ligação? |
| `Conseguiu Agendar?` | **Atendente** (dropdown: `Sim`, `Não`) | Só preencha se `Retornou?` = `Sim`. Conseguiu marcar uma data/ação com o associado? |
| `Observação` | **Atendente** (texto livre) | Qualquer detalhe da ligação. |
| `Finalizado` | **Atendente** (checkbox) | Ver regra 1 abaixo — saída manual, sempre vence. |

**Regra de como o sistema lê essas colunas** (leia com atenção — é a
parte mais importante desta aba):

1. **`Finalizado` marcado → sempre vence**, não importa o que estiver em
   `Retornou?`/`Conseguiu Agendar?`. Use isso quando o caso terminou por
   um caminho que os outros dois campos não descrevem (ex: o associado
   resolveu por conta própria por outro canal, desistiu do serviço) —
   **e também é a única forma de tirar a linha da aba depois de um
   agendamento** (ver caminho 4).
2. `Retornou?` vazio → a linha fica esperando, sem nenhuma ação.
3. `Retornou?` = **"Não"** → a linha sai sozinha desta aba e vai direto
   pra `Encaminhar pra Puma` no mesmo ciclo em que você preencher.
   **Este é o único jeito automático de uma pendência chegar na Puma.**
4. `Retornou?` = "Sim" **e** `Conseguiu Agendar?` = **"Sim"** → o sistema
   registra a ligação, mas **a linha continua na aba** — "conseguiu
   agendar" quer dizer que ficou combinado algo pro futuro (ex: visita
   de técnico), **não que o serviço já foi concluído**. Ela só sai
   quando você marcar `Finalizado` depois de confirmar que o combinado
   realmente aconteceu.
5. `Retornou?` = "Sim" **e** `Conseguiu Agendar?` = "Não" (ou vazio) →
   a linha continua na aba, esperando uma nova tentativa de agendar —
   **não vai pra Puma sozinha nesse caso.**

**Erro comum a evitar**: preencher `Conseguiu Agendar? = Sim` e achar
que a pendência já foi resolvida — ela **não sai da fila** até você
voltar depois e marcar `Finalizado` de verdade.

## 9. Aba `Encaminhar pra Puma` — cada coluna

Uma linha chega aqui automaticamente quando `Retornou? = Não` foi
registrado em `Pendente de Ligação` (ver seção 8, caminho 3).

| Coluna | Quem preenche | O que significa |
|---|---|---|
| `ID (hash)`, `Tipo Serviço`, `Identificador`, `Chassi`, `Cliente`, `Telefone`, `Cidade`, `Bairro`, `Data Contrato / Data Incidente`, `Ação Sugerida`, `Observação do Sistema`, `Nível de Urgência`, `Dias sem contato` | Sistema | Mesma "foto" das outras abas. `Dias sem contato` aqui é só acompanhamento (dias desde o encaminhamento) — sem destaque de cor, porque o tempo de retorno não depende da equipe. |
| `Data Encaminhamento` | Sistema | Quando a pendência chegou aqui. |
| `Motivo` | Sistema | Por que foi encaminhada (hoje, sempre "Associado não retornou a ligação."). |
| `Status` | **Quem acompanha o caso, ou o sistema** (dropdown: `aguardando_acao`, `em_andamento`, `concluido`) | Acompanhamento do andamento do caso junto à Puma. **Marcar `concluido` tira a linha desta aba no próximo ciclo** — é a única forma de "arquivar" um caso daqui. |
| `Observação Puma` | Livre, ou o sistema | Qualquer anotação sobre o andamento. |

**Quem preenche `Status`/`Observação Puma` no dia a dia** (decisão de
negócio fechada 2026-08-14): depende da situação —

1. **Manutenção ou instalação** — o sistema reconhece sozinho, a cada
   ciclo, quando o serviço já foi concluído por fora (mesma lógica que
   já fecha incidentes automaticamente em Manutenção e que já detecta
   "instalação já feita" em Instalação/Remoção — ver `docs/regras_
   negocio.md`/`docs/regras_negocio_instalacao_remocao.md`). Quando
   detecta, marca `Status = concluido` sozinho e anexa uma nota em
   `Observação Puma` ("Detectado automaticamente: manutenção/instalação
   concluída em [data]") sem apagar nenhuma anotação que já estivesse
   lá.
2. **Remoção, ou qualquer caso que o sistema não conseguir confirmar
   sozinho** (ex: manutenção sem placa reconhecível) — continua manual:
   o atendimento (depois de confirmar com a Puma por fora do sistema)
   ou a própria Puma, se tiver acesso a esta planilha, preenchem as 2
   colunas à mão.

`Status`/`Observação Puma` **são sincronizados de volta pro banco de
dados interno** todo ciclo (`puma_encaminhamentos.status`/
`observacao_puma`), tanto quando preenchidos manualmente quanto pela
detecção automática — alimenta o Dashboard.

## 10. Avisos importantes ("o óbvio que precisa ser dito")

- **`Situação Manual` (em `Tratativas`) NÃO tira a linha da aba.** Ela
  só pausa a esteira automática (não dispara mais WhatsApp nem escala
  pra ligação enquanto estiver preenchida). Se o caso realmente
  terminou, marque **`Finalizado`** também — são duas ações
  independentes. Esquecer o segundo passo deixa a linha "presa" na
  planilha pra sempre, mesmo já resolvida.
- **A ordenação/filtro que você aplicar na aba `Tratativas` (por
  exemplo, ordenar por `Nível de Urgência`) não é permanente.** O
  sistema reescreve a aba inteira do zero a cada execução — se você
  clicou em "Ordenar intervalo" pra reorganizar as linhas, essa ordem
  se perde no próximo ciclo. Prefira usar um **filtro** (ícone de funil
  do Google Sheets) em vez de ordenar de verdade, se quiser algo que
  sobreviva — ainda assim, reordene visualmente sempre que abrir a
  planilha de novo. A cor de `Nível de Urgência`/`Dias sem contato`, ao
  contrário, **é permanente** (configurada na estrutura da planilha,
  sobrevive a `reescrever_aba`).
- **Os campos marcados como "checkbox"/"dropdown" nas tabelas acima já
  vêm configurados como caixa de seleção/lista de verdade na planilha**
  (não são só texto "TRUE"/"FALSE" pra digitar) — clique na célula e
  marque, ou escolha da lista, não digite o valor à mão. Se algum
  checkbox/dropdown sumir ou virar texto simples depois que o sistema
  reescrever a aba, não precisa reconfigurar nada na mão — o próprio
  sistema reaplica essa validação sozinho a cada ciclo.
- **Nunca edite a coluna `ID (hash)`.** É assim que o sistema reconhece
  "essa é a mesma pendência de ontem" — mudar esse valor faz o sistema
  achar que é uma pendência nova.
- **Não adianta copiar uma linha inteira de uma aba pra outra manualmente**
  — as 3 abas são geridas pelo sistema (ele decide quando uma linha
  aparece/some de cada uma). Editar manualmente os campos que são "do
  sistema" (não marcados como "Atendente" nas tabelas acima) não tem
  efeito — eles são sempre recalculados a cada ciclo.

## 11. Exemplo completo — uma pendência do início ao fim

Pra deixar concreto como uma pendência anda pela esteira inteira, um
exemplo de **manutenção** (o caminho mais comum):

- **Dia 1** — O motor de regras encontra um incidente sem nenhuma
  comunicação do equipamento desde antes do próprio incidente
  (`REGRA_1`) — a pendência já nasce com **nível de urgência 5**
  (crítica) e aparece em `Tratativas`, célula vermelha.
- **Dia 1** — Você marca `Selecionado` e preenche `Atendimento = Base`
  (e escolhe a base) pra essa linha (dentro do horário — disparo só
  acontece até 17:30, hora de Recife, em dia útil).
- **Dia 1** — O sistema dispara a 1ª mensagem de WhatsApp — o texto
  específico já é o de "Base, 1ª tentativa", escolhido automaticamente
  a partir de `Atendimento` (`Tentativa 1` recebe a data/hora).
  `Status Sistema` passa pra `aguardando_resposta`.
- **Dias seguintes** — Sem resposta do associado, o sistema dispara a
  2ª e depois a 3ª tentativa.
- **Depois da 3ª tentativa sem resposta** — A pendência escala
  **sozinha** pra `Pendente de Ligação` — some de `Tratativas`
  automaticamente, sem você precisar fazer nada.
- **Você liga** — Preenche `Data Contato` e `Retornou? = Não` (o
  associado não atendeu).
- **Automaticamente** — A pendência sai de `Pendente de Ligação` e
  aparece em `Encaminhar pra Puma`, com `Motivo = "Associado não
  retornou a ligação."`.
- **Quem acompanha o caso junto à Puma** marca `Status = concluido`
  quando o caso se resolver por fora — a linha sai da planilha no
  ciclo seguinte.

Se em qualquer ponto o associado tivesse respondido a uma mensagem, ou
você tivesse marcado `Situação Manual`/`Finalizado`, a esteira teria
parado ali — ver seção 10 (avisos importantes) pra cada caso. Uma
pendência de **instalação**/**remoção** percorre a mesma esteira de
contato depois de "nascer" — a única diferença está em como ela nasce
e é calculada (ver `docs/pop_administrador.md`, seção 9, pro exemplo
completo desse lado).

## 12. Perguntas frequentes

**Uma pendência pode voltar de `Pendente de Ligação` pra `Tratativas`?**
Não existe esse caminho de volta hoje. Uma vez que ela escala pra
ligação, o ciclo continua pra frente (finalizado ou Puma).

**Posso ligar antes de a pendência chegar em `Pendente de Ligação`
sozinha?**
Sim, nada impede — mas o sistema só vai processar o resultado da
ligação (seção 8) se a linha estiver de fato na aba `Pendente de
Ligação`. Se ligar antes da hora, anote em `Observação Manual` na
`Tratativas` até a pendência escalar de verdade.

**O que fazer se uma pendência parece estar "esquecida" (muito tempo
sem nenhum contato)?**
A coluna **"Dias sem contato"** (ver seção 5) mostra exatamente isso —
em `Tratativas`/`Pendente de Ligação`, a célula fica vermelha quando
passa de 7 dias úteis sem nenhum contato. É só destaque visual (não
escala nem faz nada sozinho) — cabe ao atendente agir quando ver a
cor.

**Por que uma pendência apareceu com uma urgência que não parece fazer
sentido pra mim?**
Confira `Ação Sugerida`/`Observação do Sistema` primeiro — geralmente
explica a regra que gerou aquele nível. Se ainda não fizer sentido, o
dado de origem (Track N' Me, cadastro do administrador, ou SGA) pode
estar desatualizado — ver seção 2 pra entender de onde vem cada
pedaço da informação.

## 13. Aba `Alertas` — cada coluna e como resolver

4ª aba, alimentada pelo webhook do Newmo (Fase F.2) — reúne os casos em
que o sistema detecta uma resposta do associado mas **não sabe agir
sozinho**. Não substitui nada do que já existe nas outras 3 abas (o
caminho automático continua funcionando igual) — só complementa com o
que precisa de uma decisão sua. Hoje, 2 tipos de linha aparecem aqui:

| Coluna | Quem preenche | O que significa |
|---|---|---|
| `ID (hash)`, `Cliente`, `Telefone` | Sistema | Mesma "foto" das outras abas. |
| `Onde está` | Sistema | Em qual aba a pendência está agora (`Pendente de Ligação`, `Encaminhar pra Puma` ou já `Finalizado`). |
| `Tipo de Alerta` | Sistema | `Retorno tardio` ou `Agendado sem data` (ver abaixo). |
| `Contexto` | Sistema | Se for retorno tardio: a resposta literal do associado. Se for agendamento sem data: `"Confirmado via WhatsApp"`. |
| `Ação` | **Atendente** (dropdown: `Confirma conclusão`, `Foi engano, ignorar`) | Só relevante pra `Retorno tardio` — ver regra abaixo. |
| `Data Agendada` | **Atendente** (texto, `dd/mm/aaaa`) | Só relevante pra `Agendado sem data` — preencha a data combinada com o associado. |

**Tipo "Retorno tardio"**: o associado respondeu (ex: "já foi
realizado") depois que a pendência já tinha saído de `Tratativas` (foi
pra ligação ou pra Puma) — por isso o sinal não apareceria em lugar
nenhum sem esta aba. Confira `Onde está`/`Contexto` e escolha uma
`Ação`:
- **`Confirma conclusão`** — o associado está certo, a pendência já foi
  resolvida. O sistema finaliza sozinho na aba onde ela está (some de
  `Pendente de Ligação` no mesmo ciclo; some de `Encaminhar pra Puma`
  no ciclo seguinte, mesmo mecanismo de marcar `Status = concluido`
  lá).
- **`Foi engano, ignorar`** — o associado se confundiu ou a resposta
  não procede. Só limpa o alerta, sem tocar em nada mais — a pendência
  continua seguindo pelo caminho normal.

**Tipo "Agendado sem data"**: o associado confirmou um agendamento por
WhatsApp, mas só ele e você sabem a data combinada — o sistema não tem
como preencher sozinho. Preencha `Data Agendada` e a linha some daqui
no próximo ciclo (a data passa a valer na tratativa, igual ao campo
`Data Agendada` de `Tratativas`).

Detalhe técnico completo (cabeçalho, mecânica de resolução linha a
linha) em `docs/planilha_operacional.md`, seção "Aba nova 'Alertas'".

## Em aberto (ainda não confirmado com o usuário)

- ~~Layout exato do painel de controle do aplicativo~~ — **RESOLVIDO em
  2026-08-10, REVISTO em 2026-08-11 (duas vezes, mesmo dia)**: usuário
  aprovou um mockup HTML navegável da tela "Operação" ("ficou
  perfeito"), publicado em
  `https://claude.ai/code/artifact/88462b21-c94c-45c2-8e01-6f2c56162555`
  (resumo de pendências no topo, cards de etapa por fase, banner de
  reconexão manual). Isso continua sendo o Painel Operador, e o Painel
  Operador **continua um app instalado (pywebview) em cada PC** — isso
  não mudou e não muda (login manual do SGA/Track N'Me precisa de tela
  de navegador de verdade na máquina de quem está logando). O que
  mudou foi só o Painel Admin/Dashboard Cliente: em vez de também
  serem janelas locais, viraram um app web (Next.js) hospedado num
  serviço na nuvem — decisão trocada de VPS (Oracle Cloud) pra
  **Vercel** em 2026-08-11 — resolve o problema de manter/
  atualizar várias máquinas de Painel Operador sem visitar uma por uma
  (launcher local com auto-atualização + vault de credenciais central,
  ver `_handoff/HANDOFF.md`, seção "Arquitetura revista (VPS→Vercel)",
  e o plano completo em `C:\Users\Devon Local\.claude\plans\
  whimsical-growing-neumann.md`). **Progresso de infra (2026-08-11,
  chat novo #8)**: o SQL do Vault já rodou em produção, as 4
  credenciais reais já foram migradas do keyring local pra lá, e a
  função de trava de execução concorrente já existe em
  `integrations/supabase_client.py` — nenhum dos dois ainda está
  conectado ao Painel Operador (que continua sem nenhum código), é só
  a fundação já pronta pra quando a construção de verdade começar.
  Ainda pendente no mockup da tela Operação: a sidebar mostra as 4
  categorias antigas, precisa simplificar quando a construção de
  verdade começar. Construção de código do Painel Operador em si ainda
  não começou (o que já existe é só o esqueleto do app Next.js do lado
  Admin/Dashboard, `webapp/`).
- ~~Formatação condicional por cor pra `Nível de Urgência`~~ —
  **implementada e confirmada ao vivo em 2026-08-07** (1-2 verde, 3
  amarelo, 4 laranja, 5 vermelho, nas 3 abas operacionais).
- ~~Indicador de "tempo sem comunicar"/"dias desde a última
  tentativa"~~ — **implementado e confirmado ao vivo em 2026-08-07**
  (coluna "Dias sem contato" + destaque vermelho ≥ 7 dias úteis), ver
  `docs/planilha_operacional.md`.
- ~~Quem preenche `Status`/`Observação Puma` em "Encaminhar pra Puma" no
  dia a dia~~ — **resolvido 2026-08-14**: sistema reconhece sozinho
  manutenção/instalação concluídas (remoção continua manual), ver
  seção 9.
- ~~Construção de código do Painel Operador~~ — **CONCLUÍDA**: hoje é
  um app instalado (.exe, pywebview) em cada PC, distribuído por um
  Launcher com auto-atualização (Fase 1, concluída 2026-08-15, release
  real `v1.0.0` publicada no mesmo dia). A tela "Operação" do mockup
  virou a aba **"Fases da Automação"**, e o menu lateral ganhou mais 2
  itens além dela: **"Dashboard"** e **"Manual"** (ver seção 4) — a
  sidebar de 4 categorias antigas do mockup não existe mais, foi
  simplificada pra essas 3.
