// Rótulos amigáveis das etapas do pipeline — cópia manual de `label` em
// `orchestrator/catalogo_etapas.py` (Python) — mantido em sync manualmente,
// mesmo princípio da paleta de cor duplicada entre webapp/ e ui/ (não há
// arquivo compartilhado entre os 2 lados). Se um label mudar lá, atualizar
// aqui também. Compartilhado dentro do PRÓPRIO webapp (banner do watchdog em
// admin/page.tsx e a tela de Logs em admin/logs/) — nada de duplicar de novo
// dentro do mesmo projeto TypeScript.
export const LABEL_ETAPA: Record<string, string> = {
  baixar_relatorios: "Baixar relatórios (Track N' Me)",
  ler_planilha_gestor: "Ler planilha do gestor",
  motor_de_regras: "Motor de regras",
  abrir_incidentes_automaticos: "Abrir incidentes automáticos",
  enriquecimento_sga: "Consultar SGA (login manual)",
  consolidar_com_sga: "Consolidar com SGA",
  fechar_incidentes_automaticos: "Fechar incidentes automáticos",
  publicar_fila_operacional: "Publicar fila operacional",
  disparo_mensagens: "Disparo de mensagens (WhatsApp)",
  finalizar_atendimentos_diarios: "Finalizar atendimentos diários",
  escalonar_ligacao: "Escalonar para ligação",
  processar_resultado_ligacao: "Processar resultado de ligação",
  processar_alertas: "Processar alertas",
};

export function labelEtapa(etapaId: string): string {
  return LABEL_ETAPA[etapaId] ?? etapaId;
}
