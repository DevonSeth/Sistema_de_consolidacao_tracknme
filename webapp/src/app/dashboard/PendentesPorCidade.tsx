"use client";

import { useState, type ReactNode } from "react";

export default function PendentesPorCidade({
  dados,
  rodape,
}: {
  dados: { cidade: string; quantidade: number }[];
  rodape?: ReactNode;
}) {
  const [busca, setBusca] = useState("");
  const filtrados = dados.filter((d) => d.cidade.toLowerCase().includes(busca.toLowerCase()));
  const total = dados.reduce((soma, d) => soma + d.quantidade, 0);

  return (
    <div className="painel-db">
      <h2>Serviços pendentes por cidade</h2>
      <div className="desc">
        {dados.length} cidades · {total} pendências agora — ordem alfabética
      </div>

      <input
        type="text"
        placeholder="Buscar cidade..."
        value={busca}
        onChange={(e) => setBusca(e.target.value)}
        className="busca-tabela"
      />

      <div className="tabela-scroll">
        <table className="tabela-db">
          <thead>
            <tr>
              <th>Cidade</th>
              <th style={{ textAlign: "right" }}>Pendentes</th>
            </tr>
          </thead>
          <tbody>
            {filtrados.map((d) => (
              <tr key={d.cidade}>
                <td>{d.cidade}</td>
                <td style={{ textAlign: "right" }}>{d.quantidade}</td>
              </tr>
            ))}
            {filtrados.length === 0 && (
              <tr>
                <td colSpan={2} className="linha-vazia">
                  Nenhuma cidade encontrada.
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
