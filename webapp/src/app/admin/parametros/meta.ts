export type Categoria = "geral" | "risco" | "prazos" | "disparo" | "observabilidade" | "desempenho";
export type TipoCampo = "bool" | "numero" | "texto" | "tier";

export type MetaParametro = {
  label: string;
  categoria: Categoria;
  tipo: TipoCampo;
  sufixo?: string;
  aviso?: string;
};

export const CATEGORIAS: { chave: Categoria; label: string }[] = [
  { chave: "geral", label: "Geral" },
  { chave: "risco", label: "Risco de veículo" },
  { chave: "prazos", label: "Prazos" },
  { chave: "disparo", label: "Esteira de disparo" },
  { chave: "observabilidade", label: "Observabilidade" },
  { chave: "desempenho", label: "Desempenho" },
];

export const META: Record<string, MetaParametro> = {
  tempo_limiar_inatividade_horas: {
    label: "Limiar de inatividade para abrir incidente",
    categoria: "geral",
    tipo: "numero",
    sufixo: "horas",
  },
  disparar_em_feriados_fins_de_semana: {
    label: "Disparar em feriados e fins de semana",
    categoria: "geral",
    tipo: "bool",
  },
  normalizar_placas: {
    label: "Normalizar placas ao comparar",
    categoria: "geral",
    tipo: "bool",
  },
  texto_instalado_nao_encontrado: {
    label: "Texto quando técnico não encontrado",
    categoria: "geral",
    tipo: "texto",
  },
  texto_local_nao_encontrado: {
    label: "Texto quando local não encontrado",
    categoria: "geral",
    tipo: "texto",
  },
  placas_genericas: {
    label: "Placas genéricas",
    categoria: "risco",
    tipo: "texto",
  },
  modelos_alto_risco_furto: {
    label: "Modelos de alto risco de furto",
    categoria: "risco",
    tipo: "texto",
  },
  cilindradas_excecoes: {
    label: "Exceções de cilindrada",
    categoria: "risco",
    tipo: "texto",
  },
  limiar_cilindrada_risco_cc: {
    label: "Cilindrada mínima de risco (moto)",
    categoria: "risco",
    tipo: "numero",
    sufixo: "cc",
  },
  limiar_fipe_risco: {
    label: "FIPE mínimo de risco (carro)",
    categoria: "risco",
    tipo: "numero",
    sufixo: "R$",
  },
  tier_instalacao: {
    label: "Faixas de Instalação",
    categoria: "prazos",
    tipo: "tier",
  },
  tier_remocao: {
    label: "Faixas de Remoção",
    categoria: "prazos",
    tipo: "tier",
  },
  limite_tentativas_disparo: {
    label: "Tentativas antes de escalar para ligação",
    categoria: "disparo",
    tipo: "numero",
  },
  horario_corte_disparo: {
    label: "Horário de corte do disparo",
    categoria: "disparo",
    tipo: "texto",
  },
  fuso_horario: {
    label: "Fuso horário de referência",
    categoria: "disparo",
    tipo: "texto",
  },
  limiar_dias_sem_contato: {
    label: 'Limiar de "Dias sem contato"',
    categoria: "disparo",
    tipo: "numero",
    sufixo: "dias úteis",
    aviso:
      "Só tem efeito depois que alguém rodar a formatação condicional da planilha de novo manualmente — não é aplicado sozinho a cada execução do pipeline.",
  },
  watchdog_minutos_alerta_travado: {
    label: "Minutos até alertar execução travada",
    categoria: "observabilidade",
    tipo: "numero",
    sufixo: "minutos",
  },
  watchdog_fator_lentidao: {
    label: "Fator de lentidão da etapa",
    categoria: "observabilidade",
    tipo: "numero",
    sufixo: "x a média histórica",
  },
  sga_http_habilitado: {
    label: "Consultar SGA via HTTP (sem navegador)",
    categoria: "desempenho",
    tipo: "bool",
  },
  sga_http_concorrencia: {
    label: "Concorrência HTTP no SGA (workers)",
    categoria: "desempenho",
    tipo: "numero",
    sufixo: "consultas simultâneas",
    aviso:
      "Testado sem erro até 160 (nenhum teto encontrado ainda) — throughput não melhorou de 100 pra 160 na única medição real. Ajuste aos poucos e observe o resultado de cada rodada.",
  },
  sga_http_tamanho_canario: {
    label: "Tamanho do lote canário (HTTP)",
    categoria: "desempenho",
    tipo: "numero",
    sufixo: "veículos",
  },
  sga_http_limiar_nao_encontrado: {
    label: "Limiar de 'não encontrado' do circuit breaker",
    categoria: "desempenho",
    tipo: "numero",
    sufixo: "proporção 0-1",
    aviso:
      "Baseline real medido: ~2.35% dos veículos. Acima disso, a etapa aborta o caminho HTTP pro resto da execução (cai pro navegador).",
  },
  sga_http_limiar_falha_tecnica: {
    label: "Limiar de falha técnica do circuit breaker",
    categoria: "desempenho",
    tipo: "numero",
    sufixo: "proporção 0-1",
    aviso: "Baseline real medido: ~0%.",
  },
  sga_http_timeout_base_ms: {
    label: "Timeout de segurança por tentativa (SGA)",
    categoria: "desempenho",
    tipo: "numero",
    sufixo: "ms (dobra a cada tentativa)",
    aviso:
      "Achado 2026-08-20: uma consulta sem timeout travou o Painel Operador por ~45min sem erro nenhum (trava no processo Node do Playwright, por baixo do timeout dele mesmo). Esse valor é a base — dobra a cada retry (ex: 30s/60s/120s com 3 tentativas).",
  },
  tracknme_http_habilitado: {
    label: "Abrir/concluir incidente via HTTP (sem navegador)",
    categoria: "desempenho",
    tipo: "bool",
  },
  tracknme_http_concorrencia: {
    label: "Concorrência HTTP no Track N'Me (workers)",
    categoria: "desempenho",
    tipo: "numero",
    sufixo: "escritas simultâneas",
    aviso:
      "Ainda sem rodada de escalada de concorrência validada (diferente do SGA) — cada item aqui é uma escrita real (abre/conclui incidente de verdade). Ajuste aos poucos e observe o resultado de cada rodada.",
  },
  tracknme_http_tamanho_canario: {
    label: "Tamanho do lote canário (HTTP)",
    categoria: "desempenho",
    tipo: "numero",
    sufixo: "incidentes",
    aviso: "Menor que o do SGA de propósito — cada item aqui é uma escrita real, um canário menor limita o efeito de um erro em produção.",
  },
  tracknme_http_limiar_falha_tecnica: {
    label: "Limiar de falha técnica do circuit breaker (Track N'Me)",
    categoria: "desempenho",
    tipo: "numero",
    sufixo: "proporção 0-1",
    aviso: "Ainda sem baseline real medido em escala — valor inicial igual ao do SGA.",
  },
  tracknme_http_timeout_base_ms: {
    label: "Timeout de segurança por tentativa (Track N'Me)",
    categoria: "desempenho",
    tipo: "numero",
    sufixo: "ms (dobra a cada tentativa)",
    aviso:
      "Mesma proteção do SGA (achado 2026-08-20) — defesa em profundidade além do timeout=30s já configurado no cliente HTTP do Track N'Me.",
  },
};

export const META_DEFAULT: MetaParametro = {
  label: "",
  categoria: "geral",
  tipo: "texto",
};

export function metaDe(chave: string): MetaParametro {
  return META[chave] ?? { ...META_DEFAULT, label: chave };
}

export const TIER_REGEX = /^\d+=[A-Za-z0-9_]+(,\d+=[A-Za-z0-9_]+)*$/;
