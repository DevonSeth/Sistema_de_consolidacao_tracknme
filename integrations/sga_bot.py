"""
Robô Playwright para o SGA (Hinova) — reescrita a partir de `sga_bot.py`
(script legado), mantendo a lógica de negócio que já está certa:

    - Busca a situação de pagamento, cidade e bairro por CHASSI ou por
      PLACA — a tela de consulta do SGA tem os 2 campos, cada um só
      aceita o tipo certo de valor. Quem chama `consultar_situacao`
      decide qual usar (achado 2026-08-16: nunca é seguro mandar um
      pseudo-identificador de dedup interno, como um IMEI, pro campo
      Chassi) — não é responsabilidade deste módulo.
    - Login SEMPRE manual (reCAPTCHA, impossível automatizar) — o robô
      abre o navegador visível, espera o humano logar, e só então
      processa a fila.
    - Checkpoint/resume: decisão já fechada (ver HANDOFF) — não cria
      tabela nova, resume reconsultando `WHERE sga IS NULL` a cada
      execução. Não é responsabilidade deste módulo.

URL de login CORRIGIDA (confirmado, 2026-08-04): o legado usava
`orion.hinova.com.br`, que está desatualizado — o domínio certo é
`sga.hinova.com.br` com o segmento `v5`. Login bem-sucedido redireciona
pra uma tela inicial própria (`.../v5/Principal/`), NÃO pra tela de busca
por chassi -- essa continua em
`https://sga.hinova.com.br/sga/sgav4_pumabeneficios/veiculo/consultarVeiculo.php`
(mesmo path do legado, SEM o segmento `v5` -- só o domínio mudou de
`orion` pra `sga`; confirmado pelo usuário, 2026-08-04).
"""

import asyncio
import base64
import random
import re

from playwright.async_api import (
    APIRequestContext,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from core.constants import STATUS_SGA_ATIVO, STATUS_SGA_NAO_ENCONTRADO, TIPO_IDENTIFICADOR_CHASSI
from integrations.playwright_utils import SessaoCaidaError

URL_LOGIN = "https://sga.hinova.com.br/sga/sgav4_pumabeneficios/v5/login.php"
# Confirmada pelo usuário (2026-08-04) -- SEM o segmento "v5", diferente da URL de login.
URL_CONSULTAR_VEICULO = "https://sga.hinova.com.br/sga/sgav4_pumabeneficios/veiculo/consultarVeiculo.php"

SELETOR_CAMPO_CHASSI_FILTRO = "#dfsChassiFiltro"
SELETOR_CAMPO_CHASSI_ANCORA = "#dfsChassi"
SELETOR_DROPDOWN_SUGESTOES = "ul#as_ul li"
SELETOR_STATUS_VEICULO = "#cmbSituacaoVeiculo"
SELETOR_CIDADE_CORRESPONDENCIA = "#dfsCidadeCorrespondencia"
# INFERIDO por analogia com o de cidade (mesmo padrão "dfs" + nome do campo
# do Hinova) -- validar ao vivo, não foi confirmado por captura ainda.
SELETOR_BAIRRO_CORRESPONDENCIA = "#dfsBairroCorrespondencia"
# INFERIDOS por analogia com o par de chassi (mesmo padrão "dfs" + nome do
# campo + "Filtro"/âncora sem sufixo) -- a tela de consulta também tem um
# campo Placa (achado 2026-08-16), mas esses 2 seletores NÃO foram
# confirmados por captura ainda -- validar ao vivo antes de confiar cegamente.
SELETOR_CAMPO_PLACA_FILTRO = "#dfsPlacaFiltro"
SELETOR_CAMPO_PLACA_ANCORA = "#dfsPlaca"

# Reexportado de core.constants (não duplicar o valor — core/motor_regras.py
# também precisa comparar contra ele, e core/ não importa integrations/).
STATUS_NAO_ENCONTRADO = STATUS_SGA_NAO_ENCONTRADO

TIMEOUT_LOGIN_MANUAL_MS = 300_000  # 5 minutos, mesmo timeout do legado
TIMEOUT_DROPDOWN_MS = 8_000
TIMEOUT_ANCORA_MS = 12_000
TIMEOUT_STATUS_MS = 5_000
TIMEOUT_CAMPO_CORRESPONDENCIA_MS = 8_000
TIMEOUT_GOTO_MS = 15_000


async def aguardar_login_manual(playwright: Playwright) -> tuple[Browser, BrowserContext]:
    """SGA não tem login automático (reCAPTCHA) -- sempre abre o navegador
    VISÍVEL na tela de login e espera o humano logar manualmente (até 5
    minutos, mesmo timeout do legado). Retorna o contexto autenticado,
    pronto pra `consultar_situacao` processar a fila.

    Detecta o login pela mudança de URL (sai de `login.php`), igual
    `tracknme_bot._fazer_login_automatico` -- não espera por
    `#dfsChassiFiltro` aqui porque a página pós-login é uma tela inicial
    própria (`.../v5/Principal/`, confirmado ao vivo em 2026-08-04), não a
    tela de busca por chassi (essa só é aberta depois, por
    `consultar_situacao`)."""
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(URL_LOGIN)
    await page.wait_for_url(lambda url: "login.php" not in url, timeout=TIMEOUT_LOGIN_MANUAL_MS)
    await page.close()
    return browser, context


async def _ir_para_consulta(page: Page) -> None:
    """Navega (ou renavega) pra tela de consulta por chassi -- sempre uma
    recarga completa antes de cada busca, igual o legado já fazia
    ("Recarga completa com networkidle para evitar resquícios")."""
    await page.goto(URL_CONSULTAR_VEICULO, wait_until="networkidle")
    if "login.php" in page.url:
        raise SessaoCaidaError("Sessão do SGA caiu (redirecionado para login.php)")
    await page.wait_for_selector(SELETOR_CAMPO_CHASSI_FILTRO, state="visible", timeout=TIMEOUT_GOTO_MS)


async def _buscar_por_identificador(
    page: Page, valor: str, seletor_filtro: str, seletor_ancora: str
) -> dict:
    """Busca `valor` no SGA usando o par filtro/âncora informado (chassi ou
    placa, mesma interação nos dois casos) e retorna
    `{'status': ..., 'cidade': ..., 'bairro': ...}`.

    `status = 'NÃO ENCONTRADO'` é um resultado de NEGÓCIO válido (valor não
    cadastrado no SGA), não uma falha -- por isso é retornado normalmente,
    sem levantar exceção. Falhas TÉCNICAS (dropdown não carregou, âncora
    não bateu, etc.) levantam `RuntimeError`, pra `processar_fila` fazer o
    retry padrão de 2 rounds (decisão #8) em vez de mascarar como um
    resultado de negócio.
    """
    await _ir_para_consulta(page)

    await page.evaluate(
        """() => {
            const overlays = document.querySelectorAll('.fancybox-overlay, .fancybox-overlay-fixed, .blockUI');
            overlays.forEach(el => el.remove());
        }"""
    )

    await page.fill(seletor_filtro, "")
    await page.fill(seletor_filtro, valor)
    await page.press(seletor_filtro, "Backspace")
    await page.press(seletor_filtro, valor[-1])
    await page.wait_for_timeout(300)

    try:
        await page.wait_for_selector(SELETOR_DROPDOWN_SUGESTOES, state="visible", timeout=TIMEOUT_DROPDOWN_MS)
        await page.press(seletor_filtro, "ArrowDown")
        await page.press(seletor_filtro, "Enter")
    except PlaywrightTimeoutError:
        valor_atual = await page.eval_on_selector(seletor_ancora, "el => el.value.trim()")
        if valor_atual.strip().upper() != valor.strip().upper():
            return {"status": STATUS_NAO_ENCONTRADO, "cidade": "", "bairro": ""}

    valor_normalizado = valor.strip().upper()
    try:
        await page.wait_for_function(
            """([seletor, valorEsperado]) => {
                const el = document.querySelector(seletor);
                return el && el.value.trim().toUpperCase() === valorEsperado;
            }""",
            arg=[seletor_ancora, valor_normalizado],
            timeout=TIMEOUT_ANCORA_MS,
        )
    except PlaywrightTimeoutError:
        raise RuntimeError(f"Âncora não confirmou depois da busca (valor={valor})")

    try:
        await page.wait_for_selector(SELETOR_STATUS_VEICULO, state="visible", timeout=TIMEOUT_STATUS_MS)
        status = await page.eval_on_selector(
            SELETOR_STATUS_VEICULO, "el => el.options[el.selectedIndex].text"
        )
        status = status.strip()
    except PlaywrightTimeoutError:
        raise RuntimeError(f"Status do veículo não carregou (valor={valor})")

    cidade = await _extrair_campo_correspondencia(page, SELETOR_CIDADE_CORRESPONDENCIA)
    bairro = await _extrair_campo_correspondencia(page, SELETOR_BAIRRO_CORRESPONDENCIA)

    return {"status": status, "cidade": cidade, "bairro": bairro}


async def consultar_situacao(page: Page, tipo: str, valor: str) -> dict:
    """Busca `valor` no SGA e retorna `{'status': ..., 'cidade': ...,
    'bairro': ..., 'encontrado_via': tipo}`.

    Achado 2026-08-16: os campos Chassi e Placa da tela do SGA são
    INDEPENDENTES — cada um só aceita o tipo certo de valor. Quem chama
    (`orchestrator.pipeline._alvos_consulta_sga`) já decidiu ANTES de
    consultar se há um chassi CONFIRMADO no cadastro (Rastreadores Ativos)
    ou só uma placa real/válida — `tipo` (`core.constants.
    TIPO_IDENTIFICADOR_CHASSI`/`_PLACA`) diz qual campo usar. Este módulo
    nunca tenta os dois nem adivinha: se `tipo` for chassi e o SGA não
    achar, "NÃO ENCONTRADO" é o resultado final — não faz sentido tentar
    de novo pela Placa (decisão do usuário, 2026-08-16): isso reintroduz a
    mesma mistura de campos que causou o achado original (mandar um IMEI,
    usado só pra dedup interno em `core.motor_regras._resolver_chassi`,
    pro campo Chassi do SGA).
    """
    if tipo == TIPO_IDENTIFICADOR_CHASSI:
        resultado = await _buscar_por_identificador(
            page, valor, SELETOR_CAMPO_CHASSI_FILTRO, SELETOR_CAMPO_CHASSI_ANCORA
        )
    else:
        resultado = await _buscar_por_identificador(
            page, valor, SELETOR_CAMPO_PLACA_FILTRO, SELETOR_CAMPO_PLACA_ANCORA
        )
    return {**resultado, "encontrado_via": tipo}


async def _extrair_campo_correspondencia(page: Page, seletor: str) -> str:
    """Cidade/Bairro de correspondência às vezes demoram a preencher
    depois do status carregar -- até 3 tentativas com espera curta entre
    elas, igual o legado já fazia pra cidade."""
    for tentativa in range(3):
        try:
            await page.wait_for_selector(seletor, state="visible", timeout=TIMEOUT_CAMPO_CORRESPONDENCIA_MS)
            valor = await page.eval_on_selector(
                seletor,
                """el => {
                    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                        return el.value.trim();
                    }
                    return el.textContent.trim();
                }""",
            )
            if valor:
                return valor
        except PlaywrightTimeoutError:
            pass
        if tentativa < 2:
            await asyncio.sleep(0.5)
    return ""


# ---------------------------------------------------------------------------
# Consulta via HTTP puro (achado 2026-08-19) -- substitui a navegação DOM por
# 2 requisições HTTP diretas, reaproveitando a sessão pós-login via
# `storage_state` (sem navegador nenhum aberto durante a consulta). Convive
# com `consultar_situacao` (Playwright) acima -- nunca a substitui: só cobre
# busca por CHASSI (o endpoint de busca por Placa nunca foi descoberto/
# validado), e o caminho Playwright continua sendo o fallback real pra Placa
# e pra qualquer degradação detectada (ver `orchestrator.pipeline`).
#
# Mecanismo (validado em 7 rodadas ao vivo contra produção, ver memória
# `sga_api_http_viabilidade_confirmada`): `carregaChassiFiltro.php` devolve
# o id interno do veículo; `key` é esse id "criptografado" por uma função
# JS pública e trivial de reverter (`cript.js`, `fCriptografa`); `carrega
# VeiculoDadosAlteracao.php?key=...` devolve HTML com os dados via
# atribuição JS inline (`form.CAMPO.value = '...';`), não um <select>/
# <input> prontos -- por isso a extração é por regex, não por parsing de
# formulário.
# ---------------------------------------------------------------------------

TAMANHO_MINIMO_HTML_VALIDO = 10_000

MAPA_STATUS_SGA_CODIGO = {
    # Legenda completa (Bloco C2, 2026-08-24) -- extraída ao vivo das opções
    # do dropdown #cmbSituacaoVeiculo (só o caminho Playwright consegue: o
    # HTTP só traz o código numérico cru via JS inline, nunca o texto). Login
    # manual no SGA, 1 veículo qualquer, `el.options` inteiro (não só o
    # selecionado) -- revela a legenda toda de uma vez, independente de qual
    # veículo tem qual status.
    "1": STATUS_SGA_ATIVO,
    "2": "INATIVO",
    "3": "PENDENTE",
    "4": "INADIMPLENTE",
    "5": "NEGADO",
    "6": "SUBSTITUIÇÃO (INATIVO)",
    "7": "MIGRADO PARA 4394",
    "8": "ORDEM DE RETIRADA",
    "9": "INATIVO/INDENIZAÇÃO",
    "10": "TENTATIVA DE FRAUDE",
    "11": "SINISTRO - FURTO/ROUBO",
    "12": "SINISTRO - PT",
    "13": "SINISTRO - NÃO COBRAR (INATIVO)",
    "14": "PROCESSO JUDICIAL",
    "15": "CANCELADO",
    "16": "SUBSTITUIÇÃO",
    "17": "PENDENTE CADASTRO",
    "18": "PENDENTE VIDEO",
    "19": "CANCELADO POR SUBSTITUIÇÃO",
    "20": "INADIMPLENTE VIDEO",
}


def _gerar_key(id_veiculo: int) -> str:
    """Reimplementação em Python de `fCriptografa(id, "INT")` do `cript.js`
    público do SGA -- ofuscação trivial (não é segurança de verdade),
    reversível por qualquer um que leia o JS. `rand` é gerado de novo a
    cada chamada (igual o JS faz), então o mesmo `id_veiculo` nunca produz
    o mesmo `key` duas vezes -- irrelevante pra decodificação do lado do
    SGA, que só olha os dígitos embutidos."""
    rand = random.randint(100, 999)
    multiplicado = id_veiculo * rand
    resto = multiplicado % 7
    concatenado = f"{multiplicado}{rand}{resto}"
    return base64.b64encode(concatenado.encode()).decode()


def _extrair_id_veiculo(xml_bytes: bytes) -> int | None:
    """`carregaChassiFiltro.php` devolve XML declarado `iso-8859-1`
    (decodificação nunca falha -- só dígitos/tags importam aqui). `None`
    se o veículo não existe no SGA (`<results></results>` vazio) --
    resultado de negócio válido, não erro técnico."""
    xml_texto = xml_bytes.decode("iso-8859-1", errors="replace")
    m = re.search(r'<rs id="(\d+)"', xml_texto)
    return int(m.group(1)) if m else None


def _extrair_campos_html(html: str) -> dict:
    """`carregaVeiculoDadosAlteracao.php` preenche o formulário via JS
    inline (`form.CAMPO.value = '...';`), não HTML pronto -- extrai os 3
    campos que hoje vêm de `consultar_situacao` (Playwright). Campo
    ausente vira string vazia, nunca levanta exceção (mesmo contrato de
    tolerância de `_extrair_campo_correspondencia`)."""
    resultado = {"status_codigo": "", "cidade": "", "bairro": ""}
    mapa_campos = {
        "cmbSituacaoVeiculo": "status_codigo",
        "dfsCidadeCorrespondencia": "cidade",
        "dfsBairroCorrespondencia": "bairro",
    }
    for campo_js, chave in mapa_campos.items():
        m = re.search(rf"form\.{campo_js}\.value = '([^']*)'", html)
        if m:
            resultado[chave] = m.group(1)
    return resultado


def _label_status(codigo: str) -> str:
    """Traduz o código numérico do status pro texto que o resto do sistema
    já espera (`core.motor_regras.aplicar_situacoes_sga` só compara contra
    `STATUS_SGA_ATIVO`/`STATUS_SGA_NAO_ENCONTRADO` -- qualquer outro texto
    já cai corretamente em "REGRA_SGA_INATIVO"). `MAPA_STATUS_SGA_CODIGO`
    tem a legenda completa (20 códigos, extraída ao vivo do dropdown do
    SGA, Bloco C2 2026-08-24) -- um código NOVO que o SGA venha a criar no
    futuro cai aqui, sem label, mas NUNCA é confundido com ATIVO/NÃO
    ENCONTRADO -- só fica menos legível na mensagem, a decisão de negócio
    permanece correta."""
    if codigo in MAPA_STATUS_SGA_CODIGO:
        return MAPA_STATUS_SGA_CODIGO[codigo]
    return f"DESCONHECIDO (código {codigo})"


async def preparar_contexto_http(
    playwright: Playwright, browser: Browser, context: BrowserContext
) -> tuple[APIRequestContext, dict]:
    """Chamado logo após `aguardar_login_manual` -- salva a sessão
    autenticada e fecha o navegador por completo -- toda consulta seguinte
    usa só HTTP (`playwright.request`, sem navegador nenhum aberto).
    Devolve `(request_context, estado)` -- `estado` é reaproveitado por
    quem chama pra reabrir um `BrowserContext` autenticado depois, sem
    pedir captcha de novo na mesma execução (ver `orchestrator.pipeline.
    etapa_enriquecimento_sga`, Estágio Playwright de fallback pra busca
    por Placa, e `abrir_contexto_com_sessao` abaixo). Quem chama é
    responsável por `.dispose()` no `APIRequestContext` devolvido, no fim
    do uso."""
    estado = await context.storage_state()
    await context.close()
    await browser.close()
    request_context = await playwright.request.new_context(storage_state=estado)
    return request_context, estado


async def abrir_contexto_com_sessao(playwright: Playwright, estado: dict) -> tuple[Browser, BrowserContext]:
    """Reabre um `BrowserContext` autenticado a partir de uma sessão já
    validada (`estado`, devolvido por `preparar_contexto_http`) -- usado
    quando o Estágio HTTP já fez o login manual (captcha) e ainda restam
    veículos que só dá pra consultar via Playwright (busca por Placa) —
    evita pedir captcha 2x na mesma execução. Headless: a sessão já está
    autenticada, não precisa de tela visível."""
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(storage_state=estado)
    return browser, context


def _verificar_resposta_http(resposta, valor: str, nome_endpoint: str) -> None:
    """`resposta.url` reflete a URL final (pós-redirecionamento) -- se o
    SGA redirecionar pra login, a sessão caiu de verdade (não é falha
    pontual do item), aciona o mesmo fluxo de reconexão manual que o
    caminho Playwright já usa."""
    if "login.php" in resposta.url:
        raise SessaoCaidaError(f"Sessão do SGA caiu (requisição HTTP redirecionada para login.php, chassi={valor})")
    if resposta.status != 200:
        raise RuntimeError(f"{nome_endpoint} devolveu status {resposta.status} (chassi={valor})")


async def consultar_situacao_http(request_context: APIRequestContext, tipo: str, valor: str) -> dict:
    """Equivalente HTTP de `consultar_situacao`, só pra busca por CHASSI —
    o endpoint de busca por Placa nunca foi descoberto/validado (achado
    2026-08-19); `orchestrator.pipeline` mantém o caminho Playwright pra
    esses casos (~2-3% do volume real). Levanta `ValueError` se chamado
    com Placa, pra nunca mascarar um uso incorreto.

    Mesmo contrato de retorno de `consultar_situacao`
    (`{status, cidade, bairro, encontrado_via}`). `id` não resolvido no
    XML é resultado de NEGÓCIO válido (veículo/pseudo-chassi não existe no
    SGA) — devolve `STATUS_NAO_ENCONTRADO` sem 2ª requisição. HTML final
    pequeno demais ou sem o campo de status (mesmo com um `id` real
    resolvido) é falha TÉCNICA (`RuntimeError`, cai no retry padrão de
    `processar_fila_http`) — nunca confundida com "não encontrado".
    """
    if tipo != TIPO_IDENTIFICADOR_CHASSI:
        raise ValueError(f"consultar_situacao_http só busca por chassi, recebeu tipo={tipo!r}")

    base_url = URL_CONSULTAR_VEICULO.rsplit("/", 2)[0]

    resposta_filtro = await request_context.get(
        f"{base_url}/carrega/carregaChassiFiltro.php", params={"input": valor}
    )
    _verificar_resposta_http(resposta_filtro, valor, "carregaChassiFiltro.php")

    id_veiculo = _extrair_id_veiculo(await resposta_filtro.body())
    if id_veiculo is None:
        return {"status": STATUS_NAO_ENCONTRADO, "cidade": "", "bairro": "", "encontrado_via": tipo}

    key = _gerar_key(id_veiculo)
    resposta_dados = await request_context.get(
        f"{base_url}/carrega/carregaVeiculoDadosAlteracao.php", params={"key": key}
    )
    _verificar_resposta_http(resposta_dados, valor, "carregaVeiculoDadosAlteracao.php")

    corpo_bytes = await resposta_dados.body()
    if len(corpo_bytes) < TAMANHO_MINIMO_HTML_VALIDO:
        raise RuntimeError(
            f"Resposta pequena demais ({len(corpo_bytes)} bytes) -- possível página de erro (chassi={valor})"
        )

    campos = _extrair_campos_html(corpo_bytes.decode("utf-8", errors="replace"))
    if not campos["status_codigo"]:
        raise RuntimeError(f"HTML não trouxe o status do veículo -- formato inesperado (chassi={valor})")

    return {
        "status": _label_status(campos["status_codigo"]),
        "cidade": campos["cidade"],
        "bairro": campos["bairro"],
        "encontrado_via": tipo,
    }
