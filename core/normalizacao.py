"""
Funções de normalização — puras, sem I/O, fáceis de testar isoladamente.

    normalizar_telefone_e164(telefone_bruto) -> dict
        Trata o problema do 9º dígito na normalização para o Newmo/Zapio.

    normalizar_placa(placa_bruta) -> str | None
        Remove espaço/traço, uppercase. Detecta placeholders conhecidos
        (lista vem de system_parameters.placas_genericas no Supabase).

    validar_formato_placa(placa) -> bool
        Confere padrão antigo (ABC1234) ou Mercosul (ABC1D23) via regex.

    formatar_data_br_sem_hora(valor) -> str
        Normaliza qualquer formato de data já visto no pipeline (ISO com
        hora/timezone do Supabase, BR com ou sem hora vindo da origem) pra
        dd/mm/aaaa, sem hora.
"""

import re
from datetime import date, datetime

_REGEX_PLACA_ANTIGA = re.compile(r"^[A-Z]{3}[0-9]{4}$")
_REGEX_PLACA_MERCOSUL = re.compile(r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$")

_FORMATOS_DATA_BR = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y")


def normalizar_telefone_e164(telefone_bruto: str) -> dict:
    """Normaliza um telefone brasileiro para E.164 (+55DDNNNNNNNNN),
    tratando a regra do 9º dígito do celular (números antigos de 8 dígitos
    que já eram celular ganham o 9 na frente; fixos de 8 dígitos não).

    Retorna:
        {"e164": "+5581987654321", "valido": True, "motivo_invalido": None}
        ou, se não for possível normalizar:
        {"e164": None, "valido": False, "motivo_invalido": "<razão>"}
    """
    digitos = re.sub(r"\D", "", telefone_bruto or "")

    if not digitos:
        return {"e164": None, "valido": False, "motivo_invalido": "telefone vazio"}

    if digitos.startswith("0"):
        digitos = digitos[1:]

    # "55" também é DDD válido (noroeste do RS) — só remove como código do
    # país quando o tamanho indica que ele está sobrando (12/13 dígitos).
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]

    if len(digitos) not in (10, 11):
        return {
            "e164": None,
            "valido": False,
            "motivo_invalido": f"quantidade de dígitos inválida ({len(digitos)})",
        }

    ddd, numero_local = digitos[:2], digitos[2:]

    if len(numero_local) == 8:
        if numero_local[0] in "6789":
            numero_local = "9" + numero_local
        elif numero_local[0] not in "2345":
            return {
                "e164": None,
                "valido": False,
                "motivo_invalido": f"número local de 8 dígitos com formato desconhecido ({numero_local})",
            }
    elif len(numero_local) == 9:
        if numero_local[0] != "9":
            return {
                "e164": None,
                "valido": False,
                "motivo_invalido": "número de 9 dígitos não começa com 9",
            }
    else:
        return {
            "e164": None,
            "valido": False,
            "motivo_invalido": f"número local com tamanho inesperado ({len(numero_local)})",
        }

    return {"e164": f"+55{ddd}{numero_local}", "valido": True, "motivo_invalido": None}


def normalizar_placa(placa_bruta: str, placas_genericas: list[str]) -> str | None:
    """Remove espaço/traço, uppercase; retorna None se `placa_bruta` for
    vazia ou for um placeholder conhecido (placas_genericas)."""
    if not placa_bruta:
        return None

    limpa = re.sub(r"[\s\-]", "", placa_bruta).upper()
    genericas_normalizadas = {re.sub(r"[\s\-]", "", g).upper() for g in placas_genericas}

    if limpa in genericas_normalizadas:
        return None

    return limpa


def validar_formato_placa(placa: str) -> bool:
    """Confere padrão antigo (ABC1234) ou Mercosul (ABC1D23) via regex."""
    p = (placa or "").strip().upper()
    return bool(_REGEX_PLACA_ANTIGA.match(p) or _REGEX_PLACA_MERCOSUL.match(p))


def formatar_data_br_sem_hora(valor) -> str:
    """dd/mm/aaaa, sem hora, a partir de qualquer formato de data já visto
    no pipeline: `date`/`datetime` nativo, BR com ou sem hora (origem dos
    incidentes/contratos), ISO com timezone (colunas de timestamp do
    Supabase). Texto que não bate com nenhum formato conhecido volta
    inalterado — nunca lança exceção nem apaga um dado real por causa de um
    formato de origem inesperado."""
    if not valor:
        return ""
    if isinstance(valor, (date, datetime)):
        return valor.strftime("%d/%m/%Y")

    texto = str(valor).strip()
    for formato in _FORMATOS_DATA_BR:
        try:
            return datetime.strptime(texto, formato).strftime("%d/%m/%Y")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(texto).strftime("%d/%m/%Y")
    except ValueError:
        return texto
