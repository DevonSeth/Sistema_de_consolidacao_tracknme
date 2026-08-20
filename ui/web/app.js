/* Lógica da tela "Operação" — chama a Api exposta pelo pywebview
 * (ui/app.py::Api). Sem framework, DOM puro, mesmo espírito do mockup
 * aprovado. Polling a cada 800ms — backend é Python local na mesma
 * máquina, sem rede, custo desprezível. */

const FASE_LABELS = {
  A: "Fase A — Coleta",
  B: "Fase B — Classificação",
  C: "Fase C — Reconciliação",
  D: "Fase D — SGA",
  E: "Fase E — Publicação",
  F: "Fase F — Contato com o associado",
};

const STATUS_LABEL = {
  pendente: "Pendente",
  aguardando_resposta: "Aguardando resposta",
  respondido: "Respondido",
  aguardando_ligacao: "Aguardando ligação",
  encaminhado_puma: "Encaminhado à Puma",
  finalizado: "Finalizado",
  bloqueado_sga: "Bloqueado (SGA)",
};

const SELO_LABEL = {
  ocioso: "Ocioso",
  rodando: "Rodando",
  sucesso: "Sucesso",
  erro: "Erro",
  cancelada: "Cancelada",
  aguardando_reconexao: "Aguardando você",
};

let etapasCache = [];
let selecionadas = new Set();
let estaRodando = false;
let motivoParadaAnterior = undefined;
let rodandoAnterior = false;

function api() {
  return window.pywebview.api;
}

// --------------------------------------------------------------------------
// Etapas / execução
// --------------------------------------------------------------------------

async function carregarEtapas() {
  const resultado = await api().listar_etapas_com_status();
  etapasCache = resultado.etapas;
  document.getElementById("resumo-manutencao").textContent = resultado.contagem_por_origem.manutencao ?? 0;
  document.getElementById("resumo-instalacao").textContent = resultado.contagem_por_origem.instalacao ?? 0;
  document.getElementById("resumo-remocao").textContent = resultado.contagem_por_origem.remocao ?? 0;
  renderizarFases();
}

/** Agrupa por fase só entre etapas CONSECUTIVAS na ordem real de execução
 * do catálogo — uma fase pode se repetir mais adiante na esteira (ex:
 * Fase B roda no início com "Motor de regras" e de novo, mais tarde, com
 * "Consolidar com SGA"; Fase C roda "Abrir" antes do SGA e "Fechar"
 * depois). Reagrupar por rótulo (ignorando a posição) sugeriria uma
 * ordem de execução errada — a esteira é sequencial, os rótulos não.
 * A partir da 2ª vez que uma fase reaparece, o título ganha um sufixo
 * "(retomada, Nª vez nesta esteira)" — sem isso, a repetição parecia
 * erro/duplicata pra quem está aprendendo o sistema (achado 2026-08-20). */
function renderizarFases() {
  const container = document.getElementById("lista-fases");
  container.innerHTML = "";
  let faseAtual = null;
  let grupo = null;
  let numeroSecao = 0;
  const vezesPorFase = new Map();
  for (const etapa of etapasCache) {
    if (etapa.fase !== faseAtual) {
      faseAtual = etapa.fase;
      numeroSecao += 1;
      const vezes = (vezesPorFase.get(faseAtual) ?? 0) + 1;
      vezesPorFase.set(faseAtual, vezes);
      grupo = document.createElement("div");
      grupo.className = "fase-grupo";
      const titulo = document.createElement("div");
      titulo.className = "fase-titulo";
      const sufixo = vezes > 1 ? ` (retomada, ${vezes}ª vez nesta esteira)` : "";
      titulo.textContent = `${numeroSecao}. ${FASE_LABELS[faseAtual] ?? `Fase ${faseAtual}`}${sufixo}`;
      grupo.appendChild(titulo);
      container.appendChild(grupo);
    }
    grupo.appendChild(renderizarCard(etapa));
  }
  atualizarInfoSelecao();
}

function renderizarCard(etapa) {
  const card = document.createElement("div");
  card.className = "card-etapa";
  card.dataset.etapaId = etapa.id;

  const linha = document.createElement("div");
  linha.className = "card-etapa-linha";

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = selecionadas.has(etapa.id);
  checkbox.disabled = estaRodando;
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) selecionadas.add(etapa.id);
    else selecionadas.delete(etapa.id);
    atualizarInfoSelecao();
  });
  linha.appendChild(checkbox);

  const label = document.createElement("div");
  label.className = "label-etapa";
  const texto = document.createElement("span");
  texto.className = "texto";
  texto.textContent = etapa.label;
  label.appendChild(texto);
  if (etapa.manual) {
    const tag = document.createElement("span");
    tag.className = "tag-manual";
    tag.textContent = "login manual";
    label.appendChild(tag);
  }
  linha.appendChild(label);

  const selo = document.createElement("span");
  selo.className = `selo selo-${etapa.status}`;
  selo.innerHTML = `<span class="dot"></span>${SELO_LABEL[etapa.status] ?? etapa.status}`;
  linha.appendChild(selo);

  const btnApartir = document.createElement("button");
  btnApartir.className = "btn-card";
  btnApartir.textContent = "A partir daqui";
  btnApartir.disabled = estaRodando;
  btnApartir.addEventListener("click", () => executar(null, `a_partir_de:${etapa.id}`));
  linha.appendChild(btnApartir);

  card.appendChild(linha);

  if (etapa.mensagem_erro) {
    const erro = document.createElement("div");
    erro.className = "card-etapa-erro";
    erro.textContent = etapa.mensagem_erro;
    card.appendChild(erro);
  }

  // Falhas por item que persistiram depois dos retries — aparece mesmo com
  // a etapa em status de sucesso (alguns itens falharam, outros não).
  if (etapa.falhas_item && etapa.falhas_item.length > 0) {
    const bloco = document.createElement("div");
    bloco.className = "card-etapa-falhas";
    etapa.falhas_item.forEach((falha) => {
      const linha = document.createElement("div");
      linha.className = "card-etapa-falhas-item";
      linha.textContent = `${falha.descricao ?? "Item"} — ${falha.erro ?? "erro desconhecido"}`;
      bloco.appendChild(linha);
    });
    card.appendChild(bloco);
  }

  return card;
}

function atualizarInfoSelecao() {
  const n = selecionadas.size;
  document.getElementById("selecionadas-info").textContent = n === 1 ? "1 etapa selecionada" : `${n} etapas selecionadas`;
}

function atualizarBotoes() {
  document.getElementById("btn-executar-selecionadas").disabled = estaRodando;
  document.getElementById("btn-executar-tudo").disabled = estaRodando;
  document.getElementById("btn-cancelar").disabled = !estaRodando;
  document.querySelectorAll('.card-etapa input[type="checkbox"], .card-etapa .btn-card').forEach((el) => {
    el.disabled = estaRodando;
  });
}

async function executar(listaIds, modo) {
  const resultado = await api().executar_etapas(listaIds, modo);
  if (!resultado.iniciado) {
    if (resultado.motivo === "ja_rodando") alert("Já tem uma execução em andamento.");
    return;
  }
  estaRodando = true;
  atualizarBotoes();
}

// --------------------------------------------------------------------------
// Progresso ao vivo (polling)
// --------------------------------------------------------------------------

function limparProgressoDosCards() {
  document.querySelectorAll(".card-etapa-progresso").forEach((el) => el.remove());
  document.querySelectorAll(".card-etapa-workers").forEach((el) => el.remove());
}

function mostrarProgressoNoCard(etapaId, concluidos, total) {
  document.querySelectorAll(".card-etapa-progresso").forEach((el) => el.remove());
  const card = document.querySelector(`.card-etapa[data-etapa-id="${etapaId}"]`);
  if (!card) return;
  const etapa = etapasCache.find((e) => e.id === etapaId);
  const pct = total > 0 ? Math.round((Math.min(concluidos, total) / total) * 100) : 0;
  const bloco = document.createElement("div");
  bloco.className = "card-etapa-progresso";
  bloco.innerHTML = `
    <div class="trilho"><div class="fill" style="width:${pct}%"></div></div>
    <div class="texto">${etapa?.label ?? etapaId} — ${pct}%</div>
  `;
  card.appendChild(bloco);
}

/** Mostra o que cada worker está processando agora (evita a sensação de
 * travamento em etapas longas, ex: round 2 de retry em `processar_fila`,
 * que trava a % em 100% mas continua rodando de verdade) — `workers` é
 * `{worker_id: descricao}`, vindo de `Api.obter_progresso_atual().workers`. */
function mostrarWorkersNoCard(etapaId, workers) {
  document.querySelectorAll(".card-etapa-workers").forEach((el) => el.remove());
  if (!etapaId || !workers) return;
  const card = document.querySelector(`.card-etapa[data-etapa-id="${etapaId}"]`);
  if (!card) return;
  const ids = Object.keys(workers).map(Number).sort((a, b) => a - b);
  if (ids.length === 0) return;
  const bloco = document.createElement("div");
  bloco.className = "card-etapa-workers";
  bloco.innerHTML = ids
    .map((id) => `<div>Worker ${id + 1}: ${workers[id]}</div>`)
    .join("");
  card.appendChild(bloco);
}

function atualizarStatusRobo(progresso) {
  const roboTexto = document.getElementById("robo-texto");
  const roboDot = document.getElementById("robo-dot");
  if (progresso.rodando) {
    const etapaAtual = etapasCache.find((e) => e.id === progresso.etapa_atual_id);
    roboTexto.textContent = `Rodando: ${etapaAtual?.label ?? progresso.etapa_atual_id ?? "..."}`;
    roboDot.className = "robo-dot pulse";
    roboDot.style.background = "var(--status-rodando-fg)";
  } else if (progresso.motivo_parada === "aguardando_reconexao") {
    roboTexto.textContent = "Aguardando você";
    roboDot.className = "robo-dot";
    roboDot.style.background = "var(--status-aguardando-fg)";
  } else if (progresso.motivo_parada === "falha") {
    roboTexto.textContent = "Parou com erro";
    roboDot.className = "robo-dot";
    roboDot.style.background = "var(--status-erro-fg)";
  } else if (progresso.motivo_parada === "cancelada") {
    roboTexto.textContent = "Cancelada";
    roboDot.className = "robo-dot";
    roboDot.style.background = "var(--status-ocioso-fg)";
  } else {
    roboTexto.textContent = "Ocioso";
    roboDot.className = "robo-dot";
    roboDot.style.background = "var(--status-ocioso-fg)";
  }
}

async function pollProgresso() {
  const progresso = await api().obter_progresso_atual();
  estaRodando = progresso.rodando;
  atualizarBotoes();
  atualizarStatusRobo(progresso);

  // Reflete status/selos sempre que algo relevante mudou ANTES de pintar a
  // barra de progresso — carregarEtapas() reconstrói #lista-fases do zero
  // (innerHTML = ""), então chamada depois de mostrarProgressoNoCard ela
  // apagava a barra no mesmo tick, antes do navegador repintar.
  //
  // Achado 2026-08-19: uma conclusão COM SUCESSO nunca muda `motivo_parada`
  // (fica `null` antes E depois -- só falha/cancelamento/reconexão setam
  // um valor) -- por isso também precisa comparar `rodando` contra o tick
  // anterior, senão a transição "rodando -> concluído com sucesso" nunca
  // dispara o refresh e a tela fica travada mostrando "Rodando" pra sempre.
  if (progresso.rodando || rodandoAnterior || motivoParadaAnterior !== progresso.motivo_parada) {
    await carregarEtapas();
  }
  rodandoAnterior = progresso.rodando;
  motivoParadaAnterior = progresso.motivo_parada;

  if (progresso.progresso_item) {
    mostrarProgressoNoCard(progresso.progresso_item.etapa_id, progresso.progresso_item.concluidos, progresso.progresso_item.total);
    mostrarWorkersNoCard(progresso.progresso_item.etapa_id, progresso.workers);
  } else {
    limparProgressoDosCards();
  }

  const bannerReconexao = document.getElementById("banner-reconexao");
  if (progresso.motivo_parada === "aguardando_reconexao") {
    bannerReconexao.hidden = false;
    document.getElementById("banner-reconexao-texto").textContent =
      progresso.mensagem_etapa_travada || "Sessão caiu — faça login manualmente e confirme pra continuar de onde parou.";
  } else {
    bannerReconexao.hidden = true;
  }

  document.getElementById("banner-travado").hidden = progresso.motivo_parada !== "travado";
}

// --------------------------------------------------------------------------
// Painel de apoio — os 6 widgets (Fase 4)
// --------------------------------------------------------------------------

function dotOrigem(origem) {
  const span = document.createElement("span");
  span.className = `dot-origem ${origem ?? ""}`;
  return span;
}

function linhaBasica(item) {
  const linha = document.createElement("div");
  linha.className = "linha-item";
  linha.appendChild(dotOrigem(item.origem));
  const cliente = document.createElement("span");
  cliente.className = "cliente";
  cliente.textContent = `${item.cliente || "—"} · ${item.identificador || "—"}`;
  linha.appendChild(cliente);
  const extra = document.createElement("span");
  extra.className = "extra";
  extra.textContent = STATUS_LABEL[item.status] ?? item.status ?? "";
  linha.appendChild(extra);
  return linha;
}

function linhaFilaPrioridade(item) {
  const linha = document.createElement("div");
  linha.className = "linha-item";
  linha.appendChild(dotOrigem(item.origem));
  const cliente = document.createElement("span");
  cliente.className = "cliente";
  cliente.textContent = `${item.cliente || "—"} · ${item.identificador || "—"}`;
  linha.appendChild(cliente);
  if (item.nivel_urgencia) {
    const badge = document.createElement("span");
    badge.className = "badge-nivel";
    badge.textContent = item.nivel_urgencia;
    badge.style.background = `var(--nivel-${item.nivel_urgencia})`;
    linha.appendChild(badge);
  }
  const extra = document.createElement("span");
  extra.className = "extra";
  extra.textContent = item.dias_sem_contato != null ? `${item.dias_sem_contato}d sem contato` : "";
  linha.appendChild(extra);
  return linha;
}

function linhaSituacaoManual(item) {
  const linha = document.createElement("div");
  linha.className = "linha-item";
  linha.appendChild(dotOrigem(item.origem));
  const cliente = document.createElement("span");
  cliente.className = "cliente";
  cliente.textContent = `${item.cliente || "—"} · ${item.situacao_manual || "—"}`;
  linha.appendChild(cliente);
  const extra = document.createElement("span");
  extra.className = "extra";
  extra.textContent = `${item.dias_situacao_manual}d parado`;
  linha.appendChild(extra);
  return linha;
}

function linhaCodigoRegra(item) {
  const linha = document.createElement("div");
  linha.className = "linha-item";
  linha.appendChild(dotOrigem(item.origem));
  const cliente = document.createElement("span");
  cliente.className = "cliente";
  cliente.textContent = `${item.cliente || "—"} · ${item.identificador || "—"}`;
  linha.appendChild(cliente);
  const extra = document.createElement("span");
  extra.className = "extra tag-manual";
  extra.textContent = item.codigo_regra ?? "";
  linha.appendChild(extra);
  return linha;
}

/** Widget de lista genérico: título + descrição + busca client-side
 * (padrão "filtros em tudo" já estabelecido no resto do sistema) + lista
 * com rolagem embutida. `containerOuId` aceita um id (string, widgets
 * nativos já fixos no HTML) ou o próprio elemento (widgets dinâmicos,
 * ex: métricas emprestadas do Admin). `camposBusca` decide quais campos
 * do item entram na busca (nem todo item tem cliente/identificador —
 * ex: "pendentes por cidade" busca por `cidade`). */
function montarWidgetLista(containerOuId, titulo, desc, itens, renderLinha, camposBusca = ["cliente", "identificador"]) {
  const container = typeof containerOuId === "string" ? document.getElementById(containerOuId) : containerOuId;
  container.innerHTML = `
    <h3>${titulo}</h3>
    <div class="desc">${desc}</div>
    <input type="text" class="busca" placeholder="Buscar..." />
    <div class="tabela-scroll"><div class="lista"></div></div>
  `;
  const busca = container.querySelector(".busca");
  const lista = container.querySelector(".lista");

  function render(filtro) {
    const termo = (filtro ?? "").trim().toLowerCase();
    const filtrados = !termo
      ? itens
      : itens.filter((item) => camposBusca.map((campo) => item[campo] ?? "").join(" ").toLowerCase().includes(termo));
    lista.innerHTML = "";
    if (filtrados.length === 0) {
      lista.innerHTML = '<div class="widget-vazio">Nada por aqui.</div>';
      return;
    }
    for (const item of filtrados) lista.appendChild(renderLinha(item));
  }

  busca.addEventListener("input", () => render(busca.value));
  render("");
}

function renderizarFunilStatus(funil) {
  const container = document.getElementById("widget-funil-status");
  const ordem = ["pendente", "aguardando_resposta", "respondido", "aguardando_ligacao", "encaminhado_puma", "bloqueado_sga"];
  const chaves = [...new Set([...ordem, ...Object.keys(funil)])].filter((chave) => chave in funil);

  let html = `
    <h3>Funil por status</h3>
    <div class="desc">Quantas pendências estão em cada etapa do ciclo, agora.</div>
  `;
  if (chaves.length === 0) {
    html += '<div class="widget-vazio">Nada por aqui.</div>';
  } else {
    for (const chave of chaves) {
      html += `<div class="funil-linha"><span>${STATUS_LABEL[chave] ?? chave}</span><span class="num">${funil[chave]}</span></div>`;
    }
  }
  container.innerHTML = html;
}

function linhaCidade(item) {
  const linha = document.createElement("div");
  linha.className = "linha-item";
  const cidade = document.createElement("span");
  cidade.className = "cliente";
  cidade.textContent = item.cidade;
  linha.appendChild(cidade);
  const extra = document.createElement("span");
  extra.className = "extra";
  extra.textContent = `${item.quantidade} pendente(s)`;
  linha.appendChild(extra);
  return linha;
}

async function carregarDashboardsOperador() {
  const dados = await api().obter_dashboards_operador();

  montarWidgetLista(
    "widget-fila-prioridade", "Fila de prioridade",
    "Top 50 pendências abertas, por nível de urgência e dias sem contato.",
    dados.fila_prioridade, linhaFilaPrioridade
  );
  montarWidgetLista(
    "widget-sem-atendimento", "Sem Atendimento definido",
    'Pendências sem "Atendimento" preenchido — não entram na esteira de disparo até isso ser definido.',
    dados.sem_atendimento, linhaBasica
  );
  renderizarFunilStatus(dados.funil_status);
  montarWidgetLista(
    "widget-situacao-manual-parada", "Situação Manual parada",
    "Ordenado por dias desde que a Situação Manual foi definida.",
    dados.situacao_manual_parada, linhaSituacaoManual
  );
  montarWidgetLista(
    "widget-contato-invalido", "Contato inválido bloqueado",
    'Telefone não registrado no WhatsApp, aguardando "Telefone corrigido" na planilha.',
    dados.contato_invalido, linhaBasica
  );
  montarWidgetLista(
    "widget-discrepancia-remocao", "Discrepância Remoção Ativa não revisada",
    "Rastreador ainda ativo, mas consta na lista de retirada.",
    dados.discrepancia_remocao_nao_revisada, linhaCodigoRegra
  );
}

// --------------------------------------------------------------------------
// Métricas "emprestadas" do Admin — só aparecem quando ele marca
// "Visível no Painel Operador" pra uma métrica (mecanismo novo desta
// sessão, ver orchestrator/metricas_admin_operador.py). Cards/widgets
// criados dinamicamente (não existem fixos no index.html, porque quais
// aparecem depende do que o Admin ligou).
// --------------------------------------------------------------------------

const METRICAS_ADMIN_SIMPLES_LABEL = {
  pendencias_em_aberto: "Pendências em aberto (agora)",
  encaminhadas_puma: "Pendências encaminhadas à Puma (agora)",
};

// Métricas "de período" (2026-08-14) — só estas respeitam o filtro
// De/Até novo; renderizam num grid próprio (`#resumo-grid-periodo`),
// separado do `.resumo-grid` nativo (que é sempre "agora").
const METRICAS_ADMIN_PERIODO_LABEL = {
  disparos: "Total de disparos",
  retornados: "Associados retornados",
  agendamentos_confirmados: "Agendamentos confirmados",
  concluidos: "Serviços concluídos no período",
  pct_resposta: "% de resposta (retornos / disparos)",
  tempo_medio_resolucao: "Tempo médio de resolução",
  taxa_escalonamento_puma: "Taxa de escalonamento pra Puma",
  pendentes: "Serviços pendentes",
  em_andamento: "Serviços em andamento",
  pct_pendencias: "% de pendências",
  pct_pendencias_concluidas: "% de pendências concluídas",
};

const METRICAS_ADMIN_LISTA_META = {
  pendentes_por_cidade: { titulo: "Pendentes por cidade", desc: "Serviços com status pendente, agrupados por cidade." },
};

function renderizarMetricasSimplesAdmin(metricasSimples) {
  document.querySelectorAll(".resumo-card-admin").forEach((el) => el.remove());
  const gridAgora = document.querySelector(".resumo-grid");
  const gridPeriodo = document.getElementById("resumo-grid-periodo");
  for (const [chave, valor] of Object.entries(metricasSimples ?? {})) {
    const noPeriodo = chave in METRICAS_ADMIN_PERIODO_LABEL;
    const label = noPeriodo ? METRICAS_ADMIN_PERIODO_LABEL[chave] : (METRICAS_ADMIN_SIMPLES_LABEL[chave] ?? chave);
    const card = document.createElement("div");
    card.className = "resumo-card resumo-card-admin";
    card.innerHTML = `<div class="rotulo">${label}</div><div class="valor">${valor}</div>`;
    (noPeriodo ? gridPeriodo : gridAgora).appendChild(card);
  }
}

function renderizarMetricasListaAdmin(metricasLista) {
  document.querySelectorAll(".widget-apoio-admin, #titulo-metricas-admin").forEach((el) => el.remove());

  const entradas = Object.entries(metricasLista ?? {});
  if (entradas.length === 0) return;

  const grid = document.querySelector(".painel-apoio-grid");
  const titulo = document.createElement("div");
  titulo.className = "painel-apoio-titulo";
  titulo.id = "titulo-metricas-admin";
  titulo.textContent = "Métricas do Admin";
  grid.insertAdjacentElement("beforebegin", titulo);

  for (const [chave, itens] of entradas) {
    const meta = METRICAS_ADMIN_LISTA_META[chave] ?? { titulo: chave, desc: "" };
    const container = document.createElement("div");
    container.className = "widget-apoio full widget-apoio-admin";
    grid.appendChild(container);
    montarWidgetLista(container, meta.titulo, meta.desc, itens, linhaCidade, ["cidade"]);
  }
}

const METRICAS_GRAFICO_META = {
  tendencia_diaria: { titulo: "Tendência diária", desc: "Disparos e retornados por dia, últimos 30 dias" },
  estado_por_origem: { titulo: "Estado por origem", desc: "Pendente / Em andamento / Concluído, por origem, agora" },
  distribuicao_urgencia: { titulo: "Distribuição por nível de urgência", desc: "Pendências abertas agora, por nível (1 a 5)" },
  evolucao_backlog: { titulo: "Evolução do backlog", desc: "Saldo em aberto e total concluído, acumulado desde o início" },
  pendentes_por_tipo: { titulo: "Pendentes por tipo", desc: "Instalação / Remoção / Manutenção" },
};

const ORIGENS_META = [
  { chave: "instalacao", label: "Instalação", cor: "var(--origem-instalacao)" },
  { chave: "remocao", label: "Remoção", cor: "var(--origem-remocao)" },
  { chave: "manutencao", label: "Manutenção", cor: "var(--origem-manutencao)" },
];

const BUCKETS_META = [
  { chave: "pendente", label: "Pendente" },
  { chave: "em_andamento", label: "Em andamento" },
  { chave: "concluido", label: "Concluído" },
];

function _renderizarGraficoPorChave(container, chave, dado) {
  const meta = METRICAS_GRAFICO_META[chave] ?? { titulo: chave, desc: "" };

  if (chave === "tendencia_diaria") {
    renderLineChart(container, {
      titulo: meta.titulo,
      desc: meta.desc,
      series: [
        { chave: "disparos", label: "Disparos", cor: "var(--serie-disparos)" },
        { chave: "retornados", label: "Retornados", cor: "var(--serie-retornados)" },
      ],
      pontos: dado,
    });
    return;
  }

  if (chave === "evolucao_backlog") {
    renderLineChart(container, {
      titulo: meta.titulo,
      desc: meta.desc,
      series: [
        { chave: "abertas", label: "Em aberto", cor: "var(--serie-abertas)" },
        { chave: "concluidas", label: "Concluídas (total)", cor: "var(--serie-concluidas)" },
      ],
      pontos: dado,
    });
    return;
  }

  if (chave === "estado_por_origem") {
    renderBarChart(container, {
      titulo: meta.titulo,
      desc: meta.desc,
      legenda: ORIGENS_META.map((o) => ({ label: o.label, cor: o.cor })),
      grupos: BUCKETS_META.map((bucket) => ({
        chave: bucket.chave,
        label: bucket.label,
        barras: ORIGENS_META.map((o) => ({
          chave: o.chave, label: o.label, cor: o.cor, valor: dado[o.chave]?.[bucket.chave] ?? 0,
        })),
      })),
    });
    return;
  }

  if (chave === "distribuicao_urgencia") {
    renderBarChart(container, {
      titulo: meta.titulo,
      desc: meta.desc,
      grupos: [1, 2, 3, 4, 5].map((nivel) => ({
        chave: String(nivel),
        label: `Nível ${nivel}`,
        barras: [{ chave: String(nivel), label: `Nível ${nivel}`, cor: `var(--nivel-${nivel})`, valor: dado[nivel] ?? 0 }],
      })),
    });
    return;
  }

  if (chave === "pendentes_por_tipo") {
    renderBarraLista(container, {
      titulo: meta.titulo,
      desc: meta.desc,
      itens: ORIGENS_META.map((o) => ({ chave: o.chave, label: o.label, cor: o.cor, valor: dado[o.chave] ?? 0 })),
    });
    return;
  }
}

function renderizarMetricasGraficoAdmin(metricasGrafico) {
  document.querySelectorAll(".widget-apoio-grafico, #titulo-graficos-admin").forEach((el) => el.remove());

  const entradas = Object.entries(metricasGrafico ?? {});
  const grid = document.getElementById("painel-graficos-admin");
  if (entradas.length === 0) {
    grid.innerHTML = "";
    return;
  }

  const titulo = document.createElement("div");
  titulo.className = "painel-apoio-titulo";
  titulo.id = "titulo-graficos-admin";
  titulo.textContent = "Gráficos";
  grid.insertAdjacentElement("beforebegin", titulo);

  for (const [chave, dado] of entradas) {
    const container = document.createElement("div");
    container.className = "widget-apoio full widget-apoio-grafico";
    grid.appendChild(container);
    _renderizarGraficoPorChave(container, chave, dado);
  }
}

function janelaPadrao30Dias() {
  const hoje = new Date();
  const desde = new Date(hoje);
  desde.setDate(desde.getDate() - 29);
  const paraISO = (d) => d.toISOString().slice(0, 10);
  return { desde: paraISO(desde), ate: paraISO(hoje) };
}

function inicializarFiltroPeriodoAdmin() {
  const form = document.getElementById("filtro-periodo-admin");
  const inputDesde = document.getElementById("filtro-desde");
  const inputAte = document.getElementById("filtro-ate");
  const { desde, ate } = janelaPadrao30Dias();
  inputDesde.value = desde;
  inputAte.value = ate;

  form.addEventListener("submit", (evento) => {
    evento.preventDefault();
    carregarMetricasAdminOperador(inputDesde.value, inputAte.value);
  });
}

async function carregarMetricasAdminOperador(desde, ate) {
  const dados = await api().obter_metricas_admin_operador(desde, ate);
  renderizarMetricasSimplesAdmin(dados.metricas_simples);
  renderizarMetricasListaAdmin(dados.metricas_lista);
  renderizarMetricasGraficoAdmin(dados.metricas_grafico);
}

// --------------------------------------------------------------------------
// Watchdog (Observabilidade, fatia 1) — travada / etapas com falha / lentas
// --------------------------------------------------------------------------

function labelDaEtapa(etapaId) {
  const etapa = etapasCache.find((e) => e.id === etapaId);
  return etapa ? etapa.label : etapaId;
}

function textoTravada(sinal) {
  return `Execução travada há ${sinal.minutos} min (máquina: ${sinal.maquina || "desconhecida"}) — pode estar presa de verdade, veja o Painel Operador daquela máquina.`;
}

function textoEtapaComFalha(sinal) {
  return `"${labelDaEtapa(sinal.etapa_id)}" falhou na última execução: ${sinal.mensagem || "sem detalhe"}.`;
}

function textoEtapaLenta(sinal) {
  const atual = (sinal.duracao_atual_ms / 1000).toFixed(1);
  const media = (sinal.media_historica_ms / 1000).toFixed(1);
  return `"${labelDaEtapa(sinal.etapa_id)}" está mais lenta que o normal: ${atual}s (média histórica: ${media}s).`;
}

function renderizarBannerWatchdog(status) {
  const banner = document.getElementById("banner-watchdog");
  banner.innerHTML = "";

  const itens = [];
  if (status.execucao_travada) {
    itens.push({ classe: "banner-watchdog-critico", texto: textoTravada(status.execucao_travada) });
  }
  for (const falha of status.etapas_com_falha) {
    itens.push({ classe: "banner-watchdog-critico", texto: textoEtapaComFalha(falha) });
  }
  for (const lenta of status.etapas_lentas) {
    itens.push({ classe: "banner-watchdog-atencao", texto: textoEtapaLenta(lenta) });
  }

  banner.hidden = itens.length === 0;
  for (const item of itens) {
    const div = document.createElement("div");
    div.className = `banner-watchdog-item ${item.classe}`;
    div.textContent = item.texto;
    banner.appendChild(div);
  }
}

async function carregarWatchdog() {
  const status = await api().obter_status_watchdog();
  renderizarBannerWatchdog(status);
}

// --------------------------------------------------------------------------
// Troca de seção (sidebar com 3 itens — Fases da Automação / Dashboard / Manual)
// --------------------------------------------------------------------------

function mostrarSecao(nome) {
  document.querySelectorAll(".content-section").forEach((el) =>
    el.classList.toggle("active", el.dataset.section === nome)
  );
  document.querySelectorAll(".nav-item[data-section]").forEach((el) =>
    el.classList.toggle("active", el.dataset.section === nome)
  );
}

// --------------------------------------------------------------------------
// Wiring inicial
// --------------------------------------------------------------------------

function inicializarAppAutenticado() {
  document.querySelectorAll(".nav-item[data-section]").forEach((btn) =>
    btn.addEventListener("click", () => mostrarSecao(btn.dataset.section))
  );

  document.getElementById("btn-executar-selecionadas").addEventListener("click", () => {
    if (selecionadas.size === 0) {
      alert("Selecione ao menos uma etapa.");
      return;
    }
    executar(Array.from(selecionadas), "selecionadas");
  });

  document.getElementById("btn-executar-tudo").addEventListener("click", () => executar(null, "todas"));

  document.getElementById("btn-cancelar").addEventListener("click", () => api().cancelar_execucao());

  document.getElementById("btn-ir-tratativas").addEventListener("click", () => api().abrir_tratativas());

  document.getElementById("btn-abrir-log").addEventListener("click", async () => {
    const resultado = await api().abrir_log_execucoes();
    if (!resultado.aberto) {
      alert("Nenhum log registrado ainda — só grava quando uma etapa tem erro ou falha por item.");
    }
  });

  document.getElementById("btn-continuar").addEventListener("click", () => api().continuar_apos_reconexao_manual());

  carregarEtapas();
  carregarDashboardsOperador();
  inicializarFiltroPeriodoAdmin();
  const { desde, ate } = janelaPadrao30Dias();
  carregarMetricasAdminOperador(desde, ate);
  carregarWatchdog();
  atualizarBotoes();
  pollProgresso();
  setInterval(pollProgresso, 800);
  setInterval(carregarWatchdog, 15000);
}

function init() {
  document.getElementById("form-login").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const email = document.getElementById("login-email").value.trim();
    const senha = document.getElementById("login-senha").value;
    const erroEl = document.getElementById("login-erro");
    const botao = ev.target.querySelector('button[type="submit"]');
    erroEl.hidden = true;
    botao.disabled = true;
    botao.textContent = "Entrando...";

    try {
      const resultado = await api().autenticar(email, senha);
      if (!resultado.sucesso) {
        erroEl.textContent = resultado.erro;
        erroEl.hidden = false;
        return;
      }
      document.getElementById("login-overlay").remove();
      inicializarAppAutenticado();
    } catch (e) {
      erroEl.textContent = "Erro inesperado ao tentar entrar. Tente novamente.";
      erroEl.hidden = false;
    } finally {
      botao.disabled = false;
      botao.textContent = "Entrar";
    }
  });
}

window.addEventListener("pywebviewready", init);
