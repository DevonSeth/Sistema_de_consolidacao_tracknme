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

**URL de produção** (confirmada com o usuário 2026-08-15, depois de eu
não achar isso registrado em lugar nenhum na 1ª tentativa de smoke test
pós-Fase 0):
`https://sistema-de-consolidacao-tracknme.vercel.app`

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
1. Abra a URL `https://sistema-de-consolidacao-tracknme.vercel.app/admin/configuracao`.
2. Confirme que a tela carrega e que "Testar conexão" funciona pra
   Supabase/Newmo/Google Sheets (mesmos botões que já funcionam local).
3. Confirme `/dashboard` carrega os KPIs reais.

Se algo quebrar aqui, provavelmente é env var faltando/errada — a
Vercel mostra o log de build e de runtime na própria tela do projeto.

### 0.8 🤖 Implementar de verdade os 2 endpoints que faltam — CONCLUÍDO 2026-08-15

`POST /api/operador/provisionar` e `GET /api/operador/credenciais/
versao` implementados de verdade (`webapp/src/app/api/operador/`).
Contrato final (desenhado com `EnterPlanMode`):

- **`POST /api/operador/provisionar`** — recebe `{ token }`. Valida
  contra `provisioning_tokens` via `UPDATE ... WHERE token_hash = ? AND
  usado_em IS NULL AND expira_em > now() RETURNING *` (atômico, evita
  corrida de uso duplo) → 401 genérico se não achou linha (não distingue
  "não existe"/"expirado"/"já usado", evita virar oráculo) → cria linha
  em `maquinas_operador` com `chave_maquina` nova (`randomBytes(32)`,
  guardada só como `sha256` em `chave_hash`) → busca as 5 seções via
  `credenciais_buscar_decifrado` → devolve `{ chave_maquina,
  credenciais }`.
- **`GET /api/operador/credenciais/versao`** — autenticação por header
  `Authorization: Bearer <chave_maquina>` (decisão: TLS da Vercel já
  cobre o transporte, HMAC seria complexidade sem ganho real aqui) →
  hash comparado contra `maquinas_operador.chave_hash` (401 se não achar
  ou `revogado_em` preenchido) → devolve `{ versoes, credenciais }`
  **sempre as 5 seções** (não só quem mudou) — fecha o ciclo de rotação
  com só 2 endpoints, sem precisar de um 3º; quem decide o que aplicar
  localmente é o cliente Python, comparando contra a última versão
  conhecida.
- Lógica de leitura do Vault extraída pra `webapp/src/lib/vault-
  credenciais.ts` (`lerSegredo`/`lerSegredoRaw`/`listarVersoes`),
  reaproveitada também por `admin/configuracao/actions.ts` (antes
  duplicada ali).
- **Achado**: `google_sheets.credenciais_path` guardado no Vault é o
  caminho da máquina que migrou primeiro (`_handoff/migrar_credenciais_
  vault.py`) — nunca reaproveitável noutra máquina. O passo 0.9 sempre
  substitui esse campo pelo caminho local recém-escrito.

### 0.9 🤖 Lado Python: consumir o provisionamento — CONCLUÍDO 2026-08-15

- `main.py` ganhou `--provisionar <token> --base-url <url>` (via
  `argparse`, `main(argv=None)` — `argv` explícito evita depender do
  `sys.argv` de quem chama `main()` programaticamente, ex: testes).
- `integrations/provisionamento_client.py` (novo): `provisionar_maquina`
  (chama `POST /provisionar`, escreve o `.json` da service account do
  Google Sheets num arquivo local novo, e grava tudo via `config.
  manager.salvar_config` — sem lógica de keyring nova) e `verificar_e_
  sincronizar` (chamada na abertura normal, via `main.py` sem args) —
  no-op silencioso se a máquina nunca foi provisionada por este fluxo,
  e **soft-fail** em qualquer erro de rede/HTTP (nunca derruba a
  abertura do app, mesmo espírito do watchdog). Só reaplica localmente
  as seções cuja versão realmente mudou (`versoes_conhecidas`, campo
  novo e não-secreto em `config.json`, seção `provisionamento`).
- `config/manager.py` ganhou a seção `provisionamento` (`base_url`
  não-secreto, `chave_maquina` no keyring) — reaproveitando 100% a
  infraestrutura genérica já existente (`CAMPOS_OBRIGATORIOS`/
  `CAMPOS_SECRETOS`/`salvar_config`), sem mudar nenhuma lógica.
- 8 testes novos (`tests/test_provisionamento_client.py` + 2 em
  `tests/test_main.py`), tudo mockado (nunca toca o keyring real desta
  máquina).

### 0.10 🧑🤖 Validação ao vivo — CONCLUÍDO 2026-08-15

`_handoff/verificar_provisionamento.py` (mantido no repo como registro,
só leitura de segredo — nunca imprime valor, só confirma presença/
formato das chaves): insere 2 tokens de teste direto via `service_role`
(não precisou de SQL manual do usuário — `provisioning_tokens` é tabela
normal, sem policy, só `service_role` toca) e valida contra o servidor
`next dev` real (Vault real) via `httpx` cru:
1. Token válido → 200, formato de `credenciais` correto.
2. Reusar o mesmo token → 401.
3. Token expirado → 401.
4. `GET /credenciais/versao` com a `chave_maquina` real recebida → 200,
   5 seções com versão.
5. `GET` com chave inventada → 401.

**Decisão de segurança importante**: o script **nunca chama
`provisionar_maquina`/`verificar_e_sincronizar` de verdade** — essas
funções gravam segredo via `salvar_config`, que usa o keyring REAL do
SO sob a MESMA chave de serviço (`SERVICO_KEYRING`) da configuração de
produção desta máquina; rodar de verdade sobrescreveria as credenciais
reais desta máquina com valor de teste. Essa lógica de "aplicar
localmente" já está coberta pelos testes mockados (0.9) — o script de
0.10 valida só o contrato HTTP/Vault real. Ao final, apaga os 2 tokens
de teste + a `maquinas_operador` criada no meio do teste.

**Fase 0 concluída** — os 2 endpoints respondem de verdade (não mais
501) e o teste ao vivo passou de ponta a ponta. 613 testes Python.

---

## FASE 1 — Launcher/auto-update do Painel Operador

Depende da Fase 0 estar no ar (o Launcher chama `GET /api/operador/
versao-atual`, que já existe como rota mas ainda em stub).

### 1.1 🧑 Rodar o SQL da tabela `launcher_versao_atual` — CONCLUÍDO 2026-08-15

Script `_handoff/sql_fase1_launcher_versao.sql` rodado no SQL Editor do
Supabase (DDL não passa por `service_role_key`, mesma regra de sempre).
Estrutura: `versao` (text), `url_download` (text — aponta pro asset do
GitHub Release), `sha256` (text), `publicado_em` (timestamp). Tabela
confirmada existindo via `SELECT` read-only.

### 1.2 🤖 Implementar `GET /api/operador/versao-atual` de verdade — CONCLUÍDO 2026-08-15

Lê a linha (única, sempre a mais recente) de `launcher_versao_atual` e
devolve `{ versao, url_download, sha256 }`. Já estava implementado no
código (não era mais stub), mas nunca tinha sido validado ao vivo
porque a tabela do passo 1.1 não existia — achado no início desta
sessão. Validado com linha de teste descartável (`INSERT` direto via
`service_role_key` → `npm run dev` → `curl` na rota real → 200 com o
formato exato → `DELETE` da linha de teste → confirmado vazio de novo).

### 1.3 🤖 `.spec` do PyInstaller pro `PainelOperador.exe` — CONCLUÍDO 2026-08-15

Empacota `main.py` + `ui/` num único `.exe`, com os navegadores do
Playwright **embutidos** (evita `playwright install` em cada máquina).
`PainelOperador.spec` (raiz do projeto), `--onedir` (não `--onefile` —
reextrairia o Chromium embutido a cada abertura, mesmo problema que a
pasta temp do PyInstaller já causava pra outros recursos).

**3 riscos concretos de empacotar pela 1ª vez, todos resolvidos**:
1. `keyring`/`pywebview` escolhem backend por import dinâmico — sem
   `collect_all`/`hiddenimports` explícito, o `.exe` cai num backend
   errado em silêncio. Resolvido com `collect_all("pywebview")`,
   `collect_all("playwright")` e `hiddenimports=["keyring.backends.
   Windows"]`.
2. **Achado novo, não previsto neste guia**: `config/manager.py::
   _diretorio_config()`/`_diretorio_downloads()` (`orchestrator/
   pipeline.py` e `integrations/tracknme_bot.py`) resolviam tudo ao
   lado do `.exe` — funcionava pra 1 exe fixo, mas quebraria o esquema
   do Launcher (1.4) de "cada versão nova em `versoes/<versao>/`": a
   máquina "esqueceria" a credencial provisionada a cada atualização.
   Corrigido: essas 2 funções agora resolvem pra `%LOCALAPPDATA%\
   ConsolidacaoTrackNMe\` (fixo por máquina) quando `sys.frozen` — só
   `ui/app.py::_diretorio_web()` continua ao lado do `.exe` (os assets
   de UI DEVEM vir com a versão). Em dev, nada muda.
3. **Achado ao vivo, testando o artefato descartável**: `chromium.
   launch(headless=True)` (auto-login) e `launch(headless=False)`
   (fallback manual pro humano resolver reCAPTCHA) — ambos usados em
   `tracknme_bot.py` — resolvem pra **2 binários diferentes**
   (`chromium-<rev>/chrome-win64/chrome.exe` vs `chromium_headless_
   shell-<rev>/chrome-headless-shell-win64/chrome-headless-shell.exe`).
   `playwright.chromium.executable_path` só reporta o 1º — checar só
   ele engana (a 1ª versão do `.spec` fazia isso, e o headless quebrava
   em silêncio). O `.spec` final bundla os 2 (`ms-playwright/chromium*`
   descoberto dinamicamente, não hardcoded).
- `main.py` seta `PLAYWRIGHT_BROWSERS_PATH` pro `ms-playwright/` ao
  lado do `.exe` antes de qualquer import de `playwright` rodar (só
  quando `sys.frozen`).
- Novo `main.py --testar-playwright`: diagnóstico standalone (abre e
  fecha o Chromium embutido, sem UI) — usado pra validar isso ao vivo,
  fica como ferramenta permanente de suporte/depuração.
- **Achado de ambiente, não de código**: o 1º teste (numa pasta de
  scratch bem aninhada, ~300 caracteres de caminho) falhou com
  `ENOENT` no `chrome-headless-shell.exe` — não era bug, o caminho
  excedia o `MAX_PATH` clássico do Windows (260). Testando de novo num
  caminho curto (`C:\pinstest\`), tudo funcionou. Implicação pro
  Launcher (1.4): escolher uma pasta de instalação com caminho curto
  (`%LOCALAPPDATA%\ConsolidacaoTrackNMe\versoes\<versao>\` fica em
  ~180 caracteres — folga confortável).
- 5 testes novos (`tests/test_config_manager.py` — ramo `frozen` das 3
  funções de diretório; `tests/test_main.py` — a flag `--testar-
  playwright`). 619 testes Python.
- Validado ao vivo: `.exe` rodando de pasta descartável fora do
  projeto/`.venv`, janela abre, `config/` aparece em `%LOCALAPPDATA%\
  ConsolidacaoTrackNMe\` (não ao lado do `.exe`), config persiste entre
  2 cópias diferentes do `.exe` (prova o objetivo da correção #2),
  Chromium embutido abre com sucesso, sem processo órfão ao fechar.
  `build/`/`dist/` adicionados ao `.gitignore` (nunca versionar — são
  gerados localmente, centenas de MB).

### 1.4 🤖 O `Launcher.exe` (app separado, pequeno) — CONSTRUÍDO 2026-08-15

Programinha à parte, com o mínimo de dependências (não usa Playwright
nem pywebview) — `launcher.py` (raiz do projeto), na abertura:
1. Registra (idempotente) o protocolo `tracknme-operador://` no Windows.
2. Chama `GET /api/operador/versao-atual`.
3. Se a pasta local `versoes/<versao>/` já existe, só executa o
   `PainelOperador.exe` de dentro dela (fast path, sem UI nenhuma).
4. Se não existe: baixa o `.zip` via `GET /api/operador/download/
   {versao}` (ver abaixo — não `url_download` direto), confere o
   `sha256`, extrai pra uma pasta de staging e promove atomicamente pra
   `versoes/<versao>/`, executa de lá.

Isso nunca sobrescreve um `.exe` em execução (decisão já fechada —
frágil no Windows) — cada versão vive na sua própria pasta. Concorrência
(2 Launchers baixando a mesma versão nova ao mesmo tempo): o perdedor da
corrida de `os.replace` trata a falha como sucesso, já que a versão já
está no lugar certo.

**Achado de arquitetura nesta sessão, que mudou o desenho original**: o
repositório GitHub é privado — a URL de download de um asset de release
não é acessível sem autenticação, e o Launcher não pode carregar nenhum
token do GitHub embutido (extraível do binário). Decisão fechada com o
usuário (`AskUserQuestion`): **redirect assinado via Vercel**. Rota nova
`GET /api/operador/download/[versao]` (`webapp/src/app/api/operador/
download/[versao]/route.ts` — primeira rota dinâmica do projeto,
`params: Promise<{versao: string}>`, padrão do Next.js 16.3.0) busca o
`asset_id` da versão em `launcher_versao_atual` (coluna nova, ver
abaixo), chama `GET https://api.github.com/repos/{owner}/{repo}/
releases/assets/{asset_id}` com `Accept: application/octet-stream` e um
token que mora só na Vercel (`GITHUB_RELEASE_TOKEN`, env var *Sensitive*
nova, PAT fine-grained só com leitura de conteúdo neste repositório —
mesmo padrão de `SUPABASE_SERVICE_ROLE_KEY`), sem seguir o redirect
(`redirect: "manual"`) — a API do GitHub responde com um `302` pra uma
URL assinada e temporária do storage. A rota repassa esse `302` pro
Launcher seguir direto: os bytes do `.zip` (pode ter centenas de MB, o
onedir do Painel inclui o Chromium embutido) nunca passam pela function
da Vercel, só a URL passa. Preserva a decisão já fechada em 2026-08-14
de usar GitHub Releases (não Vercel Blob, não Supabase Storage).

**Coluna nova em `launcher_versao_atual`**: `asset_id bigint not null`
(`_handoff/sql_fase1_launcher_versao_asset_id.sql`) — o `url_download`
já existente é só a URL pública de navegador (inútil pra chamar a API
do GitHub num repo privado), continua existindo só como referência
humana.

**Convenção de conteúdo do `.zip`** (relevante pro passo 1.6, fechada
agora pra não virar bug descoberto tarde): os arquivos do onedir do
`PainelOperador` (`dist/PainelOperador/*`) devem ficar **direto na raiz
do zip** — zipar o *conteúdo* da pasta, não a pasta em si. Senão o
executável final cai em `versoes/<versao>/PainelOperador/
PainelOperador.exe`, quebrando a resolução de caminho do Launcher e
alongando o caminho (relevante pro `MAX_PATH`, achado do passo 1.3).

**Decisões de design**: entrypoint `launcher.py` (raiz, irmão de
`main.py`, mesma convenção). `Launcher.spec` é `--onefile` (ao contrário
do `PainelOperador.spec`, que é `--onedir`) — sem Playwright/pywebview/
Chromium embutido, a única dependência "pesada" herdada é `keyring`
(via `config.manager`), cuja reextração pro `%TEMP%` é da ordem de
poucos MB, sub-segundo. UI mínima em `tkinter` (stdlib, zero dependência
nova) — só aparece quando precisa baixar uma versão nova (barra de
progresso simples); fast path (versão já local) abre sem nenhuma janela.
`BASE_URL_PRODUCAO` é uma constante embutida no `launcher.py` (o Windows
invoca o protocolo só com a URI, sem como passar `--base-url`; e não dá
pra ler de `config.manager.carregar_config()` porque isso só existe
*depois* que o Painel já foi provisionado — ordem inversa). `--base-url`
continua existindo como override manual pro passo 1.5.

**Testes**: `tests/test_launcher.py`, 17 casos novos (636 testes
Python no total), 100% mockado (`httpx.stream`/`httpx.get` via
`monkeypatch`, mesmo padrão de `tests/test_provisionamento_client.py`;
`winreg` mockado por um fake com dict em memória — roda nativamente no
Windows do projeto). A rota `/download/[versao]` não tem teste
automatizado (confirmado que o `webapp/` não tem framework de teste
configurado nenhuma outra rota tampouco) — validação é manual (`npm run
dev` + `curl -i`), passo 1.5.

**Ainda não validado ao vivo** (fica pro passo 1.5, joint com o
usuário): build real do `.exe` (`pyinstaller Launcher.spec`), protocolo
registrado de fato no Windows, download+extração+execução de uma versão
real publicada. A rota de download também só pode ser testada de
verdade depois que `GITHUB_RELEASE_TOKEN` estiver configurado na Vercel
e existir uma release real com `asset_id` conhecido (passo 1.6/1.7).

**Registro do protocolo customizado** (2026-08-14, chat #16): o Painel
Admin (`webapp/src/app/admin/layout.tsx`) já tem um link `Abrir Painel
Operador` apontando pra `tracknme-operador://abrir` — hoje não faz nada
(nenhum programa está registrado pra esse protocolo). O instalador/
`Launcher.exe` desta fase precisa registrar esse protocolo no Windows
(chave de registro `HKEY_CURRENT_USER\Software\Classes\tracknme-
operador`, `shell\open\command` apontando pro `Launcher.exe`) — mesmo
princípio usado por launchers tipo Riot/Steam/Discord (`riot://`,
`steam://`). Sem isso, o botão do Admin não abre nada — comportamento
esperado até este passo ser implementado.

### 1.5 🧑🤖 Testar o build localmente (juntos) — CONCLUÍDO 2026-08-15

Validado em 3 partes: (1) `pyinstaller Launcher.spec` gerou o `.exe`
sem erro; (2) rodado de uma pasta descartável fora do projeto —
protocolo `tracknme-operador://` registrado corretamente no Windows,
sem crash quando não há versão publicada; (3) fast path (versão já
local) e **download real** contra uma release de teste descartável no
GitHub (`teste-1.5-descartavel`) — a esteira completa (rota de
redirect → GitHub API → download → SHA256 → extração → promoção)
funcionou ponta a ponta contra produção. Achado ao vivo corrigido na
hora: `abrir_painel()` não tratava `OSError` de um executável inválido
— corrigido (commit `9c755b2`). 637 testes Python. Detalhe completo em
`_handoff/HANDOFF.md`, seção "Fase 1, passo 1.5".

### 1.6 🧑 Publicar a 1ª release no GitHub Releases

Depois do build validado localmente: criar uma release no GitHub (UI ou
`gh release create`, se instalarmos a CLI do GitHub também), subir o
`.zip` como asset, anotar a URL de download direta do asset.

### 1.7 🤖 Atualizar `launcher_versao_atual` com a release real

Script de 1x (ou você mesmo, é só um `INSERT`) gravando `versao`/
`url_download`/`sha256`/**`asset_id`** (novo, passo 1.4 — o ID numérico
do asset na release, necessário pra rota `GET /api/operador/
download/[versao]` chamar a API do GitHub) da release publicada no
passo 1.6.

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
| 0.8 | Implementar `/provisionar` e `/credenciais/versao` | 🤖 ✅ |
| 0.9 | `main.py --provisionar` + client Python | 🤖 ✅ |
| 0.10 | Validar com token de teste descartável | 🧑🤖 ✅ |
| 1.1 | SQL `launcher_versao_atual` | 🧑 ✅ |
| 1.2 | Implementar `/versao-atual` de verdade | 🤖 ✅ |
| 1.3 | `.spec` PyInstaller do Painel Operador | 🤖 ✅ |
| 1.4 | `launcher.py` + `Launcher.spec` + rota `/download/[versao]` + `asset_id` | 🤖 ✅ (código+testes; sem validação ao vivo) |
| 1.4b | Rodar SQL `asset_id` | 🧑 ✅ |
| 1.4c | Configurar `GITHUB_RELEASE_TOKEN` na Vercel | 🧑 ✅ |
| 1.5 | Testar build local (incl. download real contra release de teste) | 🧑🤖 ✅ |
| 1.6 | Publicar release no GitHub | 🧑 |
| 1.7 | Atualizar tabela com a release real (incl. `asset_id`) | 🤖 |
| 1.8 | Validar "v2" fake | 🧑🤖 |
