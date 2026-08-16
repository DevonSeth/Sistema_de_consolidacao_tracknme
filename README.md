# Consolidação Track N'Me

Sistema de consolidação de pendências de rastreamento veicular para a
Puma. Reúne instalação, remoção e manutenção de rastreadores em uma
única fila priorizada, cruzando dados de 3 sistemas que não conversam
entre si (Track N'Me, SGA e Google Sheets) e conduz o contato com o
associado do primeiro disparo de WhatsApp até o encaminhamento pra Puma,
sem intervenção manual em nenhum desses passos.

## O problema

Antes deste sistema, saber quais rastreadores precisavam de atenção
significava cruzar manualmente 3 fontes: os relatórios do Track N'Me
(equipamentos com problema de comunicação), o SGA (situação do associado
na cooperativa) e uma planilha de cadastro de instalação/remoção mantida
à mão. Não havia priorização — um técnico decidia por instinto o que
atacar primeiro, e nada garantia que uma pendência antiga não fosse
esquecida.

## O que o sistema faz

Todo dia, de forma automática:

1. Baixa os relatórios "Incidentes" e "Rastreadores Ativos" do Track N'Me.
2. Aplica um motor de 14 regras sobre os incidentes — decide o que é
   problema real (precisa contato) e o que é ruído (fecha sozinho).
3. Lê a aba de cadastro de instalação/remoção e cruza cada linha com o
   SGA, ao vivo, pra saber se a pendência já existe de fato e há quanto
   tempo.
4. Calcula um nível de urgência (1 a 5) pra cada pendência — combinando
   prazo, risco do veículo e divergências — e publica uma fila única.
5. Dispara WhatsApp (até 3 tentativas), escala pra ligação quando não há
   resposta, e encaminha pra Puma quando a ligação também não resolve —
   com detecção automática de conclusão pra manutenção e instalação.

O time de atendimento entra só pra decidir *quem* contatar primeiro e
registrar o resultado de cada ligação — todo o resto (priorização,
disparo, escalonamento) é automático.

## Arquitetura

Três superfícies, cada uma com o público que precisa dela:

| Superfície | O quê | Tecnologia | Acesso |
|---|---|---|---|
| **Painel Admin** | Parâmetros de negócio, cadastros, credenciais, dashboards completos | Next.js 16, Vercel | Login (Supabase Auth), `role=admin` |
| **Painel Operador** | Roda a esteira diária, acompanha progresso, fila de prioridade | Python + pywebview, instalado localmente | Login, `role=operador` |
| **Dashboard Cliente** | Métricas selecionadas, sem acesso a mais nada do sistema | Next.js 16, Vercel | Login, `role=cliente` |

O Painel Operador se auto-atualiza via um Launcher próprio (checa
versão contra o backend, baixa a release mais nova do GitHub quando
existe). Credenciais de todas as integrações ficam num Vault central no
Supabase (nunca em texto puro, nunca versionadas) — o Painel Admin edita
lá, o Painel Operador sincroniza na inicialização.

## Stack

- **Orquestração e regras de negócio**: Python (`orchestrator/`, `core/`).
- **Integrações externas**: Playwright (Track N'Me, SGA — login manual
  por captcha), Google Sheets via `gspread`, WhatsApp via Newmo,
  Supabase (Postgres + Auth + Vault).
- **Painéis web**: Next.js 16 (App Router, Server Actions), hospedado na
  Vercel.
- **Painel Operador**: pywebview (janela nativa, HTML/CSS/JS sem
  framework), empacotado com PyInstaller.
- **Testes**: pytest, 637 testes cobrindo `core/`/`orchestrator/`/`ui/`.

## Estrutura

```
core/             lógica de negócio pura — motor de regras, dedup,
                  normalização, escalonamento. Sem I/O, sem rede.
integrations/     um cliente por sistema externo — Supabase, Google
                  Sheets, Track N'Me, SGA, Newmo, Gmail.
orchestrator/     pipeline.py amarra as etapas (única fonte de verdade
                  da ordem); catálogo de etapas, dashboards, watchdog.
ui/               Painel Operador (pywebview) — app.py + web/ (HTML/CSS/JS).
webapp/           Painel Admin + Dashboard Cliente (Next.js).
config/           leitura da config local (credenciais, nunca versionada).
docs/             POPs (Administrador/Operador), regras de negócio,
                  guia de deploy, manuais publicados (docs/manuais/).
tests/            suíte pytest.
supabase/         config do projeto Supabase + edge function (webhook Newmo).
_handoff/         notas de sessão e scripts pontuais — não versionado.
```

## Status

Em produção. As 3 superfícies estão no ar, com autenticação e
autorização por papel, e a esteira completa (Fases A a G) validada
ponta a ponta contra os sistemas reais. O que falta é uma rodada de
diagnóstico de eficiência/desempenho sobre a integração com Track N'Me
e SGA — o sistema já funciona corretamente, essa etapa é sobre deixá-lo
mais rápido.

## Documentação

- `docs/pop_administrador.md` / `docs/pop_operador.md` — manuais
  operacionais completos (também publicados como páginas web em
  `docs/manuais/`).
- `docs/regras_negocio.md` / `docs/regras_negocio_instalacao_remocao.md`
  — cada regra do motor, com os critérios exatos.
- `docs/deploy_vercel.md` — como o deploy funciona.
- `docs/prompt_claude_code.md` — o prompt de planejamento original que
  deu origem ao projeto.

## Desenvolvimento local

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/pytest tests/ -q

cd webapp
npm install
npm run dev
```

Credenciais locais (Supabase, Google, Track N'Me/SGA) não são
versionadas — pedir ao administrador do projeto.

---

Desenvolvido por Devon em parceria com a Viver de Rastreamento —
devon@hazelab.tec.br
