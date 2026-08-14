import { createSupabaseServiceClient } from "@/lib/supabase-server";

import ParametrosClient, { type ParametroLinha } from "./ParametrosClient";
import { metaDe } from "./meta";

export const metadata = {
  title: "Parâmetros de Negócio — Painel Admin",
};

// Sem isso, o Next.js prerenderia esta página em tempo de build (nenhuma
// API dinâmica é usada aqui — cookies()/searchParams) e congelaria o
// resultado da consulta ao Supabase; força reconsultar a cada request,
// mesmo fix já aplicado em /admin/configuracao.
export const dynamic = "force-dynamic";

export default async function ParametrosPage() {
  const supabase = createSupabaseServiceClient();
  const { data } = await supabase
    .from("system_parameters")
    .select("chave, valor, descricao");

  const parametros: ParametroLinha[] = (data ?? []).map((linha) => ({
    chave: linha.chave,
    valor: linha.valor,
    descricao: linha.descricao,
    meta: metaDe(linha.chave),
  }));

  return (
    <>
      <div className="page-header">
        <h1>Parâmetros de Negócio</h1>
        <div className="desc">
          Limiares e listas hoje fixos no código — editáveis aqui a partir de
          agora.
        </div>
      </div>

      <ParametrosClient parametros={parametros} />
    </>
  );
}
