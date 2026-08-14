/**
 * Manifesto de release do Painel Operador — consultado pelo Launcher.exe
 * em cada máquina local (Peça 5 do plano de arquitetura,
 * `whimsical-growing-neumann.md`). Resposta esperada quando implementado:
 * `{ versao: string, url_download: string, sha256: string }`.
 *
 * Placeholder — implementação real é a Fase 1 do plano.
 */
export async function GET() {
  return Response.json(
    { erro: "Não implementado — ver Fase 1 do plano de arquitetura." },
    { status: 501 },
  );
}
