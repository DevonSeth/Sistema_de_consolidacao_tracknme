"""
Motor de regras de Manutenção — porta em Python da lógica que veio no JSON do n8n
("Engine de Consolidação"), com os ajustes já combinados na conversa.

Especificação de negócio completa (14 códigos de regra + Grupo 1, cada um
com prioridade, ação, responsável e nível de urgência 1-5) está em
`docs/regras_negocio.md` — este módulo só implementa o que está lá, não é
fonte de novas decisões de negócio.

Princípios de identidade (ver `docs/regras_negocio.md`):
- CHASSI é o identificador mais confiável do VEÍCULO (dado imutável).
- IMEI é sempre o identificador do EQUIPAMENTO.
- Placa é o que o atendente reconhece no produto final, mas não é
  confiável como chave de agrupamento (erro de digitação, re-emplacamento).

Saída de cada linha inclui `acao_sugerida` e `observacao_sistema`,
preenchidos a partir de `rule_templates` (Supabase), nunca hardcoded aqui.

Três grupos de saída:
    grupo_1_abrir              -> abrir incidente automaticamente (Playwright, Track N' Me)
    grupo_2_concluir           -> concluir incidente automaticamente (Playwright, Track N' Me)
    grupo_3_tratativa_humana   -> precisa de tratativa humana (segue para SGA + fila operacional)

Precedência REGRA_1/2/3 vs. família REGRA_5_1 (decisão fechada): quando um
incidente bate as duas condições ao mesmo tempo (equipamento nunca
comunicou E o IMEI diverge do cadastro), a divergência de IMEI vence — a
família REGRA_5_1 é mais específica (indica possível troca de
equipamento) e é checada antes da cascata genérica REGRA_1/2/3, mesmo a
prioridade numérica de REGRA_1 (10) sendo menor que a de REGRA_5_1 (50).

TODO (implementação em etapas, ver plano aprovado):
    Etapa 6 — Grupo 1 (scan de abertura automática).
    Etapa 7 — montagem final de `classificar_incidentes`.
"""

from datetime import datetime

from core.constants import (
    COL_RASTREADORES_CELULAR,
    COL_RASTREADORES_CHASSI,
    COL_RASTREADORES_CLIENTE,
    COL_RASTREADORES_DATA_POSICAO,
    COL_RASTREADORES_ICCID,
    COL_RASTREADORES_IMEI,
    COL_RASTREADORES_MODELO_VEICULO,
    COL_RASTREADORES_NUMERO,
    COL_RASTREADORES_PLACA,
    COL_RASTREADORES_STATUS,
    DATA_CORTE_POSICAO_VALIDA,
    STATUS_SGA_ATIVO,
    STATUS_SGA_NAO_ENCONTRADO,
    TIPO_IDENTIFICADOR_CHASSI,
    TIPO_IDENTIFICADOR_PLACA,
)
from core.normalizacao import normalizar_placa, normalizar_telefone_e164

CODIGO_REGRA_SGA_INATIVO = "REGRA_SGA_INATIVO"
CODIGO_REGRA_SGA_NAO_ENCONTRADO = "REGRA_SGA_NAO_ENCONTRADO"
CODIGO_REGRA_MANUTENCAO_DIVERGENCIA_SGA = "REGRA_MANUTENCAO_DIVERGENCIA_SGA"

_FORMATOS_DATA = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y")


def _parse_data(valor: str) -> datetime | None:
    """Converte texto de data/hora vindo da planilha (formato brasileiro,
    com ou sem hora) para `datetime`. Retorna `None` se vazio ou em formato
    não reconhecido — tratado como "sem posição registrada" por quem chama.
    """
    valor = (valor or "").strip()
    if not valor:
        return None
    for formato in _FORMATOS_DATA:
        try:
            return datetime.strptime(valor, formato)
        except ValueError:
            continue
    return None


# --- Etapa 2: helper de comunicação/atualidade -----------------------------

def _esta_sem_comunicar(data_posicao: str, agora: datetime, limiar_horas: int) -> bool:
    """True se o equipamento deve ser considerado sem comunicar: posição
    ausente/ilegível, anterior ao corte histórico de migração
    (`DATA_CORTE_POSICAO_VALIDA`), ou mais antiga que `limiar_horas` em
    relação a `agora`.
    """
    momento = _parse_data(data_posicao)
    if momento is None:
        return True
    if momento.date() < DATA_CORTE_POSICAO_VALIDA:
        return True
    return (agora - momento).total_seconds() > limiar_horas * 3600


# --- Etapa 1: helpers de identidade (chassi > imei > placa) ---------------

def _buscar_equipamento_por_placa(placa: str, equipamentos: list[dict]) -> dict | None:
    """Busca em Rastreadores Ativos o equipamento cuja placa é idêntica
    (após normalização de formato) à `placa` informada. É essa igualdade
    incidente<->cadastro que confirma o chassi certo — não é suposição.
    """
    placa_normalizada = normalizar_placa(placa, placas_genericas=[])
    if placa_normalizada is None:
        return None
    for equipamento in equipamentos:
        placa_equipamento = normalizar_placa(
            equipamento.get(f"col_{COL_RASTREADORES_PLACA}", ""), placas_genericas=[]
        )
        if placa_equipamento is not None and placa_equipamento == placa_normalizada:
            return equipamento
    return None


def _buscar_equipamento_por_imei(imei: str, equipamentos: list[dict]) -> dict | None:
    """Busca em Rastreadores Ativos o equipamento com o IMEI idêntico ao
    informado.
    """
    imei_normalizado = (imei or "").strip()
    if not imei_normalizado:
        return None
    for equipamento in equipamentos:
        if equipamento.get(f"col_{COL_RASTREADORES_IMEI}", "").strip() == imei_normalizado:
            return equipamento
    return None


def buscar_chassi_por_placa(placa: str, equipamentos: list[dict]) -> str | None:
    """Fallback de identificador: busca o chassi na base de Rastreadores
    Ativos usando a PLACA como chave de busca (não o imei).
    """
    equipamento = _buscar_equipamento_por_placa(placa, equipamentos)
    if equipamento is None:
        return None
    chassi = equipamento.get(f"col_{COL_RASTREADORES_CHASSI}", "").strip()
    return chassi or None


def _resolver_chassi(incidente: dict, equipamentos: list[dict], parametros: dict) -> str | None:
    """Resolve o identificador mais confiável do veículo/equipamento do
    incidente, nesta ordem de fallback (ver `docs/regras_negocio.md`):

    1. Chassi confirmado via placa idêntica no cadastro (Rastreadores Ativos).
    2. Chassi confirmado via IMEI idêntico no cadastro — quando a placa não
       resolve (inválida, genérica, ou sem equipamento correspondente).
    3. O próprio IMEI do incidente, se nada bateu no cadastro.
    4. A placa normalizada do incidente, como último recurso — usando a
       lista real de `placas_genericas` (não `[]`): um placeholder (ex:
       "SEM PLACA") não vira pseudo-chassi, senão dois incidentes de
       veículos DIFERENTES que só compartilham o mesmo placeholder
       seriam fundidos como se fossem o mesmo veículo (achado
       2026-08-07 — bug pré-existente, corrigido aqui).

    Usado para agrupar incidentes do mesmo veículo/equipamento (dedup) sem
    depender só da placa, que pode ter erro de digitação ou mudar.
    """
    chassi = buscar_chassi_por_placa(incidente.get("Placa", ""), equipamentos)
    if chassi:
        return chassi

    equipamento = _buscar_equipamento_por_imei(incidente.get("Imei", ""), equipamentos)
    if equipamento is not None:
        chassi = equipamento.get(f"col_{COL_RASTREADORES_CHASSI}", "").strip()
        if chassi:
            return chassi

    imei_incidente = (incidente.get("Imei") or "").strip()
    if imei_incidente:
        return imei_incidente

    return normalizar_placa(incidente.get("Placa", ""), _placas_genericas(parametros))


# --- Etapa 3: dedup silencioso (REGRA_4 / REGRA_4_TIMESTAMP) --------------

def _filtrar_duplicados(incidentes: list[dict], equipamentos: list[dict], parametros: dict) -> list[dict]:
    """Dedup silencioso: agrupa incidentes pelo identificador mais confiável
    do veículo/equipamento (`_resolver_chassi`) e mantém só o de `Data do
    incidente` mais recente por grupo — os demais são descartados
    (`REGRA_4`, ou `REGRA_4_TIMESTAMP` quando o horário é exatamente igual
    ao do mantido). Nenhum dos dois vira linha de resultado (decisão 15,
    `docs/regras_negocio.md`), por isso esta função só retorna os
    sobreviventes, sem distinguir o motivo do descarte.

    Incidentes sem nenhum identificador resolvível (placa e imei vazios)
    não são agrupados com mais ninguém — ficam como estão.

    Roda ANTES de qualquer outra regra (inclusive `REGRA_ALERTA_CLIENTE` e
    a cascata principal).
    """
    mantidos_por_chave: dict[str, dict] = {}
    momentos_por_chave: dict[str, datetime] = {}
    sobreviventes: list[dict] = []

    for incidente in incidentes:
        chave = _resolver_chassi(incidente, equipamentos, parametros)
        if chave is None:
            sobreviventes.append(incidente)
            continue

        momento = _parse_data(incidente.get("Data do incidente", "")) or datetime.min
        if chave not in mantidos_por_chave or momento > momentos_por_chave[chave]:
            mantidos_por_chave[chave] = incidente
            momentos_por_chave[chave] = momento

    sobreviventes.extend(mantidos_por_chave.values())
    return sobreviventes


# --- Etapa 4: REGRA_ALERTA_CLIENTE (roda antes da cascata principal) ------

def _cliente_do_equipamento(equipamento: dict) -> str:
    return equipamento.get(f"col_{COL_RASTREADORES_CLIENTE}", "").strip()


def _verificar_alerta_cliente(incidente: dict, equipamentos: list[dict],
                               agora: datetime, limiar_horas: int) -> dict | None:
    """REGRA_ALERTA_CLIENTE — resolução independente, roda ANTES de toda a
    cascata principal (decisão fechada em `docs/regras_negocio.md`): se o
    nome do cliente do incidente divergir do cadastro atual, o sistema
    fecha o incidente antigo e, se o equipamento estiver sem comunicar
    (`_esta_sem_comunicar`), sinaliza abertura de um novo incidente já com
    o cliente atual.

    Retorna `None` se não houver equipamento correspondente ou se os nomes
    não divergirem (nada a fazer aqui — o incidente segue pra cascata
    principal). Caso contrário, retorna:
        {
            "codigo_regra": "REGRA_ALERTA_CLIENTE",
            "fechar_incidente": True,
            "abrir_novo_incidente": bool,
            "cliente_atual": str,
        }
    """
    equipamento = _buscar_equipamento_por_placa(incidente.get("Placa", ""), equipamentos)
    if equipamento is None:
        equipamento = _buscar_equipamento_por_imei(incidente.get("Imei", ""), equipamentos)
    if equipamento is None:
        return None

    cliente_incidente = (incidente.get("Cliente") or "").strip()
    cliente_cadastro = _cliente_do_equipamento(equipamento)
    if not cliente_incidente or not cliente_cadastro:
        return None
    if cliente_incidente.upper() == cliente_cadastro.upper():
        return None

    sem_comunicar = _esta_sem_comunicar(
        equipamento.get(f"col_{COL_RASTREADORES_DATA_POSICAO}", ""), agora, limiar_horas
    )
    return {
        "codigo_regra": "REGRA_ALERTA_CLIENTE",
        "fechar_incidente": True,
        "abrir_novo_incidente": sem_comunicar,
        "cliente_atual": cliente_cadastro,
    }


# --- Etapa 5: cascata principal, uma função pura por regra ----------------

def _placas_genericas(parametros: dict) -> list[str]:
    """`system_parameters.placas_genericas` chega como texto CSV
    (ex: 'CHASSI,SEM PLACA,0000000') — quem consome faz o split (mesmo
    comentário em `integrations/supabase_client.py`)."""
    valor = parametros.get("placas_genericas", "")
    if isinstance(valor, list):
        return valor
    return [item.strip() for item in str(valor).split(",") if item.strip()]


def _imei_diverge(incidente: dict, equipamento: dict) -> bool:
    imei_incidente = (incidente.get("Imei") or "").strip()
    imei_cadastro = equipamento.get(f"col_{COL_RASTREADORES_IMEI}", "").strip()
    return bool(imei_incidente) and bool(imei_cadastro) and imei_incidente != imei_cadastro


def _data_posicao_e_posterior_ao_incidente(equipamento: dict, incidente: dict) -> bool:
    momento_posicao = _parse_data(equipamento.get(f"col_{COL_RASTREADORES_DATA_POSICAO}", ""))
    momento_incidente = _parse_data(incidente.get("Data do incidente", ""))
    if momento_posicao is None or momento_incidente is None:
        return False
    return momento_posicao > momento_incidente


def _regra_sem_placa(incidente: dict, parametros: dict) -> bool:
    """Placa do incidente inválida/placeholder."""
    return normalizar_placa(incidente.get("Placa", ""), _placas_genericas(parametros)) is None


def _imei_diverge_com_troca_relevante(incidente: dict, equipamento: dict | None) -> bool:
    """True quando a família REGRA_5_1 (SEM_COMUNICACAO / 5_1 / 5_1_RECAIU)
    se aplica de verdade: IMEI diverge E (o cadastro nunca comunicou OU
    comunicou depois do incidente). Usado como guard pra excluir
    REGRA_1/2/3 só nesses casos — se o IMEI diverge mas a posição do
    cadastro é antiga (não posterior ao incidente) e não vazia, a
    divergência não é "relevante" o suficiente e o incidente deve seguir
    pela cascata genérica (REGRA_1/2/3) normalmente.
    """
    if equipamento is None or not _imei_diverge(incidente, equipamento):
        return False
    momento_posicao = _parse_data(equipamento.get(f"col_{COL_RASTREADORES_DATA_POSICAO}", ""))
    if momento_posicao is None:
        return True
    momento_incidente = _parse_data(incidente.get("Data do incidente", ""))
    if momento_incidente is None:
        return False
    return momento_posicao > momento_incidente


def _regra_5_1_sem_comunicacao(incidente: dict, equipamento: dict | None) -> bool:
    """IMEI diverge do cadastro e o equipamento cadastrado nunca comunicou."""
    if equipamento is None or not _imei_diverge(incidente, equipamento):
        return False
    momento_posicao = _parse_data(equipamento.get(f"col_{COL_RASTREADORES_DATA_POSICAO}", ""))
    return momento_posicao is None


def _regra_5_1(incidente: dict, equipamento: dict | None, agora: datetime, limiar_horas: int) -> bool:
    """IMEI diverge, troca confirmada (posição do cadastro após o
    incidente), equipamento novo comunicando bem dentro do limiar."""
    if equipamento is None or not _imei_diverge(incidente, equipamento):
        return False
    if not _data_posicao_e_posterior_ao_incidente(equipamento, incidente):
        return False
    data_posicao = equipamento.get(f"col_{COL_RASTREADORES_DATA_POSICAO}", "")
    return not _esta_sem_comunicar(data_posicao, agora, limiar_horas)


def _regra_5_1_recaiu(incidente: dict, equipamento: dict | None, agora: datetime, limiar_horas: int) -> bool:
    """IMEI diverge, troca confirmada, mas o equipamento novo TAMBÉM já
    ficou sem comunicar de novo (+48h)."""
    if equipamento is None or not _imei_diverge(incidente, equipamento):
        return False
    if not _data_posicao_e_posterior_ao_incidente(equipamento, incidente):
        return False
    data_posicao = equipamento.get(f"col_{COL_RASTREADORES_DATA_POSICAO}", "")
    return _esta_sem_comunicar(data_posicao, agora, limiar_horas)


def _regra_2(incidente: dict, equipamento: dict | None, agora: datetime, limiar_horas: int) -> bool:
    """Voltou a comunicar dentro do limiar (só quando a família REGRA_5_1
    não se aplica — ver `_imei_diverge_com_troca_relevante`)."""
    if equipamento is None or _imei_diverge_com_troca_relevante(incidente, equipamento):
        return False
    if not _data_posicao_e_posterior_ao_incidente(equipamento, incidente):
        return False
    data_posicao = equipamento.get(f"col_{COL_RASTREADORES_DATA_POSICAO}", "")
    return not _esta_sem_comunicar(data_posicao, agora, limiar_horas)


def _regra_1(incidente: dict, equipamento: dict | None, agora: datetime, limiar_horas: int) -> bool:
    """Sem comunicação desde antes do incidente, ou nunca comunicou (só
    quando a família REGRA_5_1 não se aplica — ver
    `_imei_diverge_com_troca_relevante`)."""
    if equipamento is None or _imei_diverge_com_troca_relevante(incidente, equipamento):
        return False
    return not _data_posicao_e_posterior_ao_incidente(equipamento, incidente)


def _regra_3(incidente: dict, equipamento: dict | None, agora: datetime, limiar_horas: int) -> bool:
    """Comunicou após o incidente, mas recaiu — já ultrapassou o limiar de
    novo (só quando a família REGRA_5_1 não se aplica — ver
    `_imei_diverge_com_troca_relevante`)."""
    if equipamento is None or _imei_diverge_com_troca_relevante(incidente, equipamento):
        return False
    if not _data_posicao_e_posterior_ao_incidente(equipamento, incidente):
        return False
    data_posicao = equipamento.get(f"col_{COL_RASTREADORES_DATA_POSICAO}", "")
    return _esta_sem_comunicar(data_posicao, agora, limiar_horas)


def _regra_5_2(incidente: dict, equipamentos: list[dict]) -> bool:
    """IMEI do incidente existe no cadastro mas sem vínculo de placa."""
    imei_incidente = (incidente.get("Imei") or "").strip()
    if not imei_incidente:
        return False
    equipamento = _buscar_equipamento_por_imei(imei_incidente, equipamentos)
    if equipamento is None:
        return False
    placa_cadastro = normalizar_placa(
        equipamento.get(f"col_{COL_RASTREADORES_PLACA}", ""), placas_genericas=[]
    )
    return placa_cadastro is None


def _regra_5_3(incidente: dict, equipamentos: list[dict]) -> bool:
    """Nem placa nem IMEI do incidente foram encontrados no cadastro atual."""
    if _buscar_equipamento_por_placa(incidente.get("Placa", ""), equipamentos) is not None:
        return False
    imei_incidente = (incidente.get("Imei") or "").strip()
    if imei_incidente and _buscar_equipamento_por_imei(imei_incidente, equipamentos) is not None:
        return False
    return True


def _regra_5_4(incidente: dict, equipamentos: list[dict]) -> bool:
    """IMEI do incidente está ativo para OUTRA placa no cadastro, divergindo
    da placa do incidente."""
    imei_incidente = (incidente.get("Imei") or "").strip()
    if not imei_incidente:
        return False
    equipamento = _buscar_equipamento_por_imei(imei_incidente, equipamentos)
    if equipamento is None:
        return False
    placa_cadastro = normalizar_placa(
        equipamento.get(f"col_{COL_RASTREADORES_PLACA}", ""), placas_genericas=[]
    )
    if placa_cadastro is None:
        return False  # sem vínculo de placa é REGRA_5_2, não aqui
    placa_incidente = normalizar_placa(incidente.get("Placa", ""), placas_genericas=[])
    return placa_incidente is None or placa_cadastro != placa_incidente


def _regra_6_1(incidente: dict, equipamentos: list[dict]) -> bool:
    """Iccid/Linha do incidente diverge da linha ativa no cadastro para o
    mesmo IMEI."""
    imei_incidente = (incidente.get("Imei") or "").strip()
    if not imei_incidente:
        return False
    equipamento = _buscar_equipamento_por_imei(imei_incidente, equipamentos)
    if equipamento is None:
        return False

    iccid_incidente = (incidente.get("Iccid") or "").strip()
    iccid_cadastro = equipamento.get(f"col_{COL_RASTREADORES_ICCID}", "").strip()
    diverge_iccid = bool(iccid_incidente) and bool(iccid_cadastro) and iccid_incidente != iccid_cadastro

    linha_incidente = (incidente.get("Linha") or "").strip()
    linha_cadastro = equipamento.get(f"col_{COL_RASTREADORES_NUMERO}", "").strip()
    diverge_linha = bool(linha_incidente) and bool(linha_cadastro) and linha_incidente != linha_cadastro

    return diverge_iccid or diverge_linha


def _classificar_incidente_existente(incidente: dict, equipamentos: list[dict],
                                      parametros: dict, agora: datetime) -> str | None:
    """Tenta cada regra da cascata principal na ordem de precedência
    fechada (ver nota no topo do módulo) e retorna o primeiro `codigo_regra`
    que bater, ou `None` se nenhuma bateu.
    """
    limiar_horas = int(parametros.get("tempo_limiar_inatividade_horas", 48))

    if _regra_sem_placa(incidente, parametros):
        return "REGRA_SEM_PLACA"

    equipamento_por_placa = _buscar_equipamento_por_placa(incidente.get("Placa", ""), equipamentos)

    if _regra_5_1_sem_comunicacao(incidente, equipamento_por_placa):
        return "REGRA_5_1_SEM_COMUNICACAO"
    if _regra_5_1(incidente, equipamento_por_placa, agora, limiar_horas):
        return "REGRA_5_1"
    if _regra_5_1_recaiu(incidente, equipamento_por_placa, agora, limiar_horas):
        return "REGRA_5_1_RECAIU"
    if _regra_2(incidente, equipamento_por_placa, agora, limiar_horas):
        return "REGRA_2"
    if _regra_1(incidente, equipamento_por_placa, agora, limiar_horas):
        return "REGRA_1"
    if _regra_3(incidente, equipamento_por_placa, agora, limiar_horas):
        return "REGRA_3"
    if _regra_5_2(incidente, equipamentos):
        return "REGRA_5_2"
    if _regra_5_3(incidente, equipamentos):
        return "REGRA_5_3"
    if _regra_5_4(incidente, equipamentos):
        return "REGRA_5_4"
    if _regra_6_1(incidente, equipamentos):
        return "REGRA_6_1"
    return None


# --- Etapa 6: Grupo 1 (scan de abertura automática) -----------------------

def _tem_incidente_aberto(equipamento: dict, incidentes: list[dict]) -> bool:
    """True se existe algum incidente com Status 'aberto' vinculado a este
    equipamento, por placa idêntica ou IMEI idêntico."""
    placa_equipamento = normalizar_placa(
        equipamento.get(f"col_{COL_RASTREADORES_PLACA}", ""), placas_genericas=[]
    )
    imei_equipamento = equipamento.get(f"col_{COL_RASTREADORES_IMEI}", "").strip()

    for incidente in incidentes:
        if (incidente.get("Status") or "").strip().lower() != "aberto":
            continue
        placa_incidente = normalizar_placa(incidente.get("Placa", ""), placas_genericas=[])
        if placa_equipamento is not None and placa_incidente == placa_equipamento:
            return True
        imei_incidente = (incidente.get("Imei") or "").strip()
        if imei_equipamento and imei_incidente == imei_equipamento:
            return True
    return False


def manutencao_concluida_para_placa(placa: str, incidentes: list[dict], parametros: dict) -> bool:
    """True só quando dá pra confirmar com segurança que a manutenção foi
    concluída: placa normalizável (não vazia, não genérica) **e** nenhum
    incidente com Status 'aberto' bate com ela — "não dá pra confirmar"
    nunca vira "concluída" (framing positivo de propósito, pra não
    exigir inversão no chamador). Mesma normalização/filtro de `_tem_
    incidente_aberto`, isolado de um `equipamento` específico. Usado
    fora deste módulo (`orchestrator.pipeline`) pra detectar quando uma
    pendência de manutenção já escalada pra Puma foi resolvida por fora:
    `REGRA_2`/`REGRA_5_3` já fecham o incidente sozinhas no Track N'Me
    quando batem — aqui só se confirma que ele não está mais aberto, sem
    reimplementar as 2 cascatas."""
    placa_normalizada = normalizar_placa(placa, _placas_genericas(parametros))
    if placa_normalizada is None:
        return False
    for incidente in incidentes:
        if (incidente.get("Status") or "").strip().lower() != "aberto":
            continue
        if normalizar_placa(incidente.get("Placa", ""), _placas_genericas(parametros)) == placa_normalizada:
            return False
    return True


def _scan_grupo_1(equipamentos: list[dict], incidentes: list[dict],
                   parametros: dict, agora: datetime) -> list[dict]:
    """Grupo 1 — scan independente (decisão 14, `docs/regras_negocio.md`):
    equipamento ATIVO, sem incidente aberto vinculado, com `Data de
    Posição` há mais de `tempo_limiar_inatividade_horas` (ou anterior ao
    corte de migração) -> deveria ter um incidente aberto automaticamente.
    Retorna a lista de equipamentos nessa situação — quem chama decide
    como abrir o incidente de verdade (Track N' Me).
    """
    limiar_horas = int(parametros.get("tempo_limiar_inatividade_horas", 48))
    candidatos = []
    for equipamento in equipamentos:
        status = equipamento.get(f"col_{COL_RASTREADORES_STATUS}", "").strip().lower()
        if status != "ativo":
            continue
        if _tem_incidente_aberto(equipamento, incidentes):
            continue
        data_posicao = equipamento.get(f"col_{COL_RASTREADORES_DATA_POSICAO}", "")
        if _esta_sem_comunicar(data_posicao, agora, limiar_horas):
            candidatos.append(equipamento)
    return candidatos


def _chassi_na_lista_retirada(chassi: str | None, instalacao_remocao: list[dict]) -> bool:
    """True se o chassi aparece em Instalação-Remoção com `Serviço` de
    retirada/remoção. Decisão fechada (`docs/regras_negocio.md`): a
    presença da linha já basta pra fechar o incidente de manutenção — não
    confirma execução física, só evita duplicar acompanhamento (mesmo
    veículo em "manutenção" e "retirada" ao mesmo tempo).
    """
    if not chassi:
        return False
    chassi_normalizado = chassi.strip().upper()
    for registro in instalacao_remocao:
        servico = (registro.get("Serviço") or "").strip().lower()
        if "retir" not in servico and "remo" not in servico:
            continue
        if (registro.get("Chassi") or "").strip().upper() == chassi_normalizado:
            return True
    return False


class _FormatoSeguro(dict):
    """Usado com `str.format_map` pra não quebrar quando um template de
    produção referenciar um placeholder que não foi antecipado aqui."""

    def __missing__(self, chave):
        return ""


def _valores_template(incidente: dict, equipamento: dict | None, parametros: dict) -> _FormatoSeguro:
    valores = _FormatoSeguro(
        tempo_limiar_inatividade_horas=parametros.get("tempo_limiar_inatividade_horas", 48),
        imei_incidente=incidente.get("Imei", ""),
        placa_incidente=incidente.get("Placa", ""),
        cliente_incidente=incidente.get("Cliente", ""),
        id_recente=incidente.get("ID", ""),
    )
    if equipamento is not None:
        valores["imei_cadastro"] = equipamento.get(f"col_{COL_RASTREADORES_IMEI}", "")
        valores["placa_cadastro"] = equipamento.get(f"col_{COL_RASTREADORES_PLACA}", "")
        valores["cliente_equipamentos"] = equipamento.get(f"col_{COL_RASTREADORES_CLIENTE}", "")
    return valores


def _montar_linha_resultado(incidente: dict, codigo_regra: str, equipamento: dict | None,
                             chassi: str | None, templates: dict, parametros: dict) -> dict:
    """Monta a linha de resultado com `acao_sugerida`/`observacao_sistema`
    preenchidos a partir de `templates` (nunca hardcoded aqui — decisão 19,
    `docs/regras_negocio.md`).

    `data_incidente`/`evento` alimentam `core.dedup.gerar_chave_unica`
    ("manutencao") na Fase E — não são reclassificados aqui, só
    repassados do `incidente` cru. `telefone` vem de "Celular" (col. 9)
    em Rastreadores Ativos quando o equipamento foi encontrado; vazio
    quando não. `tipo_identificador`/`identificador` seguem o mesmo
    princípio de identidade do módulo (chassi > placa) — usado só pra
    `tratativas` (atendente/WhatsApp), não confundir com `chassi_sga`
    abaixo. `modelo` vem do mesmo `equipamento` já resolvido por
    placa/IMEI (col. 13 — modelo do VEÍCULO, não confundir com a col. 2,
    modelo do rastreador) — usado por `core.mensagens` como fallback
    quando a placa é fictícia/ausente.

    `chassi_sga` (achado 2026-08-16): o chassi CONFIRMADO via cadastro
    (Rastreadores Ativos) — diferente de `chassi` (que pode ser um IMEI
    ou uma placa normalizada, usado só pra dedup em `_resolver_chassi`).
    `orchestrator.pipeline._alvos_consulta_sga` usa `chassi_sga` (nunca
    `chassi`) pra decidir se busca por Chassi ou por Placa no SGA — o
    campo Chassi do SGA só aceita chassi de verdade, nunca um IMEI."""
    template = templates.get(codigo_regra, {})
    valores = _valores_template(incidente, equipamento, parametros)

    telefone = ""
    chassi_sga = None
    if equipamento is not None:
        telefone = normalizar_telefone_e164(
            equipamento.get(f"col_{COL_RASTREADORES_CELULAR}", "")
        ).get("e164") or ""
        chassi_sga = equipamento.get(f"col_{COL_RASTREADORES_CHASSI}", "").strip() or None

    if chassi:
        tipo_identificador, identificador = TIPO_IDENTIFICADOR_CHASSI, chassi
    else:
        tipo_identificador, identificador = TIPO_IDENTIFICADOR_PLACA, incidente.get("Placa", "")

    return {
        "id": incidente.get("ID", ""),
        "placa": incidente.get("Placa", ""),
        "modelo": equipamento.get(f"col_{COL_RASTREADORES_MODELO_VEICULO}", "") if equipamento else "",
        "chassi": chassi or "",
        "chassi_sga": chassi_sga,
        "imei": incidente.get("Imei", ""),
        "cliente": incidente.get("Cliente", ""),
        "codigo_regra": codigo_regra,
        "acao_sugerida": str(template.get("template_acao") or "").format_map(valores),
        "observacao_sistema": str(template.get("template_observacao") or "").format_map(valores),
        "nivel_urgencia": template.get("nivel_urgencia"),
        "data_incidente": incidente.get("Data do incidente", ""),
        "evento": incidente.get("Evento", ""),
        "telefone": telefone,
        "tipo_identificador": tipo_identificador,
        "identificador": identificador,
    }


def _equipamento_para_abertura(equipamento: dict) -> dict:
    """Formato normalizado de quem entra em `grupo_1_abrir`, usado tanto
    pelo scan do Grupo 1 quanto pela reabertura de REGRA_ALERTA_CLIENTE.
    `chassi_sga` é sempre igual a `chassi` aqui — vem direto do próprio
    `equipamento` (Rastreadores Ativos), sem ambiguidade de dedup (ver
    `_montar_linha_resultado`)."""
    return {
        "placa": equipamento.get(f"col_{COL_RASTREADORES_PLACA}", ""),
        "chassi": equipamento.get(f"col_{COL_RASTREADORES_CHASSI}", ""),
        "chassi_sga": equipamento.get(f"col_{COL_RASTREADORES_CHASSI}", "") or None,
        "imei": equipamento.get(f"col_{COL_RASTREADORES_IMEI}", ""),
        "cliente": equipamento.get(f"col_{COL_RASTREADORES_CLIENTE}", ""),
    }


def classificar_incidentes(incidentes: list[dict], equipamentos: list[dict],
                            instalacao_remocao: list[dict], parametros: dict,
                            templates: dict, agora: datetime | None = None) -> dict:
    """Retorna um dict com as chaves 'grupo_1_abrir', 'grupo_2_concluir',
    'grupo_3_tratativa_humana' — cada uma uma lista de registros já com
    ação sugerida e observação preenchidas.

    Ordem de execução (ver `docs/regras_negocio.md`):
    1. Dedup silencioso (`REGRA_4`/`REGRA_4_TIMESTAMP`) — roda antes de
       tudo, inclusive de `REGRA_ALERTA_CLIENTE`.
    2. Por incidente sobrevivente: `REGRA_ALERTA_CLIENTE` primeiro
       (resolução independente); se não bater, a cascata principal.
    3. Grupo 1 (scan de abertura automática), ao final.

    `agora` é opcional (default `datetime.now()`) só pra permitir teste
    determinístico — a assinatura pública continua a combinada no plano.
    """
    agora = agora or datetime.now()
    limiar_horas = int(parametros.get("tempo_limiar_inatividade_horas", 48))

    resultado = {"grupo_1_abrir": [], "grupo_2_concluir": [], "grupo_3_tratativa_humana": []}

    for incidente in _filtrar_duplicados(incidentes, equipamentos, parametros):
        equipamento = _buscar_equipamento_por_placa(incidente.get("Placa", ""), equipamentos)
        if equipamento is None:
            equipamento = _buscar_equipamento_por_imei(incidente.get("Imei", ""), equipamentos)
        chassi = _resolver_chassi(incidente, equipamentos, parametros)

        alerta = _verificar_alerta_cliente(incidente, equipamentos, agora, limiar_horas)
        if alerta is not None:
            linha = _montar_linha_resultado(
                incidente, alerta["codigo_regra"], equipamento, chassi, templates, parametros
            )
            linha["cliente"] = alerta["cliente_atual"]
            resultado["grupo_2_concluir"].append(linha)
            if alerta["abrir_novo_incidente"] and equipamento is not None:
                resultado["grupo_1_abrir"].append(_equipamento_para_abertura(equipamento))
            continue

        codigo_regra = _classificar_incidente_existente(incidente, equipamentos, parametros, agora)
        if codigo_regra is None:
            continue

        if codigo_regra == "REGRA_5_3" and _chassi_na_lista_retirada(chassi, instalacao_remocao):
            linha = _montar_linha_resultado(incidente, codigo_regra, equipamento, chassi, templates, parametros)
            resultado["grupo_2_concluir"].append(linha)
            continue

        linha = _montar_linha_resultado(incidente, codigo_regra, equipamento, chassi, templates, parametros)
        if codigo_regra == "REGRA_2":
            resultado["grupo_2_concluir"].append(linha)
        else:
            resultado["grupo_3_tratativa_humana"].append(linha)

    resultado["grupo_1_abrir"].extend(
        _equipamento_para_abertura(e) for e in _scan_grupo_1(equipamentos, incidentes, parametros, agora)
    )
    return resultado


# --- Segunda passada: aplica o SGA sobre o resultado de classificar_incidentes ---

def _sobrescrever_linha_com_sga(linha: dict, codigo_regra: str, status_sga: str, templates: dict) -> dict:
    """Reaproveita a `linha` já montada (não o incidente/equipamento cru —
    essa passada roda depois, só tem a saída de `classificar_incidentes`
    à mão) e troca a classificação a partir de `templates`, igual
    `_montar_linha_resultado` faz na primeira passada."""
    template = templates.get(codigo_regra, {})
    valores = _FormatoSeguro(
        placa_incidente=linha.get("placa", ""),
        cliente_incidente=linha.get("cliente", ""),
        imei_incidente=linha.get("imei", ""),
        id_recente=linha.get("id", ""),
        status_sga=status_sga,
    )
    nova_linha = dict(linha)
    nova_linha["codigo_regra"] = codigo_regra
    nova_linha["acao_sugerida"] = str(template.get("template_acao") or "").format_map(valores)
    nova_linha["observacao_sistema"] = str(template.get("template_observacao") or "").format_map(valores)
    nova_linha["nivel_urgencia"] = template.get("nivel_urgencia")
    return nova_linha


def _montar_linha_divergencia_manutencao(linha_com_sga: dict, status: str, templates: dict) -> dict:
    """Monta uma linha da aba "Análise de Divergência - Manutenção"
    (2026-08-25) a partir da `linha` já sobrescrita no branch `else` de
    `aplicar_situacoes_sga` (SGA divergente de ATIVO/NÃO ENCONTRADO --
    ex: INATIVO, INADIMPLENTE, CANCELADO). Não muda o fechamento
    automático em si (`REGRA_SGA_INATIVO` continua fechando o incidente
    sozinho) -- só dá visibilidade da ocorrência (equipamento que devia
    ter sido removido fisicamente e não foi). Mesmo espírito de
    `core.motor_regras_instalacao_remocao._montar_linha_divergencia_
    remocao`, mas sem "Motivo" (só existe 1 causa possível aqui)."""
    template = templates.get(CODIGO_REGRA_MANUTENCAO_DIVERGENCIA_SGA, {})
    valores = _FormatoSeguro(status_sga=status)
    return {
        "chassi": linha_com_sga.get("chassi", ""),
        "placa": linha_com_sga.get("placa", ""),
        "cliente": linha_com_sga.get("cliente", ""),
        "evento": linha_com_sga.get("evento", ""),
        "status_sga": status,
        "observacao": str(template.get("template_observacao") or "").format_map(valores),
        "acao": str(template.get("template_acao") or "").format_map(valores),
    }


def aplicar_situacoes_sga(resultado: dict, situacoes_sga: dict[str, dict], templates: dict) -> dict:
    """Segunda passada, depois de `classificar_incidentes` — roda só
    quando o SGA já foi consultado (decisão do usuário, 2026-08-07: o
    motor de regras precisa rodar antes de abrir o Grupo 1, então o SGA
    só entra depois, numa passada separada, não dentro da cascata).

    Pra cada linha de `grupo_2_concluir`/`grupo_3_tratativa_humana` cujo
    `chassi` tem entrada conhecida em `situacoes_sga`
    (`{chassi_maiusculo: {"status":..., "cidade":..., "bairro":...}}` —
    mesmo formato devolvido por `integrations.sga_bot.consultar_situacao`):
        status == STATUS_SGA_ATIVO          -> mantém a classificação
                                                 original, mas já ganha
                                                 `sga`/`cidade`/`bairro`.
        status == STATUS_SGA_NAO_ENCONTRADO -> força `REGRA_SGA_NAO_ENCONTRADO`
                                                 (vira/permanece grupo_3).
        qualquer outro status               -> força `REGRA_SGA_INATIVO`
                                                 (vira/permanece grupo_2) —
                                                 só veículo ativo precisa de
                                                 tratamento; se reativar, o
                                                 sistema recaptura sozinho
                                                 nos critérios que já existem.
                                                 Também gera um item em
                                                 `divergencias_manutencao`
                                                 (2026-08-25) — dá
                                                 visibilidade do fechamento
                                                 automático (equipamento
                                                 que devia ter sido
                                                 removido fisicamente e não
                                                 foi) sem mudar o
                                                 comportamento.
    Essa regra tem prioridade sobre qualquer classificação da cascata
    original, mesmo uma já resolvida automaticamente (ex: REGRA_2).
    Chassi sem entrada em `situacoes_sga` (não consultado) mantém a
    classificação original, sem `sga`/`cidade`/`bairro`. `grupo_1_abrir`
    nunca é afetado — esses ainda nem existem como incidente nesta mesma
    execução.
    """
    grupo_2: list[dict] = []
    grupo_3: list[dict] = []
    divergencias_manutencao: list[dict] = []
    candidatos = [(linha, "grupo_2_concluir") for linha in resultado["grupo_2_concluir"]]
    candidatos += [(linha, "grupo_3_tratativa_humana") for linha in resultado["grupo_3_tratativa_humana"]]

    for linha, grupo_original in candidatos:
        info_sga = situacoes_sga.get((linha.get("chassi") or "").upper())
        if info_sga is None:
            (grupo_2 if grupo_original == "grupo_2_concluir" else grupo_3).append(linha)
            continue

        status = info_sga.get("status")
        linha_com_sga = {
            **linha,
            "sga": status,
            "cidade": info_sga.get("cidade", ""),
            "bairro": info_sga.get("bairro", ""),
            "encontrado_via": info_sga.get("encontrado_via"),
        }
        if status == STATUS_SGA_ATIVO:
            (grupo_2 if grupo_original == "grupo_2_concluir" else grupo_3).append(linha_com_sga)
        elif status == STATUS_SGA_NAO_ENCONTRADO:
            grupo_3.append(_sobrescrever_linha_com_sga(linha_com_sga, CODIGO_REGRA_SGA_NAO_ENCONTRADO, status, templates))
        else:
            grupo_2.append(_sobrescrever_linha_com_sga(linha_com_sga, CODIGO_REGRA_SGA_INATIVO, status, templates))
            divergencias_manutencao.append(_montar_linha_divergencia_manutencao(linha_com_sga, status, templates))

    return {
        "grupo_1_abrir": resultado["grupo_1_abrir"],
        "grupo_2_concluir": grupo_2,
        "grupo_3_tratativa_humana": grupo_3,
        "divergencias_manutencao": divergencias_manutencao,
    }
