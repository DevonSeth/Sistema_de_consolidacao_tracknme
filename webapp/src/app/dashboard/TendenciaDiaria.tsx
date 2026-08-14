"use client";

import { useRef, useState, type ReactNode } from "react";

import type { PontoSerieDiaria } from "@/lib/dashboard-metrics";
import { formatarTick, tetoAgradavel } from "@/lib/dashboard-metricas-meta";

const SERIES = [
  { chave: "disparos", label: "Disparos", cor: "var(--serie-disparos)" },
  { chave: "retornados", label: "Retornados", cor: "var(--serie-retornados)" },
] as const;

const VB_W = 760;
const VB_H = 220;
const MARGEM = { esquerda: 40, direita: 58, topo: 16, baixo: 26 };
const PLOT_W = VB_W - MARGEM.esquerda - MARGEM.direita;
const PLOT_H = VB_H - MARGEM.topo - MARGEM.baixo;

function formatarDia(iso: string): string {
  return `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
}

export default function TendenciaDiaria({
  serie,
  rodape,
}: {
  serie: PontoSerieDiaria[];
  /** Conteúdo extra ao final do card (ex: toggle de visibilidade no Painel Admin). */
  rodape?: ReactNode;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<{ idx: number; x: number; y: number } | null>(null);

  if (serie.length === 0) {
    return (
      <div className="painel-db">
        <h2>Tendência diária</h2>
        <div className="linha-vazia">Sem dados no período selecionado.</div>
        {rodape}
      </div>
    );
  }

  const n = serie.length;
  const xAt = (i: number) => MARGEM.esquerda + (n > 1 ? (i / (n - 1)) * PLOT_W : PLOT_W / 2);

  const maiorValor = Math.max(1, ...serie.flatMap((d) => [d.disparos, d.retornados]));
  const teto = tetoAgradavel(maiorValor);
  const escalaMax = teto * 1.15;
  const yAt = (v: number) => MARGEM.topo + PLOT_H - (v / escalaMax) * PLOT_H;
  const ticks = [0, teto / 2, teto];

  const caminho = (chave: "disparos" | "retornados") =>
    serie.map((d, i) => `${i === 0 ? "M" : "L"}${xAt(i)},${yAt(d[chave])}`).join(" ");

  // Ticks do eixo X — no máximo 6, sempre incluindo o primeiro e o último dia.
  const numTicksX = Math.min(6, n);
  const indicesX =
    numTicksX > 1
      ? Array.from(new Set(Array.from({ length: numTicksX }, (_, k) => Math.round((k * (n - 1)) / (numTicksX - 1)))))
      : [0];

  const ultimo = serie[n - 1];
  const yFimDisparos = yAt(ultimo.disparos);
  const yFimRetornados = yAt(ultimo.retornados);
  let labelYDisparos = yFimDisparos;
  let labelYRetornados = yFimRetornados;
  if (Math.abs(yFimDisparos - yFimRetornados) < 14) {
    const meio = (yFimDisparos + yFimRetornados) / 2;
    if (yFimDisparos <= yFimRetornados) {
      labelYDisparos = meio - 7;
      labelYRetornados = meio + 7;
    } else {
      labelYDisparos = meio + 7;
      labelYRetornados = meio - 7;
    }
  }

  function moverPonteiro(clientX: number) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const relX = ((clientX - rect.left) / rect.width) * VB_W;
    const fracao = (relX - MARGEM.esquerda) / PLOT_W;
    const idx = Math.max(0, Math.min(n - 1, Math.round(fracao * (n - 1))));
    setHover({ idx, x: clientX, y: rect.top });
  }

  const hoveredDia = hover ? serie[hover.idx] : null;

  return (
    <div className="painel-db">
      <h2>Tendência diária</h2>
      <div className="desc">Disparos e retornados por dia, no período filtrado</div>

      <div className="legenda-grafico">
        {SERIES.map((s) => (
          <div className="item" key={s.chave}>
            <span className="linha" style={{ background: s.cor }} />
            {s.label}
          </div>
        ))}
      </div>

      <svg
        ref={svgRef}
        className="grafico-linha"
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="none"
        onMouseMove={(e) => moverPonteiro(e.clientX)}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t) => {
          const y = yAt(t);
          return (
            <g key={t}>
              <line className="grade-linha" x1={MARGEM.esquerda} x2={VB_W - MARGEM.direita} y1={y} y2={y} strokeDasharray="3,3" />
              <text className="eixo-texto" x={MARGEM.esquerda - 6} y={y + 3} textAnchor="end">
                {formatarTick(t)}
              </text>
            </g>
          );
        })}

        {indicesX.map((i) => (
          <text key={i} className="eixo-texto" x={xAt(i)} y={VB_H - 6} textAnchor="middle">
            {formatarDia(serie[i].dia)}
          </text>
        ))}

        {hover && (
          <line
            className="crosshair"
            x1={xAt(hover.idx)}
            x2={xAt(hover.idx)}
            y1={MARGEM.topo}
            y2={MARGEM.topo + PLOT_H}
          />
        )}

        <path className="serie" d={caminho("disparos")} stroke="var(--serie-disparos)" />
        <path className="serie" d={caminho("retornados")} stroke="var(--serie-retornados)" />

        <circle className="ponto-final" cx={xAt(n - 1)} cy={yFimDisparos} r={4} fill="var(--serie-disparos)" />
        <circle className="ponto-final" cx={xAt(n - 1)} cy={yFimRetornados} r={4} fill="var(--serie-retornados)" />

        <line x1={xAt(n - 1) + 6} x2={xAt(n - 1) + 14} y1={labelYDisparos} y2={labelYDisparos} stroke="var(--serie-disparos)" strokeWidth={2} />
        <text className="rotulo-final" x={xAt(n - 1) + 18} y={labelYDisparos + 4} fill="var(--text)">
          {ultimo.disparos}
        </text>
        <line x1={xAt(n - 1) + 6} x2={xAt(n - 1) + 14} y1={labelYRetornados} y2={labelYRetornados} stroke="var(--serie-retornados)" strokeWidth={2} />
        <text className="rotulo-final" x={xAt(n - 1) + 18} y={labelYRetornados + 4} fill="var(--text)">
          {ultimo.retornados}
        </text>

        <rect
          className="hit-area"
          x={MARGEM.esquerda}
          y={MARGEM.topo}
          width={PLOT_W}
          height={PLOT_H}
          onMouseMove={(e) => moverPonteiro(e.clientX)}
        />
      </svg>

      {rodape}

      {hover && hoveredDia && (
        <div className="viz-tooltip" style={{ left: hover.x + 12, top: hover.y }}>
          <div className="titulo">{formatarDia(hoveredDia.dia)}</div>
          {SERIES.map((s) => (
            <div className="linha" key={s.chave}>
              <span className="rotulo">
                <span className="dot" style={{ background: s.cor }} />
                {s.label}
              </span>
              <span className="valor">{hoveredDia[s.chave]}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
