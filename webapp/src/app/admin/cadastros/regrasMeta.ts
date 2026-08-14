export type Categoria = "manutencao" | "instalacao" | "remocao";

export const CATEGORIAS: { chave: Categoria; label: string }[] = [
  { chave: "manutencao", label: "Manutenção" },
  { chave: "instalacao", label: "Instalação" },
  { chave: "remocao", label: "Remoção" },
];

// Lista confirmada ao vivo em rule_templates (31 códigos, ver plano da
// Fase 3/Passo 5) — não deriva por prefixo porque REGRA_RISCO/
// REGRA_TITULARIDADE (Instalação) não têm prefixo comum com o resto.
const CATEGORIA_POR_CODIGO: Record<string, Categoria> = {
  REGRA_SEM_PLACA: "manutencao",
  REGRA_1: "manutencao",
  REGRA_2: "manutencao",
  REGRA_3: "manutencao",
  REGRA_4: "manutencao",
  REGRA_4_TIMESTAMP: "manutencao",
  REGRA_5_1: "manutencao",
  REGRA_5_1_SEM_COMUNICACAO: "manutencao",
  REGRA_5_1_RECAIU: "manutencao",
  REGRA_5_2: "manutencao",
  REGRA_5_3: "manutencao",
  REGRA_5_4: "manutencao",
  REGRA_6_1: "manutencao",
  REGRA_ALERTA_CLIENTE: "manutencao",
  REGRA_SGA_INATIVO: "manutencao",
  REGRA_SGA_NAO_ENCONTRADO: "manutencao",

  REGRA_PRAZO_NORMAL: "instalacao",
  REGRA_PRAZO_ATRASO: "instalacao",
  REGRA_PRAZO_CRITICO: "instalacao",
  REGRA_RISCO: "instalacao",
  REGRA_PRAZO_E_RISCO: "instalacao",
  REGRA_TITULARIDADE: "instalacao",

  REGRA_REMOCAO_PRAZO_NORMAL: "remocao",
  REGRA_REMOCAO_PRAZO_ALTA: "remocao",
  REGRA_REMOCAO_PRAZO_URGENTE: "remocao",
  REGRA_REMOCAO_ATIVA_NORMAL: "remocao",
  REGRA_REMOCAO_ATIVA_ALTA: "remocao",
  REGRA_REMOCAO_ATIVA_URGENTE: "remocao",
  REGRA_REMOCAO_TITULARIDADE_NORMAL: "remocao",
  REGRA_REMOCAO_TITULARIDADE_ALTA: "remocao",
  REGRA_REMOCAO_TITULARIDADE_URGENTE: "remocao",
};

export function categoriaDe(codigoRegra: string): Categoria {
  return CATEGORIA_POR_CODIGO[codigoRegra] ?? "manutencao";
}

export function labelCategoria(categoria: Categoria): string {
  return CATEGORIAS.find((c) => c.chave === categoria)?.label ?? categoria;
}
