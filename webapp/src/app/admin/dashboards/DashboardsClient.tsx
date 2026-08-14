"use client";

import { useActionState, useState, type ReactNode } from "react";

import type {
  DistribuicaoUrgencia as DistribuicaoUrgenciaValores,
  Metricas,
  PendenciaSemContato,
  PontoEvolucao,
  PontoSerieDiaria,
} from "@/lib/dashboard-metrics";
import { METRICAS, SECOES, valorMetrica } from "@/lib/dashboard-metricas-meta";
import DistribuicaoUrgencia from "@/app/dashboard/DistribuicaoUrgencia";
import EstadoPorOrigem from "@/app/dashboard/EstadoPorOrigem";
import EvolucaoBacklog from "@/app/dashboard/EvolucaoBacklog";
import PendenciasSemContato from "@/app/dashboard/PendenciasSemContato";
import PendentesPorCidade from "@/app/dashboard/PendentesPorCidade";
import TendenciaDiaria from "@/app/dashboard/TendenciaDiaria";

import { alternarVisibilidadeMetricaAction, alternarVisibilidadeOperadorAction } from "./actions";

const CHAVES_PAINEL_CUSTOM = new Set([
  "pendentes_por_tipo",
  "estado_por_origem",
  "tendencia_diaria",
  "distribuicao_urgencia",
  "pendencias_sem_contato",
  "pendentes_por_cidade",
  "evolucao_backlog",
]);

// Candidatas ao toggle "Visível no Painel Operador" — o Operador já tem
// motor de gráfico (Fase 5, `ui/web/charts.js` + `orchestrator.
// metricas_admin_operador`) para as 5 chaves de gráfico abaixo, além das
// 3 originais. `pendencias_sem_contato` fica de fora de propósito — já é
// essencialmente coberta pela "Fila de prioridade" nativa do Operador.
const CANDIDATAS_OPERADOR = new Set([
  "pendencias_em_aberto", "encaminhadas_puma", "pendentes_por_cidade",
  "tendencia_diaria", "estado_por_origem", "distribuicao_urgencia",
  "evolucao_backlog", "pendentes_por_tipo",
  // Métricas "de período" (2026-08-14) — Painel Operador ganhou filtro
  // De/Até próprio pra elas (ver orchestrator/metricas_admin_operador.py).
  "disparos", "retornados", "agendamentos_confirmados", "concluidos",
  "pct_resposta", "tempo_medio_resolucao", "taxa_escalonamento_puma",
  "pendentes", "em_andamento", "pct_pendencias", "pct_pendencias_concluidas",
]);

type EstadoForm = { erro?: string };
const ESTADO_INICIAL: EstadoForm = {};

const ORIGENS = [
  { chave: "instalacao", label: "Instalação", cor: "var(--origem-instalacao)" },
  { chave: "remocao", label: "Remoção", cor: "var(--origem-remocao)" },
  { chave: "manutencao", label: "Manutenção", cor: "var(--origem-manutencao)" },
] as const;

export default function DashboardsClient({
  metricas,
  visibilidade,
  visibilidadeOperador,
  serieDiaria,
  distribuicaoUrgencia,
  pendenciasSemContato,
  pendentesPorCidade,
  evolucaoBacklog,
  desde,
  ate,
}: {
  metricas: Metricas;
  visibilidade: Record<string, boolean>;
  visibilidadeOperador: Record<string, boolean>;
  serieDiaria: PontoSerieDiaria[];
  distribuicaoUrgencia: DistribuicaoUrgenciaValores;
  pendenciasSemContato: PendenciaSemContato[];
  pendentesPorCidade: { cidade: string; quantidade: number }[];
  evolucaoBacklog: PontoEvolucao[];
  desde: string;
  ate: string;
}) {
  const [origensAtivas, setOrigensAtivas] = useState<Record<string, boolean>>({
    instalacao: true,
    remocao: true,
    manutencao: true,
  });

  const valoresPendentePorOrigem = ORIGENS.map((o) => metricas.estadoPorOrigem[o.chave]?.pendente ?? 0);
  const maiorValor = Math.max(1, ...valoresPendentePorOrigem);

  const painelPorChave: Record<string, ReactNode> = {
    tendencia_diaria: (
      <div className="full" key="tendencia_diaria">
        <TendenciaDiaria
          serie={serieDiaria}
          rodape={
            <>
              <TogglePainelCliente chave="tendencia_diaria" visivelInicial={visibilidade["tendencia_diaria"] ?? false} />
              <TogglePainelOperador
                chave="tendencia_diaria"
                visivelInicial={visibilidadeOperador["tendencia_diaria"] ?? false}
              />
            </>
          }
        />
      </div>
    ),
    estado_por_origem: (
      <EstadoPorOrigem
        key="estado_por_origem"
        metricas={metricas}
        rodape={
          <>
            <TogglePainelCliente chave="estado_por_origem" visivelInicial={visibilidade["estado_por_origem"] ?? false} />
            <TogglePainelOperador
              chave="estado_por_origem"
              visivelInicial={visibilidadeOperador["estado_por_origem"] ?? false}
            />
          </>
        }
      />
    ),
    pendentes_por_tipo: (
      <div className="painel-db" key="pendentes_por_tipo">
        <h2>Pendentes por tipo</h2>
        <div className="desc">Instalação / Remoção / Manutenção</div>

        <div className="origem-check-row">
          {ORIGENS.map((o) => (
            <label key={o.chave}>
              <input
                type="checkbox"
                checked={origensAtivas[o.chave]}
                onChange={(e) => setOrigensAtivas((prev) => ({ ...prev, [o.chave]: e.target.checked }))}
              />
              {o.label}
            </label>
          ))}
        </div>

        {ORIGENS.filter((o) => origensAtivas[o.chave]).map((o) => {
          const valor = metricas.estadoPorOrigem[o.chave]?.pendente ?? 0;
          const largura = (valor / maiorValor) * 100;
          return (
            <div className="barra-item" key={o.chave}>
              <div className="nome">
                <span className="dot" style={{ background: o.cor }} />
                {o.label}
              </div>
              <div className="barra-trilho">
                <div className="barra-fill" style={{ width: `${largura}%`, background: o.cor }} />
              </div>
              <div className="num">{valor}</div>
            </div>
          );
        })}

        <TogglePainelCliente chave="pendentes_por_tipo" visivelInicial={visibilidade["pendentes_por_tipo"] ?? false} />
        <TogglePainelOperador
          chave="pendentes_por_tipo"
          visivelInicial={visibilidadeOperador["pendentes_por_tipo"] ?? false}
        />
      </div>
    ),
    distribuicao_urgencia: (
      <DistribuicaoUrgencia
        key="distribuicao_urgencia"
        distribuicao={distribuicaoUrgencia}
        rodape={
          <>
            <TogglePainelCliente chave="distribuicao_urgencia" visivelInicial={visibilidade["distribuicao_urgencia"] ?? false} />
            <TogglePainelOperador
              chave="distribuicao_urgencia"
              visivelInicial={visibilidadeOperador["distribuicao_urgencia"] ?? false}
            />
          </>
        }
      />
    ),
    evolucao_backlog: (
      <div className="full" key="evolucao_backlog">
        <EvolucaoBacklog
          serie={evolucaoBacklog}
          desde={desde}
          ate={ate}
          rodape={
            <>
              <TogglePainelCliente chave="evolucao_backlog" visivelInicial={visibilidade["evolucao_backlog"] ?? false} />
              <TogglePainelOperador
                chave="evolucao_backlog"
                visivelInicial={visibilidadeOperador["evolucao_backlog"] ?? false}
              />
            </>
          }
        />
      </div>
    ),
    pendencias_sem_contato: (
      <div className="full" key="pendencias_sem_contato">
        <PendenciasSemContato
          dados={pendenciasSemContato}
          rodape={
            <TogglePainelCliente chave="pendencias_sem_contato" visivelInicial={visibilidade["pendencias_sem_contato"] ?? false} />
          }
        />
      </div>
    ),
    pendentes_por_cidade: (
      <div className="full" key="pendentes_por_cidade">
        <PendentesPorCidade
          dados={pendentesPorCidade}
          rodape={
            <>
              <TogglePainelCliente chave="pendentes_por_cidade" visivelInicial={visibilidade["pendentes_por_cidade"] ?? false} />
              <TogglePainelOperador
                chave="pendentes_por_cidade"
                visivelInicial={visibilidadeOperador["pendentes_por_cidade"] ?? false}
              />
            </>
          }
        />
      </div>
    ),
  };

  return (
    <div>
      {SECOES.map((secao) => {
        const paineis = METRICAS.filter((m) => m.secao === secao.chave && CHAVES_PAINEL_CUSTOM.has(m.chave)).map(
          (m) => painelPorChave[m.chave]
        );
        const itens = METRICAS.filter((m) => m.secao === secao.chave && !CHAVES_PAINEL_CUSTOM.has(m.chave));

        return (
          <div className="grupo-parametros" key={secao.chave}>
            <div className="grupo-titulo">{secao.label}</div>
            <div className="grupo-desc">{secao.desc}</div>

            {paineis.length > 0 && <div className="grafico-grid">{paineis}</div>}

            {itens.length > 0 && (
              <div className="kpi-grid">
                {itens.map((m) => (
                  <div className="kpi" key={m.chave}>
                    <VisibilidadeToggle chave={m.chave} visivelInicial={visibilidade[m.chave] ?? false} />
                    {CANDIDATAS_OPERADOR.has(m.chave) && (
                      <VisibilidadeOperadorToggle chave={m.chave} visivelInicial={visibilidadeOperador[m.chave] ?? false} />
                    )}
                    <div className="rotulo">{m.label}</div>
                    <div className="valor">{valorMetrica(m.chave, metricas)}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function TogglePainelCliente({ chave, visivelInicial }: { chave: string; visivelInicial: boolean }) {
  return (
    <div className="no-print" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 10 }}>
      <input
        key={String(visivelInicial)}
        type="checkbox"
        defaultChecked={visivelInicial}
        style={{ accentColor: "var(--accent)", cursor: "pointer" }}
        onChange={(e) => {
          const dados = new FormData();
          dados.set("chave", chave);
          dados.set("visivel", e.target.checked ? "true" : "false");
          alternarVisibilidadeMetricaAction(dados);
        }}
      />
      <span style={{ fontSize: 12, color: "var(--text-faint)" }}>Visível no Dashboard Cliente</span>
    </div>
  );
}

function TogglePainelOperador({ chave, visivelInicial }: { chave: string; visivelInicial: boolean }) {
  return (
    <div className="no-print" style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6 }}>
      <input
        key={String(visivelInicial)}
        type="checkbox"
        defaultChecked={visivelInicial}
        style={{ accentColor: "var(--accent)", cursor: "pointer" }}
        onChange={(e) => {
          const dados = new FormData();
          dados.set("chave", chave);
          dados.set("visivel_operador", e.target.checked ? "true" : "false");
          alternarVisibilidadeOperadorAction(dados);
        }}
      />
      <span style={{ fontSize: 12, color: "var(--text-faint)" }}>Visível no Painel Operador</span>
    </div>
  );
}

function VisibilidadeToggle({ chave, visivelInicial }: { chave: string; visivelInicial: boolean }) {
  const [estado, formAction] = useActionState<EstadoForm, FormData>(
    async (_prev, formData) => alternarVisibilidadeMetricaAction(formData),
    ESTADO_INICIAL
  );

  return (
    <label className="visibilidade" title="Visível no Dashboard Cliente">
      <input
        key={String(visivelInicial)}
        type="checkbox"
        defaultChecked={visivelInicial}
        onChange={(e) => {
          const dados = new FormData();
          dados.set("chave", chave);
          dados.set("visivel", e.target.checked ? "true" : "false");
          formAction(dados);
        }}
      />
      cliente
      {estado.erro && <span style={{ color: "var(--status-erro-fg)" }}> ⚠</span>}
    </label>
  );
}

function VisibilidadeOperadorToggle({ chave, visivelInicial }: { chave: string; visivelInicial: boolean }) {
  const [estado, formAction] = useActionState<EstadoForm, FormData>(
    async (_prev, formData) => alternarVisibilidadeOperadorAction(formData),
    ESTADO_INICIAL
  );

  return (
    <label className="visibilidade" title="Visível no Painel Operador">
      <input
        key={String(visivelInicial)}
        type="checkbox"
        defaultChecked={visivelInicial}
        onChange={(e) => {
          const dados = new FormData();
          dados.set("chave", chave);
          dados.set("visivel_operador", e.target.checked ? "true" : "false");
          formAction(dados);
        }}
      />
      operador
      {estado.erro && <span style={{ color: "var(--status-erro-fg)" }}> ⚠</span>}
    </label>
  );
}
