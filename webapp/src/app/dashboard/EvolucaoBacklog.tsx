"use client";

import { useRef, useState, type ReactNode } from "react";

import type { PontoEvolucao } from "@/lib/dashboard-metrics";
import { formatarTick, tetoAgradavel } from "@/lib/dashboard-metricas-meta";

import InfoTooltip from "./InfoTooltip";

const SERIES = [
  { chave: "abertas", label: "Em aberto", cor: "var(--serie-abertas)" },
  { chave: "concluidas", label: "Concluídas (total)", cor: "var(--serie-concluidas)" },
] as const;

const VB_W = 760;
const VB_H = 220;
const MARGEM = { esquerda: 40, direita: 84, topo: 16, baixo: 26 };
const PLOT_W = VB_W - MARGEM.esquerda - MARGEM.direita;
const PLOT_H = VB_H - MARGEM.topo - MARGEM.baixo;

function formatarDia(iso: string): string {
  return `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
}

export default function EvolucaoBacklog({
  serie,
  desde,
  ate,
  descricao,
  rodape,
}: {
  serie: PontoEvolucao[];
  desde: string;
  ate: string;
  descricao?: string;
  /** Conteúdo extra ao final do card (ex: toggle de visibilidade no Painel Admin). */
  rodape?: ReactNode;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<{ idx: number; x: number; y: number } | null>(null);
  const [visao, setVisao] = useState<"periodo" | "completo">("periodo");

  const serieExibida = visao === "periodo" ? serie.filter((p) => p.dia >= desde && p.dia <= ate) : serie;

  const toggle = (
    <div className="no-print" style={{ display: "flex", gap: 8, marginBottom: 10 }}>
      <button
        type="button"
        className={visao === "periodo" ? "btn primary small" : "btn small"}
        onClick={() => setVisao("periodo")}
      >
        Diário (período filtrado)
      </button>
      <button
        type="button"
        className={visao === "completo" ? "btn primary small" : "btn small"}
        onClick={() => setVisao("completo")}
      >
        Acumulado desde o início
      </button>
    </div>
  );

  if (serieExibida.length === 0) {
    return (
      <div className="painel-db">
        <h2>Evolução do backlog</h2>
        {toggle}
        <div className="linha-vazia">Sem dados no período selecionado.</div>
        {rodape}
      </div>
    );
  }

  const n = serieExibida.length;
  const xAt = (i: number) => MARGEM.esquerda + (n > 1 ? (i / (n - 1)) * PLOT_W : PLOT_W / 2);

  const maiorValor = Math.max(1, ...serieExibida.flatMap((d) => [d.abertas, d.concluidas]));
  const teto = tetoAgradavel(maiorValor);
  const escalaMax = teto * 1.15;
  const yAt = (v: number) => MARGEM.topo + PLOT_H - (v / escalaMax) * PLOT_H;
  const ticks = [0, teto / 2, teto];

  const caminho = (chave: "abertas" | "concluidas") =>
    serieExibida.map((d, i) => `${i === 0 ? "M" : "L"}${xAt(i)},${yAt(d[chave])}`).join(" ");

  const numTicksX = Math.min(6, n);
  const indicesX =
    numTicksX > 1
      ? Array.from(new Set(Array.from({ length: numTicksX }, (_, k) => Math.round((k * (n - 1)) / (numTicksX - 1)))))
      : [0];

  const ultimo = serieExibida[n - 1];
  const yFimAbertas = yAt(ultimo.abertas);
  const yFimConcluidas = yAt(ultimo.concluidas);
  let labelYAbertas = yFimAbertas;
  let labelYConcluidas = yFimConcluidas;
  if (Math.abs(yFimAbertas - yFimConcluidas) < 14) {
    const meio = (yFimAbertas + yFimConcluidas) / 2;
    if (yFimAbertas <= yFimConcluidas) {
      labelYAbertas = meio - 7;
      labelYConcluidas = meio + 7;
    } else {
      labelYAbertas = meio + 7;
      labelYConcluidas = meio - 7;
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

  const hoveredDia = hover ? serieExibida[hover.idx] : null;

  return (
    <div className="painel-db">
      <h2>
        Evolução do backlog
        {descricao && <InfoTooltip texto={descricao} label="Evolução do backlog" />}
      </h2>
      <div className="desc">
        {visao === "periodo" ? "Saldo em aberto e total concluído, no período filtrado" : "Saldo em aberto e total concluído, desde o primeiro registro"}
      </div>

      {toggle}

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
            {formatarDia(serieExibida[i].dia)}
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

        <path className="serie" d={caminho("abertas")} stroke="var(--serie-abertas)" />
        <path className="serie" d={caminho("concluidas")} stroke="var(--serie-concluidas)" />

        <circle className="ponto-final" cx={xAt(n - 1)} cy={yFimAbertas} r={4} fill="var(--serie-abertas)" />
        <circle className="ponto-final" cx={xAt(n - 1)} cy={yFimConcluidas} r={4} fill="var(--serie-concluidas)" />

        <line x1={xAt(n - 1) + 6} x2={xAt(n - 1) + 14} y1={labelYAbertas} y2={labelYAbertas} stroke="var(--serie-abertas)" strokeWidth={2} />
        <text className="rotulo-final" x={xAt(n - 1) + 18} y={labelYAbertas + 4} fill="var(--text)">
          {ultimo.abertas}
        </text>
        <line x1={xAt(n - 1) + 6} x2={xAt(n - 1) + 14} y1={labelYConcluidas} y2={labelYConcluidas} stroke="var(--serie-concluidas)" strokeWidth={2} />
        <text className="rotulo-final" x={xAt(n - 1) + 18} y={labelYConcluidas + 4} fill="var(--text)">
          {ultimo.concluidas}
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
