/**
 * Provisionamento de máquina nova — recebe um token de uso único gerado
 * pelo Painel Admin, devolve o pacote de credenciais decifrado do Vault
 * (Supabase) + uma chave de máquina nova pra sincronizações futuras
 * (Peças 2/3 do plano de arquitetura, `whimsical-growing-neumann.md`).
 *
 * Placeholder — implementação real é a Fase 0 do plano (schema do Vault/
 * provisioning_tokens ainda não existe no Supabase).
 */
export async function POST() {
  return Response.json(
    { erro: "Não implementado — ver Fase 0 do plano de arquitetura." },
    { status: 501 },
  );
}
