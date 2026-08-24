"use client";

import { useState, type ReactNode } from "react";

import InfoTooltip from "./InfoTooltip";

export default function PendentesPorCidade({
  dados,
  descricao,
  rodape,
}: {
  dados: { cidade: string; quantidade: number }[];
  descricao?: string;
  rodape?: ReactNode;
}) {
  const [busca, setBusca] = useState("");
  const [ordem, setOrdem] = useState<"az" | "quantidade">("az");
  const filtrados = dados
    .filter((d) => d.cidade.toLowerCase().includes(busca.toLowerCase()))
    .sort((a, b) => (ordem === "az" ? a.cidade.localeCompare(b.cidade, "pt-BR") : b.quantidade - a.quantidade));
  const total = dados.reduce((soma, d) => soma + d.quantidade, 0);

  return (
    <div className="painel-db">
      <h2>
        Serviços pendentes por cidade
        {descricao && <InfoTooltip texto={descricao} label="Serviços pendentes por cidade" />}
      </h2>
      <div className="desc">
        {dados.length} cidades · {total} pendências agora — {ordem === "az" ? "ordem alfabética" : "maior para menor"}
      </div>

      <div className="no-print" style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <button
          type="button"
          className={ordem === "az" ? "btn primary small" : "btn small"}
          onClick={() => setOrdem("az")}
        >
          A-Z
        </button>
        <button
          type="button"
          className={ordem === "quantidade" ? "btn primary small" : "btn small"}
          onClick={() => setOrdem("quantidade")}
        >
          Maior → menor
        </button>
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
