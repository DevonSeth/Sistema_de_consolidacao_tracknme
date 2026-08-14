import {
  buscarDistribuicaoUrgencia,
  buscarEvolucaoBacklog,
  buscarMetricas,
  buscarPendenciasSemContato,
  buscarPendentesPorCidade,
  buscarSerieDiaria,
  buscarUltimaAtualizacao,
  dataDefaultDesde,
  hojeISO,
} from "@/lib/dashboard-metrics";
import { createSupabaseServiceClient } from "@/lib/supabase-server";

import BotaoImprimir from "@/app/dashboard/BotaoImprimir";
import CabecalhoImpressao from "@/app/dashboard/CabecalhoImpressao";
import UltimaAtualizacao from "@/app/dashboard/UltimaAtualizacao";

import DashboardsClient from "./DashboardsClient";

export const metadata = {
  title: "Dashboards — Painel Admin",
};

export default async function DashboardsPage({
  searchParams,
}: {
  searchParams: Promise<{ desde?: string; ate?: string }>;
}) {
  const { desde: desdeParam, ate: ateParam } = await searchParams;
  const desde = desdeParam || dataDefaultDesde();
  const ate = ateParam || hojeISO();

  const metricas = await buscarMetricas(desde, ate);
  const serieDiaria = await buscarSerieDiaria(desde, ate);
  const distribuicaoUrgencia = await buscarDistribuicaoUrgencia();
  const pendenciasSemContato = await buscarPendenciasSemContato();
  const pendentesPorCidade = await buscarPendentesPorCidade();
  const evolucaoBacklog = await buscarEvolucaoBacklog();
  const ultimaAtualizacao = await buscarUltimaAtualizacao();

  const supabase = createSupabaseServiceClient();
  const { data: visibilidadeBruta } = await supabase
    .from("dashboard_metricas_cliente")
    .select("chave, visivel, visivel_operador");
  const visibilidade: Record<string, boolean> = {};
  const visibilidadeOperador: Record<string, boolean> = {};
  for (const linha of visibilidadeBruta ?? []) {
    visibilidade[linha.chave] = linha.visivel;
    visibilidadeOperador[linha.chave] = linha.visivel_operador;
  }

  return (
    <>
      <CabecalhoImpressao />
      <div className="page-header">
        <h1>Dashboards</h1>
        <div className="desc">
          Métricas de negócio do período selecionado. O que estiver marcado
          &quot;cliente&quot; também aparece no Dashboard Cliente.
        </div>
        <UltimaAtualizacao iso={ultimaAtualizacao} />
      </div>

      <form method="get" className="filtro-periodo">
        <label>
          De
          <input type="date" name="desde" defaultValue={desde} lang="pt-BR" max={ate} />
        </label>
        <label>
          Até
          <input type="date" name="ate" defaultValue={ate} lang="pt-BR" min={desde} />
        </label>
        <button type="submit" className="btn primary small">
          Filtrar
        </button>
        <BotaoImprimir />
      </form>

      <DashboardsClient
        metricas={metricas}
        visibilidade={visibilidade}
        visibilidadeOperador={visibilidadeOperador}
        serieDiaria={serieDiaria}
        distribuicaoUrgencia={distribuicaoUrgencia}
        pendenciasSemContato={pendenciasSemContato}
        pendentesPorCidade={pendentesPorCidade}
        evolucaoBacklog={evolucaoBacklog}
        desde={desde}
        ate={ate}
      />
    </>
  );
}
