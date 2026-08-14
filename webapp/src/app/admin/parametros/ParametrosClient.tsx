"use client";

import { useActionState, useMemo, useState } from "react";

import { atualizarParametroAction } from "./actions";
import { CATEGORIAS, type Categoria, type MetaParametro } from "./meta";

export type ParametroLinha = {
  chave: string;
  valor: string;
  descricao: string | null;
  meta: MetaParametro;
};

type EstadoForm = { erro?: string };
const ESTADO_INICIAL: EstadoForm = {};

export default function ParametrosClient({
  parametros,
}: {
  parametros: ParametroLinha[];
}) {
  const [busca, setBusca] = useState("");
  const [categoriaAtiva, setCategoriaAtiva] = useState<Categoria | "todas">("todas");

  const filtrados = useMemo(() => {
    const termo = busca.trim().toLowerCase();
    return parametros.filter((p) => {
      const bateCategoria = categoriaAtiva === "todas" || p.meta.categoria === categoriaAtiva;
      const bateBusca =
        !termo ||
        p.meta.label.toLowerCase().includes(termo) ||
        (p.descricao ?? "").toLowerCase().includes(termo) ||
        p.chave.toLowerCase().includes(termo);
      return bateCategoria && bateBusca;
    });
  }, [parametros, busca, categoriaAtiva]);

  const grupos = CATEGORIAS.map((cat) => ({
    ...cat,
    itens: filtrados.filter((p) => p.meta.categoria === cat.chave),
  })).filter((grupo) => grupo.itens.length > 0);

  return (
    <div>
      <div className="barra-filtro">
        <div className="campo-busca">
          <span className="ic-busca">🔎</span>
          <input
            placeholder="Buscar parâmetro…"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
          />
        </div>
        <div className="chip-filtros">
          <button
            type="button"
            className={`chip ${categoriaAtiva === "todas" ? "on" : ""}`}
            onClick={() => setCategoriaAtiva("todas")}
          >
            Todas
          </button>
          {CATEGORIAS.map((cat) => (
            <button
              key={cat.chave}
              type="button"
              className={`chip ${categoriaAtiva === cat.chave ? "on" : ""}`}
              onClick={() => setCategoriaAtiva(cat.chave)}
            >
              {cat.label}
            </button>
          ))}
        </div>
        <div className="spacer" />
        <span className="contagem-filtrada">
          {filtrados.length} de {parametros.length}
        </span>
      </div>

      {grupos.map((grupo) => (
        <div className="grupo-parametros" key={grupo.chave}>
          <div className="grupo-titulo">{grupo.label}</div>
          {grupo.itens.map((p) => (
            <CardParametro key={p.chave} parametro={p} />
          ))}
        </div>
      ))}

      {filtrados.length === 0 && (
        <div className="linha-vazia">Nenhum parâmetro encontrado.</div>
      )}
    </div>
  );
}

function CardParametro({ parametro }: { parametro: ParametroLinha }) {
  const { chave, valor, descricao, meta } = parametro;

  const [estado, formAction] = useActionState<EstadoForm, FormData>(
    async (_prev, formData) => atualizarParametroAction(formData),
    ESTADO_INICIAL
  );

  return (
    <div className="card-parametro">
      <div className="info">
        <div className="nome">{meta.label}</div>
        {descricao && <div className="desc">{descricao}</div>}
        {meta.aviso && (
          <div className="desc" style={{ color: "var(--status-erro-fg)" }}>
            ⚠️ {meta.aviso}
          </div>
        )}
        {estado.erro && (
          <div className="desc" style={{ color: "var(--status-erro-fg)" }}>
            {estado.erro}
          </div>
        )}
        {meta.tipo === "tier" && (
          <div className="desc">Formato: dias=NOME,dias=NOME,... (ex: 31=CRITICO,1=NORMAL)</div>
        )}
      </div>

      {meta.tipo === "bool" ? (
        <div className="valor-edit">
          <label className="toggle-switch">
            <input
              key={valor}
              type="checkbox"
              defaultChecked={valor === "true"}
              onChange={(e) => {
                const dados = new FormData();
                dados.set("chave", chave);
                dados.set("valor", e.target.checked ? "true" : "false");
                formAction(dados);
              }}
            />
            <span className="track"></span>
            <span className="knob"></span>
          </label>
        </div>
      ) : (
        <form action={formAction} className="valor-edit">
          <input type="hidden" name="chave" value={chave} />
          <input
            key={valor}
            type={meta.tipo === "numero" ? "number" : "text"}
            name="valor"
            defaultValue={valor}
          />
          {meta.sufixo && (
            <span style={{ color: "var(--text-muted)", fontSize: 12 }}>{meta.sufixo}</span>
          )}
          <button type="submit" className="btn small">
            Salvar
          </button>
        </form>
      )}
    </div>
  );
}
