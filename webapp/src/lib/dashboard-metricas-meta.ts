import type { Metricas } from "./dashboard-metrics";

// Taxonomia por TEMPO (não por "tipo de número") — decisão desta sessão:
// a taxonomia antiga ("fluxo"/"estado"/"percentuais") misturava métricas
// que respeitam o filtro de período com métricas "agora" na mesma
// caixinha visual (ex: `concluidos` é fluxo dentro do período, mas morava
// ao lado de `pendentes`/`em_andamento`, que são uma reconstrução de
// ESTADO no fim do período — números com semânticas diferentes,
// indistinguíveis na tela). As 3 seções abaixo são mutuamente exclusivas
// por essa semântica de tempo:
//   fluxo_periodo      -> conta EVENTOS que aconteceram DENTRO de [desde,ate]
//   estado_fim_periodo -> reconstrução de "como estava" na data `ate` (RPC dashboard_estado_em)
//   estado_agora       -> sempre o momento real da consulta, ignora o filtro
export type SecaoMetrica = "fluxo_periodo" | "estado_fim_periodo" | "estado_agora";

export type MetaMetrica = {
  chave: string;
  label: string;
  secao: SecaoMetrica;
};

// Mesmas chaves seedadas em `dashboard_metricas_cliente` — Admin sempre vê
// todas, o Dashboard Cliente só as marcadas como `visivel=true` ali.
export const METRICAS: MetaMetrica[] = [
  { chave: "disparos", label: "Total de disparos", secao: "fluxo_periodo" },
  { chave: "retornados", label: "Associados retornados", secao: "fluxo_periodo" },
  { chave: "agendamentos_confirmados", label: "Agendamentos confirmados", secao: "fluxo_periodo" },
  { chave: "concluidos", label: "Serviços concluídos no período", secao: "fluxo_periodo" },
  { chave: "pct_resposta", label: "% de resposta (retornos / disparos)", secao: "fluxo_periodo" },
  { chave: "tempo_medio_resolucao", label: "Tempo médio de resolução", secao: "fluxo_periodo" },
  { chave: "taxa_escalonamento_puma", label: "Taxa de escalonamento pra Puma", secao: "fluxo_periodo" },
  { chave: "tendencia_diaria", label: "Tendência diária (gráfico)", secao: "fluxo_periodo" },

  { chave: "pendentes", label: "Serviços pendentes", secao: "estado_fim_periodo" },
  { chave: "em_andamento", label: "Serviços em andamento", secao: "estado_fim_periodo" },
  { chave: "pct_pendencias", label: "% de pendências", secao: "estado_fim_periodo" },
  { chave: "pct_pendencias_concluidas", label: "% de pendências concluídas", secao: "estado_fim_periodo" },
  { chave: "estado_por_origem", label: "Estado por origem (gráfico)", secao: "estado_fim_periodo" },
  { chave: "pendentes_por_tipo", label: "Pendentes por tipo (Instalação/Remoção/Manutenção)", secao: "estado_fim_periodo" },

  // Sempre o momento real da consulta — não respeita o filtro de período
  // (pedido explícito do usuário).
  { chave: "pendencias_em_aberto", label: "Pendências em aberto (agora)", secao: "estado_agora" },
  { chave: "encaminhadas_puma", label: "Pendências encaminhadas à Puma (agora)", secao: "estado_agora" },
  { chave: "distribuicao_urgencia", label: "Distribuição por nível de urgência (gráfico)", secao: "estado_agora" },
  { chave: "pendencias_sem_contato", label: "Pendências com mais tempo sem contato (tabela)", secao: "estado_agora" },
  { chave: "pendentes_por_cidade", label: "Serviços pendentes por cidade (tabela)", secao: "estado_agora" },
  { chave: "evolucao_backlog", label: "Evolução do backlog (gráfico)", secao: "estado_agora" },
];

export const SECOES: { chave: SecaoMetrica; label: string; desc: string }[] = [
  { chave: "fluxo_periodo", label: "Fluxo no período", desc: "O que aconteceu entre as datas filtradas." },
  { chave: "estado_fim_periodo", label: "Estado no fim do período", desc: "Como a fila estava na data final do filtro." },
  { chave: "estado_agora", label: "Estado agora", desc: "A fila neste momento — não muda com o filtro de período." },
];

// Puramente formatação — sem nenhuma dependência de `supabase-server.ts`,
// por isso mora aqui (não em `dashboard-metrics.ts`) e pode ser importado
// com segurança por Client Components (`DashboardsClient.tsx`,
// `PendentesPorTipo.tsx`). Ver nota em `dashboard-metrics.ts` — importar
// função de lá (não só o tipo) de um "use client" vazaria o módulo do
// Supabase pro bundle do navegador.

export function formatarPercentual(valor: number): string {
  return `${(valor * 100).toFixed(1)}%`;
}

/** Teto "limpo" pro eixo Y de um gráfico (1/2/5 × potência de 10) — evita
 * ticks tipo 0/37/74. Compartilhado pelos gráficos de barra e de linha. */
export function tetoAgradavel(valor: number): number {
  if (valor <= 0) return 4;
  const exp = Math.floor(Math.log10(valor));
  const base = Math.pow(10, exp);
  const frac = valor / base;
  const niceFrac = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
  return niceFrac * base;
}

/** Formata um valor de tick do eixo Y — usado nos 3 ticks `[0, teto/2,
 * teto]` compartilhados pelos gráficos de linha/barra. `teto/2` só é
 * inteiro quando `teto` é múltiplo de 4 (ou de 20, 200...); quando
 * `tetoAgradavel` escolhe um teto "5" (5, 50, 500...), a metade vira
 * X,5 — arredondar pra inteiro aí escondia a metade real (ex: teto=5
 * mostrava "0, 3, 5" em vez de "0, 2,5, 5"). Mostra 1 casa decimal só
 * quando existir, nunca mais que isso. */
export function formatarTick(valor: number): string {
  const arredondado = Math.round(valor * 10) / 10;
  return arredondado.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
}

export function valorMetrica(chave: string, metricas: Metricas): string {
  switch (chave) {
    case "disparos":
      return String(metricas.disparos);
    case "retornados":
      return String(metricas.retornados);
    case "agendamentos_confirmados":
      return String(metricas.agendamentosConfirmados);
    case "concluidos":
      return String(metricas.concluidosNoPeriodo);
    case "pendentes":
      return String(metricas.estado.pendente);
    case "em_andamento":
      return String(metricas.estado.emAndamento);
    case "pendencias_em_aberto":
      return String(metricas.abertoAgora);
    case "encaminhadas_puma":
      return String(metricas.encaminhadasParaPuma);
    case "pct_pendencias":
      return formatarPercentual(metricas.pctPendencias);
    case "pct_pendencias_concluidas":
      return formatarPercentual(metricas.pctPendenciasConcluidas);
    case "pct_resposta":
      return formatarPercentual(metricas.pctResposta);
    case "tempo_medio_resolucao":
      return metricas.tempoMedioResolucao == null ? "—" : `${metricas.tempoMedioResolucao.toFixed(1)} dias`;
    case "taxa_escalonamento_puma":
      return metricas.taxaEscalonamentoPuma == null ? "—" : formatarPercentual(metricas.taxaEscalonamentoPuma);
    default:
      return "—";
  }
}
