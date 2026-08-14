"""
Constantes de domínio — valores de texto livre que representam estados de
negócio (não são ENUM no Postgres, só convenção da aplicação).

`core/` nunca importa de `integrations/` (regra de arquitetura do README),
mas o inverso é permitido — por isso essas constantes moram aqui:
`core/dedup.py` (mesma camada) e `integrations/supabase_client.py` (camada
de fora, pode depender de `core/`) importam deste módulo. Nunca duplicar
estes valores em `integrations/`.
"""

from datetime import date

# --- tratativas.origem ---
ORIGEM_INSTALACAO = "instalacao"
ORIGEM_REMOCAO = "remocao"
ORIGEM_MANUTENCAO = "manutencao"
ORIGENS = [ORIGEM_INSTALACAO, ORIGEM_REMOCAO, ORIGEM_MANUTENCAO]

# --- tratativas.tipo_identificador: uso interno do motor (CHASSI é o
# identificador mais confiável do veículo; PLACA só quando chassi não foi
# encontrado) — movida de integrations/supabase_client.py porque
# core/motor_regras.py também precisa dela, e core/ não importa integrations/. ---
TIPO_IDENTIFICADOR_PLACA = "placa"
TIPO_IDENTIFICADOR_CHASSI = "chassi"

# --- tratativas.status (ciclo de vida completo) ---
STATUS_PENDENTE = "pendente"
STATUS_AGUARDANDO_RESPOSTA = "aguardando_resposta"
STATUS_RESPONDIDO = "respondido"
STATUS_AGUARDANDO_LIGACAO = "aguardando_ligacao"
STATUS_ENCAMINHADO_PUMA = "encaminhado_puma"
STATUS_FINALIZADO = "finalizado"
STATUS_BLOQUEADO_SGA = "bloqueado_sga"
STATUS_TRATATIVA = [
    STATUS_PENDENTE,
    STATUS_AGUARDANDO_RESPOSTA,
    STATUS_RESPONDIDO,
    STATUS_AGUARDANDO_LIGACAO,
    STATUS_ENCAMINHADO_PUMA,
    STATUS_FINALIZADO,
    STATUS_BLOQUEADO_SGA,
]

# --- tratativas.status_contato: só sinaliza telefone inválido (cód. 7 Newmo);
# `None`/null é o estado normal, sem constante própria. ---
STATUS_CONTATO_INVALIDO = "contato_invalido"

# --- puma_encaminhamentos.status ---
PUMA_STATUS_AGUARDANDO_ACAO = "aguardando_acao"
PUMA_STATUS_EM_ANDAMENTO = "em_andamento"
PUMA_STATUS_CONCLUIDO = "concluido"
STATUS_PUMA = [PUMA_STATUS_AGUARDANDO_ACAO, PUMA_STATUS_EM_ANDAMENTO, PUMA_STATUS_CONCLUIDO]

# --- posições de coluna (1-indexed) da aba "Rastreadores Ativos" (planilha
# Administrador) — cabeçalho tem "Modelo"/"Marca" duplicados, por isso essa
# aba é sempre lida por posição (`col_1`..`col_28`), nunca por nome. Só as
# posições usadas pelo motor de regras (core.motor_regras) estão aqui;
# `integrations/google_sheets_client.py` importa estas constantes em vez de
# defini-las (mesmo padrão de ORIGEM_*/STATUS_* acima). ---
COL_RASTREADORES_ID = 1
COL_RASTREADORES_NUMERO = 4  # "Linha" do incidente corresponde a este campo
COL_RASTREADORES_IMEI = 3
COL_RASTREADORES_DATA_POSICAO = 7
COL_RASTREADORES_CLIENTE = 8
COL_RASTREADORES_CELULAR = 9
COL_RASTREADORES_PLACA = 10
COL_RASTREADORES_STATUS = 16
COL_RASTREADORES_CHASSI = 27
COL_RASTREADORES_ICCID = 28

# "Modelo" aparece duplicado nas posições 2 e 13 com significados diferentes
# (confirmado ao vivo contra o xlsx real, 2026-08-10): posição 2 é o modelo
# do RASTREADOR (ex: "J16"), posição 13 é o modelo do VEÍCULO (ex: "CG 160
# FAN FLEX") — a única útil pra mensagem de WhatsApp (core.mensagens).
COL_RASTREADORES_MODELO = 13

# Usada só por REGRA_INSTALACAO_JA_FEITA (core.motor_regras_instalacao_
# remocao) — nenhuma outra regra precisa da data em que o rastreador foi
# de fato instalado.
COL_RASTREADORES_DATA_INSTALACAO = 18

# --- tratativas.atendimento (Fase F.1 — WhatsApp) ---
ATENDIMENTO_BASE = "base"
ATENDIMENTO_ROTA = "rota"
ATENDIMENTO_ACAO = "acao"

# --- corte histórico de migração: qualquer `Data de Posição` anterior a
# esta data é tratada como "nunca comunicou" (equivalente a campo vazio).
# Data fixa (não é system_parameter — não deve mudar de novo). ---
DATA_CORTE_POSICAO_VALIDA = date(2025, 9, 30)

# --- status do SGA (Hinova) — texto livre do dropdown, só estes 2 valores
# têm significado especial pro sistema (o resto é tratado como "inativo,
# fecha incidente automaticamente" por core.motor_regras.aplicar_situacoes_sga).
# Aqui (não em integrations/sga_bot.py) porque core/motor_regras.py também
# precisa comparar contra eles, e core/ não importa integrations/. ---
STATUS_SGA_ATIVO = "ATIVO"
STATUS_SGA_NAO_ENCONTRADO = "NÃO ENCONTRADO"

# --- tratativas.codigo_regra (subconjunto): remoção com veículo ainda ativo
# no SGA — discrepância que o atendente pode suprimir marcando
# `discrepancia_revisada` (orchestrator/pipeline.py e, agora, também
# orchestrator/dashboards_operador.py, por isso promovida pra cá em vez de
# ficar privada num módulo só). ---
CODIGOS_REMOCAO_ATIVA = {
    "REGRA_REMOCAO_ATIVA_NORMAL",
    "REGRA_REMOCAO_ATIVA_ALTA",
    "REGRA_REMOCAO_ATIVA_URGENTE",
}
