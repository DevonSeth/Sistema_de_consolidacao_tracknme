"""
Geração da chave única de deduplicação — muda de fórmula por origem:

    Instalação/Remoção -> cpf + chassi + situacao + data_contrato
    Manutenção          -> placa + evento

`dados` já vem em snake_case (a conversão dos nomes de coluna em português
da planilha para essas chaves acontece antes, no orchestrator — este módulo
não conhece Sheets).

A string concatenada leva `origem` como primeiro campo (evita colisão entre
origens diferentes mesmo em coincidência absurda de dados) e é reduzida a um
hash sha256 truncado em 16 caracteres hex. Esse mesmo valor alimenta tanto
`tratativas.chave_unica` no Supabase quanto a coluna "ID (hash)" da planilha
Operacional — são o mesmo dado, dois lugares.

Achado 2026-08-24: `data_incidente` (Track N'Me) já fez parte desta chave
até este fix, mas o Track N'Me atualiza esse timestamp periodicamente
enquanto o incidente segue aberto — cada atualização mintava uma chave nova
pro mesmo veículo/evento, deixando a tratativa anterior órfã pra sempre
(nada nunca a fechava). Removido do hash; `data_incidente` continua sendo
exibido/persistido normalmente, só não entra mais no cálculo da chave (ver
Bloco H, migração em `core/dedup.py`/`orchestrator/pipeline.py`).

Exemplo (Manutenção):
    gerar_chave_unica("manutencao", {
        "placa": "XYZ9A87",
        "evento": "Sem comunicação",
    })
    == sha256("manutencao|XYZ9A87|Sem comunicação")[:16]
"""

import hashlib
import re

from core.constants import ORIGEM_INSTALACAO, ORIGEM_MANUTENCAO, ORIGEM_REMOCAO
from core.normalizacao import normalizar_placa

TAMANHO_HASH = 16

_CAMPOS_POR_ORIGEM = {
    ORIGEM_INSTALACAO: ["cpf", "chassi", "situacao", "data_contrato"],
    ORIGEM_REMOCAO: ["cpf", "chassi", "situacao", "data_contrato"],
    ORIGEM_MANUTENCAO: ["placa", "evento"],
}


def _normalizar_campo(campo: str, valor) -> str:
    if valor is None:
        return ""
    if campo == "cpf":
        return re.sub(r"\D", "", valor)
    if campo == "chassi":
        return valor.strip().upper()
    if campo == "placa":
        return normalizar_placa(valor, placas_genericas=[]) or ""
    if campo == "situacao":
        return valor.strip().lower()
    return str(valor).strip()


def gerar_chave_unica(origem: str, dados: dict) -> str:
    campos = _CAMPOS_POR_ORIGEM.get(origem)
    if campos is None:
        raise ValueError(f"origem desconhecida: {origem!r}")

    faltando = [campo for campo in campos if campo not in dados]
    if faltando:
        raise KeyError(
            f"dados faltando campo(s) obrigatório(s) para origem {origem!r}: {faltando}"
        )

    valores = [origem] + [_normalizar_campo(campo, dados[campo]) for campo in campos]
    bruta = "|".join(valores)
    return hashlib.sha256(bruta.encode()).hexdigest()[:TAMANHO_HASH]
