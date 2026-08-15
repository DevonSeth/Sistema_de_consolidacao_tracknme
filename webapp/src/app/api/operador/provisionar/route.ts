import { createHash, randomBytes } from "node:crypto";

import { createSupabaseServiceClient } from "@/lib/supabase-server";
import { lerSegredo, lerSegredoRaw } from "@/lib/vault-credenciais";

/**
 * Provisionamento de máquina nova — recebe um token de uso único gerado
 * pelo Painel Admin, devolve o pacote de credenciais decifrado do Vault
 * (Supabase) + uma chave de máquina nova pra sincronizações futuras via
 * `GET /api/operador/credenciais/versao` (Fase 0, passo 0.8).
 *
 * Schema (`provisioning_tokens`/`maquinas_operador`/`credenciais_versao`)
 * já rodado em produção — ver `_handoff/sql_fase0_vault_credenciais.sql`.
 */

const SECOES_JSON = ["tracknme", "newmo", "supabase", "google_sheets"] as const;

function sha256(valor: string): string {
  return createHash("sha256").update(valor).digest("hex");
}

export async function POST(request: Request) {
  let token: string;
  try {
    const corpo = await request.json();
    token = String(corpo.token ?? "");
  } catch {
    return Response.json({ erro: "Corpo inválido — esperado JSON com { token }." }, { status: 400 });
  }
  if (!token) {
    return Response.json({ erro: "Campo 'token' é obrigatório." }, { status: 400 });
  }

  try {
    const supabase = createSupabaseServiceClient();
    const tokenHash = sha256(token);
    const agora = new Date().toISOString();

    // Update condicional atômico — garante uso único mesmo sob corrida
    // (2 provisionamentos simultâneos com o mesmo token só um vence).
    const { data: tokenRow, error: erroToken } = await supabase
      .from("provisioning_tokens")
      .update({ usado_em: agora })
      .eq("token_hash", tokenHash)
      .is("usado_em", null)
      .gt("expira_em", agora)
      .select()
      .maybeSingle();
    if (erroToken) throw new Error(erroToken.message);
    if (!tokenRow) {
      // Mensagem genérica de propósito — não distingue "não existe" de
      // "expirado" de "já usado" (evita virar oráculo pra tentativa de força bruta).
      return Response.json(
        { erro: "Token inválido, expirado ou já utilizado." },
        { status: 401 },
      );
    }

    const chaveMaquina = randomBytes(32).toString("hex");
    const chaveHash = sha256(chaveMaquina);
    const { error: erroMaquina } = await supabase
      .from("maquinas_operador")
      .insert({ rotulo: tokenRow.rotulo_maquina, chave_hash: chaveHash });
    if (erroMaquina) throw new Error(erroMaquina.message);

    const credenciais: Record<string, unknown> = {};
    for (const secao of SECOES_JSON) {
      credenciais[secao] = await lerSegredo(secao);
    }
    credenciais.google_sheets_arquivo_credenciais = await lerSegredoRaw(
      "google_sheets_arquivo_credenciais",
    );

    return Response.json({ chave_maquina: chaveMaquina, credenciais }, { status: 200 });
  } catch (e) {
    return Response.json(
      { erro: e instanceof Error ? e.message : "Erro desconhecido ao provisionar." },
      { status: 500 },
    );
  }
}
