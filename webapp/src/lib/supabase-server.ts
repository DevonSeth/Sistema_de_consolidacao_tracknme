import { createClient } from "@supabase/supabase-js";
import { createServerClient, type CookieMethodsServer } from "@supabase/ssr";

if (!process.env.SUPABASE_URL || !process.env.SUPABASE_SERVICE_ROLE_KEY) {
  throw new Error(
    "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY ausentes — confira webapp/.env.local"
  );
}

// Checados acima; reatribuídos como `string` porque o TypeScript não
// carrega a narrowing de `process.env.*` (string | undefined) através de
// fronteiras de função/closure (as funções abaixo são chamadas bem depois
// deste módulo terminar de rodar).
const SUPABASE_URL: string = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_ROLE_KEY: string = process.env.SUPABASE_SERVICE_ROLE_KEY;

/**
 * Client sem cookies — CRUD/leitura direta (DAL, Server Actions de negócio
 * dos Passos 3+). Sem sessão de usuário: sempre autentica como
 * `service_role`, mesma chave usada em todo o resto do app (decisão já
 * fechada: este app não tem chave `anon`, é 100% server-rendered).
 */
export function createSupabaseServiceClient() {
  return createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
}

/**
 * Client com cookies de sessão — login, proxy (checagem de sessão) e DAL
 * (`verifySession`). `cookieMethods` decide de onde vêm/pra onde vão os
 * cookies (cookieStore do Next em Server Action/Component, ou
 * request/response do proxy).
 */
export function createSupabaseServerClient(cookieMethods: CookieMethodsServer) {
  return createServerClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    cookies: cookieMethods,
  });
}
