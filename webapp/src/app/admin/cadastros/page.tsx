import Link from "next/link";

import { createSupabaseServiceClient } from "@/lib/supabase-server";

import TabelaBases, { type Base } from "./TabelaBases";
import TabelaPontosAcao, { type PontoAcao } from "./TabelaPontosAcao";
import TabelaRegras, { type Regra } from "./TabelaRegras";

export const metadata = {
  title: "Cadastros — Painel Admin",
};

export default async function CadastrosPage({
  searchParams,
}: {
  searchParams: Promise<{ sub?: string }>;
}) {
  const { sub } = await searchParams;
  const subAtiva = sub === "pontos" ? "pontos" : sub === "regras" ? "regras" : "bases";

  const supabase = createSupabaseServiceClient();

  let basesIniciais: Base[] = [];
  let pontosIniciais: PontoAcao[] = [];
  let regrasIniciais: Regra[] = [];
  if (subAtiva === "bases") {
    const { data } = await supabase
      .from("bases")
      .select("id, nome, endereco, ativo")
      .order("nome");
    basesIniciais = data ?? [];
  } else if (subAtiva === "pontos") {
    const { data } = await supabase
      .from("pontos_acao")
      .select("id, nome_local, endereco, data, ativo")
      .order("data");
    pontosIniciais = data ?? [];
  } else {
    const { data } = await supabase
      .from("rule_templates")
      .select(
        "id, codigo_regra, ativo, prioridade, template_acao, template_observacao, nivel_urgencia"
      )
      .order("prioridade");
    regrasIniciais = data ?? [];
  }

  return (
    <>
      <div className="page-header">
        <h1>Cadastros</h1>
        <div className="desc">
          Bases, pontos de ação e as regras que geram as pendências.
        </div>
      </div>

      <div className="subtabs">
        <Link
          href="?sub=bases"
          className={`subtab-btn ${subAtiva === "bases" ? "on" : ""}`}
        >
          Bases
        </Link>
        <Link
          href="?sub=pontos"
          className={`subtab-btn ${subAtiva === "pontos" ? "on" : ""}`}
        >
          Pontos de Ação
        </Link>
        <Link
          href="?sub=regras"
          className={`subtab-btn ${subAtiva === "regras" ? "on" : ""}`}
        >
          Regras
        </Link>
      </div>

      {subAtiva === "bases" ? (
        <TabelaBases basesIniciais={basesIniciais} />
      ) : subAtiva === "pontos" ? (
        <TabelaPontosAcao pontosIniciais={pontosIniciais} />
      ) : (
        <TabelaRegras regrasIniciais={regrasIniciais} />
      )}
    </>
  );
}
