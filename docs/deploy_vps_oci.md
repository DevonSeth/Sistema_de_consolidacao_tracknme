# Deploy da VPS (Oracle Cloud Infrastructure) — passo a passo

> **SUPERADO em 2026-08-11 (mesma sessão, chat novo #8)**: a decisão de
> hospedagem mudou de VPS (OCI) pra **Vercel** — ver `_handoff/
> HANDOFF.md`, seção "Arquitetura revista (VPS→Vercel)", e a memória
> persistente `arquitetura_interface_vps_oci`. Nenhum passo abaixo foi
> executado (nem a instância foi criada), então não há nada em produção
> pra desfazer. Este arquivo fica de referência histórica — se a Vercel
> não for viável no futuro, os passos abaixo continuam válidos como
> alternativa. Um doc novo (`deploy_vercel.md`, ou similar) deve ser
> escrito quando a Fase 0 (agora "deploy na Vercel") for feita de
> verdade — ainda não existe.

Documento vivo, começado em 2026-08-11 (Fase 0 do plano de arquitetura
`whimsical-growing-neumann.md`). Objetivo: nenhum passo de infraestrutura
fica "só na cabeça de quem instalou" — qualquer pessoa consegue repetir ou
recriar esse ambiente lendo este arquivo, inclusive migrando pra outro
provedor/conta se precisar no futuro.

Convenção: cada passo diz se é **interativo** (exige conta própria/decisão
humana, alguém precisa estar na tela) ou **automatizável** (comando exato,
pode ser copiado e colado).

## Antes de começar

- Conta Oracle Cloud própria (Always Free) — **interativo**, precisa de
  cartão de crédito pra verificação (não cobra nada dentro do Always Free,
  mas a Oracle exige o cadastro).
- Uma chave SSH (par pública/privada) pra acessar a máquina depois de
  criada — pode ser gerada no passo 2, não precisa ter antes.
- **Ainda em aberto**: um domínio próprio (ex: `algumacoisa.com.br`) pro
  Caddy conseguir emitir HTTPS automático (Let's Encrypt exige um domínio
  real apontando pro IP da VPS — não funciona só com o IP puro). Sem
  domínio, dá pra rodar só em HTTP por enquanto (ok pra testar, não pra
  produção de verdade) ou usar um certificado autoassinado. **Perguntar
  ao usuário antes deste passo virar bloqueante.**

## Passo 1 — Criar a conta Oracle Cloud (interativo)

1. Acessar `https://www.oracle.com/cloud/free/` e criar a conta.
2. Escolher a **home region** com cuidado — no Always Free, **não dá pra
   trocar de region depois** sem recriar tudo. Escolher a mais próxima
   fisicamente (ex: uma region na América do Sul, se disponível pro
   Always Free; senão, a mais próxima que tiver capacidade Ampere A1
   disponível — Oracle às vezes esgota a capacidade grátis em regions
   populares, nesse caso vale tentar outra region do mesmo continente).
3. Confirmar e-mail/cartão — não é cobrado dentro do Always Free.

## Passo 2 — Criar a instância Compute (interativo, feito no console web)

1. No console OCI: **Compute → Instances → Create Instance**.
2. **Image**: Ubuntu (a versão LTS mais recente disponível) — mais
   familiar pra instalar Node/Caddy via `apt`, e a maioria dos guias de
   Caddy assume Debian/Ubuntu.
3. **Shape**: trocar de "Intel/AMD" (padrão) pra **Ampere (arm64)**,
   shape `VM.Standard.A1.Flex` — configurar **1-2 OCPU / 6-12 GB RAM**
   (dá pra ajustar depois; o Always Free cobre até 4 OCPU/24GB no total,
   somado entre todas as instâncias A1 da conta — não precisa gastar
   tudo numa instância só).
4. **Chave SSH**: gerar um par novo aqui mesmo (o console oferece "Generate
   a key pair for me") e **baixar a chave privada na hora** — não tem uma
   segunda chance de baixar depois. Guardar em local seguro (nunca
   versionar no repositório).
5. **Networking**: deixar criar a VCN (rede virtual) padrão sugerida pelo
   assistente, com um **subnet público** (a instância precisa de IP
   público pra ser acessada de fora).
6. Criar. Aguardar o estado virar "Running" e anotar o **IP público**.

## Passo 3 — Abrir as portas 80/443 (duas camadas, as DUAS são necessárias)

**Achado conhecido de quem já mexeu com OCI antes** (já registrado no
HANDOFF): a OCI bloqueia tráfego de entrada por padrão em **duas camadas
independentes** — esquecer uma das duas faz o Caddy parecer "não
funciona" mesmo configurado certo.

1. **Network Security List/Security Group** (console web, camada de
   rede da OCI): **Networking → Virtual Cloud Networks → (a VCN
   criada) → Security Lists → Default Security List → Add Ingress
   Rules**. Adicionar 2 regras: `0.0.0.0/0` porta TCP `80`, e `0.0.0.0/0`
   porta TCP `443`.
2. **Firewall do próprio Ubuntu** (dentro da instância, via SSH,
   **automatizável**):
   ```bash
   sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save
   ```
   (imagens Ubuntu da OCI vêm com `iptables-persistent` já instalado —
   se o comando `netfilter-persistent` não existir, `sudo apt install
   iptables-persistent` antes.)

## Passo 4 — Conectar via SSH (automatizável, depois de ter o IP + chave)

```powershell
ssh -i "caminho\para\a\chave_privada.key" ubuntu@<ip-publico-da-instancia>
```

(usuário padrão das imagens Ubuntu da OCI é `ubuntu`, não `root`.)

## Passo 5 — Instalar Node.js + Caddy na instância (automatizável, via SSH)

```bash
# Node.js LTS (via NodeSource, arm64 é suportado nativamente)
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt-get install -y nodejs

# Caddy (repositório oficial)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

## Passo 6 — Deploy do `webapp/` (automatizável, via SSH — mecanismo exato de transferência de código ainda a decidir: git clone de um repositório privado, ou `scp` direto)

```bash
cd /opt
sudo git clone <url-do-repositorio> sistema-pendencias-puma   # OU scp da pasta webapp/ pra cá
cd sistema-pendencias-puma/webapp
npm ci
npm run build
```

Rodar como serviço persistente (systemd, sobrevive a reboot/queda de
SSH) — criar `/etc/systemd/system/webapp.service`:

```ini
[Unit]
Description=Sistema de Pendencias Puma - webapp (Next.js)
After=network.target

[Service]
WorkingDirectory=/opt/sistema-pendencias-puma/webapp
ExecStart=/usr/bin/npm run start
Restart=always
User=ubuntu
Environment=NODE_ENV=production
Environment=PORT=3000

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now webapp
```

## Passo 7 — Caddy como proxy reverso com HTTPS automático

**Só funciona de verdade com um domínio real apontando pro IP da
instância** (registro DNS tipo A). Editar `/etc/caddy/Caddyfile`:

```
admin.seu-dominio.com.br {
    reverse_proxy localhost:3000
}
dashboard.seu-dominio.com.br {
    reverse_proxy localhost:3000
}
```

(o mesmo processo Next.js atende as duas áreas, `/admin` e `/dashboard`
— o Caddyfile acima é só um exemplo de 2 subdomínios apontando pro mesmo
app; pode ser 1 domínio só com os 2 caminhos, se preferir não usar
subdomínio.)

```bash
sudo systemctl reload caddy
```

**Sem domínio ainda**: rodar só `reverse_proxy localhost:3000` num bloco
`:80` (HTTP puro, sem certificado) — serve pra testar que o deploy
funciona, mas não é o estado final desejado.

## Status

**Nada disso foi executado ainda** — é o roteiro pronto pra quando o
usuário decidir provisionar a instância. Atualizar esta seção conforme
cada passo for executado de verdade (IP real, domínio escolhido, etc.).
