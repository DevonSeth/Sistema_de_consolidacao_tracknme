import { createSupabaseServiceClient } from "@/lib/supabase-server";

const GITHUB_OWNER = "DevonSeth";
const GITHUB_REPO = "Sistema_de_consolidacao_tracknme";

/**
 * Proxy-redirect pro asset do GitHub Release do Painel Operador (Fase 1,
 * passo 1.4) — consultado pelo Launcher.exe quando precisa baixar uma
 * versão nova. O repositório é privado, então o asset não é baixável sem
 * autenticação; o Launcher não pode carregar nenhum token do GitHub
 * embutido (extraível do binário). Solução: este endpoint chama a API do
 * GitHub com um token que mora só na Vercel, pega a URL assinada e
 * temporária que o GitHub devolve (redirect 302 pro storage) e repassa
 * esse redirect pro Launcher seguir direto — os bytes do .zip (pode ter
 * centenas de MB, o onedir do Painel inclui o Chromium embutido) nunca
 * passam por esta function.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ versao: string }> },
) {
  const { versao } = await params;

  try {
    const token = process.env.GITHUB_RELEASE_TOKEN;
    if (!token) {
      throw new Error("GITHUB_RELEASE_TOKEN ausente — configure a env var na Vercel.");
    }

    const supabase = createSupabaseServiceClient();
    const { data, error } = await supabase
      .from("launcher_versao_atual")
      .select("asset_id")
      .eq("versao", versao)
      .order("publicado_em", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (error) throw new Error(error.message);
    if (!data) {
      return Response.json({ erro: `Versão ${versao} não encontrada.` }, { status: 404 });
    }

    const respostaGithub = await fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/releases/assets/${data.asset_id}`,
      {
        headers: {
          Accept: "application/octet-stream",
          Authorization: `Bearer ${token}`,
          "User-Agent": "tracknme-launcher-download-proxy",
        },
        redirect: "manual",
      },
    );

    const location = respostaGithub.headers.get("location");
    if (respostaGithub.status !== 302 || !location) {
      return Response.json(
        { erro: `Falha ao resolver asset no GitHub (HTTP ${respostaGithub.status}).` },
        { status: 502 },
      );
    }

    return Response.redirect(location, 302);
  } catch (e) {
    return Response.json(
      { erro: e instanceof Error ? e.message : "Erro desconhecido ao gerar link de download." },
      { status: 500 },
    );
  }
}
