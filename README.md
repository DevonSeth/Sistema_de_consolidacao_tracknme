# Sistema de Pendências — Instalação/Remoção + Manutenção

Esqueleto do projeto, organizado por responsabilidade. Nenhum arquivo aqui tem lógica de
negócio implementada ainda — são stubs com docstrings explicando o que cada módulo deve
fazer, prontos para serem desenvolvidos no Claude Code.

## Estrutura de pastas

```
config/           -> leitura/escrita de configuração local (credenciais + seed);
                      config.json e o .json da service account do Google ficam
                      aqui dentro (nenhum dos dois é versionado, ver .gitignore)
core/             -> lógica de negócio pura (sem I/O, sem rede, fácil de testar)
integrations/     -> clientes de cada sistema externo (Supabase, Newmo, Google, Playwright)
orchestrator/     -> amarra tudo: a rotina diária completa, por etapas
ui/               -> interface de configuração e operação (pywebview + HTML/CSS/JS)
logs/             -> logs locais em runtime (não versionar conteúdo, só a pasta)
referencia_legado/ -> scripts antigos (broquel_bot.py, sga_bot.py, sheet_manager.py)
                      e docs de referência (API Newmo, formato do webhook) — só
                      consulta de seletores/lógica de negócio, NUNCA importado
                      pelo app novo. Apagar esta pasta (ou excluí-la do build)
                      antes de gerar o executável final com PyInstaller.
```

## Princípios que guiaram essa organização

- **Nenhuma credencial hardcoded.** Tudo passa por `config/manager.py`.
- **Lógica de negócio (`core/`) não conhece Playwright, Supabase nem Sheets.** Ela recebe
  dados já carregados e devolve decisões — isso é o que permite testar sem rede.
- **Cada integração é isolada e testável sozinha** (`integrations/*.py`), para bater com a
  Fase 4 do nosso planejamento (módulos isolados, um por vez).
- **`orchestrator/pipeline.py`** é o único lugar que conhece a ordem das etapas (Fases A-G
  que já fechamos) — a UI só chama funções daqui, nunca implementa fluxo por conta própria.

## Ordem sugerida de implementação (retomando o que já fechamos)

1. `config/manager.py` — sem isso, nada mais funciona de forma segura.
2. `integrations/supabase_client.py` — todo o resto depende de gravar/ler daqui.
3. `integrations/google_sheets_client.py` — leitura das 3 planilhas do administrador.
4. `core/normalizacao.py`, `core/dedup.py` — lógica pura, sem dependência externa.
5. `core/motor_regras.py` — o motor de Manutenção (porta da lógica do n8n).
6. `integrations/tracknme_bot.py`, `integrations/sga_bot.py` — os robôs Playwright.
7. `integrations/newmo_client.py` — disparo de WhatsApp.
8. `integrations/gmail_client.py`, relatórios.
9. `orchestrator/pipeline.py` — amarra tudo.
10. `ui/` — tela de configuração e operação.
