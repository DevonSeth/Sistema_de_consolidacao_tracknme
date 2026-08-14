import os
import sys
import logging
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from sheet_manager import conectar_sheets

# ------------------------------------------------------------
# Configurações
# ------------------------------------------------------------
URL_LOGIN = "https://broquelrastreamento.tracknme.com.br/monitoring/login"
URL_TRACKBACK = "https://broquelrastreamento.tracknme.com.br/monitoring/trackback"
NOME_PLANILHA = "INCIDENTES"
ABA_PRINCIPAL = "INCIDENTES_ABRIR"

WORKERS_SIMULTANEOS = 3
TIMEOUT_LOGIN = 300_000
MAX_RETRIES_POR_TAREFA = 2

TIMEOUT_AUTOCOMPLETE = 10_000
TIMEOUT_PAINEL_PLACA = 8_000
TIMEOUT_MODAL = 10_000
TIMEOUT_GOTO = 15_000
TIMEOUT_POPUP = 5_000

DEBUG = True
PASTA_SCREENSHOTS = "screenshots_broquel"
os.makedirs(PASTA_SCREENSHOTS, exist_ok=True)

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
async def tirar_screenshot(page, nome):
    if DEBUG:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        caminho = os.path.join(PASTA_SCREENSHOTS, f"{timestamp}_{nome}.png")
        try:
            await page.screenshot(path=caminho, full_page=True)
            logging.debug(f"Screenshot salvo: {caminho}")
        except Exception as e:
            logging.error(f"Erro ao salvar screenshot {nome}: {e}")

async def fechar_popup_release(page):
    try:
        popup = await page.wait_for_selector('div.modalRelease', state='visible', timeout=TIMEOUT_POPUP)
        if popup:
            for seletor in ['button[aria-label="Fechar"]', 'button[aria-label="Close"]', 'div.modalRelease button']:
                try:
                    await page.click(seletor, timeout=2000)
                    break
                except:
                    continue
            else:
                await page.keyboard.press('Escape')
            await page.wait_for_timeout(500)
    except PlaywrightTimeoutError:
        pass

async def preparar_pagina(page):
    await fechar_popup_release(page)
    if not await page.locator('#autoSuggest').is_visible():
        await page.click('[data-testid="search"]', timeout=5000)
        await page.wait_for_selector('#autoSuggest', state='visible', timeout=TIMEOUT_GOTO)
    await page.click('#autoSuggest', timeout=3000)
    await page.wait_for_timeout(300)

async def fechar_painel(page):
    try:
        await page.click('button[data-testid="close"]', timeout=3000)
        await page.wait_for_timeout(500)
        logging.debug("Painel fechado com botão data-testid='close'")
    except PlaywrightTimeoutError:
        logging.warning("Botão close não encontrado, tentando fechar com tecla Escape")
        await page.keyboard.press('Escape')
        await page.wait_for_timeout(500)

# ------------------------------------------------------------
# Função principal com interação robusta no modal
# ------------------------------------------------------------
async def abrir_incidente(page, placa, cliente):
    await preparar_pagina(page)
    logging.info(f"Iniciando processo para placa {placa}, cliente {cliente}")

    # 1. Digitar placa
    try:
        await page.fill('#autoSuggest', '')
        await page.type('#autoSuggest', placa, delay=80)
        await page.press('#autoSuggest', 'Backspace')
        await page.press('#autoSuggest', placa[-1])
        await page.wait_for_timeout(800)
    except Exception as e:
        await tirar_screenshot(page, "erro_preencher_campo")
        return f"Erro ao preencher campo: {e}"

    # 2. Aguardar dropdown
    try:
        await page.wait_for_selector('div[role="tooltip"] ul[role="listbox"]', state='visible', timeout=TIMEOUT_AUTOCOMPLETE)
    except PlaywrightTimeoutError:
        await tirar_screenshot(page, "dropdown_nao_apareceu")
        return "Placa não encontrada (dropdown não apareceu)"

    # 3. Coletar itens e filtrar pelo cliente
    itens = await page.query_selector_all('div[role="tooltip"] ul[role="listbox"] li[role="option"]')
    if not itens:
        await tirar_screenshot(page, "nenhum_item_dropdown")
        return "Placa não encontrada (nenhum item)"

    cliente_lower = cliente.strip().lower()
    indices_validos = []
    for idx, item in enumerate(itens):
        texto = await item.text_content()
        if texto and cliente_lower in texto.lower():
            indices_validos.append(idx)

    if not indices_validos:
        await tirar_screenshot(page, "cliente_nao_encontrado")
        return "Cliente não encontrado pelo nome"

    # 4. Tentar cada item válido
    placa_confirmada = False
    for tentativa, idx in enumerate(indices_validos):
        if tentativa > 0:
            await fechar_painel(page)
            await page.click('#autoSuggest', timeout=3000)
            await page.fill('#autoSuggest', '')
            await page.type('#autoSuggest', placa, delay=80)
            await page.wait_for_timeout(800)
            try:
                await page.wait_for_selector('div[role="tooltip"] ul[role="listbox"]', state='visible', timeout=TIMEOUT_AUTOCOMPLETE)
            except:
                await tirar_screenshot(page, "reabrir_dropdown_falhou")
                return "FALHA ao reabrir dropdown"
            itens = await page.query_selector_all('div[role="tooltip"] ul[role="listbox"] li[role="option"]')
            if idx >= len(itens):
                continue
            item = itens[idx]

        # Clica no item do dropdown
        try:
            await item.click()
            await page.wait_for_timeout(1000)
            logging.debug("Item do dropdown clicado")
        except Exception as e:
            logging.warning(f"Falha ao clicar no item do dropdown: {e}")
            continue

        # Clica no item correspondente na lista lateral
        try:
            await page.wait_for_selector('li[data-testid^="device_"]', state='visible', timeout=TIMEOUT_PAINEL_PLACA)
            item_lateral = page.locator(f'li[data-testid^="device_"] p.mdv-body-content-item-title[title="{placa}"]').first
            if await item_lateral.count() == 0:
                item_lateral = page.locator(f'li[data-testid^="device_"] p.mdv-body-content-item-title[title*="{placa}"]').first
                if await item_lateral.count() == 0:
                    raise Exception("Item lateral não encontrado")
            await item_lateral.click()
            logging.debug("Item da lista lateral clicado")
            await page.wait_for_timeout(1000)
            # Aguarda o botão Incidentes ficar habilitado
            await page.wait_for_selector('span[title="Criar Incidente"]:not(.disabled)', state='visible', timeout=5000)
            placa_confirmada = True
            break
        except Exception as e:
            logging.warning(f"Falha ao selecionar na lista lateral: {e}")
            await tirar_screenshot(page, f"erro_lista_lateral_tentativa_{tentativa+1}")
            continue

    if not placa_confirmada:
        await fechar_painel(page)
        await tirar_screenshot(page, "placa_nao_confirmada")
        return "Veículo consta com contrato inativo, verificar manualmente"

    # 5. Clicar em "Incidentes"
    try:
        await page.click('span[title="Criar Incidente"]', timeout=5000)
        logging.debug("Botão Incidentes clicado")
        await page.wait_for_timeout(1000)
    except PlaywrightTimeoutError:
        await tirar_screenshot(page, "botao_incidentes_nao_encontrado")
        await fechar_painel(page)
        return "FALHA: botão Incidentes"

    # 6. Aguardar modal aparecer (usando o botão Criar como referência)
    try:
        await page.wait_for_selector('#btn-create-incident', state='visible', timeout=TIMEOUT_MODAL)
        logging.debug("Modal aberto (botão Criar visível)")
        await tirar_screenshot(page, "modal_aberto")
    except PlaywrightTimeoutError:
        await tirar_screenshot(page, "modal_nao_abriu")
        await fechar_painel(page)
        return "FALHA: modal não abriu (botão Criar não visível)"

    # 7. Abrir dropdown de tipos (usando o id do campo)
    try:
        # Primeiro, clicar no campo de seleção (possui id "mui-component-select-description")
        await page.click('#mui-component-select-description', timeout=5000)
        logging.debug("Dropdown de tipos aberto via id")
        await page.wait_for_timeout(500)
    except Exception as e:
        # Fallback: tentar o seletor por role
        try:
            await page.click('div[role="dialog"] div.MuiSelect-root[role="button"]', timeout=5000)
            logging.debug("Dropdown de tipos aberto via role")
            await page.wait_for_timeout(500)
        except Exception as e2:
            await tirar_screenshot(page, "dropdown_tipos_nao_abriu")
            await fechar_painel(page)
            return f"FALHA: dropdown de tipos ({e2})"

    # 8. Selecionar opção "Sem comunicação por 48 horas"
    try:
        # Aguarda o item com data-value específico
        await page.wait_for_selector('li[role="option"][data-value="NO_COMMUNICATION_48HS"]', state='visible', timeout=5000)
        await page.click('li[role="option"][data-value="NO_COMMUNICATION_48HS"]')
        logging.debug("Opção 'Sem comunicação por 48 horas' selecionada via data-value")
        await page.wait_for_timeout(500)
    except PlaywrightTimeoutError:
        # Fallback: tentar pelo texto
        try:
            await page.click('li:has-text("Sem comunicação por 48 horas")', timeout=5000)
            logging.debug("Opção selecionada via fallback textual")
        except Exception as e:
            await tirar_screenshot(page, "opcao_tipo_nao_encontrada")
            await fechar_painel(page)
            return f"FALHA: tipo incidente ({e})"

    # 9. Clicar em Criar
    try:
        await page.click('#btn-create-incident', timeout=5000)
        logging.debug("Botão Criar clicado")
    except Exception as e:
        # Fallback: usar texto do botão
        try:
            await page.click('button:has-text("Criar")', timeout=5000)
            logging.debug("Botão Criar (fallback) clicado")
        except Exception as e2:
            await tirar_screenshot(page, "botao_criar_nao_encontrado")
            await fechar_painel(page)
            return f"FALHA: botão Criar ({e2})"

    # 10. Aguardar redirecionamento e voltar
    try:
        # Aguarda a URL conter "/incident" (indica que o incidente foi criado)
        await page.wait_for_url(lambda url: "/incident" in url, timeout=15000)
        logging.debug("Redirecionado para página do incidente")
        # Clica em Voltar
        await page.click('button:has-text("Voltar")', timeout=5000)
        await page.wait_for_load_state('networkidle')
        logging.debug("Voltou para trackback")
    except Exception as e:
        logging.warning(f"Erro no redirecionamento/voltar: {e}")
        # Tenta voltar manualmente
        try:
            await page.go_back()
        except:
            pass

    # 11. Fechar painel
    await fechar_painel(page)
    await tirar_screenshot(page, "sucesso_incidente_aberto")
    return "Incidente aberto"

# ------------------------------------------------------------
# Worker, Gravador, Main (inalterados)
# ------------------------------------------------------------
async def worker_bot(worker_id, fila_tarefas, fila_atualizacao, context, page):
    while True:
        tarefa = await fila_tarefas.get()
        if tarefa is None:
            break
        _, placa, cliente = tarefa
        resultado = None
        for attempt in range(1, MAX_RETRIES_POR_TAREFA + 1):
            try:
                if page.is_closed():
                    page = await context.new_page()
                await page.goto(URL_TRACKBACK, wait_until='domcontentloaded')
                await preparar_pagina(page)
                resultado = await abrir_incidente(page, placa, cliente)
                if resultado and "FALHA" not in resultado and "erro" not in resultado.lower():
                    break
            except Exception as e:
                logging.error(f"[W{worker_id}] Tentativa {attempt} para {placa}: {e}")
                await tirar_screenshot(page, f"erro_worker_{worker_id}_tentativa_{attempt}")
                if attempt == MAX_RETRIES_POR_TAREFA:
                    resultado = f"FALHA PERMANENTE ({e})"
                else:
                    await asyncio.sleep(2 ** attempt)
        if resultado is None:
            resultado = "FALHA PERMANENTE"
        await fila_atualizacao.put((placa, resultado))
        logging.info(f"[W{worker_id}] {placa} -> {resultado}")
        fila_tarefas.task_done()
    await page.close()

async def gravador(ws, fila_atualizacao, col_placa, col_resultado):
    while True:
        item = await fila_atualizacao.get()
        if item is None:
            fila_atualizacao.task_done()
            break
        placa, resultado = item
        try:
            cell = await asyncio.to_thread(ws.find, placa.strip(), in_column=col_placa)
            if cell:
                await asyncio.to_thread(ws.update_cell, cell.row, col_resultado, resultado)
            else:
                logging.error(f"Placa '{placa}' não encontrada na planilha")
        except Exception as e:
            logging.error(f"Erro ao gravar '{placa}': {e}")
        fila_atualizacao.task_done()

def obter_indice_coluna(ws, nome):
    for i, cab in enumerate(ws.row_values(1), start=1):
        if cab.strip().lower() == nome.strip().lower():
            return i
    raise ValueError(f"Coluna '{nome}' não encontrada.")

async def main():
    gc = await asyncio.to_thread(conectar_sheets)
    sh = await asyncio.to_thread(gc.open, NOME_PLANILHA)
    ws = await asyncio.to_thread(sh.worksheet, ABA_PRINCIPAL)

    col_placa = obter_indice_coluna(ws, "Placa do veículo")
    col_resultado = obter_indice_coluna(ws, "Resultado")

    dados = await asyncio.to_thread(ws.get_all_records)
    processados = sum(1 for r in dados if str(r.get('Resultado', '')).strip() != '')
    if processados > 0:
        print(f"\n--- CHECKPOINT: {processados} registros já processados ---")
        resp = input("Reiniciar (r) ou Continuar (c)? ").strip().lower()
        if resp == 'r':
            total_rows = ws.row_count
            if total_rows > 1:
                range_res = ws.range(2, col_resultado, total_rows, col_resultado)
                for c in range_res:
                    c.value = ''
                await asyncio.to_thread(ws.update_cells, range_res)
            dados = await asyncio.to_thread(ws.get_all_records)

    fila_tarefas = asyncio.Queue()
    for i, row in enumerate(dados):
        if str(row.get('Resultado', '')).strip() == "":
            placa = str(row.get('Placa do veículo', '')).strip()
            cliente = str(row.get('Cliente', '')).strip()
            if placa and cliente:
                fila_tarefas.put_nowait((i, placa, cliente))

    total_tarefas = fila_tarefas.qsize()
    if total_tarefas == 0:
        logging.info("Nenhuma tarefa pendente.")
        return
    logging.info(f"Tarefas: {total_tarefas}")

    fila_atualizacao = asyncio.Queue()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=300)
        workers_data = []
        for i in range(WORKERS_SIMULTANEOS):
            print(f"\n--- ABA {i+1}/{WORKERS_SIMULTANEOS}: LOGIN (5 min) ---")
            try:
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await page.goto(URL_LOGIN)
                await page.wait_for_selector('#autoSuggest', timeout=TIMEOUT_LOGIN)
                print(f"Login {i+1} OK.")
                await preparar_pagina(page)
                workers_data.append((ctx, page))
            except Exception as e:
                logging.error(f"Erro fatal no worker {i+1}: {e}")
                await browser.close()
                return

        print("\nIniciando processamento...")
        t_gravador = asyncio.create_task(gravador(ws, fila_atualizacao, col_placa, col_resultado))
        workers = [asyncio.create_task(worker_bot(i+1, fila_tarefas, fila_atualizacao, ctx, pg)) for i, (ctx, pg) in enumerate(workers_data)]

        await fila_tarefas.join()
        for _ in range(WORKERS_SIMULTANEOS):
            await fila_tarefas.put(None)
        await asyncio.gather(*workers)
        await fila_atualizacao.put(None)
        await t_gravador
        await browser.close()

    logging.info("Concluído.")

if __name__ == "__main__":
    asyncio.run(main())