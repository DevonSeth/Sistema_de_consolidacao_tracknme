"use client";

import { useActionState, useMemo, useState } from "react";

import { salvarCredencialAction, testarConexaoAction } from "./actions";
import { CAMPOS_SECAO, CAMPOS_SECRETOS, CARDS, type CardMeta, type Secao } from "./meta";

export type ValoresIniciais = Record<Secao, Record<string, string>>;

type EstadoForm = { erro?: string };
const ESTADO_INICIAL: EstadoForm = {};

type ResultadoTeste = { ok: boolean; mensagem: string };
type StatusFiltro = "todas" | "ok" | "falha";

export default function ConfiguracaoClient({
  valoresIniciais,
}: {
  valoresIniciais: ValoresIniciais;
}) {
  const [resultados, setResultados] = useState<Partial<Record<Secao, ResultadoTeste>>>({});
  const [filtro, setFiltro] = useState<StatusFiltro>("todas");

  const cardsFiltrados = useMemo(() => {
    return CARDS.filter((card) => {
      if (filtro === "todas") return true;
      const resultado = resultados[card.secao];
      if (!resultado) return false;
      return filtro === "ok" ? resultado.ok : !resultado.ok;
    });
  }, [filtro, resultados]);

  return (
    <div>
      <div className="barra-filtro">
        <div className="chip-filtros">
          <button
            type="button"
            className={`chip ${filtro === "todas" ? "on" : ""}`}
            onClick={() => setFiltro("todas")}
          >
            Todas
          </button>
          <button
            type="button"
            className={`chip ${filtro === "ok" ? "on" : ""}`}
            onClick={() => setFiltro("ok")}
          >
            Conectadas
          </button>
          <button
            type="button"
            className={`chip ${filtro === "falha" ? "on" : ""}`}
            onClick={() => setFiltro("falha")}
          >
            Com erro
          </button>
        </div>
      </div>

      <div>
        {cardsFiltrados.map((card) => (
          <CardIntegracao
            key={card.secao}
            card={card}
            valoresIniciais={valoresIniciais[card.secao]}
            resultado={resultados[card.secao] ?? null}
            onResultado={(resultado) =>
              setResultados((prev) => ({ ...prev, [card.secao]: resultado }))
            }
          />
        ))}
        {cardsFiltrados.length === 0 && (
          <div className="linha-vazia">Nenhuma integração nesse filtro.</div>
        )}
      </div>
    </div>
  );
}

function CardIntegracao({
  card,
  valoresIniciais,
  resultado,
  onResultado,
}: {
  card: CardMeta;
  valoresIniciais: Record<string, string>;
  resultado: ResultadoTeste | null;
  onResultado: (resultado: ResultadoTeste) => void;
}) {
  const [editando, setEditando] = useState(false);
  const [testando, setTestando] = useState(false);

  const [estado, actionSalvar] = useActionState<EstadoForm, FormData>(
    async (_prev, formData) => {
      const resultado = await salvarCredencialAction(formData);
      if (!resultado.erro) setEditando(false);
      return resultado;
    },
    ESTADO_INICIAL
  );

  const campos = CAMPOS_SECAO[card.secao];
  const secretos = new Set(CAMPOS_SECRETOS[card.secao]);

  async function testar() {
    setTestando(true);
    try {
      const resultadoTeste = await testarConexaoAction(card.secao);
      onResultado(resultadoTeste);
    } finally {
      setTestando(false);
    }
  }

  return (
    <div className="card-integracao">
      <div className="head">
        <div className="titulo">
          {card.emoji} {card.titulo}
        </div>
        {resultado ? (
          <span className={`resultado-teste ${resultado.ok ? "ok" : "falha"}`}>
            {resultado.ok ? "✓" : "✕"} {resultado.mensagem}
          </span>
        ) : (
          <span
            className="resultado-teste"
            style={{ background: "var(--status-ocioso-bg)", color: "var(--status-ocioso-fg)" }}
          >
            Não testado nesta sessão
          </span>
        )}
      </div>
      <div className="desc-int">{card.descricao}</div>
      {card.notaFixa && (
        <div className="desc-int" style={{ color: "var(--text-faint)" }}>
          {card.notaFixa}
        </div>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" className="btn small" onClick={() => setEditando((v) => !v)}>
          {editando ? "Cancelar" : "Editar"}
        </button>
        {card.testavel && (
          <button
            type="button"
            className="btn small primary"
            onClick={testar}
            disabled={testando}
          >
            {testando ? "Testando…" : "Testar conexão"}
          </button>
        )}
      </div>

      {editando && (
        <form action={actionSalvar} className="painel-form" style={{ marginTop: 12 }}>
          <input type="hidden" name="secao" value={card.secao} />
          {campos.map((campo) => (
            <div key={campo.nome}>
              <label htmlFor={`${card.secao}-${campo.nome}`}>{campo.label}</label>
              <input
                id={`${card.secao}-${campo.nome}`}
                name={campo.nome}
                type={
                  campo.tipo === "senha" ? "password" : campo.tipo === "numero" ? "number" : "text"
                }
                defaultValue={secretos.has(campo.nome) ? "" : valoresIniciais[campo.nome] ?? ""}
                placeholder={secretos.has(campo.nome) ? "deixe em branco pra manter" : undefined}
              />
            </div>
          ))}
          {estado.erro && (
            <p style={{ color: "var(--status-erro-fg)", fontSize: 12.5, margin: 0 }}>
              {estado.erro}
            </p>
          )}
          <div className="acoes-form">
            <button type="submit" className="btn primary small">
              Salvar
            </button>
            <button type="button" className="btn small" onClick={() => setEditando(false)}>
              Cancelar
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
