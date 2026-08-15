import { createSupabaseServiceClient } from "@/lib/supabase-server";

/**
 * Manifesto de release do Painel Operador — consultado pelo Launcher.exe
 * em cada máquina local (Fase 1, passo 1.2). Sem autenticação — é só
 * metadado público de release (versão/URL/hash), não segredo; o Launcher
 * ainda nem tem `chave_maquina` na primeira execução.
 */
export async function GET() {
  try {
    const supabase = createSupabaseServiceClient();
    const { data, error } = await supabase
      .from("launcher_versao_atual")
      .select("versao, url_download, sha256")
      .order("publicado_em", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (error) throw new Error(error.message);
    if (!data) {
      return Response.json({ erro: "Nenhuma versão publicada ainda." }, { status: 404 });
    }
    return Response.json(data, { status: 200 });
  } catch (e) {
    return Response.json(
      { erro: e instanceof Error ? e.message : "Erro desconhecido ao consultar versão." },
      { status: 500 },
    );
  }
}
