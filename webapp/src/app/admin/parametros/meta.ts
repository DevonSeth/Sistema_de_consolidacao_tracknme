export type Categoria = "geral" | "risco" | "prazos" | "disparo" | "observabilidade";
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
