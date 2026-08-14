/**
 * Checagem de versão do pacote de credenciais — cada Painel Operador
 * chama isso ao abrir (assinado com a chave da própria máquina) pra saber
 * se precisa sincronizar um segredo rotacionado (Peça 4 do plano de
 * arquitetura, `whimsical-growing-neumann.md`).
 *
 * Placeholder — implementação real é a Fase 0 do plano.
 */
export async function GET() {
  return Response.json(
    { erro: "Não implementado — ver Fase 0 do plano de arquitetura." },
    { status: 501 },
  );
}
