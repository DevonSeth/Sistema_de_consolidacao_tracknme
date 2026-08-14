"use client";

import { useActionState, useMemo, useState } from "react";

import {
  alternarAtivoPontoAcaoAction,
  atualizarPontoAcaoAction,
  criarPontoAcaoAction,
} from "./actions";

export type PontoAcao = {
  id: string;
  nome_local: string;
  endereco: string | null;
  data: string | null;
  ativo: boolean;
};

type Modo = "lista" | "novo" | { editando: PontoAcao };
type EstadoForm = { erro?: string };

const ESTADO_INICIAL: EstadoForm = {};

export default function TabelaPontosAcao({
  pontosIniciais,
}: {
  pontosIniciais: PontoAcao[];
}) {
  const [busca, setBusca] = useState("");
  const [filtroStatus, setFiltroStatus] = useState<"todas" | "ativo" | "inativo">(
    "todas"
  );
  const [modo, setModo] = useState<Modo>("lista");

  const [estadoCriar, actionCriar] = useActionState<EstadoForm, FormData>(
    async (_prev, formData) => {
      const resultado = await criarPontoAcaoAction(formData);
      if (!resultado.erro) setModo("lista");
      return resultado;
    },
    ESTADO_INICIAL
  );

  const [estadoEditar, actionEditar] = useActionState<EstadoForm, FormData>(
    async (_prev, formData) => {
      const resultado = await atualizarPontoAcaoAction(formData);
      if (!resultado.erro) setModo("lista");
      return resultado;
    },
    ESTADO_INICIAL
  );

  const filtradas = useMemo(() => {
    const termo = busca.trim().toLowerCase();
    return pontosIniciais.filter((ponto) => {
      const bateStatus =
        filtroStatus === "todas" || (filtroStatus === "ativo") === ponto.ativo;
      const bateBusca =
        !termo ||
        ponto.nome_local.toLowerCase().includes(termo) ||
        (ponto.endereco ?? "").toLowerCase().includes(termo);
      return bateStatus && bateBusca;
    });
  }, [pontosIniciais, busca, filtroStatus]);

  const editando = typeof modo === "object" ? modo.editando : null;

  return (
    <div>
      <div className="barra-filtro">
        <div className="campo-busca">
          <span className="ic-busca">🔎</span>
          <input
            placeholder="Buscar ponto de ação…"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
          />
        </div>
        <div className="chip-filtros">
          <button
            type="button"
            className={`chip ${filtroStatus === "todas" ? "on" : ""}`}
            onClick={() => setFiltroStatus("todas")}
          >
            Todas
          </button>
          <button
            type="button"
            className={`chip ${filtroStatus === "ativo" ? "on" : ""}`}
            onClick={() => setFiltroStatus("ativo")}
          >
            Ativos
          </button>
          <button
            type="button"
            className={`chip ${filtroStatus === "inativo" ? "on" : ""}`}
            onClick={() => setFiltroStatus("inativo")}
          >
            Inativos
          </button>
        </div>
        <div className="spacer" />
        <span className="contagem-filtrada">
          {filtradas.length} de {pontosIniciais.length}
        </span>
        <button
          type="button"
          className="btn primary small"
          onClick={() => setModo("novo")}
        >
          + Adicionar ponto de ação
        </button>
      </div>

      {modo === "novo" && (
        <form action={actionCriar} className="painel-form">
          <div>
            <label htmlFor="nome-local-novo">Nome / Local</label>
            <input id="nome-local-novo" name="nome_local" required />
          </div>
          <div>
            <label htmlFor="endereco-novo-pa">Endereço</label>
            <input id="endereco-novo-pa" name="endereco" />
          </div>
          <div>
            <label htmlFor="data-novo-pa">Data</label>
            <input id="data-novo-pa" name="data" type="date" />
          </div>
          {estadoCriar.erro && (
            <p style={{ color: "var(--status-erro-fg)", fontSize: 12.5, margin: 0 }}>
              {estadoCriar.erro}
            </p>
          )}
          <div className="acoes-form">
            <button type="submit" className="btn primary small">
              Salvar
            </button>
            <button type="button" className="btn small" onClick={() => setModo("lista")}>
              Cancelar
            </button>
          </div>
        </form>
      )}

      {editando && (
        <form action={actionEditar} className="painel-form">
          <input type="hidden" name="id" value={editando.id} />
          <div>
            <label htmlFor="nome-local-edit">Nome / Local</label>
            <input
              id="nome-local-edit"
              name="nome_local"
              defaultValue={editando.nome_local}
              required
            />
          </div>
          <div>
            <label htmlFor="endereco-edit-pa">Endereço</label>
            <input
              id="endereco-edit-pa"
              name="endereco"
              defaultValue={editando.endereco ?? ""}
            />
          </div>
          <div>
            <label htmlFor="data-edit-pa">Data</label>
            <input
              id="data-edit-pa"
              name="data"
              type="date"
              defaultValue={editando.data ?? ""}
            />
          </div>
          {estadoEditar.erro && (
            <p style={{ color: "var(--status-erro-fg)", fontSize: 12.5, margin: 0 }}>
              {estadoEditar.erro}
            </p>
          )}
          <div className="acoes-form">
            <button type="submit" className="btn primary small">
              Salvar
            </button>
            <button type="button" className="btn small" onClick={() => setModo("lista")}>
              Cancelar
            </button>
          </div>
        </form>
      )}

      <table className="tabela-cadastro">
        <thead>
          <tr>
            <th>Nome / Local</th>
            <th>Endereço</th>
            <th>Data</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {filtradas.map((ponto) => (
            <tr key={ponto.id}>
              <td>{ponto.nome_local}</td>
              <td className="celula-texto">{ponto.endereco}</td>
              <td className="mono">{formatarDataBr(ponto.data)}</td>
              <td>
                <span
                  className={`pill-status ${ponto.ativo ? "pill-ativo" : "pill-inativo"}`}
                >
                  {ponto.ativo ? "Ativo" : "Inativo"}
                </span>
              </td>
              <td className="acoes">
                <button
                  type="button"
                  className="link-acao"
                  onClick={() => setModo({ editando: ponto })}
                >
                  Editar
                </button>
                <form
                  action={async (formData) => {
                    await alternarAtivoPontoAcaoAction(formData);
                  }}
                  style={{ display: "inline" }}
                >
                  <input type="hidden" name="id" value={ponto.id} />
                  <input type="hidden" name="ativo" value={String(!ponto.ativo)} />
                  <button type="submit" className="link-acao">
                    {ponto.ativo ? "Desativar" : "Reativar"}
                  </button>
                </form>
              </td>
            </tr>
          ))}
          {filtradas.length === 0 && (
            <tr>
              <td colSpan={5} className="linha-vazia">
                Nenhum ponto de ação encontrado.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function formatarDataBr(dataIso: string | null): string {
  if (!dataIso) return "";
  const [ano, mes, dia] = dataIso.split("-");
  return `${dia}/${mes}/${ano}`;
}
