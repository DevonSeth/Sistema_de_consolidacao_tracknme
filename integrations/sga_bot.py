"""
Robô Playwright para o SGA (Hinova) — reescrita a partir de `sga_bot.py`
(script legado), mantendo a lógica de negócio que já está certa:

    - Busca a situação de pagamento, cidade e bairro por CHASSI.
    - O chassi é obtido buscando a PLACA na base de Rastreadores Ativos
      (já implementado assim no legado — mantém, é responsabilidade de
      quem chama `consultar_situacao`, não deste módulo).
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

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from core.constants import STATUS_SGA_NAO_ENCONTRADO
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


async def consultar_situacao(page: Page, chassi: str) -> dict:
    """Busca `chassi` no SGA e retorna
    `{'status': ..., 'cidade': ..., 'bairro': ...}`.

    `status = 'NÃO ENCONTRADO'` é um resultado de NEGÓCIO válido (chassi
    não cadastrado no SGA), não uma falha -- por isso é retornado
    normalmente, sem levantar exceção. Falhas TÉCNICAS (dropdown não
    carregou, âncora não bateu, etc.) levantam `RuntimeError`, pra
    `processar_fila` fazer o retry padrão de 2 rounds (decisão #8) em vez
    de mascarar como um resultado de negócio.
    """
    await _ir_para_consulta(page)

    campo = SELETOR_CAMPO_CHASSI_FILTRO
    await page.evaluate(
        """() => {
            const overlays = document.querySelectorAll('.fancybox-overlay, .fancybox-overlay-fixed, .blockUI');
            overlays.forEach(el => el.remove());
        }"""
    )

    await page.fill(campo, "")
    await page.fill(campo, chassi)
    await page.press(campo, "Backspace")
    await page.press(campo, chassi[-1])
    await page.wait_for_timeout(300)

    try:
        await page.wait_for_selector(SELETOR_DROPDOWN_SUGESTOES, state="visible", timeout=TIMEOUT_DROPDOWN_MS)
        await page.press(campo, "ArrowDown")
        await page.press(campo, "Enter")
    except PlaywrightTimeoutError:
        valor_atual = await page.eval_on_selector(SELETOR_CAMPO_CHASSI_ANCORA, "el => el.value.trim()")
        if valor_atual.strip().upper() != chassi.strip().upper():
            return {"status": STATUS_NAO_ENCONTRADO, "cidade": "", "bairro": ""}

    chassi_normalizado = chassi.strip().upper()
    try:
        await page.wait_for_function(
            """(chassiEsperado) => {
                const el = document.querySelector("#dfsChassi");
                return el && el.value.trim().toUpperCase() === chassiEsperado;
            }""",
            arg=chassi_normalizado,
            timeout=TIMEOUT_ANCORA_MS,
        )
    except PlaywrightTimeoutError:
        raise RuntimeError(f"Âncora do chassi não confirmou depois da busca (chassi={chassi})")

    try:
        await page.wait_for_selector(SELETOR_STATUS_VEICULO, state="visible", timeout=TIMEOUT_STATUS_MS)
        status = await page.eval_on_selector(
            SELETOR_STATUS_VEICULO, "el => el.options[el.selectedIndex].text"
        )
        status = status.strip()
    except PlaywrightTimeoutError:
        raise RuntimeError(f"Status do veículo não carregou (chassi={chassi})")

    cidade = await _extrair_campo_correspondencia(page, SELETOR_CIDADE_CORRESPONDENCIA)
    bairro = await _extrair_campo_correspondencia(page, SELETOR_BAIRRO_CORRESPONDENCIA)

    return {"status": status, "cidade": cidade, "bairro": bairro}


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


async def _acao_consultar_situacao(page: Page, item: str) -> dict:
    return await consultar_situacao(page, item)
