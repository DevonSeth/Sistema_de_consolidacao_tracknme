import { createSupabaseServiceClient } from "@/lib/supabase-server";

import ConfiguracaoClient, { type ValoresIniciais } from "./ConfiguracaoClient";
import { CAMPOS_SECRETOS, CARDS } from "./meta";

export const metadata = {
  title: "Configuração — Painel Admin",
};

// Sem isso, o Next.js prerenderia esta página em tempo de build (nenhuma
// API dinâmica é usada aqui — cookies()/searchParams) e congelaria o
// resultado da consulta ao Vault; força reconsultar a cada request, pra
// nunca mostrar credencial desatualizada.
export const dynamic = "force-dynamic";

export default async function ConfiguracaoPage() {
  const supabase = createSupabaseServiceClient();

  const valoresIniciais = {} as ValoresIniciais;
  for (const card of CARDS) {
    const { data } = await supabase.rpc("credenciais_buscar_decifrado", {
      p_secao: card.secao,
    });
    const valores = data ? JSON.parse(data as string) : {};
    const secretos = new Set(CAMPOS_SECRETOS[card.secao]);

    const naoSecretos: Record<string, string> = {};
    for (const [chave, valor] of Object.entries(valores)) {
      if (!secretos.has(chave)) naoSecretos[chave] = String(valor);
    }
    valoresIniciais[card.secao] = naoSecretos;
  }

  // Versão atual do Painel Operador — usada só pra montar o caminho do
  // .exe no comando de provisionamento pronto pra copiar (mesma tabela
  // que já alimenta GET /api/operador/versao-atual).
  const { data: versaoRow } = await supabase
    .from("launcher_versao_atual")
    .select("versao")
    .maybeSingle();
  const versaoAtual = versaoRow?.versao ?? "";

  return (
    <>
      <div className="page-header">
        <h1>Configuração</h1>
        <div className="desc">
          Credenciais por integração — testadas antes de rodar qualquer etapa de verdade.
          Editar aqui grava no Vault, mas só passa a valer nas máquinas do Painel Operador
          quando o Launcher (auto-sincronização) existir.
        </div>
      </div>

      <ConfiguracaoClient valoresIniciais={valoresIniciais} versaoAtual={versaoAtual} />
    </>
  );
}
