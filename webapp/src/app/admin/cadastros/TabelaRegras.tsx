"use client";

import { useActionState, useMemo, useState } from "react";

import { alternarAtivoRegraAction, atualizarRegraAction } from "./actions";
import { CATEGORIAS, categoriaDe, labelCategoria, type Categoria } from "./regrasMeta";

export type Regra = {
  id: string;
  codigo_regra: string;
  ativo: boolean;
  prioridade: number;
  template_acao: string | null;
  template_observacao: string | null;
  nivel_urgencia: number | null;
};

type EstadoForm = { erro?: string };
const ESTADO_INICIAL: EstadoForm = {};

const NIVEIS = [1, 2, 3, 4, 5];

export default function TabelaRegras({ regrasIniciais }: { regrasIniciais: Regra[] }) {
  const [busca, setBusca] = useState("");
  const [categoriaAtiva, setCategoriaAtiva] = useState<Categoria | "todas">("todas");
  const [nivelAtivo, setNivelAtivo] = useState<string>("todos");
  const [editandoId, setEditandoId] = useState<string | null>(null);

  const filtradas = useMemo(() => {
    const termo = busca.trim().toLowerCase();
    return regrasIniciais.filter((regra) => {
      const bateCategoria =
        categoriaAtiva === "todas" || categoriaDe(regra.codigo_regra) === categoriaAtiva;
      const bateNivel =
        nivelAtivo === "todos" || String(regra.nivel_urgencia ?? "") === nivelAtivo;
      const bateBusca =
        !termo ||
        regra.codigo_regra.toLowerCase().includes(termo) ||
        (regra.template_acao ?? "").toLowerCase().includes(termo) ||
        (regra.template_observacao ?? "").toLowerCase().includes(termo);
      return bateCategoria && bateNivel && bateBusca;
    });
  }, [regrasIniciais, busca, categoriaAtiva, nivelAtivo]);

  return (
    <div>
      <div className="barra-filtro">
        <div className="campo-busca">
          <span className="ic-busca">🔎</span>
          <input
            placeholder="Buscar código ou texto…"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
          />
        </div>
        <select
          className="select-filtro"
          value={categoriaAtiva}
          onChange={(e) => setCategoriaAtiva(e.target.value as Categoria | "todas")}
        >
          <option value="todas">Todas as categorias</option>
          {CATEGORIAS.map((cat) => (
            <option key={cat.chave} value={cat.chave}>
              {cat.label}
            </option>
          ))}
        </select>
        <select
          className="select-filtro"
          value={nivelAtivo}
          onChange={(e) => setNivelAtivo(e.target.value)}
        >
          <option value="todos">Todos os níveis</option>
          {NIVEIS.map((n) => (
            <option key={n} value={String(n)}>
              Nível {n}
            </option>
          ))}
        </select>
        <div className="spacer" />
        <span className="contagem-filtrada">
          {filtradas.length} de {regrasIniciais.length}
        </span>
      </div>

      <table className="tabela-cadastro">
        <thead>
          <tr>
            <th>Código</th>
            <th>Categoria</th>
            <th>Nível</th>
            <th>Ação sugerida</th>
            <th>Ativo</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {filtradas.map((regra) => (
            <LinhaRegra
              key={regra.id}
              regra={regra}
              editando={editandoId === regra.id}
              onEditar={() => setEditandoId(regra.id)}
              onFechar={() => setEditandoId(null)}
            />
          ))}
          {filtradas.length === 0 && (
            <tr>
              <td colSpan={6} className="linha-vazia">
                Nenhuma regra encontrada.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <div className="desc" style={{ marginTop: 8 }}>
        Desativar uma regra não impede a classificação de acontecer — só deixa a
        observação/ação vazia no resultado. &ldquo;Prioridade&rdquo; é só metadado descritivo —
        não decide a ordem real da cascata, que é fixa no código.
      </div>
    </div>
  );
}

function LinhaRegra({
  regra,
  editando,
  onEditar,
  onFechar,
}: {
  regra: Regra;
  editando: boolean;
  onEditar: () => void;
  onFechar: () => void;
}) {
  const categoria = categoriaDe(regra.codigo_regra);

  const [estadoToggle, actionToggle] = useActionState<EstadoForm, FormData>(
    async (_prev, formData) => alternarAtivoRegraAction(formData),
    ESTADO_INICIAL
  );

  const [estadoEditar, actionEditar] = useActionState<EstadoForm, FormData>(
    async (_prev, formData) => {
      const resultado = await atualizarRegraAction(formData);
      if (!resultado.erro) onFechar();
      return resultado;
    },
    ESTADO_INICIAL
  );

  return (
    <>
      <tr>
        <td className="mono">{regra.codigo_regra}</td>
        <td>{labelCategoria(categoria)}</td>
        <td>
          {regra.nivel_urgencia === null ? (
            <span style={{ color: "var(--text-faint)" }}>—</span>
          ) : (
            <span
              className="pill-nivel"
              style={{ background: `var(--nivel-${regra.nivel_urgencia})` }}
            >
              {regra.nivel_urgencia}
            </span>
          )}
        </td>
        <td className="celula-texto">{regra.template_acao || "—"}</td>
        <td>
          <label className="toggle-switch">
            <input
              key={String(regra.ativo)}
              type="checkbox"
              defaultChecked={regra.ativo}
              onChange={(e) => {
                const dados = new FormData();
                dados.set("id", regra.id);
                dados.set("ativo", e.target.checked ? "true" : "false");
                actionToggle(dados);
              }}
            />
            <span className="track"></span>
            <span className="knob"></span>
          </label>
          {estadoToggle.erro && (
            <div className="desc" style={{ color: "var(--status-erro-fg)" }}>
              {estadoToggle.erro}
            </div>
          )}
        </td>
        <td className="acoes">
          <button type="button" className="link-acao" onClick={editando ? onFechar : onEditar}>
            {editando ? "Fechar" : "Editar"}
          </button>
        </td>
      </tr>
      {editando && (
        <tr>
          <td colSpan={6}>
            <form action={actionEditar} className="painel-form" style={{ maxWidth: 520 }}>
              <input type="hidden" name="id" value={regra.id} />
              <div>
                <label htmlFor={`obs-${regra.id}`}>Observação</label>
                <textarea
                  id={`obs-${regra.id}`}
                  name="template_observacao"
                  defaultValue={regra.template_observacao ?? ""}
                  rows={2}
                />
              </div>
              <div>
                <label htmlFor={`acao-${regra.id}`}>Ação sugerida</label>
                <textarea
                  id={`acao-${regra.id}`}
                  name="template_acao"
                  defaultValue={regra.template_acao ?? ""}
                  rows={2}
                />
              </div>
              <div>
                <label htmlFor={`nivel-${regra.id}`}>Nível de urgência</label>
                {regra.nivel_urgencia === null ? (
                  <>
                    <select id={`nivel-${regra.id}`} disabled defaultValue="">
                      <option value="">—</option>
                    </select>
                    <div className="desc">
                      Não aplicável — dedup silencioso, nunca vira linha visível.
                    </div>
                  </>
                ) : (
                  <select
                    id={`nivel-${regra.id}`}
                    name="nivel_urgencia"
                    defaultValue={String(regra.nivel_urgencia)}
                  >
                    {NIVEIS.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <div>
                <label htmlFor={`prioridade-${regra.id}`}>Prioridade (só leitura)</label>
                <input id={`prioridade-${regra.id}`} value={regra.prioridade} disabled />
                <div className="desc">
                  Metadado descritivo — não decide a ordem real da cascata (fixa no código).
                </div>
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
                <button type="button" className="btn small" onClick={onFechar}>
                  Cancelar
                </button>
              </div>
            </form>
          </td>
        </tr>
      )}
    </>
  );
}
