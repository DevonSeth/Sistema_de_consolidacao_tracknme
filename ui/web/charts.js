// --------------------------------------------------------------------------
// Motor de gráfico do Painel Operador — porta em JS puro (sem framework,
// sem build step) dos 5 gráficos que já existem em React/TSX no Dashboard
// Admin/Cliente (`webapp/src/app/dashboard/*.tsx`). 3 primitivas cobrem os
// 5 casos:
//   - renderLineChart  -> Tendência diária / Evolução do backlog (mesma
//     estrutura SVG hoje em React, só muda série/cor).
//   - renderBarChart   -> Estado por origem (agrupado) / Distribuição de
//     urgência (1 barra por grupo) — mesmas classes CSS nos dois casos.
//   - renderBarraLista -> Pendentes por tipo (barra horizontal simples,
//     sem SVG, com checkbox por origem).
// Quem monta a config (cores/labels/formato) a partir do dado do Python é
// `app.js::renderizarMetricasGraficoAdmin` — este arquivo é agnóstico do
// que está sendo desenhado.
// --------------------------------------------------------------------------

/** Teto "limpo" pro eixo Y (1/2/5 × potência de 10) — porta de
 * `webapp/src/lib/dashboard-metricas-meta.ts::tetoAgradavel`. */
function tetoAgradavel(valor) {
  if (valor <= 0) return 4;
  const exp = Math.floor(Math.log10(valor));
  const base = Math.pow(10, exp);
  const frac = valor / base;
  const niceFrac = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
  return niceFrac * base;
}

/** Formata um tick do eixo Y — porta de `formatarTick` (TS). `teto/2` só
 * é inteiro quando `teto` é múltiplo de 4; quando `tetoAgradavel` escolhe
 * um teto "5" (5, 50, 500...), a metade vira X,5 — mostra 1 casa decimal
 * só quando existir, nunca arredonda escondendo a metade real. */
function formatarTick(valor) {
  const arredondado = Math.round(valor * 10) / 10;
  return arredondado.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
}

function formatarDiaPadrao(iso) {
  return `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
}

// --------------------------------------------------------------------------
// Tooltip compartilhado (1 elemento reaproveitado por todos os gráficos,
// em vez de criar um novo por render — só existem 5 gráficos na tela, mas
// evita acumular elementos órfãos em `document.body`).
// --------------------------------------------------------------------------

let _tooltipEl = null;

function _tooltip() {
  if (!_tooltipEl) {
    _tooltipEl = document.createElement("div");
    _tooltipEl.className = "viz-tooltip";
    _tooltipEl.style.display = "none";
    document.body.appendChild(_tooltipEl);
  }
  return _tooltipEl;
}

function mostrarTooltip(clientX, clientY, html) {
  const el = _tooltip();
  el.innerHTML = html;
  el.style.left = `${clientX + 12}px`;
  el.style.top = `${clientY}px`;
  el.style.display = "";
}

function esconderTooltip() {
  _tooltip().style.display = "none";
}

function _svgEl(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [chave, valor] of Object.entries(attrs)) el.setAttribute(chave, valor);
  return el;
}

function _montarCabecalho(container, titulo, desc) {
  container.innerHTML = "";
  const h3 = document.createElement("h3");
  h3.textContent = titulo;
  container.appendChild(h3);
  if (desc) {
    const descEl = document.createElement("div");
    descEl.className = "desc";
    descEl.textContent = desc;
    container.appendChild(descEl);
  }
}

// --------------------------------------------------------------------------
// renderLineChart — cfg: { titulo, desc, series: [{chave,label,cor}],
// pontos: [{dia, [chave série]: valor}], formatarDia? }
// --------------------------------------------------------------------------

const _VB_W = 760;
const _VB_H = 220;
const _MARGEM_LINHA = { esquerda: 40, direita: 70, topo: 16, baixo: 26 };

function renderLineChart(container, cfg) {
  const { titulo, desc, series, pontos, formatarDia = formatarDiaPadrao } = cfg;
  _montarCabecalho(container, titulo, desc);

  if (!pontos || pontos.length === 0) {
    const vazio = document.createElement("div");
    vazio.className = "widget-vazio";
    vazio.textContent = "Sem dados no momento.";
    container.appendChild(vazio);
    return;
  }

  const legenda = document.createElement("div");
  legenda.className = "legenda-grafico";
  for (const s of series) {
    const item = document.createElement("div");
    item.className = "item";
    item.innerHTML = `<span class="linha" style="background:${s.cor}"></span>${s.label}`;
    legenda.appendChild(item);
  }
  container.appendChild(legenda);

  const n = pontos.length;
  const plotW = _VB_W - _MARGEM_LINHA.esquerda - _MARGEM_LINHA.direita;
  const plotH = _VB_H - _MARGEM_LINHA.topo - _MARGEM_LINHA.baixo;
  const xAt = (i) => _MARGEM_LINHA.esquerda + (n > 1 ? (i / (n - 1)) * plotW : plotW / 2);

  const maiorValor = Math.max(1, ...pontos.flatMap((p) => series.map((s) => p[s.chave] ?? 0)));
  const teto = tetoAgradavel(maiorValor);
  const escalaMax = teto * 1.15;
  const yAt = (v) => _MARGEM_LINHA.topo + plotH - (v / escalaMax) * plotH;
  const ticks = [0, teto / 2, teto];

  const caminho = (chave) =>
    pontos.map((p, i) => `${i === 0 ? "M" : "L"}${xAt(i)},${yAt(p[chave] ?? 0)}`).join(" ");

  const numTicksX = Math.min(6, n);
  const indicesX =
    numTicksX > 1
      ? [...new Set(Array.from({ length: numTicksX }, (_, k) => Math.round((k * (n - 1)) / (numTicksX - 1))))]
      : [0];

  const svg = _svgEl("svg", { class: "grafico-linha", viewBox: `0 0 ${_VB_W} ${_VB_H}`, preserveAspectRatio: "none" });

  for (const t of ticks) {
    const y = yAt(t);
    svg.appendChild(
      _svgEl("line", {
        class: "grade-linha", x1: _MARGEM_LINHA.esquerda, x2: _VB_W - _MARGEM_LINHA.direita,
        y1: y, y2: y, "stroke-dasharray": "3,3",
      })
    );
    const texto = _svgEl("text", { class: "eixo-texto", x: _MARGEM_LINHA.esquerda - 6, y: y + 3, "text-anchor": "end" });
    texto.textContent = formatarTick(t);
    svg.appendChild(texto);
  }

  for (const i of indicesX) {
    const texto = _svgEl("text", { class: "eixo-texto", x: xAt(i), y: _VB_H - 6, "text-anchor": "middle" });
    texto.textContent = formatarDia(pontos[i].dia);
    svg.appendChild(texto);
  }

  const crosshair = _svgEl("line", {
    class: "crosshair", x1: 0, x2: 0, y1: _MARGEM_LINHA.topo, y2: _MARGEM_LINHA.topo + plotH,
  });
  crosshair.style.display = "none";
  svg.appendChild(crosshair);

  for (const s of series) {
    svg.appendChild(_svgEl("path", { class: "serie", d: caminho(s.chave), stroke: s.cor }));
  }

  const ultimo = pontos[n - 1];
  const finais = series.map((s) => ({ s, y: yAt(ultimo[s.chave] ?? 0) }));
  if (finais.length === 2 && Math.abs(finais[0].y - finais[1].y) < 14) {
    const meio = (finais[0].y + finais[1].y) / 2;
    if (finais[0].y <= finais[1].y) {
      finais[0].labelY = meio - 7;
      finais[1].labelY = meio + 7;
    } else {
      finais[0].labelY = meio + 7;
      finais[1].labelY = meio - 7;
    }
  } else {
    finais.forEach((f) => (f.labelY = f.y));
  }
  for (const f of finais) {
    svg.appendChild(_svgEl("circle", { class: "ponto-final", cx: xAt(n - 1), cy: f.y, r: 4, fill: f.s.cor }));
    svg.appendChild(
      _svgEl("line", {
        x1: xAt(n - 1) + 6, x2: xAt(n - 1) + 14, y1: f.labelY, y2: f.labelY, stroke: f.s.cor, "stroke-width": 2,
      })
    );
    const texto = _svgEl("text", { class: "rotulo-final", x: xAt(n - 1) + 18, y: f.labelY + 4, fill: "var(--text)" });
    texto.textContent = ultimo[f.s.chave] ?? 0;
    svg.appendChild(texto);
  }

  function mover(clientX, clientY) {
    const rect = svg.getBoundingClientRect();
    const relX = ((clientX - rect.left) / rect.width) * _VB_W;
    const fracao = (relX - _MARGEM_LINHA.esquerda) / plotW;
    const idx = Math.max(0, Math.min(n - 1, Math.round(fracao * (n - 1))));
    const ponto = pontos[idx];
    crosshair.setAttribute("x1", xAt(idx));
    crosshair.setAttribute("x2", xAt(idx));
    crosshair.style.display = "";
    const linhas = series
      .map(
        (s) =>
          `<div class="linha"><span class="rotulo"><span class="dot" style="background:${s.cor}"></span>${s.label}</span><span class="valor">${ponto[s.chave] ?? 0}</span></div>`
      )
      .join("");
    mostrarTooltip(clientX, clientY, `<div class="titulo">${formatarDia(ponto.dia)}</div>${linhas}`);
  }

  const hitArea = _svgEl("rect", {
    class: "hit-area", x: _MARGEM_LINHA.esquerda, y: _MARGEM_LINHA.topo, width: plotW, height: plotH,
  });
  hitArea.addEventListener("mousemove", (e) => mover(e.clientX, e.clientY));
  svg.addEventListener("mouseleave", () => {
    crosshair.style.display = "none";
    esconderTooltip();
  });
  svg.appendChild(hitArea);

  container.appendChild(svg);
}

// --------------------------------------------------------------------------
// renderBarChart — cfg: { titulo, desc, legenda?: [{label,cor}],
// grupos: [{chave, label, barras: [{chave,label,cor,valor}]}] }
// Cobre tanto agrupado (Estado por origem, 3 barras por grupo) quanto
// 1 barra por grupo com cor própria (Distribuição de urgência).
// --------------------------------------------------------------------------

function renderBarChart(container, cfg) {
  const { titulo, desc, legenda, grupos } = cfg;
  _montarCabecalho(container, titulo, desc);

  if (legenda && legenda.length > 0) {
    const legendaEl = document.createElement("div");
    legendaEl.className = "legenda-grafico";
    for (const l of legenda) {
      const item = document.createElement("div");
      item.className = "item";
      item.innerHTML = `<span class="dot" style="background:${l.cor}"></span>${l.label}`;
      legendaEl.appendChild(item);
    }
    container.appendChild(legendaEl);
  }

  const todosValores = grupos.flatMap((g) => g.barras.map((b) => b.valor));
  const teto = tetoAgradavel(Math.max(1, ...todosValores));
  const escalaMax = teto * 1.15;
  const ticks = [0, teto / 2, teto];

  const grafico = document.createElement("div");
  grafico.className = "grafico-colunas";

  const eixoY = document.createElement("div");
  eixoY.className = "eixo-y";
  for (const t of ticks) {
    const span = document.createElement("span");
    span.className = "tick";
    span.style.bottom = `${(t / escalaMax) * 100}%`;
    span.textContent = formatarTick(t);
    eixoY.appendChild(span);
  }
  grafico.appendChild(eixoY);

  for (const t of ticks) {
    const grade = document.createElement("div");
    grade.className = "grade";
    grade.style.bottom = `${(t / escalaMax) * 100}%`;
    grafico.appendChild(grade);
  }

  for (const g of grupos) {
    const grupoEl = document.createElement("div");
    grupoEl.className = "grupo-coluna";

    const colunas = document.createElement("div");
    colunas.className = "colunas-do-grupo";

    for (const b of g.barras) {
      const altura = (b.valor / escalaMax) * 100;
      const wrap = document.createElement("div");
      wrap.className = "coluna-wrap";
      wrap.tabIndex = 0;
      wrap.innerHTML = `<span class="valor">${b.valor}</span><div class="coluna" style="height:${altura}%;background:${b.cor}"></div>`;

      const conteudoTooltip = `<div class="titulo">${g.label}</div><div class="linha"><span class="rotulo"><span class="dot" style="background:${b.cor}"></span>${b.label}</span><span class="valor">${b.valor}</span></div>`;
      wrap.addEventListener("mousemove", (e) => mostrarTooltip(e.clientX, e.clientY, conteudoTooltip));
      wrap.addEventListener("mouseleave", esconderTooltip);
      wrap.addEventListener("focus", () => {
        const r = wrap.getBoundingClientRect();
        mostrarTooltip(r.left + r.width / 2, r.top, conteudoTooltip);
      });
      wrap.addEventListener("blur", esconderTooltip);

      colunas.appendChild(wrap);
    }

    grupoEl.appendChild(colunas);
    const label = document.createElement("div");
    label.className = "grupo-label";
    label.textContent = g.label;
    grupoEl.appendChild(label);
    grafico.appendChild(grupoEl);
  }

  container.appendChild(grafico);
}

// --------------------------------------------------------------------------
// renderBarraLista — cfg: { titulo, desc, itens: [{chave,label,cor,valor}] }
// Porta de `PendentesPorTipo.tsx` — barra horizontal via width%, sem SVG,
// com checkbox por item pra ligar/desligar (mesmo padrão do React).
// --------------------------------------------------------------------------

function renderBarraLista(container, cfg) {
  const { titulo, desc, itens } = cfg;
  _montarCabecalho(container, titulo, desc);

  const ativos = new Set(itens.map((item) => item.chave));

  const checkRow = document.createElement("div");
  checkRow.className = "origem-check-row";

  const listaEl = document.createElement("div");

  function renderLista() {
    listaEl.innerHTML = "";
    const maiorValor = Math.max(1, ...itens.map((item) => item.valor));
    for (const item of itens) {
      if (!ativos.has(item.chave)) continue;
      const largura = (item.valor / maiorValor) * 100;
      const linha = document.createElement("div");
      linha.className = "barra-item";
      linha.innerHTML = `
        <div class="nome"><span class="dot" style="background:${item.cor}"></span>${item.label}</div>
        <div class="barra-trilho"><div class="barra-fill" style="width:${largura}%;background:${item.cor}"></div></div>
        <div class="num">${item.valor}</div>`;
      listaEl.appendChild(linha);
    }
  }

  for (const item of itens) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) ativos.add(item.chave);
      else ativos.delete(item.chave);
      renderLista();
    });
    label.appendChild(checkbox);
    label.appendChild(document.createTextNode(item.label));
    checkRow.appendChild(label);
  }

  container.appendChild(checkRow);
  container.appendChild(listaEl);
  renderLista();
}
