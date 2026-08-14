export const metadata = {
  title: "Manual do Sistema — Painel Admin",
};

export default function ManualPage() {
  return (
    <>
      <div className="page-header">
        <h1>Manual do Sistema</h1>
        <div className="desc">
          Como usar cada tela do Painel Admin — sem precisar sair daqui pra consultar.
        </div>
      </div>

      <div className="grupo-parametros">
        <div className="grupo-titulo">Parâmetros de Negócio</div>
        <div className="grupo-desc">
          Os limiares e listas que hoje ficariam &quot;escondidos&quot; no código — editáveis
          aqui, com busca e filtro por categoria (Geral, Risco de veículo, Prazos, Esteira de
          disparo, Observabilidade). Exemplos: quantas horas um equipamento fica sem comunicar
          antes de virar incidente, quais modelos de moto contam como &quot;alto risco de
          furto&quot;, o horário de corte do disparo de WhatsApp. <strong>Editar aqui tem efeito
          imediato no próximo ciclo automático</strong> — não precisa de deploy nem reiniciar
          nada. Use a busca se não souber em qual categoria um parâmetro está; o texto de cada
          linha já explica o que ele controla.
        </div>
      </div>

      <div className="grupo-parametros">
        <div className="grupo-titulo">Cadastros</div>
        <div className="grupo-desc">
          3 abas:
        </div>
        <div className="grupo-desc">
          <strong>Bases</strong> — os locais onde o associado pode ir instalar/rastrear (nome,
          endereço, ativo/inativo). Usado no disparo de WhatsApp quando o atendimento marca
          &quot;Atendimento = Base&quot; numa pendência.
        </div>
        <div className="grupo-desc">
          <strong>Pontos de Ação</strong> — locais de ação com data marcada (mutirões, eventos).
          Mesma lógica das Bases, mas com uma data associada.
        </div>
        <div className="grupo-desc">
          <strong>Regras</strong> — as ~31 linhas que o motor de regras usa pra decidir texto de
          ação/observação e nível de urgência de cada código (REGRA_1, REGRA_5_1 etc.). Editável
          por linha: texto de observação, texto de ação, nível de urgência (1-5), ativo/inativo.
          &quot;Prioridade&quot; aparece só pra leitura — é metadado descritivo, não decide a
          ordem real da cascata de regras (isso é fixo no código).
        </div>
      </div>

      <div className="grupo-parametros">
        <div className="grupo-titulo">Configuração</div>
        <div className="grupo-desc">
          As credenciais de cada integração (Track N&apos;Me, Newmo/WhatsApp, Supabase, Google
          Sheets), guardadas no Vault (nunca em texto puro em lugar nenhum acessível). Pra cada
          uma: <strong>&quot;Testar conexão&quot;</strong> confirma que a credencial ainda
          funciona de verdade (chama a API real) — <strong>exceto Track N&apos;Me</strong>, que
          exige um navegador com captcha manual, então só é testável rodando o Painel Operador
          local. <strong>&quot;Editar&quot;</strong> abre os campos pra atualizar um valor (ex:
          token expirou, senha mudou).
        </div>
        <div className="grupo-desc">
          <strong>Importante</strong>: editar uma credencial aqui grava no Vault central, mas
          <strong> só passa a valer nas máquinas do Painel Operador quando o Launcher existir</strong>
          {" "}(ainda não construído) — até lá, cada máquina continua com a credencial que já tem
          localmente, e uma atualização feita aqui não chega lá sozinha.
        </div>
      </div>

      <div className="grupo-parametros">
        <div className="grupo-titulo">Dashboards</div>
        <div className="grupo-desc">
          Os números de negócio, com filtro De/Até no topo (a maioria respeita o filtro; algumas,
          marcadas &quot;Estado agora&quot;, mostram sempre o momento atual, ignorando o filtro —
          o rótulo da seção já avisa qual é qual). Cada métrica tem até 2 caixinhas de
          visibilidade:
        </div>
        <div className="grupo-desc">
          <strong>&quot;Visível no Dashboard Cliente&quot;</strong> — aparece pro link que a Puma
          usa pra acompanhar (sem login, sem acesso a mais nada do sistema).
        </div>
        <div className="grupo-desc">
          <strong>&quot;Visível no Painel Operador&quot;</strong> — aparece na aba &quot;Painel de
          apoio&quot; do Painel Operador local, pro atendimento acompanhar sem precisar abrir o
          Admin. Nem toda métrica tem essa opção (só as que o Painel Operador já sabe
          calcular/desenhar).
        </div>
        <div className="grupo-desc">
          Nenhuma das duas afeta o que você vê aqui no Admin — o Admin sempre mostra tudo, essas
          caixinhas só controlam o que os OUTROS 2 públicos enxergam.
        </div>
        <div className="grupo-desc">
          <strong>Baixar PDF</strong> exporta a tela atual (com o filtro aplicado) num formato de
          impressão limpo, com o cabeçalho &quot;Relatório de pendências - Viver de
          Rastreamento&quot; — usa o &quot;Imprimir&quot; do próprio navegador (Ctrl+P/Cmd+P), não
          baixa um arquivo direto; escolha &quot;Salvar como PDF&quot; na tela de impressão do
          navegador.
        </div>
      </div>

      <div className="grupo-parametros">
        <div className="grupo-titulo">Abrir Painel Operador</div>
        <div className="grupo-desc">
          Atalho pra abrir o Painel Operador já instalado nesta máquina, sem precisar procurar o
          ícone/pasta. Só funciona depois que o instalador do Painel Operador estiver pronto —
          até lá, clicar não faz nada.
        </div>
      </div>
    </>
  );
}
