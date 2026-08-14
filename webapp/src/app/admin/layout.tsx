import type { ReactNode } from "react";
import Link from "next/link";

export default function AdminLayout({ children }: { children: ReactNode }) {
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

        <div className="sidebar-footer">
          <div className="papel">👤 Administrador</div>
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
