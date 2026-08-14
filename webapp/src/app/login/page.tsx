import { loginAction } from "./actions";

export const metadata = {
  title: "Login — Consolidação Track N'Me",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ erro?: string }>;
}) {
  const { erro } = await searchParams;

  return (
    <main className="login-shell">
      <div className="login-card">
        <img src="/logo-viver-de-rastreamento.png" alt="Viver de Rastreamento" className="login-logo" />
        <h1>Consolidação Track N&apos;Me</h1>
        <p className="login-sub">Viver de Rastreamento</p>

        <form action={loginAction} className="login-form">
          <label htmlFor="email">E-mail</label>
          <input id="email" name="email" type="email" required />

          <label htmlFor="senha">Senha</label>
          <input id="senha" name="senha" type="password" required />

          {erro && <p className="login-erro">E-mail ou senha inválidos.</p>}

          <button type="submit" className="btn primary">
            Entrar
          </button>
        </form>

        <footer className="login-footer">
          Desenvolvido por Devon em parceria com a Viver de Rastreamento —
          devon@hazelab.tec.br
        </footer>
      </div>
    </main>
  );
}
