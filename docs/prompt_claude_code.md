# Contexto do projeto

Estou construindo um sistema em Python para consolidar e tratar pendências de
instalação/remoção e manutenção de rastreadores veiculares (associação de veículos
compartilhados/cooperativa), substituindo um fluxo antigo em n8n. O resultado final
precisa ser um **executável instalável** (via PyInstaller), rodando localmente na
máquina de cada atendente, sem depender de n8n ou de qualquer orquestrador externo.

A pasta já tem um esqueleto criado (README.md, config/, core/, integrations/,
orchestrator/, ui/) com **docstrings detalhadas em cada arquivo** — leia todas antes
de escrever qualquer código, elas contêm decisões de arquitetura já fechadas comigo
em uma conversa longa de planejamento. Não são só comentários genéricos, são a
especificação real do que cada módulo deve fazer.

**Antes de começar**: eu vou colocar os 3 scripts legados (`broquel_bot.py`,
`sga_bot.py`, `sheet_manager.py`) numa pasta `referencia_legado/` na raiz do projeto.
Eles NÃO devem ser usados como base de código (têm credencial hardcoded, escrevem
direto no Google Sheets, fazem login manual onde deveria ser automático) — servem
só como referência de seletores Playwright e da lógica de negócio que já funcionava.

## Stack decidida

- Python 3.11+, `pywebview` para a interface (janela nativa, HTML/CSS/JS embutido,
  sem servidor separado), `playwright` para automação de navegador, `supabase-py`
  para o banco, `gspread` para Google Sheets, Gmail API via OAuth2 para relatórios,
  `keyring` para segredos, `openpyxl` para ler os relatórios baixados, `PyInstaller`
  para empacotar no final.
- **Nenhuma credencial hardcoded em nenhum arquivo.** Tudo passa por
  `config/manager.py`. Segredos vão no `keyring` do SO; o arquivo de service account
  do Google fica como arquivo separado, só o caminho vai na config.
- Supabase é a **única fonte de verdade**. O Google Sheets é sempre espelho (lido
  ou reescrito do zero), nunca fonte primária de decisão de negócio.

## Regras de negócio já fechadas (não redecidir, só implementar)

- **Dedup**: Instalação/Remoção usa `cpf+chassi+situacao+data_contrato`; Manutenção
  usa `placa+data_incidente+evento`.
- **Identificador do veículo**: sempre priorizar a **placa** (associado reconhece
  mais fácil no WhatsApp); manter o **chassi** sempre disponível como campo separado
  de backup. Para achar o chassi quando a placa do incidente for inválida/ausente,
  buscar a mesma placa na base de **Rastreadores Ativos** — nunca usar o imei para
  esse fallback (imei serve só para detectar troca de equipamento, uma anomalia
  diferente).
- **Normalização de telefone**: E.164, `+55` + DDD + regra do 9º dígito. Isso é
  usado também na integração Newmo/Zapio (ver seção da API abaixo) — ainda não
  implementado, é uma das primeiras coisas a fazer.
- **Motor de regras de Manutenção**: portar a lógica do antigo Code node do n8n
  (cruzamento incidente × cadastro por placa/imei, classificação por atualidade de
  posição com limiar de 48h, textos de ação/observação vindos de `rule_templates`
  no Supabase, não hardcoded). Gera 3 grupos de saída: abrir incidente automático,
  concluir incidente automático, precisa de tratativa humana.
- **Regra de abrir incidente automaticamente**: equipamento ativo, vinculado a uma
  placa sem incidente aberto, com `data_posição > 48h`.
- **Regra de concluir incidente automaticamente**: veículo com incidente ativo que
  voltou a comunicar dentro de 48h.
- **Disparo de WhatsApp**: só o atendente seleciona manualmente quais linhas
  disparar (`selecionado = true`). Corte automático de disparo às 17:30
  (fuso `America/Recife`), verificado a **cada item do loop**, não só uma vez no
  início — o que sobrar fica pendente pro próximo dia sem lógica extra de retomada.
- **Escalonamento**: 3 tentativas de mensagem sem resposta → ligação (tentativa
  única, feita pelo atendente). Ligação sem sucesso em agendar → encaminha
  automaticamente pro Puma. Tudo isso já tem tabelas no Supabase
  (`ligacoes`, `puma_encaminhamentos`).
- **SGA**: login **sempre manual** (tem reCAPTCHA, impossível automatizar). O robô
  abre o navegador, espera o humano logar, e só então processa a fila.
- **Track N' Me (conta "Broquel Rastreamento")**: login **automático** via
  usuário/senha da config. Se falhar, handoff para o humano: abrir navegador
  visível, pausar a fila, esperar confirmação manual antes de continuar — nunca
  simplesmente falhar a execução inteira.
- **Detecção de sessão caída no meio da fila** (SGA ou Track N' Me): pausar sem
  descartar os itens restantes, sinalizar na UI, esperar reconexão.
- **Resume inteligente**: progresso marcado por item processado (não por planilha
  inteira). Ao reabrir o app com uma etapa incompleta, oferecer "continuar de onde
  parou" ou "reiniciar etapa".

## Integração Newmo/Zapio (WhatsApp) — detalhes da API

- Base: `https://model.newmo.com.br/api/v2`, auth Bearer Token.
- `GET /canal`, `GET /setor?canal=<guid>` — usados uma vez para obter os IDs que
  vão na config (não chamar isso em toda execução).
- `POST /mensagem/template` — envia o disparo. Resposta traz `atendimentoId` e
  `mensagemId`, que devem ser gravados na tratativa para casar com a resposta do
  webhook depois (usar `atendimento_id`, não telefone, como chave de
  correspondência — telefone é só fallback).
- Códigos de retorno (`resultado.cod`): `1` sucesso; `4`/`6` falha temporária
  (retry com backoff, não marca erro definitivo); `7` número não registrado no
  WhatsApp (marca `status_contato = 'contato_invalido'`, **não** consome tentativa).
- Templates de mensagem ainda não foram aprovados pelo WhatsApp — implementar com
  os campos `template.codigo` e `template.variaveis` vindos da config (placeholder
  por enquanto), não hardcoded.
- O recebimento de resposta do cliente é feito por uma **Supabase Edge Function**
  separada (não pelo executável local, que não pode expor webhook público) — essa
  function grava direto na tabela `tratativas`; o app Python só lê o resultado já
  gravado, não recebe o POST diretamente.

## Planilhas (Google Sheets) — estrutura já fechada

**Planilha "Administrador" (só leitura pelo sistema):**
- `Instalação-Remoção` — 22 colunas, sem alteração.
- `Incidentes` — 15 colunas, espelho literal da exportação do Track N' Me.
- `Rastreadores Ativos` — 28 colunas, com **"Modelo" e "Marca" duplicados de
  propósito** (posições 2/13 e 12/15) — ler por **posição de coluna**, nunca por
  nome, porque o cabeçalho tem nomes repetidos.

**Planilha "Operacional - Pendencias - Puma" (sistema lê e escreve, sempre
reescrevendo a aba inteira, nunca editando célula a célula):**
- `Tratativas` — 24 colunas (inclui `Ação Sugerida`, `Observação do Sistema`,
  `Identificador`, `Chassi` separados).
- `Pendente de Ligação` — 18 colunas.
- `Encaminhar pra Puma` — 15 colunas.

Os nomes exatos de cada coluna estão nos comentários de
`integrations/google_sheets_client.py` e podem ser vistos direto nos arquivos
`.xlsx` que já foram gerados (se estiverem na pasta, use-os como referência; senão,
me pergunte antes de assumir um nome de coluna).

## Banco de dados (Supabase) — já criado, não recriar

Tabelas já existem em produção: `tratativas`, `ligacoes`, `puma_encaminhamentos`,
`system_parameters`, `rule_templates`. O SQL de criação está documentado nos
comentários de `integrations/supabase_client.py`. **Log de execução e auditoria de
ações automáticas (`log_execucoes`, `log_acoes_automaticas`) ainda não foram
criados** — ficam para quando chegarmos na fase de observabilidade, não implementar
ainda a menos que eu peça.

## Interface (pywebview)

- Tela de configuração: campos por integração (Track N' Me, Newmo, Supabase,
  Google Sheets, Gmail) — **sem campo de credencial para SGA** (login manual).
- Tela de operação: lista de etapas com checkbox + botões "Executar selecionadas" /
  "A partir daqui" / "Executar tudo", indicador de progresso, e um estado especial
  de "aguardando reconexão manual".
- Visual: moderno, limpo — evitar explicitamente qualquer coisa que pareça um
  aplicativo de sistema operacional antigo.

## Como quero trabalhar com você

1. **Leia primeiro** o `README.md` e todos os arquivos em `config/`, `core/`,
   `integrations/`, `orchestrator/`, `ui/` — são só docstrings/assinaturas de
   função hoje, mas contêm a especificação completa.
2. Comece pela ordem sugerida no `README.md`: `config/manager.py` primeiro (nada
   funciona com segurança sem isso), depois `integrations/supabase_client.py`,
   depois os módulos puros em `core/` (mais fáceis de testar sem rede), e só depois
   os robôs Playwright e a UI.
3. Sempre que uma decisão de negócio não estiver clara ou parecer faltar no que
   documentei aqui, **pergunte antes de assumir** — várias decisões vieram de uma
   negociação específica com o cliente final e não devem ser "otimizadas" sem
   confirmar comigo.
4. Escreva testes unitários para os módulos de `core/` (são lógica pura, sem
   I/O — devem ser fáceis de testar isoladamente antes de integrar).
5. Não implemente ainda: geração do refresh token do Gmail (fica para quando
   chegarmos na fase de relatório), códigos de template do Newmo (ainda não
   aprovados pelo WhatsApp), script de "concluir incidente" do Track N' Me
   (o cliente ainda vai me enviar a orientação de como isso deveria funcionar).

Pode começar lendo a estrutura do projeto e me dizendo se ficou tudo claro antes de
escrever a primeira linha de código.
