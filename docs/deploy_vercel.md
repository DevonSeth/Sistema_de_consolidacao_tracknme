# Deploy na Vercel + Launcher/auto-update — guia passo a passo (Fase 0 e Fase 1)

Este é o documento que `docs/deploy_vps_oci.md` já avisava que precisaria
existir quando a Fase 0 (antes pensada pra VPS OCI, revista pra Vercel em
2026-08-11) fosse feita de verdade. Cobre as duas fases que faltam pra
completar a arquitetura de distribuição: **Fase 0** (deploy do `webapp/`
na Vercel + fundação de provisionamento de credencial) e **Fase 1**
(Launcher/auto-update do Painel Operador local).

Decisões técnicas fechadas com o usuário em 2026-08-14 (chat #16, via
`AskUserQuestion`) que moldam este guia:
1. **Git + GitHub** (não Vercel CLI solto) — o projeto hoje não é um
   repositório git nem tem git instalado nesta máquina.
2. **GitHub Releases** pra hospedar o pacote (.zip) do Launcher — não
   Vercel Blob.
3. **Tabela no Supabase** (`launcher_versao_atual`) como fonte da
   "versão mais nova" — não consulta direto a API do GitHub.

Cada passo abaixo diz **quem faz**: 🧑 você (ação que só você pode
tomar — conta pessoal, botão de UI externa) ou 🤖 eu (código/config que
eu escrevo e você aprova).

---

## Achado importante antes de começar: bug no `.gitignore` da raiz

O projeto já tem um `.gitignore` na raiz (nunca usado de verdade, porque
nunca houve `git init`). Ele tem uma regra `*.json` (com exceções só pra
`requirements.txt`/`supabase/**/*.json`) — pensada pra esconder
`config/*.json` do Python (que hoje nem existe mais, é tudo keyring).
**Problema**: essa regra também bloquearia `webapp/package.json`,
`webapp/package-lock.json` e `webapp/tsconfig.json` — sem eles versionados,
o build na Vercel quebraria (não saberia as dependências nem a config do
TypeScript). Isso precisa de 3 linhas de exceção novas no `.gitignore` da
raiz antes do primeiro commit (passo 0.3 abaixo).

---

## FASE 0 — Deploy do `webapp/` na Vercel + fundação de provisionamento

### 0.1 🧑 Instalar o Git nesta máquina

Não está no PATH desta sessão (pode já estar instalado noutro perfil,
mas o mais seguro é reinstalar/confirmar). Caminho mais rápido no
Windows 11, PowerShell:

```powershell
winget install --id Git.Git -e --source winget
```

Depois, **feche e abra um terminal novo** (o PATH só atualiza numa sessão
nova) e confirme:

```powershell
git --version
```

Se `winget` não estiver disponível, baixe o instalador em
https://git-scm.com/download/win e rode com as opções padrão.

**Status**: instalado em 2026-08-14 via `winget install --id Git.Git -e
--source winget --accept-package-agreements --accept-source-agreements`
(versão 2.55.0.3). Mesma pegadinha do Node.js: o PATH não atualiza numa
sessão de terminal já aberta — sessões novas de PowerShell (Claude Code
ou não) precisam de `$env:Path += ";C:\Program Files\Git\cmd"` no início,
até reiniciar a máquina (ou abrir um terminal novo depois da instalação,
que já pega o PATH atualizado sozinho).

### 0.2 🧑 Criar o repositório no GitHub

1. Crie (se ainda não tiver) uma conta em https://github.com.
2. "New repository" → nome (ex: `consolidacao-tracknme-puma`) →
   **Private** → **não** marque "Add a README"/".gitignore"/"license"
   (o projeto já tem arquivos, evita conflito na hora de subir).
3. Guarde a URL do repositório (`https://github.com/<seu-usuario>/<repo>.git`).

### 0.3 🤖 Corrigir o `.gitignore`, inicializar o repositório local e configurar identidade

Eu faço (com sua aprovação, já que envolve rodar `git init`/`git add`):
- Adiciono as 3 exceções de `.json` que faltam no `.gitignore` da raiz.
- Confirmo que `webapp/.env.local` (segredos reais do Supabase) e
  `webapp/node_modules/` continuam ignorados (já estão, via
  `webapp/.gitignore`).
- Rodo `git init`, revisamos juntos a saída de `git status` (garantir
  que nada sensível apareceu pra ser commitado), e o primeiro commit.
- Configuro `git remote add origin <url do passo 0.2>`.

**Nota sobre `_handoff/`**: continua fora do git (regra já existente no
`.gitignore`) — são scripts de 1x e o HANDOFF.md, que já vivem no seu
disco local + na memória persistente do Claude Code. Se no futuro você
quiser versionar o HANDOFF.md especificamente, é só me pedir.

### 0.4 🧑 Primeiro push

```powershell
git push -u origin main
```

(Se pedir login, o Git vai abrir o navegador pra autenticar com sua
conta GitHub — fluxo padrão, sem senha digitada no terminal.)

### 0.5 🧑 Criar o projeto na Vercel e conectar ao repositório

1. Conta em https://vercel.com (pode entrar direto com a conta GitHub).
2. "Add New..." → "Project" → escolher o repositório criado no passo 0.2
   (a Vercel pede permissão pra ler seus repositórios GitHub — autorize
   só o repositório específico, não "todos", se a tela oferecer essa
   opção granular).
3. **Configuração de build** — como o `webapp/` não está na raiz do
   repositório, na tela de import:
   - **Root Directory**: `webapp`
   - **Framework Preset**: a Vercel deve detectar "Next.js" sozinha
     depois de apontar o Root Directory certo.
   - Build Command / Output Directory / Install Command: deixe nos
     defaults (a Vercel já sabe o padrão de um projeto Next.js).

### 0.6 🧑 Configurar as variáveis de ambiente na Vercel

Ainda na tela de configuração do projeto (ou depois, em Project
Settings → Environment Variables), adicionar exatamente as 2 que o
código já espera (`webapp/src/lib/supabase-server.ts`):

| Nome | Valor | Observação |
|---|---|---|
| `SUPABASE_URL` | o mesmo valor que já está em `webapp/.env.local` | pode marcar como normal |
| `SUPABASE_SERVICE_ROLE_KEY` | o mesmo valor que já está em `webapp/.env.local` | **marque como "Sensitive"** (a Vercel encripta e não mostra o valor de novo depois de salvo) |

Aplique nos 3 ambientes (Production/Preview/Development) — o app
inteiro é server-rendered com a `service_role`, então até os previews de
PR precisam da mesma config pra funcionar.

Eu não vejo o conteúdo real de `webapp/.env.local` neste chat (só sei
que existe e tem os nomes certos) — copie os valores direto do arquivo
no seu editor pra colar na Vercel, sem me colar aqui.

### 0.7 🧑 Primeiro deploy + smoke test

A Vercel dispara o deploy sozinha assim que o projeto é criado (e depois,
automaticamente, a cada `git push` pra `main`). Quando terminar:
1. Abra a URL `https://<nome-do-projeto>.vercel.app/admin/configuracao`.
2. Confirme que a tela carrega e que "Testar conexão" funciona pra
   Supabase/Newmo/Google Sheets (mesmos botões que já funcionam local).
3. Confirme `/dashboard` carrega os KPIs reais.

Se algo quebrar aqui, provavelmente é env var faltando/errada — a
Vercel mostra o log de build e de runtime na própria tela do projeto.

### 0.8 🤖 Implementar de verdade os 2 endpoints que faltam

Hoje `POST /api/operador/provisionar` e `GET /api/operador/credenciais/
versao` são stubs (sempre HTTP 501). Contrato proposto (vou desenhar
com `EnterPlanMode` quando chegarmos aqui, isto é só a visão geral):

- **`POST /api/operador/provisionar`** — recebe `{ token }` (o token de
  uso único que o Admin gera na tela de Configuração). Valida contra
  `provisioning_tokens` (hash bate, não expirou, não usado ainda) →
  marca o token como usado → cria uma linha nova em `maquinas_operador`
  com uma `chave_maquina` gerada na hora → chama a RPC `credenciais_
  buscar_decifrado` pra cada seção de credencial → devolve tudo
  (credenciais decifradas + a `chave_maquina` nova) numa resposta só.
- **`GET /api/operador/credenciais/versao`** — a máquina manda sua
  `chave_maquina` (autenticação — ainda a definir se por header
  `Authorization: Bearer` ou HMAC assinado, decido na hora do plano) →
  o endpoint confere contra `maquinas_operador` → compara com
  `credenciais_versao` → responde se precisa sincronizar de novo.

### 0.9 🤖 Lado Python: consumir o provisionamento

- `main.py` ganha parsing de argumento `--provisionar <token>` (hoje não
  existe nenhum parsing de CLI arg).
- Nova função (`config/manager.py` ou um novo `integrations/
  provisionamento_client.py`) que chama `POST /api/operador/
  provisionar` com o token, recebe as credenciais + `chave_maquina`, e
  grava tudo no keyring via `salvar_config` (já existe, sem mudança).
- Nova checagem, na abertura normal do app (não só no provisionamento),
  que chama `GET /api/operador/credenciais/versao` e sincroniza se
  mudou — este é o ponto que fecha o ciclo de rotação de credencial.

### 0.10 🧑🤖 Validação ao vivo (juntos)

Mesmo princípio de sempre pra testar API/função nova pela primeira vez —
com dado descartável, nunca produção real:
1. Gero um token de teste (via SQL direto, uso único, expiração curta).
2. Rodamos `python main.py --provisionar <token-de-teste>` numa
   "máquina" de teste (pode ser esta mesma, com um keyring de teste
   separado).
3. Confirmamos: keyring gravado certo, token marcado como usado,
   reusar o mesmo token é bloqueado, token expirado é bloqueado.
4. Limpo o dado de teste (`maquinas_operador`/`provisioning_tokens` de
   teste) ao final.

**Fase 0 concluída quando**: `/api/operador/provisionar` e `/api/
operador/credenciais/versao` respondem de verdade (não mais 501), e o
teste acima passa.

---

## FASE 1 — Launcher/auto-update do Painel Operador

Depende da Fase 0 estar no ar (o Launcher chama `GET /api/operador/
versao-atual`, que já existe como rota mas ainda em stub).

### 1.1 🧑 Rodar o SQL da tabela `launcher_versao_atual`

Eu preparo o script (`_handoff/sql_fase1_launcher_versao.sql`), você
roda no SQL Editor do Supabase (DDL não passa por `service_role_key`,
mesma regra de sempre). Estrutura: `versao` (text), `url_download`
(text — aponta pro asset do GitHub Release), `sha256` (text),
`publicado_em` (timestamp).

### 1.2 🤖 Implementar `GET /api/operador/versao-atual` de verdade

Lê a linha (única, sempre a mais recente) de `launcher_versao_atual` e
devolve `{ versao, url_download, sha256 }` — já é exatamente o formato
que o comentário no stub já documenta.

### 1.3 🤖 `.spec` do PyInstaller pro `PainelOperador.exe`

Empacota `main.py` + `ui/` num único `.exe`, com os navegadores do
Playwright **embutidos** (evita `playwright install` em cada máquina —
decisão já fechada no plano de arquitetura). Vou desenhar isso com
`EnterPlanMode` quando chegarmos aqui (primeira vez usando PyInstaller
neste projeto — regra de sempre: testar num artefato descartável antes
de qualquer coisa "oficial").

### 1.4 🤖 O `Launcher.exe` (app separado, pequeno)

Programinha à parte, com o mínimo de dependências (não usa Playwright
nem pywebview) — na abertura:
1. Chama `GET /api/operador/versao-atual`.
2. Se a pasta local `versoes/<versao>/` já existe, só executa o
   `PainelOperador.exe` de dentro dela.
3. Se não existe: baixa o `.zip` de `url_download` (GitHub Releases),
   confere o `sha256`, extrai pra `versoes/<versao>/`, executa de lá.

Isso nunca sobrescreve um `.exe` em execução (decisão já fechada —
frágil no Windows) — cada versão vive na sua própria pasta.

### 1.5 🧑🤖 Testar o build localmente (juntos)

Rodar o `Launcher.exe` + `PainelOperador.exe` gerados nesta própria
máquina antes de publicar qualquer coisa — confirma que o executável
funciona fora do `.venv` (dependências realmente embutidas).

### 1.6 🧑 Publicar a 1ª release no GitHub Releases

Depois do build validado localmente: criar uma release no GitHub (UI ou
`gh release create`, se instalarmos a CLI do GitHub também), subir o
`.zip` como asset, anotar a URL de download direta do asset.

### 1.7 🤖 Atualizar `launcher_versao_atual` com a release real

Script de 1x (ou você mesmo, é só um `UPDATE`) gravando `versao`/
`url_download`/`sha256` da release publicada no passo 1.6.

### 1.8 🧑🤖 Validação: "versão 2" fake (juntos)

Mesmo teste que o plano de arquitetura já previa: publicar uma "v2"
fake (pode ser literalmente a mesma build com o número trocado),
confirmar que o Launcher baixa e roda a v2 **sem tocar** na pasta da v1
(a v1 continua intacta e executável).

**Fase 1 concluída quando**: o teste do passo 1.8 passa e o `Launcher.exe`
é o único `.exe` que o usuário final abre no dia a dia.

---

## Resumo — quem faz o quê, na ordem

| # | Passo | Quem |
|---|---|---|
| 0.1 | Instalar Git | 🧑 |
| 0.2 | Criar repo no GitHub | 🧑 |
| 0.3 | Corrigir `.gitignore` + `git init` + 1º commit | 🤖 (com aprovação) |
| 0.4 | `git push` | 🧑 |
| 0.5 | Criar projeto na Vercel (Root Directory = `webapp`) | 🧑 |
| 0.6 | Env vars na Vercel | 🧑 |
| 0.7 | Smoke test do deploy | 🧑 (eu confirmo junto) |
| 0.8 | Implementar `/provisionar` e `/credenciais/versao` | 🤖 |
| 0.9 | `main.py --provisionar` + client Python | 🤖 |
| 0.10 | Validar com token de teste descartável | 🧑🤖 |
| 1.1 | SQL `launcher_versao_atual` | 🧑 |
| 1.2 | Implementar `/versao-atual` de verdade | 🤖 |
| 1.3 | `.spec` PyInstaller do Painel Operador | 🤖 |
| 1.4 | `Launcher.exe` | 🤖 |
| 1.5 | Testar build local | 🧑🤖 |
| 1.6 | Publicar release no GitHub | 🧑 |
| 1.7 | Atualizar tabela com a release real | 🤖 |
| 1.8 | Validar "v2" fake | 🧑🤖 |
