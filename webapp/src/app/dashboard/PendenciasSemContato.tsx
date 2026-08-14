"use client";

import { useState, type ReactNode } from "react";

import type { PendenciaSemContato } from "@/lib/dashboard-metrics";

const ORIGENS = [
  { chave: "instalacao", label: "Instalação", cor: "var(--origem-instalacao)" },
  { chave: "remocao", label: "Remoção", cor: "var(--origem-remocao)" },
  { chave: "manutencao", label: "Manutenção", cor: "var(--origem-manutencao)" },
] as const;

const NIVEL_COR: Record<number, string> = {
  1: "var(--nivel-1)",
  2: "var(--nivel-2)",
  3: "var(--nivel-3)",
  4: "var(--nivel-4)",
  5: "var(--nivel-5)",
};

export default function PendenciasSemContato({
  dados,
  rodape,
}: {
  dados: PendenciaSemContato[];
  rodape?: ReactNode;
}) {
  const [busca, setBusca] = useState("");
  const [origensAtivas, setOrigensAtivas] = useState<Record<string, boolean>>({
    instalacao: true,
    remocao: true,
    manutencao: true,
  });

  const filtrados = dados.filter(
    (d) =>
      (origensAtivas[d.origem] ?? true) &&
      (d.cliente.toLowerCase().includes(busca.toLowerCase()) || d.identificador.toLowerCase().includes(busca.toLowerCase()))
  );

  return (
    <div className="painel-db">
      <h2>Pendências com mais tempo sem contato</h2>
      <div className="desc">As {dados.length} mais paradas agora (dias úteis, aproximado) — não respeita o filtro de período</div>

      <div className="origem-check-row" style={{ marginBottom: 8 }}>
        {ORIGENS.map((o) => (
          <label key={o.chave}>
            <input
              type="checkbox"
              checked={origensAtivas[o.chave] ?? true}
              onChange={(e) => setOrigensAtivas((prev) => ({ ...prev, [o.chave]: e.target.checked }))}
            />
            {o.label}
          </label>
        ))}
      </div>

      <input
        type="text"
        placeholder="Buscar por cliente ou identificador..."
        value={busca}
        onChange={(e) => setBusca(e.target.value)}
        className="busca-tabela"
      />

      <div className="tabela-scroll">
        <table className="tabela-db">
          <thead>
            <tr>
              <th>Cliente</th>
              <th>Identificador</th>
              <th>Origem</th>
              <th>Cidade</th>
              <th style={{ textAlign: "right" }}>Dias sem contato</th>
              <th style={{ textAlign: "right" }}>Nível</th>
            </tr>
          </thead>
          <tbody>
            {filtrados.map((d, i) => (
              <tr key={`${d.identificador}-${i}`}>
                <td>{d.cliente}</td>
                <td>{d.identificador}</td>
                <td>{ORIGENS.find((o) => o.chave === d.origem)?.label ?? d.origem}</td>
                <td>{d.cidade}</td>
                <td style={{ textAlign: "right" }}>{d.diasSemContato}</td>
                <td style={{ textAlign: "right" }}>
                  {d.nivelUrgencia != null ? (
                    <span className="dot" style={{ background: NIVEL_COR[d.nivelUrgencia], display: "inline-block" }} />
                  ) : (
                    "—"
                  )}{" "}
                  {d.nivelUrgencia ?? ""}
                </td>
              </tr>
            ))}
            {filtrados.length === 0 && (
              <tr>
                <td colSpan={6} className="linha-vazia">
                  Nenhuma pendência encontrada.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {rodape}
    </div>
  );
}
