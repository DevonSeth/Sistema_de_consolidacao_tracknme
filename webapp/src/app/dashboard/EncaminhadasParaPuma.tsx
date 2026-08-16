"use client";

import { useState, type ReactNode } from "react";

import type { EncaminhadaParaPuma } from "@/lib/dashboard-metrics";

import InfoTooltip from "./InfoTooltip";

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

export default function EncaminhadasParaPuma({
  dados,
  descricao,
  rodape,
}: {
  dados: EncaminhadaParaPuma[];
  descricao?: string;
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
      <h2>
        Encaminhadas pra Puma
        {descricao && <InfoTooltip texto={descricao} label="Encaminhadas pra Puma" />}
      </h2>
      <div className="desc">
        {dados.length} aguardando ação da Puma agora — dias úteis desde o encaminhamento (aproximado), não respeita o filtro de período
      </div>

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
              <th>Motivo</th>
              <th style={{ textAlign: "right" }}>Dias no estado</th>
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
                <td className="celula-motivo" title={d.motivo}>
                  {d.motivo}
                </td>
                <td style={{ textAlign: "right" }}>{d.diasNoEstado}</td>
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
                <td colSpan={7} className="linha-vazia">
                  Nenhuma pendência encaminhada pra Puma no momento.
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
