"use client";

import { useState, type ReactNode } from "react";

import type { Metricas } from "@/lib/dashboard-metrics";
import { formatarTick, tetoAgradavel } from "@/lib/dashboard-metricas-meta";

const ORIGENS = [
  { chave: "instalacao", label: "Instalação", cor: "var(--origem-instalacao)" },
  { chave: "remocao", label: "Remoção", cor: "var(--origem-remocao)" },
  { chave: "manutencao", label: "Manutenção", cor: "var(--origem-manutencao)" },
] as const;

const BUCKETS = [
  { chave: "pendente", label: "Pendente" },
  { chave: "emAndamento", label: "Em andamento" },
  { chave: "concluido", label: "Concluído" },
] as const;

type Tooltip = { x: number; y: number; origem: string; bucket: string; valor: number; cor: string };

export default function EstadoPorOrigem({
  metricas,
  rodape,
}: {
  metricas: Metricas;
  /** Conteúdo extra ao final do card (ex: toggle de visibilidade no Painel Admin). */
  rodape?: ReactNode;
}) {
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);

  const maiorValor = Math.max(
    1,
    ...ORIGENS.flatMap((o) => BUCKETS.map((b) => metricas.estadoPorOrigem[o.chave]?.[b.chave] ?? 0))
  );
  const teto = tetoAgradavel(maiorValor);
  const ticks = [0, teto / 2, teto];
  // Headroom acima do teto — sem isso, o tick/gridline do valor máximo cai
  // bem na borda superior do container e colide com a legenda logo acima.
  const escalaMax = teto * 1.15;

  return (
    <div className="painel-db">
      <h2>Estado por origem</h2>
      <div className="desc">Pendente / Em andamento / Concluído, por origem, no período filtrado</div>

      <div className="legenda-grafico">
        {ORIGENS.map((o) => (
          <div className="item" key={o.chave}>
            <span className="dot" style={{ background: o.cor }} />
            {o.label}
          </div>
        ))}
      </div>

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

        {BUCKETS.map((bucket) => (
          <div className="grupo-coluna" key={bucket.chave}>
            <div className="colunas-do-grupo">
              {ORIGENS.map((o) => {
                const valor = metricas.estadoPorOrigem[o.chave]?.[bucket.chave] ?? 0;
                const altura = (valor / escalaMax) * 100;
                return (
                  <div
                    key={o.chave}
                    className="coluna-wrap"
                    tabIndex={0}
                    onMouseMove={(e) =>
                      setTooltip({ x: e.clientX, y: e.clientY, origem: o.label, bucket: bucket.label, valor, cor: o.cor })
                    }
                    onMouseLeave={() => setTooltip(null)}
                    onFocus={(e) => {
                      const r = e.currentTarget.getBoundingClientRect();
                      setTooltip({ x: r.left + r.width / 2, y: r.top, origem: o.label, bucket: bucket.label, valor, cor: o.cor });
                    }}
                    onBlur={() => setTooltip(null)}
                  >
                    <span className="valor">{valor}</span>
                    <div className="coluna" style={{ height: `${altura}%`, background: o.cor }} />
                  </div>
                );
              })}
            </div>
            <div className="grupo-label">{bucket.label}</div>
          </div>
        ))}
      </div>

      {rodape}

      {tooltip && (
        <div className="viz-tooltip" style={{ left: tooltip.x + 12, top: tooltip.y - 46 }}>
          <div className="titulo">{tooltip.bucket}</div>
          <div className="linha">
            <span className="rotulo">
              <span className="dot" style={{ background: tooltip.cor }} />
              {tooltip.origem}
            </span>
            <span className="valor">{tooltip.valor}</span>
          </div>
        </div>
      )}
    </div>
  );
}
