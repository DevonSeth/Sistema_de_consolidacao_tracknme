"use client";

import { useState, type ReactNode } from "react";

import type { DistribuicaoUrgencia as DistribuicaoUrgenciaValores } from "@/lib/dashboard-metrics";
import { formatarTick, tetoAgradavel } from "@/lib/dashboard-metricas-meta";

const NIVEIS = [
  { valor: 1, cor: "var(--nivel-1)" },
  { valor: 2, cor: "var(--nivel-2)" },
  { valor: 3, cor: "var(--nivel-3)" },
  { valor: 4, cor: "var(--nivel-4)" },
  { valor: 5, cor: "var(--nivel-5)" },
] as const;

type Tooltip = { x: number; y: number; nivel: number; valor: number; cor: string };

export default function DistribuicaoUrgencia({
  distribuicao,
  rodape,
}: {
  distribuicao: DistribuicaoUrgenciaValores;
  rodape?: ReactNode;
}) {
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);

  const maiorValor = Math.max(1, ...NIVEIS.map((n) => distribuicao[n.valor] ?? 0));
  const teto = tetoAgradavel(maiorValor);
  const escalaMax = teto * 1.15;
  const ticks = [0, teto / 2, teto];

  return (
    <div className="painel-db">
      <h2>Distribuição por nível de urgência</h2>
      <div className="desc">Pendências abertas agora, por nível (1 a 5) — não respeita o filtro de período</div>

      <div className="grafico-colunas">
        <div className="eixo-y">
          {ticks.map((t) => (
            <span key={t} className="tick" style={{ bottom: `${(t / escalaMax) * 100}%` }}>
              {formatarTick(t)}
            </span>
          ))}
        </div>
        {ticks.map((t) => (
          <div key={t} className="grade" style={{ bottom: `${(t / escalaMax) * 100}%` }} />
        ))}

        {NIVEIS.map((n) => {
          const valor = distribuicao[n.valor] ?? 0;
          const altura = (valor / escalaMax) * 100;
          return (
            <div className="grupo-coluna" key={n.valor}>
              <div className="colunas-do-grupo">
                <div
                  className="coluna-wrap"
                  style={{ width: 46 }}
                  tabIndex={0}
                  onMouseMove={(e) => setTooltip({ x: e.clientX, y: e.clientY, nivel: n.valor, valor, cor: n.cor })}
                  onMouseLeave={() => setTooltip(null)}
                  onFocus={(e) => {
                    const r = e.currentTarget.getBoundingClientRect();
                    setTooltip({ x: r.left + r.width / 2, y: r.top, nivel: n.valor, valor, cor: n.cor });
                  }}
                  onBlur={() => setTooltip(null)}
                >
                  <span className="valor">{valor}</span>
                  <div className="coluna" style={{ height: `${altura}%`, background: n.cor }} />
                </div>
              </div>
              <div className="grupo-label">Nível {n.valor}</div>
            </div>
          );
        })}
      </div>

      {rodape}

      {tooltip && (
        <div className="viz-tooltip" style={{ left: tooltip.x + 12, top: tooltip.y - 40 }}>
          <div className="linha">
            <span className="rotulo">
              <span className="dot" style={{ background: tooltip.cor }} />
              Nível {tooltip.nivel}
            </span>
            <span className="valor">{tooltip.valor}</span>
          </div>
        </div>
      )}
    </div>
  );
}
