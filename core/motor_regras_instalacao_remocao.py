"""
Motor de regras de Instalação/Remoção — cascata de classificação de
pendências de instalação e remoção de rastreadores. Mesmo padrão
arquitetural de `core/motor_regras.py` (funções puras, sem I/O, `agora`
injetável pra testabilidade, texto sempre vindo de `rule_templates`,
nunca hardcoded aqui). Ver `docs/regras_negocio_instalacao_remocao.md`
pra especificação de negócio completa (16 códigos de regra).

Diferença proposital em relação a `core/motor_regras.py`: não existe
aqui o conceito de "sistema resolve automaticamente" (grupo_1_abrir/
grupo_2_concluir) — toda regra que bate gera uma tratativa pra revisão
humana, por isso `classificar_instalacao_remocao` retorna listas
simples, não um dict de grupos.

`REGRA_INSTALACAO_JA_FEITA` é a única exceção ao "toda regra vira
tratativa": chassi já instalado e sem divergência de titularidade é
limpeza de cadastro, não atendimento — por isso `classificar_instalacao_
remocao` retorna um PAR de listas (`tratativas`,
`divergencias_instalacao`), a segunda isolada da esteira normal.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from core.constants import (
    COL_RASTREADORES_CHASSI,
    COL_RASTREADORES_CLIENTE,
    COL_RASTREADORES_DATA_INSTALACAO,
    COL_RASTREADORES_IMEI,
    COL_RASTREADORES_MODELO_EQUIPAMENTO,
    ORIGEM_INSTALACAO,
    ORIGEM_REMOCAO,
    STATUS_SGA_ATIVO,
    TIPO_IDENTIFICADOR_CHASSI,
)
from core.normalizacao import normalizar_telefone_e164

_FORMATOS_DATA = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y")
_REGEX_CILINDRADA = re.compile(r"\d{2,4}")

_LIMIAR_CILINDRADA_RISCO = 300
_LIMIAR_FIPE_RISCO = 80_000

# (dias mínimo, nome do tier) — checados do maior pro menor, primeiro
# que bater decide (ver `_tier`).
_TIER_INSTALACAO = ((31, "CRITICO"), (11, "ATRASO"), (1, "NORMAL"))
_TIER_REMOCAO = ((21, "URGENTE"), (11, "ALTA"), (1, "NORMAL"))


class _FormatoSeguro(dict):
    """Usado com `str.format_map` pra não quebrar quando um template de
    produção referenciar um placeholder que não foi antecipado aqui."""

    def __missing__(self, chave):
        return ""


# --- Etapa 1: parsing de dados brutos da planilha ---

def _parse_data(valor: str) -> date | None:
    valor = (valor or "").strip()
    if not valor:
        return None
    for formato in _FORMATOS_DATA:
        try:
            return datetime.strptime(valor, formato).date()
        except ValueError:
            continue
    return None


def _dias_desde(data: date | None, agora: datetime) -> int | None:
    if data is None:
        return None
    return (agora.date() - data).days


def _tier(dias: int, tabela: tuple) -> str:
    for minimo, nome in tabela:
        if dias >= minimo:
            return nome
    return tabela[-1][1]


def _parse_valor_fipe(valor: str) -> float | None:
    """Aceita número puro ('75000', formato confirmado ao vivo na
    planilha real) ou moeda BR ('R$ 45.000,00'), pra não quebrar se o
    administrador digitar formatado."""
    texto = (valor or "").strip().replace("R$", "").strip()
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _cilindradas_excecoes(parametros: dict) -> dict[str, int]:
    """`system_parameters.cilindradas_excecoes` chega como CSV de pares
    'MODELO=CC' (ex: 'DOMINAR 400=400') — vazio até o primeiro caso que
    a extração automática errar precisar de correção manual."""
    valor = parametros.get("cilindradas_excecoes", "")
    if isinstance(valor, dict):
        return valor
    excecoes = {}
    for par in str(valor).split(","):
        par = par.strip()
        if not par or "=" not in par:
            continue
        modelo, cilindrada = par.split("=", 1)
        modelo = modelo.strip().upper()
        if modelo and cilindrada.strip().isdigit():
            excecoes[modelo] = int(cilindrada.strip())
    return excecoes


def _extrair_cilindrada(modelo: str, excecoes: dict[str, int]) -> int | None:
    """Cilindrada embutida no texto do `Modelo` (ex: 'CG 160 FAN FLEX'
    -> 160) — primeiro número de 2-4 dígitos no texto. `excecoes` tem
    prioridade sobre a extração automática (decisão fechada, ver doc de
    regras)."""
    modelo_normalizado = (modelo or "").strip().upper()
    for chave, cilindrada in excecoes.items():
        if chave in modelo_normalizado:
            return cilindrada
    encontrado = _REGEX_CILINDRADA.search(modelo_normalizado)
    return int(encontrado.group()) if encontrado else None


def _modelos_alto_risco_furto(parametros: dict) -> list[str]:
    valor = parametros.get("modelos_alto_risco_furto", "")
    if isinstance(valor, list):
        return valor
    return [item.strip().upper() for item in str(valor).split(",") if item.strip()]


def _modelo_alto_risco_furto(modelo: str, parametros: dict) -> bool:
    modelo_normalizado = (modelo or "").strip().upper()
    if not modelo_normalizado:
        return False
    return any(item in modelo_normalizado for item in _modelos_alto_risco_furto(parametros))


def _modelos_removiveis(parametros: dict) -> list[str]:
    """`system_parameters.modelos_removiveis` (Bloco B, 2026-08-24) —
    exclusiva de Remoção, não reaproveita `modelos_alto_risco_furto`
    (listas com propósitos diferentes: risco de furto vs. modelo de
    equipamento elegível pra remoção)."""
    valor = parametros.get("modelos_removiveis", "")
    if isinstance(valor, list):
        return valor
    return [item.strip().upper() for item in str(valor).split(",") if item.strip()]


def _modelo_removivel(modelo_equipamento: str, parametros: dict) -> bool:
    """Lista vazia/não configurada bloqueia tudo (decisão fechada com o
    usuário) — até o Admin configurar `modelos_removiveis`, nenhuma
    remoção vira tratativa, todas caem em
    `REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO`."""
    modelos = _modelos_removiveis(parametros)
    if not modelos:
        return False
    modelo_normalizado = (modelo_equipamento or "").strip().upper()
    if not modelo_normalizado:
        return False
    return any(item in modelo_normalizado for item in modelos)


def _parse_tier(valor, default: tuple) -> tuple:
    """`system_parameters.tier_instalacao`/`tier_remocao` chegam como CSV
    de pares 'dias=NOME' (ex: '31=CRITICO,11=ATRASO,1=NORMAL') — sempre
    reordenado do maior dia mínimo pro menor, porque `_tier` depende
    dessa ordem pra decidir corretamente (primeiro que bater vence)."""
    if isinstance(valor, tuple):
        return valor
    if not valor:
        return default
    pares = []
    for item in str(valor).split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        minimo_str, nome = item.split("=", 1)
        minimo_str = minimo_str.strip()
        nome = nome.strip()
        if minimo_str.isdigit() and nome:
            pares.append((int(minimo_str), nome))
    if not pares:
        return default
    return tuple(sorted(pares, key=lambda par: par[0], reverse=True))


def _tier_instalacao(parametros: dict) -> tuple:
    return _parse_tier(parametros.get("tier_instalacao", ""), _TIER_INSTALACAO)


def _tier_remocao(parametros: dict) -> tuple:
    return _parse_tier(parametros.get("tier_remocao", ""), _TIER_REMOCAO)


# --- Etapa 2: critérios de negócio ---

def _veiculo_de_risco(registro: dict, parametros: dict) -> bool:
    """Moto de cilindrada >=300cc, ou carro com FIPE > R$80mil OU
    modelo entre os mais furtados (qualquer um dos dois já classifica).
    Critério fechado com o usuário — 'não é moto' já basta pra cair na
    lógica de carro, não depende do rótulo exato de `Tipo Veículo`
    (confirmado ao vivo: vem como 'Passeio', não 'Carro'). Limiares
    editáveis via `system_parameters` (`limiar_cilindrada_risco_cc`/
    `limiar_fipe_risco`), mesmo padrão de `core.motor_regras`
    (`int(parametros.get(chave, default))`)."""
    tipo_veiculo = (registro.get("Tipo Veículo") or "").strip().lower()
    modelo = registro.get("Modelo", "")
    if "moto" in tipo_veiculo:
        cilindrada = _extrair_cilindrada(modelo, _cilindradas_excecoes(parametros))
        limiar_cilindrada = int(parametros.get("limiar_cilindrada_risco_cc", _LIMIAR_CILINDRADA_RISCO))
        return cilindrada is not None and cilindrada >= limiar_cilindrada
    fipe = _parse_valor_fipe(registro.get("Valor FIPE", ""))
    limiar_fipe = float(parametros.get("limiar_fipe_risco", _LIMIAR_FIPE_RISCO))
    if fipe is not None and fipe > limiar_fipe:
        return True
    return _modelo_alto_risco_furto(modelo, parametros)


def _titularidade_diverge(nome_associado: str, cliente_cadastro: str) -> bool:
    nome = (nome_associado or "").strip().upper()
    cliente = (cliente_cadastro or "").strip().upper()
    if not nome or not cliente:
        return False
    return nome != cliente


def _resolver_origem(servico: str) -> str | None:
    """Mesmo padrão substring já usado em `core.motor_regras.
    _chassi_na_lista_retirada` pro campo `Serviço`."""
    texto = (servico or "").strip().lower()
    if not texto:
        return None
    if "retir" in texto or "remo" in texto:
        return ORIGEM_REMOCAO
    return ORIGEM_INSTALACAO


# --- Etapa 3: busca em Rastreadores Ativos e consolidação ---

def _buscar_equipamento_por_chassi(chassi: str, equipamentos: list[dict]) -> dict | None:
    """Busca em Rastreadores Ativos o equipamento com o chassi idêntico
    ao informado — aqui o chassi já vem direto na linha de Instalação-
    Remoção, não precisa da cascata de fallback usada em Manutenção."""
    chassi_normalizado = (chassi or "").strip().upper()
    if not chassi_normalizado:
        return None
    for equipamento in equipamentos:
        if equipamento.get(f"col_{COL_RASTREADORES_CHASSI}", "").strip().upper() == chassi_normalizado:
            return equipamento
    return None


def chassi_ja_instalado(chassi: str, equipamentos: list[dict]) -> bool:
    """True se o chassi já aparece em Rastreadores Ativos — mesma
    condição de `REGRA_INSTALACAO_JA_FEITA`, exposta pra uso fora deste
    módulo (`orchestrator.pipeline`): detectar quando uma instalação já
    escalada pra Puma foi concluída por fora."""
    return _buscar_equipamento_por_chassi(chassi, equipamentos) is not None


def _consolidar_por_chassi(registros: list[dict]) -> list[dict]:
    """O administrador só insere linhas novas, nunca apaga — o
    registro mais recente por chassi prevalece. 'Mais recente' = a
    última ocorrência na ordem de leitura da planilha (ordem de
    inserção), não `Data contrato` (dado de negócio, não timestamp de
    inserção)."""
    mais_recente_por_chassi: dict[str, dict] = {}
    for registro in registros:
        chassi = (registro.get("Chassi") or "").strip().upper()
        if not chassi:
            continue
        mais_recente_por_chassi[chassi] = registro
    return list(mais_recente_por_chassi.values())


# --- Etapa 4: cascata por origem ---

def _classificar_instalacao(registro: dict, equipamento: dict | None, parametros: dict,
                             agora: datetime) -> tuple[str, int | None] | None:
    if equipamento is not None:
        cliente_cadastro = equipamento.get(f"col_{COL_RASTREADORES_CLIENTE}", "")
        if _titularidade_diverge(registro.get("Nome Associado", ""), cliente_cadastro):
            return "REGRA_TITULARIDADE", None
        return "REGRA_INSTALACAO_JA_FEITA", None

    dias = _dias_desde(_parse_data(registro.get("Data contrato", "")), agora)
    if dias is None:
        return None
    tier = _tier(dias, _tier_instalacao(parametros))
    risco = _veiculo_de_risco(registro, parametros)
    if risco and tier in ("ATRASO", "CRITICO"):
        return "REGRA_PRAZO_E_RISCO", dias
    if risco:
        return "REGRA_RISCO", dias
    return f"REGRA_PRAZO_{tier}", dias


def _classificar_remocao(registro: dict, equipamento: dict | None, situacao_sga: dict | None,
                          agora: datetime, parametros: dict | None = None) -> tuple[str, int | None] | None:
    """Gating: só existe pendência de remoção pro sistema depois que o
    SGA confirmar `INATIVO` ou `ATIVO` pra esse chassi (decisão já
    fechada — 'SGA é o gatilho de negócio'). O nível de urgência final
    vem sempre dos dias desde `INATIVO` — a divergência (chassi ainda
    ativo / nome diferente / modelo não permitido) só muda qual
    código/texto é usado, nunca o nível (confirmado com o usuário).
    `parametros` é opcional (default `None` -> trata como `{}`,
    preservando as faixas de dias padrão) só pra não quebrar quem já
    chama esta função direto sem esse argumento.

    Bloco B (2026-08-24), ordem de precedência (mais específico primeiro,
    mesma arquitetura de cascata do resto do módulo):
    1. Sem `situacao_sga`/status vazio -> `None` (sem dado ainda).
    2. `status == ATIVO` (literal, não qualquer não-INATIVO) ->
       `REGRA_REMOCAO_SGA_ATIVO`, sem tier/dias -- divergência pura, não
       compete na esteira de urgência (mesmo espírito de
       `REGRA_INSTALACAO_JA_FEITA`).
    3. Qualquer outro status (nem `ATIVO` nem `INATIVO`) -> `None`
       (gating original, sem mudança).
    4. `status == INATIVO`: titularidade divergente tem precedência
       sobre o filtro de modelo (decisão do usuário) -> se bater, sempre
       `REGRA_REMOCAO_TITULARIDADE_{tier}`, mesmo que o modelo também
       não seja permitido. Senão, `REGRA_REMOCAO_PRAZO_{tier}` (sem
       equipamento) ou `REGRA_REMOCAO_ATIVA_{tier}` (com equipamento) --
       o filtro de modelo (`REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO`) só
       se aplica quando HÁ equipamento encontrado pra checar o modelo
       (decisão revertida 2026-08-25: até então, sem equipamento
       encontrado também bloqueava — dado real da 1ª esteira A→E
       pós-reset mostrou que isso escondia 2199 das 2213 divergências de
       Remoção como "modelo não permitido" quando na verdade era
       "equipamento não encontrado", deixando quase nenhuma remoção virar
       tratativa de disparo de verdade; usuário confirmou reverter).
    """
    if not situacao_sga:
        return None
    status = situacao_sga.get("status")
    if status == STATUS_SGA_ATIVO:
        return "REGRA_REMOCAO_SGA_ATIVO", None
    if status != "INATIVO":
        return None

    desde = situacao_sga.get("desde")
    if desde is None:
        return None
    desde_data = desde.date() if isinstance(desde, datetime) else desde
    dias = (agora.date() - desde_data).days
    tier = _tier(dias, _tier_remocao(parametros or {}))

    if equipamento is None:
        return f"REGRA_REMOCAO_PRAZO_{tier}", dias

    cliente_cadastro = equipamento.get(f"col_{COL_RASTREADORES_CLIENTE}", "")
    if _titularidade_diverge(registro.get("Nome Associado", ""), cliente_cadastro):
        return f"REGRA_REMOCAO_TITULARIDADE_{tier}", dias

    modelo_equipamento = equipamento.get(f"col_{COL_RASTREADORES_MODELO_EQUIPAMENTO}", "")
    if not _modelo_removivel(modelo_equipamento, parametros or {}):
        return "REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO", dias
    return f"REGRA_REMOCAO_ATIVA_{tier}", dias


def _classificar_registro(registro: dict, equipamento: dict | None, situacao_sga: dict | None,
                           parametros: dict, origem: str, agora: datetime) -> tuple[str, int | None] | None:
    if origem == ORIGEM_INSTALACAO:
        return _classificar_instalacao(registro, equipamento, parametros, agora)
    if origem == ORIGEM_REMOCAO:
        return _classificar_remocao(registro, equipamento, situacao_sga, agora, parametros)
    return None


# --- Etapa 5: montagem do resultado ---

def _valores_template(registro: dict, equipamento: dict | None, dias: int | None) -> _FormatoSeguro:
    valores = _FormatoSeguro(
        dias=dias if dias is not None else "",
        nome_associado=registro.get("Nome Associado", ""),
        chassi_incidente=registro.get("Chassi", ""),
        placa_incidente=registro.get("Placa", ""),
    )
    if equipamento is not None:
        valores["cliente_cadastro"] = equipamento.get(f"col_{COL_RASTREADORES_CLIENTE}", "")
    return valores


def _montar_linha_resultado(registro: dict, codigo_regra: str, equipamento: dict | None,
                             origem: str, dias: int | None, situacao_sga: dict | None,
                             templates: dict) -> dict:
    """Monta a linha de resultado com `acao_sugerida`/`observacao_sistema`
    preenchidos a partir de `templates` (nunca hardcoded aqui — mesma
    decisão já fechada em Manutenção, `docs/regras_negocio.md`).

    `cpf`/`situacao`/`data_contrato` alimentam `core.dedup.
    gerar_chave_unica` ("instalacao"/"remocao") na Fase E. `telefone` vem
    de `celular` (a aba já traz o número — sem fallback pra `Celular
    Aux`/`Telefone Comercial`, decisão já fechada). `cidade`/`bairro`
    vêm da própria aba (preenchidos pelo administrador), não do SGA —
    diferente de Manutenção, que não tem outra fonte. `tipo_identificador`
    é sempre chassi aqui (chassi sempre presente na linha). `sga` é o
    status vivo já consultado (`situacao_sga`, mesmo dict usado pro
    gating de remoção). `modelo` vem direto da própria aba — usado por
    `core.mensagens` como fallback quando a placa é fictícia/ausente."""
    template = templates.get(codigo_regra, {})
    valores = _valores_template(registro, equipamento, dias)
    telefone = normalizar_telefone_e164(registro.get("celular", "")).get("e164") or ""
    chassi = registro.get("Chassi", "")
    return {
        "chassi": chassi,
        "placa": registro.get("Placa", ""),
        "modelo": registro.get("Modelo", ""),
        "cliente": registro.get("Nome Associado", ""),
        "origem": origem,
        "codigo_regra": codigo_regra,
        "acao_sugerida": str(template.get("template_acao") or "").format_map(valores),
        "observacao_sistema": str(template.get("template_observacao") or "").format_map(valores),
        "nivel_urgencia": template.get("nivel_urgencia"),
        "cpf": registro.get("CPF", ""),
        "situacao": registro.get("Situação", ""),
        "data_contrato": registro.get("Data contrato", ""),
        "telefone": telefone,
        "cidade": registro.get("Cidade", ""),
        "bairro": registro.get("Bairro", ""),
        "tipo_identificador": TIPO_IDENTIFICADOR_CHASSI,
        "identificador": chassi,
        "sga": situacao_sga.get("status", "") if situacao_sga else "",
    }


_MOTIVO_DIVERGENCIA_INSTALACAO = {
    "REGRA_INSTALACAO_JA_FEITA": "Instalação já concluída",
    "REGRA_TITULARIDADE": "Titularidade divergente",
}

_MOTIVO_DIVERGENCIA_REMOCAO = {
    "REGRA_REMOCAO_SGA_ATIVO": "SGA confirma veículo ainda ativo",
    "REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO": "Modelo de equipamento fora da lista permitida para remoção",
    "REGRA_REMOCAO_TITULARIDADE": "Titularidade divergente",
}


def _montar_linha_divergencia(registro: dict, equipamento: dict | None, templates: dict,
                               codigo_regra: str) -> dict:
    """Monta uma linha da aba "Análise de Divergência - Instalação" —
    `REGRA_INSTALACAO_JA_FEITA` (chassi já instalado, sem divergência de
    titularidade, mas ainda esquecido em Instalação-Remoção) ou
    `REGRA_TITULARIDADE` (chassi já instalado, nome diverge — Bloco B,
    2026-08-24: deixou de virar tratativa, é limpeza de cadastro/aviso
    administrativo, não atendimento ao associado). Nunca vai pra
    Tratativas — por isso não carrega telefone/cidade/bairro/sga/
    nivel_urgencia (campos que só fazem sentido pra tratativa de
    atendimento). `cpf`/`situacao` só alimentam `core.dedup.
    gerar_chave_unica` na Fase E, calculado no orchestrator (este módulo
    continua sem importar `core.dedup`)."""
    template = templates.get(codigo_regra, {})
    valores = _valores_template(registro, equipamento, None)
    equipamento = equipamento or {}
    return {
        "chassi": registro.get("Chassi", ""),
        "placa": registro.get("Placa", ""),
        "cliente_cadastro": registro.get("Nome Associado", ""),
        "cliente_rastreadores": equipamento.get(f"col_{COL_RASTREADORES_CLIENTE}", ""),
        "data_contrato": registro.get("Data contrato", ""),
        "data_instalacao": equipamento.get(f"col_{COL_RASTREADORES_DATA_INSTALACAO}", ""),
        "imei": equipamento.get(f"col_{COL_RASTREADORES_IMEI}", ""),
        "motivo": _MOTIVO_DIVERGENCIA_INSTALACAO.get(codigo_regra, ""),
        "observacao": str(template.get("template_observacao") or "").format_map(valores),
        "acao": str(template.get("template_acao") or "").format_map(valores),
        "cpf": registro.get("CPF", ""),
        "situacao": registro.get("Situação", ""),
    }


def _montar_linha_divergencia_remocao(registro: dict, equipamento: dict | None,
                                       situacao_sga: dict | None, templates: dict,
                                       codigo_regra: str) -> dict:
    """Monta uma linha da aba "Análise de Divergência - Remoção" (Bloco
    B, 2026-08-24) — `REGRA_REMOCAO_SGA_ATIVO` (SGA ainda confirma
    ATIVO), `REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO` (modelo do
    equipamento fora da lista configurável, ou equipamento não
    encontrado) ou `REGRA_REMOCAO_TITULARIDADE_*` (nome diverge). Mesmo
    espírito de `_montar_linha_divergencia`: relatório mecânico, sem
    telefone/cidade/bairro/nivel_urgencia. `cpf`/`situacao`/
    `data_contrato` alimentam `core.dedup.gerar_chave_unica("remocao",
    ...)` no orchestrator."""
    template = templates.get(codigo_regra, {})
    valores = _valores_template(registro, equipamento, None)
    equipamento = equipamento or {}
    motivo_chave = "REGRA_REMOCAO_TITULARIDADE" if codigo_regra.startswith("REGRA_REMOCAO_TITULARIDADE_") else codigo_regra
    return {
        "chassi": registro.get("Chassi", ""),
        "placa": registro.get("Placa", ""),
        "cliente_cadastro": registro.get("Nome Associado", ""),
        "cliente_rastreadores": equipamento.get(f"col_{COL_RASTREADORES_CLIENTE}", ""),
        "modelo_equipamento": equipamento.get(f"col_{COL_RASTREADORES_MODELO_EQUIPAMENTO}", ""),
        "status_sga": situacao_sga.get("status", "") if situacao_sga else "",
        "motivo": _MOTIVO_DIVERGENCIA_REMOCAO.get(motivo_chave, ""),
        "observacao": str(template.get("template_observacao") or "").format_map(valores),
        "acao": str(template.get("template_acao") or "").format_map(valores),
        "cpf": registro.get("CPF", ""),
        "situacao": registro.get("Situação", ""),
        "data_contrato": registro.get("Data contrato", ""),
    }


def atualizar_situacao_sga(chassi: str, status_novo: str, registro_anterior: dict | None,
                            agora: datetime, encontrado_via: str | None = None,
                            cidade: str = "", bairro: str = "") -> dict:
    """Transformação pura pra manter `situacao_veiculo_sga` (Supabase):
    se o status mudou (ou não havia registro), `desde` reinicia agora;
    se é o mesmo status de antes, `desde` não muda, só `atualizado_em`.
    Não faz I/O — quem lê/grava no Supabase é o orchestrator, ainda não
    implementado.

    `encontrado_via` ("chassi"/"placa", opcional -- ver
    `integrations.sga_bot.consultar_situacao`) e `cidade`/`bairro` (Bloco
    C1, 2026-08-24 -- antes só existiam na consulta ao vivo, nunca eram
    persistidos, então uma tratativa que reaproveitasse o checkpoint de
    24h ficava sem esse dado) são só repassados pro registro persistido,
    sem influenciar `desde` -- não fazem parte da regra de negócio."""
    status_anterior = registro_anterior.get("status") if registro_anterior else None
    desde_anterior = registro_anterior.get("desde") if registro_anterior else None
    desde = agora if (status_anterior != status_novo or desde_anterior is None) else desde_anterior
    return {
        "chassi": chassi, "status": status_novo, "desde": desde, "atualizado_em": agora,
        "encontrado_via": encontrado_via, "cidade": cidade, "bairro": bairro,
    }


_CODIGOS_DIVERGENCIA_INSTALACAO = {"REGRA_INSTALACAO_JA_FEITA", "REGRA_TITULARIDADE"}
_CODIGOS_DIVERGENCIA_REMOCAO = {"REGRA_REMOCAO_SGA_ATIVO", "REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO"}


def classificar_instalacao_remocao(registros: list[dict], equipamentos: list[dict],
                                    situacoes_sga: dict[str, dict], parametros: dict,
                                    templates: dict,
                                    agora: datetime | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """Classifica as pendências de Instalação/Remoção. Retorna um trio
    `(tratativas, divergencias_instalacao, divergencias_remocao)`:

    - `tratativas`: lista simples (não 3 grupos como `core.motor_regras.
      classificar_incidentes`) — toda regra que bate aqui é revisão
      humana, não existe resolução automática pelo sistema. Alimenta a
      aba "Tratativas".
    - `divergencias_instalacao`: `REGRA_INSTALACAO_JA_FEITA` (chassi já
      instalado, sem divergência de titularidade, mas ainda esquecido em
      Instalação-Remoção) e `REGRA_TITULARIDADE` (chassi já instalado,
      nome diverge — Bloco B, 2026-08-24) — nunca entram em
      `tratativas`; vão pra aba própria "Análise de Divergência -
      Instalação" (decisão de negócio: é limpeza de cadastro/aviso
      administrativo, não atendimento ao associado).
    - `divergencias_remocao` (Bloco B, 2026-08-24): `REGRA_REMOCAO_
      SGA_ATIVO`, `REGRA_REMOCAO_EQUIPAMENTO_NAO_PERMITIDO` e
      `REGRA_REMOCAO_TITULARIDADE_*` — mesmo espírito, aba própria
      "Análise de Divergência - Remoção".

    `situacoes_sga` é um dict pré-calculado `{chassi_maiusculo:
    {"status":, "desde":}}` (chave em upper) — este módulo não consulta
    o SGA nem o Supabase, mantém-se puro/sem I/O.
    """
    agora = agora or datetime.now()
    resultado = []
    divergencias_instalacao = []
    divergencias_remocao = []
    for registro in _consolidar_por_chassi(registros):
        origem = _resolver_origem(registro.get("Serviço", ""))
        if origem is None:
            continue
        chassi = (registro.get("Chassi") or "").strip()
        equipamento = _buscar_equipamento_por_chassi(chassi, equipamentos)
        situacao_sga = situacoes_sga.get(chassi.upper()) if chassi else None

        classificacao = _classificar_registro(registro, equipamento, situacao_sga, parametros, origem, agora)
        if classificacao is None:
            continue
        codigo_regra, dias = classificacao
        if codigo_regra in _CODIGOS_DIVERGENCIA_INSTALACAO:
            divergencias_instalacao.append(_montar_linha_divergencia(registro, equipamento, templates, codigo_regra))
            continue
        if codigo_regra in _CODIGOS_DIVERGENCIA_REMOCAO or codigo_regra.startswith("REGRA_REMOCAO_TITULARIDADE_"):
            divergencias_remocao.append(
                _montar_linha_divergencia_remocao(registro, equipamento, situacao_sga, templates, codigo_regra)
            )
            continue
        resultado.append(
            _montar_linha_resultado(registro, codigo_regra, equipamento, origem, dias, situacao_sga, templates)
        )
    return resultado, divergencias_instalacao, divergencias_remocao
