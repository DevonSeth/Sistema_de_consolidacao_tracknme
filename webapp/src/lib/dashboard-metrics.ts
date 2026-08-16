import { createSupabaseServiceClient } from "@/lib/supabase-server";

/**
 * Métricas de negócio do Dashboard — consumida pelas 2 páginas (Admin e
 * Cliente, `/admin/dashboards` e `/dashboard`), sempre a MESMA função:
 * nunca 2 fontes de verdade pro mesmo número.
 *
 * Distinção importante entre os 2 grupos de métrica (decisão fechada
 * com o usuário, ver plano):
 * - Fluxo (disparos/retornados/agendamentos/concluídos NO período): já
 *   filtrável de verdade por timestamp real que já existe.
 * - Estado (pendente/andamento/concluído): não existe histórico de
 *   status até esta fatia — agora existe (`historico_status_tratativa`/
 *   `historico_status_puma`, Passo 1/2 do plano). O estado sempre
 *   reflete "como estava no FIM do período filtrado" (`p_data = ate`),
 *   via a função `dashboard_estado_em` (RPC, Postgres) — não dá pra
 *   fazer essa reconstrução só com queries supabase-js, por isso é RPC.
 */

type SupabaseServiceClient = ReturnType<typeof createSupabaseServiceClient>;

export type EstadoOrigem = { pendente: number; emAndamento: number; concluido: number };

export type Metricas = {
  disparos: number;
  retornados: number;
  agendamentosConfirmados: number;
  concluidosNoPeriodo: number;
  estado: EstadoOrigem;
  estadoPorOrigem: Record<string, EstadoOrigem>;
  pctPendencias: number;
  pctPendenciasConcluidas: number;
  pctResposta: number;
  /** Pendências em aberto (pendente + em andamento) AGORA — sempre o
   * momento real da consulta, nunca o `ate` do filtro. Pedido explícito
   * do usuário: esse número não pode mudar só porque alguém filtrou uma
   * data no passado. */
  abertoAgora: number;
  /** Encaminhadas pra Puma e ainda pendentes de ação dela AGORA (exclui as
   * já concluídas pela Puma) — mesmo espírito "agora" de `abertoAgora`. */
  encaminhadasParaPuma: number;
  /** Média de dias entre criação e conclusão, das concluídas NO
   * período — `null` se ninguém concluiu nada no período. */
  tempoMedioResolucao: number | null;
  /** % de ligações no período que não retornaram (escalaram pra Puma
   * automaticamente) — `null` se não houve ligação nenhuma no período. */
  taxaEscalonamentoPuma: number | null;
};

const ORIGENS = ["instalacao", "remocao", "manutencao"];

function novoEstadoOrigem(): EstadoOrigem {
  return { pendente: 0, emAndamento: 0, concluido: 0 };
}

async function contarNoPeriodo(
  supabase: SupabaseServiceClient,
  tabela: string,
  coluna: string,
  desde: string,
  ate: string,
  filtroIgual?: { coluna: string; valor: boolean }
): Promise<number> {
  let query = supabase
    .from(tabela)
    .select("id", { count: "exact", head: true })
    .gte(coluna, desde)
    .lte(coluna, ate);
  if (filtroIgual) {
    query = query.eq(filtroIgual.coluna, filtroIgual.valor);
  }
  const { count, error } = await query;
  if (error) throw new Error(error.message);
  return count ?? 0;
}

/**
 * `desde`/`ate` chegam como "YYYY-MM-DD" (valor puro de `<input
 * type="date">`) — normaliza pro início/fim do dia em UTC antes de
 * filtrar, senão `ate` (meia-noite) excluiria qualquer evento mais
 * tarde no próprio dia final escolhido.
 */
export async function buscarMetricas(desde: string, ate: string): Promise<Metricas> {
  const supabase = createSupabaseServiceClient();
  const desdeISO = `${desde}T00:00:00.000Z`;
  const ateISO = `${ate}T23:59:59.999Z`;

  // "Disparos" são 3 COLUNAS (tentativa_1/2/3), não 1 — contar só uma
  // subconta os disparos que caíram nas outras 2.
  const [disparo1, disparo2, disparo3] = await Promise.all([
    contarNoPeriodo(supabase, "tratativas", "tentativa_1", desdeISO, ateISO),
    contarNoPeriodo(supabase, "tratativas", "tentativa_2", desdeISO, ateISO),
    contarNoPeriodo(supabase, "tratativas", "tentativa_3", desdeISO, ateISO),
  ]);
  const disparos = disparo1 + disparo2 + disparo3;

  // "Retornados" conta os 2 canais: resposta de WhatsApp (data_resposta)
  // + ligação com retornou=true. Usa `ligacoes.created_at` (não
  // `data_contato`, que vem de parse de texto digitado na planilha e
  // pode ser null silenciosamente).
  const [respostaWhatsapp, ligacoesRetornaram] = await Promise.all([
    contarNoPeriodo(supabase, "tratativas", "data_resposta", desdeISO, ateISO),
    contarNoPeriodo(supabase, "ligacoes", "created_at", desdeISO, ateISO, { coluna: "retornou", valor: true }),
  ]);
  const retornados = respostaWhatsapp + ligacoesRetornaram;

  const agendamentosConfirmados = await contarNoPeriodo(
    supabase, "ligacoes", "created_at", desdeISO, ateISO, { coluna: "conseguiu_agendar", valor: true }
  );

  // "Concluídos no período" (fluxo — quantos concluíram DENTRO do
  // intervalo): finalizado_em (tratativas) + concluido_em (puma).
  const [concluidosTratativas, concluidosPuma] = await Promise.all([
    contarNoPeriodo(supabase, "tratativas", "finalizado_em", desdeISO, ateISO),
    contarNoPeriodo(supabase, "puma_encaminhamentos", "concluido_em", desdeISO, ateISO),
  ]);
  const concluidosNoPeriodo = concluidosTratativas + concluidosPuma;

  // Estado (pendente/andamento/concluído) como estava no FIM do período.
  const { data: estadoBruto, error: erroEstado } = await supabase.rpc("dashboard_estado_em", {
    p_data: ateISO,
  });
  if (erroEstado) throw new Error(erroEstado.message);

  const estado = novoEstadoOrigem();
  const estadoPorOrigem: Record<string, EstadoOrigem> = {};
  for (const origem of ORIGENS) estadoPorOrigem[origem] = novoEstadoOrigem();

  for (const linha of (estadoBruto ?? []) as { origem: string; bucket: string; quantidade: number }[]) {
    const quantidade = Number(linha.quantidade);
    if (!estadoPorOrigem[linha.origem]) estadoPorOrigem[linha.origem] = novoEstadoOrigem();
    const alvo = estadoPorOrigem[linha.origem];

    if (linha.bucket === "concluido") {
      alvo.concluido += quantidade;
      estado.concluido += quantidade;
    } else if (linha.bucket === "pendente") {
      alvo.pendente += quantidade;
      estado.pendente += quantidade;
    } else {
      alvo.emAndamento += quantidade;
      estado.emAndamento += quantidade;
    }
  }

  const totalEstado = estado.pendente + estado.emAndamento + estado.concluido;
  const pctPendencias = totalEstado > 0 ? estado.pendente / totalEstado : 0;
  const pctPendenciasConcluidas = totalEstado > 0 ? estado.concluido / totalEstado : 0;
  const pctResposta = disparos > 0 ? retornados / disparos : 0;

  const [abertoAgora, encaminhadasParaPuma, tempoMedioResolucao, taxaEscalonamentoPuma] = await Promise.all([
    buscarAbertoAgora(supabase),
    buscarEncaminhadasParaPumaAgora(supabase),
    buscarTempoMedioResolucao(desde, ate),
    buscarTaxaEscalonamentoPuma(desde, ate),
  ]);

  return {
    disparos,
    retornados,
    agendamentosConfirmados,
    concluidosNoPeriodo,
    estado,
    estadoPorOrigem,
    pctPendencias,
    pctPendenciasConcluidas,
    pctResposta,
    abertoAgora,
    encaminhadasParaPuma,
    tempoMedioResolucao,
    taxaEscalonamentoPuma,
  };
}

/** Pendente + em andamento, reconstruído com `p_data` = agora de verdade
 * (nunca o `ate` do filtro) — sempre soma tudo (não separa por origem,
 * ninguém pediu esse recorte pra esta métrica). */
async function buscarAbertoAgora(supabase: SupabaseServiceClient): Promise<number> {
  const { data, error } = await supabase.rpc("dashboard_estado_em", {
    p_data: new Date().toISOString(),
  });
  if (error) throw new Error(error.message);

  let total = 0;
  for (const linha of (data ?? []) as { bucket: string; quantidade: number }[]) {
    if (linha.bucket === "pendente" || linha.bucket === "em_andamento") {
      total += Number(linha.quantidade);
    }
  }
  return total;
}

// `formatarPercentual`/`valorMetrica` moraram aqui antes — mudaram pra
// `dashboard-metricas-meta.ts` porque este módulo importa `supabase-
// server.ts` (`SUPABASE_SERVICE_ROLE_KEY`, só servidor). `DashboardsClient.
// tsx` (Client Component) precisa de `valorMetrica`, e um import "runtime"
// (não `import type`) desse arquivo vazaria `supabase-server.ts` pro bundle
// do navegador — achado ao vivo: `process.env.SUPABASE_URL` não existe no
// browser, só `NEXT_PUBLIC_*` é inlined lá, e o guard-clause do módulo
// lançava exatamente esse erro em runtime no cliente.

export type PontoSerieDiaria = { dia: string; disparos: number; retornados: number };

/** Série diária (disparos/retornados por dia) pro gráfico de tendência —
 * `dashboard_serie_diaria` (RPC) devolve 1 linha por dia do intervalo,
 * inclusive dias com 0 em ambas as colunas (sem buraco no eixo X). */
export async function buscarSerieDiaria(desde: string, ate: string): Promise<PontoSerieDiaria[]> {
  const supabase = createSupabaseServiceClient();
  const desdeISO = `${desde}T00:00:00.000Z`;
  const ateISO = `${ate}T23:59:59.999Z`;

  const { data, error } = await supabase.rpc("dashboard_serie_diaria", {
    p_desde: desdeISO,
    p_ate: ateISO,
  });
  if (error) throw new Error(error.message);

  return (data ?? []) as PontoSerieDiaria[];
}

type TratativaAbertaAgora = {
  id: string;
  codigo_regra: string | null;
  origem: string | null;
  cidade: string | null;
  cliente: string | null;
  identificador: string | null;
  tentativa_1: string | null;
  tentativa_2: string | null;
  tentativa_3: string | null;
  created_at: string;
  status: string;
};

/** Tratativas ainda não concluídas AGORA (não respeita o filtro de
 * período — mesmo espírito de `abertoAgora`, pensado pra triagem do dia
 * a dia, não pra relatório histórico). "Concluída" é a união dos 2
 * caminhos (mesma regra de `dashboard_estado_em`): `status='finalizado'`
 * OU o encaminhamento pra Puma mais recente já está `concluido` — esse
 * segundo caminho não atualiza `tratativas.status` (ver `sincronizar_
 * status_puma`), por isso a exclusão é feita à parte, com 2 queries. */
async function buscarAbertasAgora(supabase: SupabaseServiceClient): Promise<TratativaAbertaAgora[]> {
  const { data: abertas, error } = await supabase
    .from("tratativas")
    .select("id, codigo_regra, origem, cidade, cliente, identificador, tentativa_1, tentativa_2, tentativa_3, created_at, status")
    .neq("status", "finalizado");
  if (error) throw new Error(error.message);
  if (!abertas || abertas.length === 0) return [];

  const ids = abertas.map((t) => t.id);
  const { data: concluidosPuma, error: erroPuma } = await supabase
    .from("puma_encaminhamentos")
    .select("tratativa_id")
    .eq("status", "concluido")
    .in("tratativa_id", ids);
  if (erroPuma) throw new Error(erroPuma.message);

  const idsConcluidosPuma = new Set((concluidosPuma ?? []).map((r) => r.tratativa_id));
  return abertas.filter((t) => !idsConcluidosPuma.has(t.id));
}

/** Tratativas encaminhadas pra Puma que AINDA estão pendentes de ação dela
 * (exclui as já concluídas pela Puma) — `tratativas.status` nunca sai de
 * `'encaminhado_puma'` mesmo depois de concluído (só `puma_encaminhamentos.
 * status` muda, ver `sincronizar_status_puma` no lado Python), então
 * reaproveita a mesma exclusão de `buscarAbertasAgora` em vez de duplicá-la. */
async function buscarEncaminhadasParaPumaAgora(supabase: SupabaseServiceClient): Promise<number> {
  const abertas = await buscarAbertasAgora(supabase);
  return abertas.filter((t) => t.status === "encaminhado_puma").length;
}

async function buscarUrgenciaPorRegra(supabase: SupabaseServiceClient): Promise<Map<string, number | null>> {
  const { data, error } = await supabase.from("rule_templates").select("codigo_regra, nivel_urgencia");
  if (error) throw new Error(error.message);
  return new Map((data ?? []).map((r) => [r.codigo_regra as string, r.nivel_urgencia as number | null]));
}

/** 1-5 -> quantidade de tratativas abertas agora naquele nível (via
 * `codigo_regra` -> `rule_templates.nivel_urgencia`). */
export type DistribuicaoUrgencia = Record<number, number>;

export async function buscarDistribuicaoUrgencia(): Promise<DistribuicaoUrgencia> {
  const supabase = createSupabaseServiceClient();
  const [abertas, urgenciaPorRegra] = await Promise.all([buscarAbertasAgora(supabase), buscarUrgenciaPorRegra(supabase)]);

  const distribuicao: DistribuicaoUrgencia = {};
  for (const t of abertas) {
    const nivel = t.codigo_regra ? urgenciaPorRegra.get(t.codigo_regra) : null;
    if (nivel == null) continue;
    distribuicao[nivel] = (distribuicao[nivel] ?? 0) + 1;
  }
  return distribuicao;
}

export type PendenciaSemContato = {
  identificador: string;
  cliente: string;
  origem: string;
  cidade: string;
  diasSemContato: number;
  nivelUrgencia: number | null;
};

/** Aproximação de dias úteis (seg-sex) estritamente entre `inicio` e
 * `fim` — sem feriados (a planilha usa a lib `holidays` BR/PE pro
 * indicador oficial; aqui é só uma referência rápida pro dashboard,
 * pode divergir em ±1 dia perto de feriado). */
function diasUteisAproximados(inicio: Date, fim: Date): number {
  const cursor = new Date(inicio);
  cursor.setUTCHours(0, 0, 0, 0);
  const fimSemHora = new Date(fim);
  fimSemHora.setUTCHours(0, 0, 0, 0);
  let dias = 0;
  while (cursor < fimSemHora) {
    cursor.setUTCDate(cursor.getUTCDate() + 1);
    const diaSemana = cursor.getUTCDay();
    if (diaSemana !== 0 && diaSemana !== 6) dias++;
  }
  return dias;
}

/** Mesma prioridade de `orchestrator.pipeline._ultimo_contato_tratativa`:
 * tentativa_3 > tentativa_2 > tentativa_1 > created_at (preenchidos em
 * ordem cronológica, o primeiro não-vazio já é o marco certo). */
function ultimoContato(t: TratativaAbertaAgora): Date | null {
  for (const campo of ["tentativa_3", "tentativa_2", "tentativa_1", "created_at"] as const) {
    const valor = t[campo];
    if (valor) return new Date(valor);
  }
  return null;
}

/** Pendências abertas agora com mais dias úteis sem contato, mais
 * paradas primeiro — limitado a `limite` linhas (não é um relatório
 * completo, é uma lista de "o que atacar primeiro"). */
export async function buscarPendenciasSemContato(limite = 20): Promise<PendenciaSemContato[]> {
  const supabase = createSupabaseServiceClient();
  const [abertas, urgenciaPorRegra] = await Promise.all([buscarAbertasAgora(supabase), buscarUrgenciaPorRegra(supabase)]);
  const agora = new Date();

  return abertas
    .map((t) => {
      const contato = ultimoContato(t);
      return {
        identificador: t.identificador || "—",
        cliente: t.cliente || "—",
        origem: t.origem ?? "",
        cidade: t.cidade?.trim() || "Sem cidade cadastrada",
        diasSemContato: contato ? diasUteisAproximados(contato, agora) : 0,
        nivelUrgencia: t.codigo_regra ? urgenciaPorRegra.get(t.codigo_regra) ?? null : null,
      };
    })
    .sort((a, b) => b.diasSemContato - a.diasSemContato)
    .slice(0, limite);
}

export type EncaminhadaParaPuma = {
  identificador: string;
  cliente: string;
  origem: string;
  cidade: string;
  motivo: string;
  diasNoEstado: number;
  nivelUrgencia: number | null;
};

/** Pendências com `tratativas.status='encaminhado_puma'` que ainda não
 * foram concluídas pela Puma — mesma exclusão de `buscarAbertasAgora`,
 * garante que o total bate com o widget `encaminhadas_puma`. "Dias no
 * estado" usa a mesma aproximação de dias úteis de `diasSemContato`, mas
 * a partir de `puma_encaminhamentos.data_encaminhamento` da linha MAIS
 * RECENTE de cada tratativa (re-encaminhamento é suportado no schema —
 * uma tratativa pode ter mais de uma linha em `puma_encaminhamentos`).
 * Sem limite de linhas (o widget mostra o total, a tabela mostra tudo —
 * limitar aqui criaria "widget diz N, tabela mostra menos"). */
export async function buscarEncaminhadasParaPuma(): Promise<EncaminhadaParaPuma[]> {
  const supabase = createSupabaseServiceClient();
  const [abertas, urgenciaPorRegra] = await Promise.all([buscarAbertasAgora(supabase), buscarUrgenciaPorRegra(supabase)]);
  const encaminhadas = abertas.filter((t) => t.status === "encaminhado_puma");
  if (encaminhadas.length === 0) return [];

  const ids = encaminhadas.map((t) => t.id);
  const { data: encaminhamentos, error } = await supabase
    .from("puma_encaminhamentos")
    .select("tratativa_id, motivo, data_encaminhamento")
    .in("tratativa_id", ids)
    .order("data_encaminhamento", { ascending: false });
  if (error) throw new Error(error.message);

  // Ordenado desc — a 1ª ocorrência de cada tratativa_id já é a mais recente.
  const maisRecentePorId = new Map<string, { motivo: string | null; data_encaminhamento: string }>();
  for (const e of encaminhamentos ?? []) {
    if (!maisRecentePorId.has(e.tratativa_id)) maisRecentePorId.set(e.tratativa_id, e);
  }

  const agora = new Date();
  return encaminhadas
    .map((t) => {
      const enc = maisRecentePorId.get(t.id);
      const dataEncaminhamento = enc?.data_encaminhamento ? new Date(enc.data_encaminhamento) : null;
      return {
        identificador: t.identificador || "—",
        cliente: t.cliente || "—",
        origem: t.origem ?? "",
        cidade: t.cidade?.trim() || "Sem cidade cadastrada",
        motivo: enc?.motivo || "—",
        diasNoEstado: dataEncaminhamento ? diasUteisAproximados(dataEncaminhamento, agora) : 0,
        nivelUrgencia: t.codigo_regra ? urgenciaPorRegra.get(t.codigo_regra) ?? null : null,
      };
    })
    .sort((a, b) => b.diasNoEstado - a.diasNoEstado);
}

/** Serviços com `status='pendente'` agora, agrupados por cidade —
 * alfabética, sem limite (decisão do usuário — a UI rola internamente
 * em vez de limitar linhas). */
export async function buscarPendentesPorCidade(): Promise<{ cidade: string; quantidade: number }[]> {
  const supabase = createSupabaseServiceClient();
  const { data, error } = await supabase.from("tratativas").select("cidade").eq("status", "pendente");
  if (error) throw new Error(error.message);

  const contagem = new Map<string, number>();
  for (const linha of data ?? []) {
    const cidade = (linha.cidade as string | null)?.trim() || "Sem cidade cadastrada";
    contagem.set(cidade, (contagem.get(cidade) ?? 0) + 1);
  }
  return Array.from(contagem.entries())
    .map(([cidade, quantidade]) => ({ cidade, quantidade }))
    .sort((a, b) => a.cidade.localeCompare(b.cidade, "pt-BR"));
}

export type PontoEvolucao = { dia: string; abertas: number; concluidas: number };

function contarPorDia(datas: (string | null)[]): Map<string, number> {
  const mapa = new Map<string, number>();
  for (const valor of datas) {
    if (!valor) continue;
    const dia = valor.slice(0, 10);
    mapa.set(dia, (mapa.get(dia) ?? 0) + 1);
  }
  return mapa;
}

/** Evolução do backlog desde o primeiro registro do sistema — SEM filtro de
 * período (o corte "diário no período filtrado" vs "acumulado desde o
 * início" é feito no componente, `EvolucaoBacklog.tsx`, fatiando esta MESMA
 * série: o saldo em aberto de um dia qualquer depende de todo o histórico
 * anterior, não só do período visível na tela, então não faz sentido
 * recalcular 2 vezes com escopos diferentes).
 *
 * `abertas` = saldo em aberto NAQUELE dia (sobe quando abre mais do que
 * fecha, desce quando fecha mais do que abre — é um ESTADO, não um total
 * acumulado). `concluidas` = total acumulado de concluídas até aquele dia
 * (só sobe). "Concluída" é a união dos 2 caminhos de sempre: `finalizado_em`
 * (tratativas) OU `concluido_em` (puma_encaminhamentos) — mesma regra de
 * `concluidosNoPeriodo`/`dashboard_estado_em`, sem distinguir aqui porque
 * pra este gráfico só o TOTAL concluído importa, não por qual caminho. */
export async function buscarEvolucaoBacklog(): Promise<PontoEvolucao[]> {
  const supabase = createSupabaseServiceClient();

  const { data: primeiraLinha, error: erroPrimeira } = await supabase
    .from("tratativas")
    .select("created_at")
    .order("created_at", { ascending: true })
    .limit(1);
  if (erroPrimeira) throw new Error(erroPrimeira.message);
  if (!primeiraLinha || primeiraLinha.length === 0) return [];

  const [
    { data: criadas, error: erroCriadas },
    { data: finalizadas, error: erroFinalizadas },
    { data: pumaConcluidas, error: erroPuma },
  ] = await Promise.all([
    supabase.from("tratativas").select("created_at"),
    supabase.from("tratativas").select("finalizado_em").not("finalizado_em", "is", null),
    supabase.from("puma_encaminhamentos").select("concluido_em").not("concluido_em", "is", null),
  ]);
  if (erroCriadas) throw new Error(erroCriadas.message);
  if (erroFinalizadas) throw new Error(erroFinalizadas.message);
  if (erroPuma) throw new Error(erroPuma.message);

  const criadasPorDia = contarPorDia((criadas ?? []).map((r) => r.created_at as string));
  const concluidasPorDia = contarPorDia([
    ...(finalizadas ?? []).map((r) => r.finalizado_em as string | null),
    ...(pumaConcluidas ?? []).map((r) => r.concluido_em as string | null),
  ]);

  const cursor = new Date(`${(primeiraLinha[0].created_at as string).slice(0, 10)}T00:00:00.000Z`);
  const fim = new Date(`${hojeISO()}T00:00:00.000Z`);

  const serie: PontoEvolucao[] = [];
  let criadasAcumulado = 0;
  let concluidasAcumulado = 0;
  while (cursor <= fim) {
    const dia = cursor.toISOString().slice(0, 10);
    criadasAcumulado += criadasPorDia.get(dia) ?? 0;
    concluidasAcumulado += concluidasPorDia.get(dia) ?? 0;
    serie.push({ dia, abertas: criadasAcumulado - concluidasAcumulado, concluidas: concluidasAcumulado });
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return serie;
}

/** Média de dias entre a criação e a conclusão, só das tratativas
 * concluídas DENTRO do período (mesmos 2 caminhos de `concluidosNoPeriodo`:
 * `finalizado_em` direto, ou `concluido_em` do encaminhamento Puma — pra
 * esse 2º caminho, busca o `created_at` da tratativa em uma 2ª query
 * simples, sem embed de relação, por segurança). `null` se não houve
 * nenhuma conclusão no período. */
export async function buscarTempoMedioResolucao(desde: string, ate: string): Promise<number | null> {
  const supabase = createSupabaseServiceClient();
  const desdeISO = `${desde}T00:00:00.000Z`;
  const ateISO = `${ate}T23:59:59.999Z`;

  const { data: tratativasConcluidas, error: erroTratativas } = await supabase
    .from("tratativas")
    .select("created_at, finalizado_em")
    .gte("finalizado_em", desdeISO)
    .lte("finalizado_em", ateISO);
  if (erroTratativas) throw new Error(erroTratativas.message);

  const { data: encaminhamentosConcluidos, error: erroPuma } = await supabase
    .from("puma_encaminhamentos")
    .select("tratativa_id, concluido_em")
    .gte("concluido_em", desdeISO)
    .lte("concluido_em", ateISO);
  if (erroPuma) throw new Error(erroPuma.message);

  const diasPorItem: number[] = [];
  for (const t of tratativasConcluidas ?? []) {
    if (!t.created_at || !t.finalizado_em) continue;
    diasPorItem.push((new Date(t.finalizado_em).getTime() - new Date(t.created_at).getTime()) / 86_400_000);
  }

  const idsTratativasPuma = (encaminhamentosConcluidos ?? []).map((e) => e.tratativa_id).filter(Boolean);
  if (idsTratativasPuma.length > 0) {
    const { data: tratativasDosEncaminhamentos, error: erroBusca } = await supabase
      .from("tratativas")
      .select("id, created_at")
      .in("id", idsTratativasPuma);
    if (erroBusca) throw new Error(erroBusca.message);

    const criadoEmPorId = new Map((tratativasDosEncaminhamentos ?? []).map((t) => [t.id, t.created_at]));
    for (const e of encaminhamentosConcluidos ?? []) {
      const criadoEm = criadoEmPorId.get(e.tratativa_id);
      if (!criadoEm || !e.concluido_em) continue;
      diasPorItem.push((new Date(e.concluido_em).getTime() - new Date(criadoEm).getTime()) / 86_400_000);
    }
  }

  if (diasPorItem.length === 0) return null;
  return diasPorItem.reduce((a, b) => a + b, 0) / diasPorItem.length;
}

/** % de ligações no período que NÃO retornaram — único caminho
 * automático pra "Encaminhar pra Puma" (regra de `etapa_processar_
 * resultado_ligacao`/F.4), por isso mede bem "quanto do volume de
 * ligações precisou escalar pra Puma". `null` se não houve ligação
 * nenhuma no período (evita dividir por zero e mostrar "0%" enganoso). */
export async function buscarTaxaEscalonamentoPuma(desde: string, ate: string): Promise<number | null> {
  const supabase = createSupabaseServiceClient();
  const desdeISO = `${desde}T00:00:00.000Z`;
  const ateISO = `${ate}T23:59:59.999Z`;

  const [totalLigacoes, ligacoesNaoRetornaram] = await Promise.all([
    contarNoPeriodo(supabase, "ligacoes", "created_at", desdeISO, ateISO),
    contarNoPeriodo(supabase, "ligacoes", "created_at", desdeISO, ateISO, { coluna: "retornou", valor: false }),
  ]);

  if (totalLigacoes === 0) return null;
  return ligacoesNaoRetornaram / totalLigacoes;
}

/** Frescor do dado exibido no Dashboard — a execução de sucesso mais
 * recente registrada em `log_execucoes` (Observabilidade fatia 1,
 * 2026-08-14), qualquer etapa. `null` se a tabela ainda não tem nenhuma
 * linha (instalação nova, ou nenhuma execução desde que a tabela foi
 * criada) — quem chama decide o que mostrar nesse caso (hoje, nada). */
export async function buscarUltimaAtualizacao(): Promise<string | null> {
  const supabase = createSupabaseServiceClient();
  const { data } = await supabase
    .from("log_execucoes")
    .select("finalizado_em")
    .eq("sucesso", true)
    .order("finalizado_em", { ascending: false })
    .limit(1)
    .maybeSingle();
  return data?.finalizado_em ?? null;
}

/** Default do filtro de período (últimos 30 dias) quando a página abre
 * sem `?desde=&ate=` na URL. */
export function dataDefaultDesde(): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - 30);
  return d.toISOString().slice(0, 10);
}

export function hojeISO(): string {
  return new Date().toISOString().slice(0, 10);
}
