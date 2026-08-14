import os
import sys
import logging
import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from sheet_manager import conectar_sheets

# ------------------------------------------------------------
# Configurações
# ------------------------------------------------------------
URL_SGA = "https://orion.hinova.com.br/sga/sgav4_pumabeneficios/veiculo/consultarVeiculo.php"
NOME_PLANILHA = "INCIDENTES"
ABA_PRINCIPAL = "INCIDENTES_TRATAR"
ABA_CHASSI = "INPUT_EQUIPAMENTOS"
WORKERS_SIMULTANEOS = 3
COLUNA_SGA = 26                # Coluna onde será escrito o status (índice 1‑based)
TIMEOUT_LOGIN = 300_000        # 5 minutos para login manual
MAX_RETRIES_POR_TAREFA = 2     # Tentativas por veículo

# Timeouts ajustados
TIMEOUT_DROPDOWN = 8_000
TIMEOUT_ANCORA = 12_000
TIMEOUT_STATUS = 5_000
TIMEOUT_CIDADE = 8_000         # Aumentado para garantir carregamento
TIMEOUT_GOTO = 15_000

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# ------------------------------------------------------------
# Busca robusta com extração da cidade (CORRIGIDA)
# ------------------------------------------------------------
async def buscar_por_chassi(page, chassi):
    campo = "#dfsChassiFiltro"

    # Remove overlays
    await page.evaluate("""() => {
        const overlays = document.querySelectorAll('.fancybox-overlay, .fancybox-overlay-fixed, .blockUI');
        overlays.forEach(el => el.remove());
    }""")

    # Limpa e preenche o campo
    await page.fill(campo, '')
    await page.fill(campo, chassi)
    await page.press(campo, 'Backspace')
    await page.press(campo, chassi[-1])
    await page.wait_for_timeout(300)

    # --- Dropdown ---
    try:
        await page.wait_for_selector('ul#as_ul li', state='visible', timeout=TIMEOUT_DROPDOWN)
        await page.press(campo, 'ArrowDown')
        await page.press(campo, 'Enter')
    except PlaywrightTimeoutError:
        try:
            valor = await page.eval_on_selector("#dfsChassi", "el => el.value.trim()")
            if valor == chassi.strip():
                pass  # chassi preenchido sem dropdown (raro)
            else:
                return {"status": "NÃO ENCONTRADO", "cidade": ""}
        except:
            return {"status": "FALHA NO CARREGAMENTO (DROPDOWN)", "cidade": ""}

    # --- Validação da âncora ---
    safe_chassi = chassi.strip().upper()
    try:
        await page.wait_for_function(
            f"""() => {{
                const el = document.querySelector("#dfsChassi");
                return el && el.value.trim().toUpperCase() === "{safe_chassi}";
            }}""",
            timeout=TIMEOUT_ANCORA
        )
    except PlaywrightTimeoutError:
        try:
            atual = await page.eval_on_selector("#dfsChassi", "el => el.value.trim()")
            logging.warning(f"Âncora incorreta: esperado '{chassi}', obtido '{atual}'")
        except:
            pass
        return {"status": "FALHA NO CARREGAMENTO (ÂNCORA)", "cidade": ""}

    # --- Status (primeiro, porque garante que a página do veículo carregou) ---
    status = "FALHA NO CARREGAMENTO (STATUS)"
    try:
        await page.wait_for_selector("#cmbSituacaoVeiculo", state='visible', timeout=TIMEOUT_STATUS)
        status = await page.eval_on_selector(
            "#cmbSituacaoVeiculo",
            "el => el.options[el.selectedIndex].text"
        )
        status = status.strip()
    except PlaywrightTimeoutError:
        # Se não carregar o status, já retorna falha, mas tenta cidade mesmo assim
        pass

    # --- Extração da cidade (agora após o status, com retry) ---
    cidade = ""
    try:
        # Aguarda o campo estar visível e com valor não vazio
        for _ in range(3):  # até 3 tentativas
            try:
                await page.wait_for_selector("#dfsCidadeCorrespondencia", state='visible', timeout=TIMEOUT_CIDADE)
                cidade = await page.eval_on_selector("#dfsCidadeCorrespondencia", """
                    el => {
                        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                            return el.value.trim();
                        }
                        return el.textContent.trim();
                    }
                """)
                if cidade:
                    break
                else:
                    await asyncio.sleep(0.5)
            except PlaywrightTimeoutError:
                await asyncio.sleep(0.5)
        if not cidade:
            cidade = "CIDADE NÃO DISPONÍVEL"
            logging.warning(f"Cidade vazia para chassi {chassi}")
    except Exception as e:
        logging.error(f"Erro ao extrair cidade para chassi {chassi}: {e}")
        cidade = "ERRO NA CIDADE"

    return {"status": status, "cidade": cidade}

# ------------------------------------------------------------
# Worker (com recarga completa e limpa)
# ------------------------------------------------------------
async def worker_bot(worker_id, fila_tarefas, fila_atualizacao, context):
    page = await context.new_page()
    # Navegação inicial com networkidle para garantir carregamento total
    await page.goto(URL_SGA, wait_until='networkidle')
    await page.wait_for_selector('#dfsChassiFiltro', state='visible', timeout=TIMEOUT_GOTO)

    while True:
        tarefa = await fila_tarefas.get()
        if tarefa is None:
            break

        index, placa, chassi = tarefa
        resultado = None

        for attempt in range(1, MAX_RETRIES_POR_TAREFA + 1):
            try:
                if page.is_closed():
                    logging.warning(f"[Worker {worker_id}] Página fechada, recriando...")
                    page = await context.new_page()

                # Recarga completa com networkidle para evitar resquícios
                await page.goto(URL_SGA, wait_until='networkidle')
                await page.wait_for_selector('#dfsChassiFiltro', state='visible', timeout=TIMEOUT_GOTO)

                resultado = await buscar_por_chassi(page, chassi)
                break
            except PlaywrightTimeoutError as e:
                logging.error(f"[Worker {worker_id}] Timeout na tentativa {attempt} para Placa {placa}: {e}")
                if attempt == MAX_RETRIES_POR_TAREFA:
                    resultado = {"status": "FALHA PERMANENTE", "cidade": ""}
                else:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logging.error(f"[Worker {worker_id}] Erro na tentativa {attempt} para Placa {placa}: {e}")
                if attempt == MAX_RETRIES_POR_TAREFA:
                    resultado = {"status": "FALHA PERMANENTE", "cidade": ""}
                else:
                    await asyncio.sleep(2 ** attempt)

        if resultado is None:
            resultado = {"status": "FALHA PERMANENTE", "cidade": ""}

        await fila_atualizacao.put((placa, resultado["status"], resultado["cidade"]))
        logging.info(f"[Worker {worker_id}] Placa {placa} -> {resultado['status']} | Cidade: {resultado['cidade']}")
        fila_tarefas.task_done()

    await page.close()

# ------------------------------------------------------------
# Gravador (inalterado)
# ------------------------------------------------------------
async def gravador(ws, fila_atualizacao, col_placa, col_sga, col_cidade):
    while True:
        item = await fila_atualizacao.get()
        if item is None:
            fila_atualizacao.task_done()
            break

        placa, status, cidade = item
        try:
            cell = await asyncio.to_thread(ws.find, placa.strip(), in_column=col_placa)
            if cell:
                row = cell.row
                await asyncio.to_thread(ws.update_cell, row, col_sga, status)
                await asyncio.to_thread(ws.update_cell, row, col_cidade, cidade)
            else:
                logging.error(f"Placa '{placa}' não encontrada na planilha. Status e cidade não gravados.")
        except Exception as e:
            logging.error(f"Erro ao gravar status/cidade para '{placa}': {e}")
        fila_atualizacao.task_done()

# ------------------------------------------------------------
# Utilitário para índice de coluna
# ------------------------------------------------------------
def obter_indice_coluna(ws, nome_coluna):
    cabecalhos = ws.row_values(1)
    for i, cab in enumerate(cabecalhos, start=1):
        if cab.strip().lower() == nome_coluna.strip().lower():
            return i
    raise ValueError(f"Coluna '{nome_coluna}' não encontrada na planilha.")

# ------------------------------------------------------------
# Main com checkpoint e integração da cidade
# ------------------------------------------------------------
async def main():
    gc = await asyncio.to_thread(conectar_sheets)
    sh = await asyncio.to_thread(gc.open, NOME_PLANILHA)
    ws = await asyncio.to_thread(sh.worksheet, ABA_PRINCIPAL)
    ws_chassi = await asyncio.to_thread(sh.worksheet, ABA_CHASSI)

    col_placa = obter_indice_coluna(ws, "Placa")
    col_sga = obter_indice_coluna(ws, "SGA")
    col_cidade = obter_indice_coluna(ws, "Cidade")

    dados = await asyncio.to_thread(ws.get_all_records)

    # Checkpoint
    processados = sum(1 for row in dados if str(row.get('SGA', '')).strip() != '')
    total = len(dados)

    if processados > 0:
        print(f"\n--- CHECKPOINT ENCONTRADO ---")
        print(f"{processados} de {total} registros já possuem status na coluna SGA.")
        while True:
            resp = input("Deseja REINICIAR tudo (limpar status e cidade) ou CONTINUAR de onde parou? (r/c): ").lower().strip()
            if resp in ('r', 'c'):
                break
            print("Opção inválida. Digite 'r' para reiniciar ou 'c' para continuar.")

        if resp == 'r':
            print("Limpando as colunas SGA e Cidade...")
            total_rows = ws.row_count
            if total_rows > 1:
                range_sga = ws.range(2, col_sga, total_rows, col_sga)
                for cell in range_sga:
                    cell.value = ''
                await asyncio.to_thread(ws.update_cells, range_sga)
                range_cidade = ws.range(2, col_cidade, total_rows, col_cidade)
                for cell in range_cidade:
                    cell.value = ''
                await asyncio.to_thread(ws.update_cells, range_cidade)
            print("Colunas limpas. Iniciando processamento completo.\n")
            dados = await asyncio.to_thread(ws.get_all_records)
        else:
            print("Continuando processamento apenas dos registros em branco...\n")
    else:
        print("Nenhum registro processado anteriormente. Iniciando do zero.\n")

    # Mapa de chassis
    col_placa_chassi = obter_indice_coluna(ws_chassi, "Placa do veículo")
    col_chassi = obter_indice_coluna(ws_chassi, "Chassi")
    all_values_chassi = await asyncio.to_thread(ws_chassi.get_all_values)
    chassi_map = {}
    for row in all_values_chassi[1:]:
        placa_val = row[col_placa_chassi - 1].strip()
        chassi_val = row[col_chassi - 1].strip()
        if placa_val and chassi_val:
            chassi_map[placa_val] = chassi_val

    fila_tarefas = asyncio.Queue()
    for i, row in enumerate(dados):
        if str(row.get('SGA', '')).strip() == "":
            placa = str(row.get('Placa', '')).strip()
            chassi = chassi_map.get(placa)
            if chassi:
                fila_tarefas.put_nowait((i, placa, chassi))

    total_tarefas = fila_tarefas.qsize()
    if total_tarefas == 0:
        logging.info("Nenhuma tarefa pendente. Encerrando.")
        return
    logging.info(f"Tarefas a processar: {total_tarefas}")

    fila_atualizacao = asyncio.Queue()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()

        page_login = await context.new_page()
        await page_login.goto(URL_SGA)
        print("--- FAÇA O LOGIN MANUALMENTE (5 minutos) ---")
        try:
            await page_login.wait_for_selector("#dfsChassiFiltro", timeout=TIMEOUT_LOGIN)
        except PlaywrightTimeoutError:
            logging.error("Tempo de login esgotado. Encerrando.")
            await browser.close()
            return
        await page_login.close()

        t_gravador = asyncio.create_task(gravador(ws, fila_atualizacao, col_placa, col_sga, col_cidade))
        workers = [
            asyncio.create_task(worker_bot(i+1, fila_tarefas, fila_atualizacao, context))
            for i in range(WORKERS_SIMULTANEOS)
        ]

        await fila_tarefas.join()

        for _ in range(WORKERS_SIMULTANEOS):
            await fila_tarefas.put(None)
        await asyncio.gather(*workers)

        await fila_atualizacao.put(None)
        await t_gravador

        await browser.close()

    logging.info("Processamento concluído com sucesso.")

if __name__ == "__main__":
    asyncio.run(main())