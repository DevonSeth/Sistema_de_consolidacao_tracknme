import { verifySession } from "@/lib/dal";
import { createSupabaseServiceClient } from "@/lib/supabase-server";

export const metadata = {
  title: "Painel Admin — Consolidação Track N'Me",
};

// `LABEL_ETAPA` é uma cópia dos `label` de `orchestrator/catalogo_etapas.py`
// (Python) — mantido em sync manualmente, mesmo princípio da paleta de cor
// duplicada entre webapp/ e ui/ (não há arquivo compartilhado entre os 2
// lados). Se um label mudar lá, atualizar aqui também.
const LABEL_ETAPA: Record<string, string> = {
  baixar_relatorios: "Baixar relatórios (Track N' Me)",
  ler_planilha_gestor: "Ler planilha do gestor",
  motor_de_regras: "Motor de regras",
  abrir_incidentes_automaticos: "Abrir incidentes automáticos",
  enriquecimento_sga: "Consultar SGA (login manual)",
  consolidar_com_sga: "Consolidar com SGA",
  fechar_incidentes_automaticos: "Fechar incidentes automáticos",
  publicar_fila_operacional: "Publicar fila operacional",
  disparo_mensagens: "Disparo de mensagens (WhatsApp)",
  finalizar_atendimentos_diarios: "Finalizar atendimentos diários",
  escalonar_ligacao: "Escalonar para ligação",
  processar_resultado_ligacao: "Processar resultado de ligação",
  processar_alertas: "Processar alertas",
};

function labelEtapa(etapaId: string): string {
  return LABEL_ETAPA[etapaId] ?? etapaId;
}

type LinhaLogExecucao = {
  etapa_id: string;
  iniciado_em: string;
  duracao_ms: number;
  sucesso: boolean;
  motivo_parada: string | null;
  mensagem: string | null;
};

type ItemWatchdog = { severidade: "critico" | "atencao"; texto: string };

const LIMITE_HISTORICO_POR_ETAPA = 10;
const MINIMO_HISTORICO_PARA_LENTIDAO = 3;

// Porta fiel de `orchestrator/watchdog.py::avaliar_watchdog` — mesmos 3
// sinais (execução travada, etapa com falha na execução mais recente,
// etapa mais lenta que a própria média histórica), lendo direto do
// Supabase (sem endpoint próprio, mesmo princípio de `admin/dashboards`).
async function avaliarWatchdog(): Promise<ItemWatchdog[]> {
  const supabase = createSupabaseServiceClient();

  const [{ data: parametrosBrutos }, { data: lock }, { data: execucoesBrutas }] = await Promise.all([
    supabase
      .from("system_parameters")
      .select("chave, valor")
      .in("chave", ["watchdog_minutos_alerta_travado", "watchdog_fator_lentidao"]),
    supabase
      .from("execucao_lock")
      .select("em_execucao, maquina, iniciado_em")
      .eq("id", "pipeline_diario")
      .single(),
    supabase
      .from("log_execucoes")
      .select("etapa_id, iniciado_em, duracao_ms, sucesso, motivo_parada, mensagem")
      .order("etapa_id")
      .order("iniciado_em", { ascending: false })
      .limit(2000),
  ]);

  const parametros: Record<string, string> = {};
  for (const linha of parametrosBrutos ?? []) parametros[linha.chave] = linha.valor;
  const minutosAlerta = Number(parametros.watchdog_minutos_alerta_travado ?? 60);
  const fatorLentidao = Number(parametros.watchdog_fator_lentidao ?? 2);

  const porEtapa = new Map<string, LinhaLogExecucao[]>();
  for (const linha of (execucoesBrutas ?? []) as LinhaLogExecucao[]) {
    const lista = porEtapa.get(linha.etapa_id) ?? [];
    if (lista.length < LIMITE_HISTORICO_POR_ETAPA) lista.push(linha);
    porEtapa.set(linha.etapa_id, lista);
  }

  const itens: ItemWatchdog[] = [];

  if (lock?.em_execucao && lock.iniciado_em) {
    const minutos = (Date.now() - new Date(lock.iniciado_em).getTime()) / 60_000;
    if (minutos >= minutosAlerta) {
      itens.push({
        severidade: "critico",
        texto: `Execução travada há ${Math.round(minutos)} min (máquina: ${lock.maquina ?? "desconhecida"}) — pode estar presa de verdade.`,
      });
    }
  }

  for (const [etapaId, execucoes] of porEtapa) {
    const maisRecente = execucoes[0];
    if (maisRecente.motivo_parada === "falha") {
      itens.push({
        severidade: "critico",
        texto: `"${labelEtapa(etapaId)}" falhou na última execução: ${maisRecente.mensagem || "sem detalhe"}.`,
      });
    }
  }

  for (const [etapaId, execucoes] of porEtapa) {
    const sucessos = execucoes.filter((e) => e.sucesso);
    if (sucessos.length === 0) continue;
    const [atual, ...historico] = sucessos;
    if (historico.length < MINIMO_HISTORICO_PARA_LENTIDAO) continue; // sem baseline ainda
    const mediaHistoricaMs = historico.reduce((soma, e) => soma + e.duracao_ms, 0) / historico.length;
    if (atual.duracao_ms > mediaHistoricaMs * fatorLentidao) {
      itens.push({
        severidade: "atencao",
        texto: `"${labelEtapa(etapaId)}" está mais lenta que o normal: ${(atual.duracao_ms / 1000).toFixed(1)}s (média histórica: ${(mediaHistoricaMs / 1000).toFixed(1)}s).`,
      });
    }
  }

  return itens;
}

export default async function AdminPage() {
  const { user } = await verifySession();
  const itensWatchdog = await avaliarWatchdog();

  return (
    <>
      <div className="page-header">
        <h1>Painel Admin</h1>
        <p className="desc">Logado como: {user.email}</p>
      </div>

      {itensWatchdog.length > 0 && (
        <div className="banner-watchdog">
          {itensWatchdog.map((item, indice) => (
            <div key={indice} className={`banner-watchdog-item ${item.severidade}`}>
              {item.texto}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
