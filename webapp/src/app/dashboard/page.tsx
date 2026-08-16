import type { ReactNode } from "react";

import {
  buscarDistribuicaoUrgencia,
  buscarEncaminhadasParaPuma,
  buscarEvolucaoBacklog,
  buscarMetricas,
  buscarPendenciasSemContato,
  buscarPendentesPorCidade,
  buscarSerieDiaria,
  buscarUltimaAtualizacao,
  dataDefaultDesde,
  hojeISO,
} from "@/lib/dashboard-metrics";
import { descricaoMetrica, METRICAS, SECOES, valorMetrica } from "@/lib/dashboard-metricas-meta";
import { createSupabaseServiceClient } from "@/lib/supabase-server";

import { signOutAction } from "../login/actions";
import BotaoImprimir from "./BotaoImprimir";
import CabecalhoImpressao from "./CabecalhoImpressao";
import DistribuicaoUrgencia from "./DistribuicaoUrgencia";
import EncaminhadasParaPuma from "./EncaminhadasParaPuma";
import EstadoPorOrigem from "./EstadoPorOrigem";
import EvolucaoBacklog from "./EvolucaoBacklog";
import InfoTooltip from "./InfoTooltip";
import PendenciasSemContato from "./PendenciasSemContato";
import PendentesPorCidade from "./PendentesPorCidade";
import PendentesPorTipo from "./PendentesPorTipo";
import TendenciaDiaria from "./TendenciaDiaria";
import UltimaAtualizacao from "./UltimaAtualizacao";

const CHAVES_PAINEL_CUSTOM = new Set([
  "pendentes_por_tipo",
  "estado_por_origem",
  "tendencia_diaria",
  "distribuicao_urgencia",
  "pendencias_sem_contato",
  "pendentes_por_cidade",
  "evolucao_backlog",
  "encaminhar_puma_tabela",
]);

export const metadata = {
  title: "Dashboard — Sistema de Pendências Puma",
};

export default async function DashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ desde?: string; ate?: string }>;
}) {
  const { desde: desdeParam, ate: ateParam } = await searchParams;
  const desde = desdeParam || dataDefaultDesde();
  const ate = ateParam || hojeISO();

  const supabase = createSupabaseServiceClient();
  const { data: visibilidadeBruta } = await supabase
    .from("dashboard_metricas_cliente")
    .select("chave, visivel");
  const visiveis = new Set((visibilidadeBruta ?? []).filter((l) => l.visivel).map((l) => l.chave));

  const metricas = visiveis.size > 0 ? await buscarMetricas(desde, ate) : null;
  const serieDiaria = visiveis.has("tendencia_diaria") ? await buscarSerieDiaria(desde, ate) : null;
  const distribuicaoUrgencia = visiveis.has("distribuicao_urgencia") ? await buscarDistribuicaoUrgencia() : null;
  const pendenciasSemContato = visiveis.has("pendencias_sem_contato") ? await buscarPendenciasSemContato() : null;
  const pendentesPorCidade = visiveis.has("pendentes_por_cidade") ? await buscarPendentesPorCidade() : null;
  const evolucaoBacklog = visiveis.has("evolucao_backlog") ? await buscarEvolucaoBacklog() : null;
  const encaminhadasParaPuma = visiveis.has("encaminhar_puma_tabela") ? await buscarEncaminhadasParaPuma() : null;
  const ultimaAtualizacao = await buscarUltimaAtualizacao();

  // Painel custom de cada chave, já resolvido (ou null se não visível) —
  // usado abaixo pra agrupar cada gráfico/tabela dentro da MESMA seção de
  // tempo dos KPIs relacionados a ele (em vez de todos os gráficos juntos
  // no topo, separados dos números).
  const painelPorChave: Record<string, ReactNode | null> = metricas
    ? {
        tendencia_diaria: serieDiaria && (
          <div className="full" key="tendencia_diaria">
            <TendenciaDiaria serie={serieDiaria} descricao={descricaoMetrica("tendencia_diaria")} />
          </div>
        ),
        estado_por_origem: visiveis.has("estado_por_origem") && (
          <EstadoPorOrigem key="estado_por_origem" metricas={metricas} descricao={descricaoMetrica("estado_por_origem")} />
        ),
        pendentes_por_tipo: visiveis.has("pendentes_por_tipo") && (
          <PendentesPorTipo key="pendentes_por_tipo" metricas={metricas} descricao={descricaoMetrica("pendentes_por_tipo")} />
        ),
        distribuicao_urgencia: distribuicaoUrgencia && (
          <DistribuicaoUrgencia
            key="distribuicao_urgencia"
            distribuicao={distribuicaoUrgencia}
            descricao={descricaoMetrica("distribuicao_urgencia")}
          />
        ),
        evolucao_backlog: evolucaoBacklog && (
          <div className="full" key="evolucao_backlog">
            <EvolucaoBacklog serie={evolucaoBacklog} desde={desde} ate={ate} descricao={descricaoMetrica("evolucao_backlog")} />
          </div>
        ),
        pendencias_sem_contato: pendenciasSemContato && (
          <div className="full" key="pendencias_sem_contato">
            <PendenciasSemContato dados={pendenciasSemContato} descricao={descricaoMetrica("pendencias_sem_contato")} />
          </div>
        ),
        pendentes_por_cidade: pendentesPorCidade && (
          <div className="full" key="pendentes_por_cidade">
            <PendentesPorCidade dados={pendentesPorCidade} descricao={descricaoMetrica("pendentes_por_cidade")} />
          </div>
        ),
        encaminhar_puma_tabela: encaminhadasParaPuma && (
          <div className="full" key="encaminhar_puma_tabela">
            <EncaminhadasParaPuma dados={encaminhadasParaPuma} descricao={descricaoMetrica("encaminhar_puma_tabela")} />
          </div>
        ),
      }
    : {};

  const algumConteudo = metricas != null && METRICAS.some((m) => visiveis.has(m.chave));

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 1100, margin: "0 auto" }}>
      <CabecalhoImpressao />
      <div className="page-header dashboard-header">
        <img src="/logo-viver-de-rastreamento.png" alt="Viver de Rastreamento" className="dashboard-logo" />
        <div>
          <h1>Dashboard</h1>
          <div className="desc">Sistema de Pendências Puma — acompanhamento por período.</div>
          <UltimaAtualizacao iso={ultimaAtualizacao} />
        </div>
        <form action={signOutAction} className="no-print" style={{ marginLeft: "auto" }}>
          <button type="submit" className="btn small">
            Sair
          </button>
        </form>
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

      {!metricas || !algumConteudo ? (
        <div className="linha-vazia">
          {!metricas ? "Nenhuma métrica disponível no momento." : "Nenhuma métrica liberada pro cliente ainda."}
        </div>
      ) : (
        SECOES.map((secao) => {
          const paineis = METRICAS.filter(
            (m) => m.secao === secao.chave && CHAVES_PAINEL_CUSTOM.has(m.chave) && painelPorChave[m.chave]
          ).map((m) => painelPorChave[m.chave]);
          const kpis = METRICAS.filter((m) => m.secao === secao.chave && visiveis.has(m.chave) && !CHAVES_PAINEL_CUSTOM.has(m.chave));

          if (paineis.length === 0 && kpis.length === 0) return null;

          return (
            <div className="grupo-parametros" key={secao.chave}>
              <div className="grupo-titulo">{secao.label}</div>
              <div className="grupo-desc">{secao.desc}</div>

              {paineis.length > 0 && <div className="grafico-grid">{paineis}</div>}

              {kpis.length > 0 && (
                <div className="kpi-grid">
                  {kpis.map((m) => (
                    <div className="kpi" key={m.chave}>
                      <div className="rotulo">
                        {m.label}
                        {m.descricao && <InfoTooltip texto={m.descricao} label={m.label} />}
                      </div>
                      <div className="valor">{valorMetrica(m.chave, metricas)}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })
      )}

      <footer className="dashboard-footer">
        Desenvolvido por Devon em parceria com a Viver de Rastreamento —
        devon@hazelab.tec.br
      </footer>
    </main>
  );
}
