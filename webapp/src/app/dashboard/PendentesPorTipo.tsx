"use client";

import { useState } from "react";

import type { Metricas } from "@/lib/dashboard-metrics";

const ORIGENS = [
  { chave: "instalacao", label: "Instalação", cor: "var(--origem-instalacao)" },
  { chave: "remocao", label: "Remoção", cor: "var(--origem-remocao)" },
  { chave: "manutencao", label: "Manutenção", cor: "var(--origem-manutencao)" },
] as const;

/** Mesmo painel de "Pendentes por tipo" do Admin, só que sem o toggle de
 * visibilidade (não faz sentido aqui — quem está vendo já é o cliente). */
export default function PendentesPorTipo({ metricas }: { metricas: Metricas }) {
  const [origensAtivas, setOrigensAtivas] = useState<Record<string, boolean>>({
    instalacao: true,
    remocao: true,
    manutencao: true,
  });

  const valores = ORIGENS.map((o) => metricas.estadoPorOrigem[o.chave]?.pendente ?? 0);
  const maiorValor = Math.max(1, ...valores);

  return (
    <div className="painel-db">
      <h2>Pendentes por tipo</h2>
      <div className="desc">Instalação / Remoção / Manutenção</div>

      <div className="origem-check-row">
        {ORIGENS.map((o) => (
          <label key={o.chave}>
            <input
              type="checkbox"
              checked={origensAtivas[o.chave]}
              onChange={(e) => setOrigensAtivas((prev) => ({ ...prev, [o.chave]: e.target.checked }))}
            />
            {o.label}
          </label>
        ))}
      </div>

      {ORIGENS.filter((o) => origensAtivas[o.chave]).map((o) => {
        const valor = metricas.estadoPorOrigem[o.chave]?.pendente ?? 0;
        const largura = (valor / maiorValor) * 100;
        return (
          <div className="barra-item" key={o.chave}>
            <div className="nome">
              <span className="dot" style={{ background: o.cor }} />
              {o.label}
            </div>
            <div className="barra-trilho">
              <div className="barra-fill" style={{ width: `${largura}%`, background: o.cor }} />
            </div>
            <div className="num">{valor}</div>
          </div>
        );
      })}
    </div>
  );
}
