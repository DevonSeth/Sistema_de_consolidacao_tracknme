"use client";

import { useMemo, useState } from "react";

import { labelEtapa } from "@/lib/etapa-labels";

export type LinhaLog = {
  etapa_id: string;
  iniciado_em: string;
  finalizado_em: string;
  duracao_ms: number;
  sucesso: boolean;
  motivo_parada: string | null;
  mensagem: string | null;
};

const LABEL_STATUS: Record<string, string> = {
  sucesso: "Sucesso",
  falha: "Falha",
  cancelada: "Cancelada",
  aguardando_reconexao: "Aguardando reconexão",
};

const CLASSE_PILL: Record<string, string> = {
  sucesso: "pill-ativo",
  falha: "pill-erro",
  cancelada: "pill-inativo",
  aguardando_reconexao: "pill-atencao",
};

function statusDaLinha(linha: LinhaLog): keyof typeof LABEL_STATUS {
  if (linha.motivo_parada === "aguardando_reconexao") return "aguardando_reconexao";
  if (linha.motivo_parada === "cancelada") return "cancelada";
  if (!linha.sucesso) return "falha";
  return "sucesso";
}

// Duração em ms -> texto curto ("28min 16s", "1.3h", "820ms") — só o que
// já vimos na prática (segundos a horas), sem precisar de lib nova.
function formatarDuracao(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const segundosTotais = Math.round(ms / 1000);
  if (segundosTotais < 60) return `${segundosTotais}s`;
  const minutosTotais = Math.floor(segundosTotais / 60);
  const segundos = segundosTotais % 60;
  if (minutosTotais < 60) return `${minutosTotais}min ${segundos}s`;
  const horas = Math.floor(minutosTotais / 60);
  const minutos = minutosTotais % 60;
  return `${horas}h ${minutos}min`;
}

function formatarDataHora(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "medium" });
}

export default function LogsClient({ linhas }: { linhas: LinhaLog[] }) {
  const [busca, setBusca] = useState("");
  const [statusAtivo, setStatusAtivo] = useState<"todos" | keyof typeof LABEL_STATUS>("todos");

  const filtradas = useMemo(() => {
    const termo = busca.trim().toLowerCase();
    return linhas.filter((linha) => {
      const status = statusDaLinha(linha);
      const bateStatus = statusAtivo === "todos" || statusAtivo === status;
      const bateBusca = !termo || (linha.mensagem ?? "").toLowerCase().includes(termo) || labelEtapa(linha.etapa_id).toLowerCase().includes(termo);
      return bateStatus && bateBusca;
    });
  }, [linhas, busca, statusAtivo]);

  return (
    <div className="painel-db">
      <div className="barra-filtro">
        <div className="campo-busca">
          <span className="ic-busca">🔎</span>
          <input
            placeholder="Buscar por etapa ou mensagem…"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
          />
        </div>
        <div className="chip-filtros">
          <button type="button" className={`chip ${statusAtivo === "todos" ? "on" : ""}`} onClick={() => setStatusAtivo("todos")}>
            Todos
          </button>
          {(Object.keys(LABEL_STATUS) as (keyof typeof LABEL_STATUS)[]).map((status) => (
            <button
              key={status}
              type="button"
              className={`chip ${statusAtivo === status ? "on" : ""}`}
              onClick={() => setStatusAtivo(status)}
            >
              {LABEL_STATUS[status]}
            </button>
          ))}
        </div>
        <div className="spacer" />
        <span className="contagem-filtrada">
          {filtradas.length} de {linhas.length}
        </span>
      </div>

      <div className="tabela-scroll">
        <table className="tabela-db">
          <thead>
            <tr>
              <th>Etapa</th>
              <th>Início</th>
              <th style={{ textAlign: "right" }}>Duração</th>
              <th>Status</th>
              <th>Mensagem</th>
            </tr>
          </thead>
          <tbody>
            {filtradas.map((linha, i) => {
              const status = statusDaLinha(linha);
              return (
                <tr key={`${linha.etapa_id}-${linha.iniciado_em}-${i}`}>
                  <td>{labelEtapa(linha.etapa_id)}</td>
                  <td>{formatarDataHora(linha.iniciado_em)}</td>
                  <td style={{ textAlign: "right" }}>{formatarDuracao(linha.duracao_ms)}</td>
                  <td>
                    <span className={`pill-status ${CLASSE_PILL[status]}`}>{LABEL_STATUS[status]}</span>
                  </td>
                  <td className="celula-motivo" title={linha.mensagem ?? ""}>
                    {linha.mensagem || "—"}
                  </td>
                </tr>
              );
            })}
            {filtradas.length === 0 && (
              <tr>
                <td colSpan={5} className="linha-vazia">
                  Nenhuma execução encontrada no período/filtro selecionado.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
