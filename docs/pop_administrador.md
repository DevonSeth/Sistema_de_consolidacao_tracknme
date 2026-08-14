# POP — Administrador

Documento vivo, reescrito com detalhe completo em 2026-08-07 (a versão
anterior era um rascunho inicial de 2026-08-06) e expandido no mesmo dia
com a visão de sistema completo e um exemplo de ponta a ponta. Objetivo:
qualquer pessoa que nunca usou o sistema consegue operar como
administrador só lendo este documento — nenhum passo fica implícito,
mesmo o que pareça óbvio pra quem já conhece o projeto.

## 1. Visão geral do papel

O administrador é responsável por **alimentar** a única fonte de dado
que o sistema não consegue obter sozinho: pendências de **instalação**
e **remoção** de rastreador veicular. Tudo o mais (incidentes de
manutenção, situação do equipamento no Track N' Me, situação do
associado na cooperativa via SGA, cálculo de urgência, abertura/
fechamento automático de incidente) o sistema já resolve sozinho — o
administrador não precisa mexer em nada disso.

Em uma frase: **o administrador só cuida de "quem precisa instalar ou
tirar um rastreador"; o resto é automático.**

## 2. Como isso se encaixa no sistema inteiro

Só pra você ter a visão completa (não é obrigatório entender isso pra
fazer seu trabalho, mas ajuda a confiar no sistema e a saber pra quem
perguntar se algo parecer errado). Todo dia (execução automática — hoje
disparada manualmente pelo atendimento; no futuro, por um painel de
controle), o sistema faz, nesta ordem:

1. **Baixa dois relatórios do Track N' Me** (o sistema que monitora os
   rastreadores de verdade): a lista de "Incidentes" (problemas de
   comunicação de equipamentos já instalados) e "Rastreadores Ativos"
   (todo equipamento que está funcionando agora). Esses dois relatórios
   substituem o que antes era copiado e colado manualmente na planilha
   Administrador — hoje o sistema escreve isso sozinho, direto do
   Track N' Me, nas abas `Incidentes`/`Rastreadores Ativos` (que você
   **não edita** — ver seção 3).
2. **Aplica o "motor de regras" de Manutenção** nos Incidentes — um
   conjunto de 14 regras que decide, pra cada incidente: é um problema
   real que precisa de contato com o associado, ou é um alarme
   falso/já resolvido que pode ser fechado sozinho? (ex: se o
   equipamento voltou a comunicar sozinho, ou se o rastreador já está
   numa lista de retirada.)
3. **Lê a aba `Instalação-Remoção` que você preenche** e cruza com o
   **SGA** (sistema da cooperativa, consultado ao vivo) — é aqui que a
   sua parte entra: o sistema usa o `Chassi` de cada linha sua pra
   saber se o veículo está ativo/inativo na cooperativa, e a `Data
   contrato` pra calcular há quanto tempo uma instalação está
   pendente.
4. **Calcula a urgência (1 a 5)** de cada pendência — manutenção,
   instalação e remoção juntas — e junta tudo numa fila única.
5. **Publica essa fila na planilha "Operacional - Pendencias - Puma"**,
   aba `Tratativas` — é aqui que o atendimento (ver
   `docs/pop_operador.md`) assume: eles decidem quem contatar primeiro,
   disparam WhatsApp, ligam, e acompanham até resolver.

**A sua responsabilidade termina no passo 3.** Depois de cadastrar a
linha corretamente (chassi certo, `Data contrato`/`Serviço`
preenchidos), o resto acontece sozinho. Se uma pendência que você
cadastrou nunca aparece na fila do atendimento, o motivo mais comum é:
`Chassi` errado/inconsistente, ou (no caso de remoção) o SGA ainda não
confirmou que o veículo ficou `INATIVO` — ver o exemplo completo na
seção 9.

## 3. Ferramenta

Google Sheets, planilha **"Administrador"**, aba **"Instalação-Remoção"**.
Essa é a **única aba de toda a planilha Administrador que o administrador
edita** — as outras duas abas dessa mesma planilha (`Incidentes`,
`Rastreadores Ativos`) são escritas automaticamente pelo sistema a cada
execução (espelham o que foi baixado do Track N' Me) e **não devem ser
editadas manualmente**: qualquer edição manual nelas é apagada no
próximo ciclo, porque o sistema limpa e reescreve as duas do zero toda
vez que roda.

Não existe hoje nenhuma tela própria do sistema para essa tarefa — é
Google Sheets puro, a planilha em si é a interface.

## 4. Quando inserir uma linha nova

**Sempre que uma pendência de instalação ou remoção for recebida** —
ou seja, assim que alguém (associado, cooperativa, comercial) informar
que um veículo precisa ganhar um rastreador novo, ou que um rastreador
precisa ser retirado de um veículo. Não espere nenhuma confirmação do
sistema antes de cadastrar — o cadastro É o que faz a pendência existir
pro sistema.

## 5. Como inserir a linha — campo por campo

Adicione **uma linha nova no final da aba** (nunca insira no meio, nunca
sobrescreva uma linha existente — ver seção 6). Preencha estas 21
colunas, nesta ordem:

| Coluna | O que colocar | Obrigatório? |
|---|---|---|
| `Situação` | Situação de pagamento/associado, o que o SGA mostra pra esse associado hoje (ex: "Regular", "Inadimplente"). É só informativo/histórico — o sistema consulta o SGA **ao vivo** a cada execução pra saber a situação atual de verdade, não usa o que está escrito aqui pra calcular nada. | Recomendado, mas não trava nada se ficar desatualizado |
| `Data contrato` | Data em que o contrato/pedido foi fechado, formato `dd/mm/aaaa`. É a partir dela que o sistema calcula a urgência de uma **instalação** pendente (quanto mais dias desde essa data, mais urgente). | **Sim, pra Instalação** |
| `Nome Associado` | Nome completo de quem contratou. | Sim |
| `CPF` | CPF do associado, com ou sem pontuação (o sistema usa exatamente como está escrito pra montar a chave de identificação da pendência — ver seção 8). | Sim |
| `celular` | Telefone celular do associado, **este é o único telefone que o sistema usa pra contato automático** — ver seção 7, é o ponto mais importante desta tabela inteira. | Sim, se quiser que o contato automático funcione |
| `Celular Aux` | Um segundo celular, se tiver. **O sistema nunca olha pra este campo.** É só anotação/referência humana. | Não |
| `Telefone Comercial` | Telefone comercial/fixo, se tiver. **O sistema nunca olha pra este campo.** | Não |
| `Logradouro`, `Número`, `Bairro`, `Cidade`, `Estado` | Endereço completo do veículo/associado. `Bairro`/`Cidade` aparecem depois na planilha de atendimento (aba `Tratativas`), o resto é só cadastro. | Sim |
| `Placa` | Placa do veículo, formato antigo (ABC1234) ou Mercosul (ABC1D23). Serve só pra facilitar a leitura humana — o sistema **nunca** usa a placa pra identificar o veículo (ver seção 8). | Sim, se disponível — mas não é o identificador |
| `Modelo` | Modelo do veículo, texto livre — mas **se for moto, o nome precisa conter a cilindrada como número** (ex: "CG 160 FAN FLEX", "DOMINAR 400 NS"). O sistema lê o primeiro número de 2 a 4 dígitos do texto pra saber a cilindrada e decidir se é um veículo de risco (300cc+). Se o modelo não tiver a cilindrada no nome, o sistema vai errar essa conta — nesse caso, avise pra incluir na lista de exceções manual (`system_parameters` no Supabase, fora do alcance direto do administrador hoje). | Sim |
| `Ano Modelo` | Ano do modelo. | Sim |
| `Valor FIPE` | Valor FIPE do veículo em reais, só o número (ex: `75000`). Usado pra decidir se um carro é "de risco" (FIPE acima de R$ 80.000 conta como risco, mesmo que o modelo não esteja na lista de mais roubados). | Sim, pra carro |
| `Tipo Veículo` | "Passeio", "Moto", "Caminhão" etc. — usado junto com `Modelo` pra aplicar a regra de risco certa (cilindrada pra moto, FIPE/lista pra carro). | Sim |
| `Voluntário` | Se o associado aderiu voluntariamente ou não. Informativo, o sistema não usa. | Não |
| `Cooperativa` | Nome da cooperativa. Informativo. | Não |
| `Chassi` | Chassi do veículo (17 caracteres). **Este é o identificador real do veículo pro sistema — ver seção 8.** Sem chassi certo, o sistema não consegue cruzar essa pendência com o Track N' Me nem com o SGA, e a pendência fica "invisível" pro cálculo de urgência. | **Sim, obrigatório de verdade** |
| `Marca` | Marca do veículo. | Sim |
| `Serviço` | Só duas opções possíveis, em texto livre mas com uma destas duas ideias: **"Instalação"** (o rastreador ainda precisa ser instalado) ou **"Retirada"/"Remoção"** (o rastreador precisa ser retirado). O sistema procura essas palavras dentro do texto (não precisa ser exatamente "Instalação" letra por letra, mas precisa conter claramente uma das duas ideias). Se escrever algo ambíguo, a linha não vai ser classificada como nem uma coisa nem outra. | **Sim, obrigatório de verdade** |

**Os 3 campos em negrito na coluna "Obrigatório" (`Chassi`, `Serviço`, e
`Data contrato` pra instalação) são o mínimo absoluto pra pendência
"existir" pro sistema.** Os demais ajudam a qualidade do atendimento
(telefone, endereço) mas não travam o cálculo de urgência.

## 6. As 3 regras inegociáveis desta aba

1. **Nunca apague uma linha antiga, mesmo que esteja errada ou
   desatualizada.** A aba é, na prática, um **histórico que só cresce**.
   Se um dado mudou (ex: correção de telefone, mudança de endereço), **não
   edite a linha existente** — insira uma linha nova, no final, com o
   `Chassi` igual e os dados atualizados. O sistema identifica sozinho
   qual é o registro válido de cada veículo: **sempre usa a linha mais
   recente daquele `Chassi`** e ignora as anteriores no processamento
   (elas continuam ali, só como histórico bruto, nunca são deletadas
   por ninguém).
   - *Por que isso importa*: se você editar uma linha existente em vez
     de inserir uma nova, perde o histórico de quando cada mudança
     aconteceu — e não tem nenhum ganho, porque o sistema ia usar a
     linha mais recente de qualquer jeito.
2. **`celular` é sempre o telefone usado pelo contato automático — sem
   nenhum "plano B".** Se `celular` estiver vazio, o sistema trata a
   pendência como **"sem telefone"**, mesmo que `Celular Aux` ou
   `Telefone Comercial` estejam perfeitamente preenchidos. Isso é
   proposital (evita ligar/mandar mensagem pro telefone errado sem
   querer) — mas na prática significa que **esquecer de preencher
   `celular` corretamente trava o contato automático daquela pendência
   inteira**, mesmo com todo o resto certo.
3. **`Chassi` precisa ser exato e consistente entre linhas do mesmo
   veículo.** Se a mesma pendência aparecer com o chassi escrito de duas
   formas diferentes (espaço a mais, letra maiúscula/minúscula
   diferente, um caractere trocado), o sistema vai tratar como **dois
   veículos diferentes** — vai gerar pendência duplicada, ou pior, vai
   achar que uma delas nunca teve nenhuma atualização. Copie e cole o
   chassi de uma fonte confiável (ex: o próprio Track N' Me) em vez de
   digitar de cabeça.

## 7. O que o sistema faz sozinho a partir daqui (não precisa fazer nada disso manualmente)

- **Cruza cada linha com o SGA** (situação do associado/veículo na
  cooperativa, consultado ao vivo, não o que está escrito na coluna
  `Situação`) e com **Rastreadores Ativos** (Track N' Me — se o
  equipamento já aparece lá, a instalação já foi feita de verdade).
- **Decide se a pendência já "nasceu" pro sistema ou não**: uma
  **remoção** só vira pendência ativa depois que o SGA confirmar pelo
  menos uma vez que o veículo está `INATIVO` na cooperativa — antes
  disso, mesmo com a linha cadastrada aqui, o sistema ainda não cobra
  ninguém por ela (o SGA é quem decide que motivo real existe pra tirar
  o rastreador).
- **Calcula a urgência automaticamente** (1 a 5) combinando: quantos
  dias desde `Data contrato` (instalação) ou desde que o SGA confirmou
  `INATIVO` (remoção); se o veículo é considerado "de risco" (moto
  300cc+, ou carro com FIPE alto ou modelo muito roubado); e se há
  alguma divergência a resolver (ex: consta pra retirada mas o
  equipamento ainda está ativo no Track N' Me; ou o nome do associado
  mudou). Detalhe completo de cada regra, incluindo as tabelas de dias
  exatas, em `docs/regras_negocio_instalacao_remocao.md` (ver também o
  exemplo com números reais na seção 9 abaixo).
- **Nunca precisa ser avisado quando uma pendência for resolvida.**
  Quem confirma isso é o atendimento, na planilha Operacional (ver
  `docs/pop_operador.md`) — o administrador não tem nenhuma ação a
  tomar depois de cadastrar a linha.

## 8. Por que CHASSI e não PLACA

O sistema usa **exclusivamente o chassi** para identificar um veículo de
verdade — a placa aparece na planilha só pra facilitar a leitura humana,
nunca é usada para cruzar dado ou decidir se duas linhas são o mesmo
veículo. Motivo: placa pode ficar vazia, pode mudar de formato (antigo
para Mercosul), pode ter erro de digitação mais fácil — o chassi é um
dado imutável do veículo. **Se só tiver a placa e não tiver o chassi
ainda, é melhor esperar descobrir o chassi antes de cadastrar** do que
cadastrar sem ele — sem chassi, a pendência não se conecta a nada.

## 9. Exemplo completo — do cadastro até a resolução

Pra deixar concreto como uma linha sua se transforma numa pendência de
atendimento de verdade, com prazos reais. Exemplo de **remoção**:

- **Dia 1** — Você cadastra: `Chassi = 9BWZZZ377VT004251`, `Nome
  Associado = Carlos Silva`, `Serviço = Retirada`. Nesse momento o
  veículo ainda está `ATIVO` no SGA.
- **Dias 1 até o dia em que sair** — Toda execução automática consulta
  o SGA pra esse chassi e vê `ATIVO`. A pendência **ainda não "nasceu"
  de verdade** pro sistema — não tem urgência calculada, não aparece
  pro atendimento contatar ninguém. Isso é esperado: só faz sentido
  cobrar a retirada quando o motivo real (o associado deixar de ser
  ativo) existir.
- **Dia 15** — O associado sai da cooperativa; o SGA passa a mostrar
  `INATIVO` pra esse chassi. A partir daqui o sistema começa a contar
  dias desde essa confirmação (**não** desde o dia 1 do seu cadastro —
  o cadastro só registra a intenção, o SGA confirma o motivo real).
- **Dia 20 (5 dias depois do `INATIVO`)** — Urgência calculada:
  **nível 1** ("Normal", faixa de 1 a 10 dias). A pendência já aparece
  em `Tratativas`, mas com prioridade baixa (célula verde).
- **Dia 30 (15 dias depois do `INATIVO`)** — Urgência sobe pra
  **nível 3** ("Alta", faixa de 11 a 20 dias) — célula amarela.
- **Dia 40 (25 dias depois do `INATIVO`)** — Urgência sobe pra
  **nível 5** ("Urgente", faixa de 21 a 30 dias) — célula vermelha,
  topo da fila.
- **A partir do dia 45 (30+ dias)** — Congela em nível 5 "pra sempre"
  (não sobe mais, mas também não desce sozinho) até alguém resolver.
- **Quando a retirada for feita de verdade** — não existe hoje nenhuma
  confirmação automática disso (diferente da instalação, ver abaixo).
  O atendimento precisa marcar `Finalizado` manualmente na aba
  `Tratativas`, depois de confirmar por fora que o rastreador foi
  retirado de fato.

O mesmo princípio vale pra **instalação** — só que:
- o prazo conta a partir da `Data contrato` (não de um status do SGA)
  — mesmas faixas de dias (1-10 normal, 11-30 em atraso nível 3, 31+
  crítico nível 4; nível 5 só quando o veículo também é "de risco");
- a confirmação de execução é **automática**: o sistema vê o
  equipamento aparecer em "Rastreadores Ativos" do Track N' Me e fecha
  a pendência sozinho, **sem precisar de `Finalizado` manual**.

## 10. Perguntas frequentes

**E se eu errar um dado depois de já ter cadastrado a linha?**
Insira uma linha nova com o `Chassi` igual e o dado corrigido (ver regra
1 da seção 6) — não edite a linha errada.

**Uma pendência de instalação e uma de remoção podem existir ao mesmo
tempo pro mesmo chassi?**
Sim, tecnicamente — mas na prática isso significaria trocar de
rastreador (tirar um e botar outro), um cenário raro. Se acontecer, use
a linha mais recente pra refletir qual é a ação pendente de verdade
agora.

**Preciso avisar alguém quando terminar de cadastrar uma linha?**
Não. O sistema lê a aba inteira a cada execução automática — não existe
um "enviar"/"confirmar", é só escrever na planilha.

**O sistema mostra pra mim quando uma pendência que cadastrei foi
resolvida?**
Não diretamente — quem acompanha isso é o atendimento. Se quiser saber
o status de uma pendência específica, peça pro atendimento consultar a
aba `Tratativas`/`Encaminhar pra Puma` da planilha Operacional pelo
chassi.

**Por que uma pendência de remoção que cadastrei há semanas ainda não
apareceu pro atendimento?**
O motivo mais comum: o SGA ainda mostra o veículo como `ATIVO` — ver o
exemplo da seção 9. Enquanto isso, a pendência existe na sua aba, mas
ainda não "nasceu" pro sistema de verdade. Confirme com o SGA (ou peça
pro atendimento confirmar) se o associado realmente já saiu da
cooperativa.

## Painel do administrador — app web, login real (revisto 2026-08-11, chat novo #8)

**Decisão de 2026-08-10 ("painel único e compartilhado")
substituída em 2026-08-11 por "3 painéis separados"; a tecnologia por
trás do Painel Admin mudou de novo, e a hospedagem também (VPS trocada
por Vercel)** — ver `_handoff/HANDOFF.md`, seção "Arquitetura revista
(VPS→Vercel)", pro detalhe completo. Os 3 papéis continuam os mesmos
(princípio do usuário: "a ferramenta precisa auxiliar aos usuários a
tomarem decisões" — cada papel vê só o que precisa), só onde cada um
roda que mudou:

- **Painel Admin** — mexe em todos os parâmetros de negócio, cadastros
  (Bases/Pontos de Ação/regras), credenciais do sistema, e vê todos os
  dashboards sem filtro. **Não tem a tela de rodar/acompanhar etapas**
  — pra isso, o administrador vai até o Painel Operador (evita
  duplicar a mesma função nos dois lugares). **É um app web (Next.js)
  com login real (e-mail/senha)** — hospedado na Vercel quando o deploy
  acontecer; até lá, também pode rodar localmente na máquina de quem
  administra.
- **Painel Operador** — do atendimento, ver `docs/pop_operador.md`.
  Continua sendo um app instalado (pywebview) em cada PC — isso não
  mudou, e não pode mudar (o login manual do SGA/Track N'Me precisa de
  uma tela de navegador de verdade na máquina de quem está logando).
- **Dashboard Cliente** — a Puma vê um recorte configurado pelo
  administrador (Fase G, ainda não implementada), acessado por um link
  único — precisa estar hospedado de verdade (não local), diferente do
  Painel Admin.

Mockup navegável do Painel Admin (Parâmetros de Negócio, Cadastros,
Configuração, Dashboards, com filtro em cada tela) aprovado pelo
usuário em
`https://claude.ai/code/artifact/1be04454-2ba2-416a-ad66-018ee5c91847`
— ainda não fechado 100% (faltam decidir os dados exatos da aba
Dashboards). **Construção de código, atualizado 2026-08-11 (chat novo
#8)**: login (Supabase Auth), Cadastros (Bases + Pontos de Ação e,
agora também, **Regras** — as 31 linhas de `rule_templates`, editáveis
por linha: observação, ação, nível de urgência e ativo/inativo, com
busca e filtro por categoria/nível; `prioridade` fica só leitura, é
metadado descritivo, não decide a ordem real da cascata) e Parâmetros
de Negócio já são telas de verdade, funcionando contra o Supabase
real — faltam Configuração e Dashboards (ver `_handoff/HANDOFF.md`
pro estado exato e o que falta em cada uma).

## 11. Painel Admin (app web) — cada tela, pra quem nunca usou

Expandido em 2026-08-14 (chat #16) — antes desta seção, este documento
só cobria o cadastro na planilha Instalação-Remoção (seções 1-10). O
Painel Admin é a tela web (`https://sistema-de-consolidacao-tracknme.
vercel.app/admin`, login com e-mail/senha) onde o administrador
configura o sistema — não é onde o dia a dia de atendimento acontece
(isso continua no Google Sheets, ver seções anteriores, e no Painel
Operador, `docs/pop_operador.md`). O menu lateral tem 6 itens:

### Parâmetros de Negócio

Os limiares e listas que hoje ficariam "escondidos" no código —
editáveis aqui, com busca e filtro por categoria (Geral, Risco de
veículo, Prazos, Esteira de disparo, Observabilidade). Exemplos: quantas
horas um equipamento fica sem comunicar antes de virar incidente,
quais modelos de moto contam como "alto risco de furto", o horário de
corte do disparo de WhatsApp. **Editar aqui tem efeito imediato no
próximo ciclo automático** — não precisa de deploy nem reiniciar nada.
Use a busca se não souber em qual categoria um parâmetro está; o texto
de cada linha já explica o que ele controla.

### Cadastros

3 abas:
- **Bases** — os locais onde o associado pode ir instalar/rastrear
  (nome, endereço, ativo/inativo). Usado no disparo de WhatsApp quando
  o atendimento marca `Atendimento = Base` numa pendência.
- **Pontos de Ação** — locais de ação com data marcada (mutirões,
  eventos). Mesma lógica das Bases, mas com uma data associada.
- **Regras** — as ~31 linhas de `rule_templates` que o motor de regras
  usa pra decidir texto de ação/observação e nível de urgência de cada
  código (`REGRA_1`, `REGRA_5_1` etc.). Editável por linha: texto de
  observação, texto de ação, nível de urgência (1-5), ativo/inativo.
  `Prioridade` aparece só pra leitura — é metadado descritivo, não
  decide a ordem real da cascata de regras (isso é fixo no código).

### Configuração

As credenciais de cada integração (Track N'Me, Newmo/WhatsApp,
Supabase, Google Sheets), guardadas no Vault (nunca em texto puro em
lugar nenhum acessível). Pra cada uma: **"Testar conexão"** confirma
que a credencial ainda funciona de verdade (chama a API real) —
**exceto Track N'Me**, que exige um navegador com captcha manual,
então só é testável rodando o Painel Operador local. **"Editar"** abre
os campos pra atualizar um valor (ex: token expirou, senha mudou).

**Importante**: editar uma credencial aqui grava no Vault central, mas
**só passa a valer nas máquinas do Painel Operador quando o Launcher
existir** (Fase 1, ainda não construída) — até lá, cada máquina
continua com a credencial que já tem localmente (keyring do Windows),
e uma atualização feita aqui não chega lá sozinha.

### Dashboards

Os números de negócio, com filtro De/Até no topo (a maioria respeita o
filtro; algumas, marcadas "Estado agora", mostram sempre o momento
atual, ignorando o filtro — o rótulo da seção já avisa qual é qual).
Cada métrica tem até 2 caixinhas de visibilidade:
- **"Visível no Dashboard Cliente"** — aparece pro link que a Puma usa
  pra acompanhar (sem login, sem acesso a mais nada do sistema).
- **"Visível no Painel Operador"** — aparece na aba "Painel de apoio"
  do Painel Operador local, pro atendimento acompanhar sem precisar
  abrir o Admin. Nem toda métrica tem essa opção (só as que o Painel
  Operador já sabe calcular/desenhar).
Nenhuma das duas afeta o que VOCÊ vê aqui no Admin — o Admin sempre
mostra tudo, essas caixinhas só controlam o que os OUTROS 2 públicos
enxergam.

**Baixar PDF** exporta a tela atual (com o filtro aplicado) num formato
de impressão limpo, com o cabeçalho "Relatório de pendências - Viver de
Rastreamento" — usa o "Imprimir" do próprio navegador (`Ctrl+P`/
`Cmd+P`), não baixa um arquivo direto; escolha "Salvar como PDF" na
tela de impressão do navegador.

### Manual do Sistema

Esta mesma documentação, dentro do próprio app — pra não precisar sair
da tela pra consultar.

### Abrir Painel Operador

Atalho pra abrir o Painel Operador já instalado nesta máquina, sem
precisar procurar o ícone/pasta. **Só funciona depois que a Fase 1
(Launcher/instalador) estiver pronta** — até lá, clicar não faz nada
(infraestrutura já existe, esperando o instalador registrar o atalho
no Windows).

## Em aberto (ainda não confirmado com o usuário)

- Os dados exatos que entram na aba "Dashboards" do Painel Admin —
  usuário avisou que ainda vai trabalhar nisso antes de fechar.
- ~~Se algum parâmetro de negócio... deve ficar editável pelo
  administrador~~ — **RESOLVIDO 2026-08-11**: os 16 parâmetros (10
  originais + 6 migrados do hardcode) já são editáveis de verdade na
  categoria "Parâmetros de Negócio" do Painel Admin, com busca e filtro
  por categoria (Geral/Risco de veículo/Prazos/Esteira de disparo).
