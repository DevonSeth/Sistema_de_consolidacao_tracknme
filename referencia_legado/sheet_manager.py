import gspread
import os

def conectar_sheets():
    # 1. Pega o caminho absoluto deste script (scripts/sheet_manager.py)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 2. Sobe um nível para chegar na raiz do projeto (projeto-sga-automação/)
    base_dir = os.path.dirname(script_dir)
    # 3. Constrói o caminho correto para a pasta config
    json_path = os.path.join(base_dir, 'config', 'credenciais.json')
    
    # Autenticação
    gc = gspread.service_account(filename=json_path)
    return gc

def ler_incidentes(nome_planilha, nome_aba):
    gc = conectar_sheets()
    sh = gc.open(nome_planilha)
    worksheet = sh.worksheet(nome_aba)
    
    # Retorna todos os dados como uma lista de dicionários
    return worksheet.get_all_records()

if __name__ == "__main__":
    # Teste de conexão
    try:
        dados = ler_incidentes('INCIDENTES', 'INCIDENTES_TRATAR')
        print(f"Sucesso! Conectado. Total de linhas lidas: {len(dados)}")
    except Exception as e:
        print(f"Erro na conexão: {e}")