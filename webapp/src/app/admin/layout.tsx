import type { ReactNode } from "react";
import Link from "next/link";

import { lerSegredo } from "@/lib/vault-credenciais";

import { signOutAction } from "../login/actions";

// gid da aba "Instalação-Remoção" na planilha Administrador — não muda ao
// reordenar/renomear colunas, só se a aba em si for apagada e recriada
// (ver _handoff/obter_gid_abas_botoes.py, script de descoberta read-only).
const GID_INSTALACAO_REMOCAO = "969551937";

export default async function AdminLayout({ children }: { children: ReactNode }) {
  const googleSheets = await lerSegredo("google_sheets");
  const planilhaAdminId = String(googleSheets.planilha_administrador_id ?? "");
  const urlPlanilhaAdmin = planilhaAdminId
    ? `https://docs.google.com/spreadsheets/d/${planilhaAdminId}/edit#gid=${GID_INSTALACAO_REMOCAO}`
    : null;

  return (
    <div className="app-shell">
      <nav className="sidebar">
        <div className="sidebar-brand">
          <div className="mark">
            <img src="/logo-viver-de-rastreamento.png" alt="Viver de Rastreamento" />
          </div>
          <div>
            <div className="name">Consolidação Track N&apos;Me</div>
            <div className="sub">Painel Admin</div>
          </div>
        </div>

        <div className="sidebar-section-label">Menu</div>
        <Link className="nav-item" href="/admin/parametros">
          <span className="ic">◧</span> Parâmetros de Negócio
        </Link>
        <Link className="nav-item" href="/admin/cadastros">
          <span className="ic">◫</span> Cadastros
        </Link>
        <Link className="nav-item" href="/admin/configuracao">
          <span className="ic">⚙</span> Configuração
        </Link>
        <Link className="nav-item" href="/admin/dashboards">
          <span className="ic">▤</span> Dashboards
        </Link>
        <Link className="nav-item" href="/admin/logs">
          <span className="ic">🕐</span> Log de Execuções
        </Link>
        <Link className="nav-item" href="/admin/manual">
          <span className="ic">📖</span> Manual do Sistema
        </Link>
        {urlPlanilhaAdmin && (
          <a
            className="nav-item"
            href={urlPlanilhaAdmin}
            target="_blank"
            rel="noopener noreferrer"
            title="Abre a aba Instalação-Remoção no Google Sheets"
          >
            <span className="ic">➕</span> Adicionar Pendências
          </a>
        )}
        <a
          className="nav-item"
          href="tracknme-operador://abrir"
          title="Requer o Launcher já ter rodado ao menos 1 vez nesta máquina"
        >
          <span className="ic">🖥</span> Abrir Painel Operador
        </a>
        {/* Achado 2026-08-26: numa máquina nova, o link acima não faz nada
            até o Launcher.exe rodar 1x (é ele que registra o protocolo
            tracknme-operador:// no Windows) — sem um jeito de baixá-lo,
            o botão parecia quebrado. `/releases/latest/download/<asset>`
            é a URL estável do GitHub: resolve pro asset da release
            marcada "latest", sem precisar mudar este link a cada versão
            nova — só é preciso lembrar de sempre anexar Launcher.exe na
            release mais recente (ver docs/deploy_vercel.md seção 1.6). */}
        <a
          className="nav-item nav-item-secundario"
          href="https://github.com/DevonSeth/Sistema_de_consolidacao_tracknme/releases/latest/download/Launcher.exe"
          title="Baixe e rode 1 vez — só necessário na 1ª vez nesta máquina"
        >
          <span className="ic">⬇</span> Baixar Launcher (1ª vez nesta máquina)
        </a>

        <div className="sidebar-footer">
          <div className="papel">👤 Administrador</div>
          <form action={signOutAction}>
            <button type="submit" className="btn small no-print" style={{ marginTop: 6 }}>
              Sair
            </button>
          </form>
          <br />
          Pra rodar as etapas do robô, use o Painel Operador — não duplicado
          aqui.
          <br />
          <br />
          Desenvolvido por Devon em parceria com a Viver de Rastreamento —
          devon@hazelab.tec.br
        </div>
      </nav>

      <main className="content">{children}</main>
    </div>
  );
}
