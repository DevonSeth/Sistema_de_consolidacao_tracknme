import { dataDefaultDesde, hojeISO } from "@/lib/dashboard-metrics";
import { LABEL_ETAPA } from "@/lib/etapa-labels";
import { createSupabaseServiceClient } from "@/lib/supabase-server";

import LogsClient, { type LinhaLog } from "./LogsClient";

export const metadata = {
  title: "Log de Execuções — Painel Admin",
};

// Sem retenção definida em log_execucoes ainda — limita o carregamento
// inicial, o filtro de período serve pra ir além disso quando precisar.
const LIMITE_LINHAS = 300;

export default async function LogsPage({
  searchParams,
}: {
  searchParams: Promise<{ desde?: string; ate?: string; etapa?: string }>;
}) {
  const { desde: desdeParam, ate: ateParam, etapa } = await searchParams;
  const desde = desdeParam || dataDefaultDesde();
  const ate = ateParam || hojeISO();

  const supabase = createSupabaseServiceClient();
  let query = supabase
    .from("log_execucoes")
    .select("etapa_id, iniciado_em, finalizado_em, duracao_ms, sucesso, motivo_parada, mensagem")
    .gte("iniciado_em", `${desde}T00:00:00`)
    .lte("iniciado_em", `${ate}T23:59:59`)
    .order("iniciado_em", { ascending: false })
    .limit(LIMITE_LINHAS);
  if (etapa) query = query.eq("etapa_id", etapa);

  const { data } = await query;
  const linhas = (data ?? []) as LinhaLog[];

  const etapasOrdenadas = Object.entries(LABEL_ETAPA);

  return (
    <>
      <div className="page-header">
        <h1>Log de Execuções</h1>
        <div className="desc">
          Duração real de cada etapa do robô, por execução — {linhas.length} de até {LIMITE_LINHAS} linhas no período selecionado.
        </div>
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
        <label>
          Etapa
          <select name="etapa" defaultValue={etapa ?? ""}>
            <option value="">Todas</option>
            {etapasOrdenadas.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn primary small">
          Filtrar
        </button>
      </form>

      <LogsClient linhas={linhas} />
    </>
  );
}
