import { createHash } from "node:crypto";

import { createSupabaseServiceClient } from "@/lib/supabase-server";
import { lerSegredo, lerSegredoRaw, listarVersoes } from "@/lib/vault-credenciais";

/**
 * Checagem de versão do pacote de credenciais — cada Painel Operador
 * chama isso ao abrir (autenticado com a chave da própria máquina,
 * recebida no provisionamento) pra saber se algum segredo foi rotacionado
 * (Fase 0, passo 0.8). Devolve as versões (timestamp por seção) E os
 * valores decifrados — fecha o ciclo de rotação sem precisar de um 3º
 * endpoint; quem decide o que aplicar localmente é o cliente Python,
 * comparando contra a última versão que ele já conhece.
 */

const SECOES_JSON = ["tracknme", "newmo", "supabase", "google_sheets"] as const;

function sha256(valor: string): string {
  return createHash("sha256").update(valor).digest("hex");
}

export async function GET(request: Request) {
  const auth = request.headers.get("authorization");
  const chaveMaquina = auth?.startsWith("Bearer ") ? auth.slice("Bearer ".length) : null;
  if (!chaveMaquina) {
    return Response.json({ erro: "Header Authorization: Bearer <chave_maquina> é obrigatório." }, { status: 401 });
  }

  try {
    const supabase = createSupabaseServiceClient();
    const chaveHash = sha256(chaveMaquina);
    const { data: maquina, error: erroMaquina } = await supabase
      .from("maquinas_operador")
      .select("id, revogado_em")
      .eq("chave_hash", chaveHash)
      .maybeSingle();
    if (erroMaquina) throw new Error(erroMaquina.message);
    if (!maquina || maquina.revogado_em) {
      return Response.json({ erro: "Chave de máquina inválida ou revogada." }, { status: 401 });
    }

    const versoes = await listarVersoes();
    const credenciais: Record<string, unknown> = {};
    for (const secao of SECOES_JSON) {
      credenciais[secao] = await lerSegredo(secao);
    }
    credenciais.google_sheets_arquivo_credenciais = await lerSegredoRaw(
      "google_sheets_arquivo_credenciais",
    );

    return Response.json({ versoes, credenciais }, { status: 200 });
  } catch (e) {
    return Response.json(
      { erro: e instanceof Error ? e.message : "Erro desconhecido ao consultar versão." },
      { status: 500 },
    );
  }
}
