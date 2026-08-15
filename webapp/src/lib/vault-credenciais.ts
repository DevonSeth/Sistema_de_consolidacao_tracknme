import { createSupabaseServiceClient } from "@/lib/supabase-server";

/**
 * Leitura do Vault (Supabase) — usada tanto pela tela de Configuração
 * (`admin/configuracao/actions.ts`) quanto pelos endpoints de
 * provisionamento do Painel Operador (`api/operador/*`). Escrita
 * (`credenciais_definir`) continua só em `actions.ts`, único lugar que
 * grava credencial hoje.
 */

export async function lerSegredoRaw(secao: string): Promise<string | null> {
  const supabase = createSupabaseServiceClient();
  const { data, error } = await supabase.rpc("credenciais_buscar_decifrado", {
    p_secao: secao,
  });
  if (error) throw new Error(error.message);
  return (data as string | null) ?? null;
}

export async function lerSegredo(secao: string): Promise<Record<string, unknown>> {
  const bruto = await lerSegredoRaw(secao);
  return bruto ? JSON.parse(bruto) : {};
}

/** `{secao: atualizado_em}` das 5 seções guardadas em `credenciais_versao`. */
export async function listarVersoes(): Promise<Record<string, string>> {
  const supabase = createSupabaseServiceClient();
  const { data, error } = await supabase
    .from("credenciais_versao")
    .select("secao, atualizado_em");
  if (error) throw new Error(error.message);
  return Object.fromEntries((data ?? []).map((linha) => [linha.secao, linha.atualizado_em]));
}
