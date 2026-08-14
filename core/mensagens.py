"""
Seleção de template/variáveis de WhatsApp (Fase F.1) — puro, sem I/O, não
conhece Newmo/Supabase/Sheets diretamente (mesmo espírito de
`core/escalonamento.py`). Especificação completa dos 14 templates (corpo,
variáveis, botões) em `docs/templates_whatsapp.md`.

    resolver_placa_para_mensagem(placa, modelo) -> str
        Regra de negócio fechada: placa vazia -> "placa não cadastrada";
        placa fictícia (começa com "SGA" ou não bate padrão antigo/Mercosul,
        via `core.normalizacao.validar_formato_placa`) -> usa `modelo` do
        veículo (ou "placa não cadastrada" se `modelo` também vazio); senão
        a própria placa.

    nome_template_logico(origem, atendimento, tentativas) -> str
        Chave interna "{familia}_{atendimento}_{tom}", desacoplada do nome
        real cadastrado no Newmo (que já mudou por colisão/categoria errada
        — ver `docs/templates_whatsapp.md` seção 10). `config["newmo"]
        ["templates"]` mapeia essa chave pro `template.codigo` numérico
        real.

    variaveis_template(nome_logico, tratativa, base, ponto_acao) -> list[str]
        Monta a lista de variáveis na ordem exata do template (seção 5.3 do
        doc). `pendencia_instalacao_manutencao_base_ultima` é uma exceção
        deliberada (só 3 variáveis, sem endereço — mudança feita pra sair
        da classificação `Marketing` na Meta, seção 10 item 5 do doc).
"""

from datetime import date, datetime

from core.constants import ORIGEM_INSTALACAO, ORIGEM_MANUTENCAO, ORIGEM_REMOCAO
from core.normalizacao import validar_formato_placa

_FAMILIA_POR_ORIGEM = {
    ORIGEM_INSTALACAO: "instalacao_manutencao",
    ORIGEM_MANUTENCAO: "instalacao_manutencao",
    ORIGEM_REMOCAO: "remocao",
}

_TIPO_SERVICO_POR_ORIGEM = {
    ORIGEM_INSTALACAO: "instalação",
    ORIGEM_MANUTENCAO: "manutenção",
}

# Slots de variável por template lógico, na ordem exata de `{{1}}`, `{{2}}`...
# (docs/templates_whatsapp.md seção 5.3). "instalacao_manutencao_base_ultima"
# é a exceção de 3 variáveis (sem "endereco_base") — ver seção 10 item 5.
_VARIAVEIS_POR_TEMPLATE = {
    "instalacao_manutencao_base_normal": ["nome", "tipo_servico", "placa", "endereco_base"],
    "instalacao_manutencao_base_ultima": ["nome", "tipo_servico", "placa"],
    "instalacao_manutencao_rota_normal": ["nome", "tipo_servico", "placa"],
    "instalacao_manutencao_rota_ultima": ["nome", "tipo_servico", "placa"],
    "instalacao_manutencao_acao_normal": ["nome", "tipo_servico", "placa", "data_acao", "endereco_acao"],
    "instalacao_manutencao_acao_ultima": ["nome", "tipo_servico", "placa", "data_acao", "endereco_acao"],
    "remocao_base_normal": ["nome", "placa", "endereco_base"],
    "remocao_base_ultima": ["nome", "placa", "endereco_base"],
    "remocao_rota_normal": ["nome", "placa"],
    "remocao_rota_ultima": ["nome", "placa"],
    "remocao_acao_normal": ["nome", "placa", "data_acao", "endereco_acao"],
    "remocao_acao_ultima": ["nome", "placa", "data_acao", "endereco_acao"],
}


def resolver_placa_para_mensagem(placa: str, modelo: str) -> str:
    placa = (placa or "").strip()
    if not placa:
        return "placa não cadastrada"
    if placa.upper().startswith("SGA") or not validar_formato_placa(placa):
        return (modelo or "").strip() or "placa não cadastrada"
    return placa


def nome_template_logico(origem: str, atendimento: str, tentativas: int) -> str:
    familia = _FAMILIA_POR_ORIGEM[origem]
    tom = "ultima" if tentativas >= 2 else "normal"
    return f"{familia}_{atendimento}_{tom}"


def _formatar_data(data) -> str:
    """`ponto_acao["data"]` vem do Supabase (coluna `date`) como `date`
    nativo ou string ISO `"aaaa-mm-dd"`, dependendo de quem chama."""
    if not data:
        return ""
    if isinstance(data, (date, datetime)):
        return data.strftime("%d/%m/%Y")
    return datetime.fromisoformat(str(data)).strftime("%d/%m/%Y")


def _valor_slot(slot: str, tratativa: dict, base: dict | None, ponto_acao: dict | None) -> str:
    if slot == "nome":
        return tratativa.get("cliente", "")
    if slot == "tipo_servico":
        return _TIPO_SERVICO_POR_ORIGEM.get(tratativa.get("origem"), "")
    if slot == "placa":
        return resolver_placa_para_mensagem(tratativa.get("placa", ""), tratativa.get("modelo", ""))
    if slot == "endereco_base":
        return (base or {}).get("endereco", "")
    if slot == "data_acao":
        return _formatar_data((ponto_acao or {}).get("data"))
    if slot == "endereco_acao":
        return (ponto_acao or {}).get("endereco", "")
    raise ValueError(f"slot de variável desconhecido: {slot!r}")


def variaveis_template(nome_logico: str, tratativa: dict, base: dict | None, ponto_acao: dict | None) -> list[str]:
    slots = _VARIAVEIS_POR_TEMPLATE[nome_logico]
    return [_valor_slot(slot, tratativa, base, ponto_acao) for slot in slots]
