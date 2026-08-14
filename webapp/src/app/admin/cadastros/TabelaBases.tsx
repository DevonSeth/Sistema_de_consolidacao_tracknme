"use client";

import { useActionState, useMemo, useState } from "react";

import {
  alternarAtivoBaseAction,
  atualizarBaseAction,
  criarBaseAction,
} from "./actions";

export type Base = {
  id: string;
  nome: string;
  endereco: string | null;
  ativo: boolean;
};

type Modo = "lista" | "novo" | { editando: Base };
type EstadoForm = { erro?: string };

const ESTADO_INICIAL: EstadoForm = {};

export default function TabelaBases({ basesIniciais }: { basesIniciais: Base[] }) {
  const [busca, setBusca] = useState("");
  const [filtroStatus, setFiltroStatus] = useState<"todas" | "ativo" | "inativo">(
    "todas"
  );
  const [modo, setModo] = useState<Modo>("lista");

  const [estadoCriar, actionCriar] = useActionState<EstadoForm, FormData>(
    async (_prev, formData) => {
      const resultado = await criarBaseAction(formData);
      if (!resultado.erro) setModo("lista");
      return resultado;
    },
    ESTADO_INICIAL
  );

  const [estadoEditar, actionEditar] = useActionState<EstadoForm, FormData>(
    async (_prev, formData) => {
      const resultado = await atualizarBaseAction(formData);
      if (!resultado.erro) setModo("lista");
      return resultado;
    },
    ESTADO_INICIAL
  );

  const filtradas = useMemo(() => {
    const termo = busca.trim().toLowerCase();
    return basesIniciais.filter((base) => {
      const bateStatus =
        filtroStatus === "todas" || (filtroStatus === "ativo") === base.ativo;
      const bateBusca =
        !termo ||
        base.nome.toLowerCase().includes(termo) ||
        (base.endereco ?? "").toLowerCase().includes(termo);
      return bateStatus && bateBusca;
    });
  }, [basesIniciais, busca, filtroStatus]);

  const editando = typeof modo === "object" ? modo.editando : null;

  return (
    <div>
      <div className="barra-filtro">
        <div className="campo-busca">
          <span className="ic-busca">🔎</span>
          <input
            placeholder="Buscar base…"
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
            Ativas
          </button>
          <button
            type="button"
            className={`chip ${filtroStatus === "inativo" ? "on" : ""}`}
            onClick={() => setFiltroStatus("inativo")}
          >
            Inativas
          </button>
        </div>
        <div className="spacer" />
        <span className="contagem-filtrada">
          {filtradas.length} de {basesIniciais.length}
        </span>
        <button
          type="button"
          className="btn primary small"
          onClick={() => setModo("novo")}
        >
          + Adicionar base
        </button>
      </div>

      {modo === "novo" && (
        <form action={actionCriar} className="painel-form">
          <div>
            <label htmlFor="nome-nova">Nome</label>
            <input id="nome-nova" name="nome" required />
          </div>
          <div>
            <label htmlFor="endereco-novo">Endereço</label>
            <input id="endereco-novo" name="endereco" />
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
            <label htmlFor="nome-edit">Nome</label>
            <input id="nome-edit" name="nome" defaultValue={editando.nome} required />
          </div>
          <div>
            <label htmlFor="endereco-edit">Endereço</label>
            <input
              id="endereco-edit"
              name="endereco"
              defaultValue={editando.endereco ?? ""}
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
            <th>Nome</th>
            <th>Endereço</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {filtradas.map((base) => (
            <tr key={base.id}>
              <td>{base.nome}</td>
              <td className="celula-texto">{base.endereco}</td>
              <td>
                <span
                  className={`pill-status ${base.ativo ? "pill-ativo" : "pill-inativo"}`}
                >
                  {base.ativo ? "Ativa" : "Inativa"}
                </span>
              </td>
              <td className="acoes">
                <button
                  type="button"
                  className="link-acao"
                  onClick={() => setModo({ editando: base })}
                >
                  Editar
                </button>
                <form
                  action={async (formData) => {
                    await alternarAtivoBaseAction(formData);
                  }}
                  style={{ display: "inline" }}
                >
                  <input type="hidden" name="id" value={base.id} />
                  <input type="hidden" name="ativo" value={String(!base.ativo)} />
                  <button type="submit" className="link-acao">
                    {base.ativo ? "Desativar" : "Reativar"}
                  </button>
                </form>
              </td>
            </tr>
          ))}
          {filtradas.length === 0 && (
            <tr>
              <td colSpan={4} className="linha-vazia">
                Nenhuma base encontrada.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
