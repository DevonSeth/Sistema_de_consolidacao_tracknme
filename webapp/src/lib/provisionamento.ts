import { createHash } from "node:crypto";

/**
 * Helper compartilhado entre a geração de token (Server Action do Admin,
 * `webapp/src/app/admin/configuracao/actions.ts`) e o consumo dele
 * (`webapp/src/app/api/operador/provisionar/route.ts`) — o token em si
 * nunca é armazenado, só o hash (`provisioning_tokens.token_hash`).
 */
export function sha256(valor: string): string {
  return createHash("sha256").update(valor).digest("hex");
}
