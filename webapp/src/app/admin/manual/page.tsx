import type { ReactNode } from "react";

export const metadata = {
  title: "Manual do Sistema — Painel Admin",
};

function Secao({
  id,
  icone,
  titulo,
  children,
}: {
  id: string;
  icone: string;
  titulo: string;
  children: ReactNode;
}) {
  return (
    <div className="manual-secao" id={id}>
      <h2>
        <span>{icone}</span> {titulo}
      </h2>
      {children}
    </div>
  );
}

function Subcard({ titulo, children }: { titulo: string; children: ReactNode }) {
  return (
    <div className="manual-subcard">
      <div className="titulo">{titulo}</div>
      <div className="desc">{children}</div>
    </div>
  );
}

function Callout({ tom, children }: { tom?: "atencao"; children: ReactNode }) {
  return <div className={`manual-callout${tom ? ` ${tom}` : ""}`}>{children}</div>;
}

export default function ManualPage() {
  return (
    <>
      <div className="page-header">
        <h1>Manual do Sistema</h1>
        <div className="desc">
          Como usar cada tela do Painel Admin — sem precisar sair daqui pra consultar.
        </div>
      </div>

      <nav className="manual-nav no-print">
        <a className="chip" href="#parametros">
          ◧ Parâmetros
        </a>
        <a className="chip" href="#cadastros">
          ◫ Cadastros
        </a>
        <a className="chip" href="#configuracao">
          ⚙ Configuração
        </a>
        <a className="chip" href="#dashboards">
          ▤ Dashboards
        </a>
        <a className="chip" href="#operador">
          🖥 Painel Operador
        </a>
      </nav>

      <Secao id="parametros" icone="◧" titulo="Parâmetros de Negócio">
        <div className="desc">
          Os limiares e listas que hoje ficariam &quot;escondidos&quot; no código — editáveis
          aqui, com busca e filtro por categoria (Geral, Risco de veículo, Prazos, Esteira de
          disparo, Observabilidade). Exemplos: quantas horas um equipamento fica sem comunicar
          antes de virar incidente, quais modelos de moto contam como &quot;alto risco de
          furto&quot;, o horário de corte do disparo de WhatsApp. Use a busca se não souber em
          qual categoria um parâmetro está; o texto de cada linha já explica o que ele controla.
        </div>
        <Callout>
          Editar aqui tem <strong>efeito imediato no próximo ciclo automático</strong> — não
          precisa de deploy nem reiniciar nada.
        </Callout>
      </Secao>

      <Secao id="cadastros" icone="◫" titulo="Cadastros">
        <div className="desc">3 abas, cada uma com sua própria lista:</div>
        <div className="manual-subcards">
          <Subcard titulo="Bases">
            Os locais onde o associado pode ir instalar/rastrear (nome, endereço,
            ativo/inativo). Usado no disparo de WhatsApp quando o atendimento marca
            &quot;Atendimento = Base&quot; numa pendência.
          </Subcard>
          <Subcard titulo="Pontos de Ação">
            Locais de ação com data marcada (mutirões, eventos). Mesma lógica das Bases, mas
            com uma data associada.
          </Subcard>
          <Subcard titulo="Regras">
            As ~31 linhas que o motor de regras usa pra decidir texto de ação/observação e
            nível de urgência de cada código (REGRA_1, REGRA_5_1 etc.). Editável por linha:
            texto de observação, texto de ação, nível de urgência (1-5), ativo/inativo.
            &quot;Prioridade&quot; aparece só pra leitura — é metadado descritivo, não decide a
            ordem real da cascata de regras (isso é fixo no código).
          </Subcard>
        </div>
      </Secao>

      <Secao id="configuracao" icone="⚙" titulo="Configuração">
        <div className="desc">
          As credenciais de cada integração (Track N&apos;Me, Newmo/WhatsApp, Supabase, Google
          Sheets), guardadas no Vault (nunca em texto puro em lugar nenhum acessível).
        </div>
        <ol className="manual-steps">
          <li>
            <strong>&quot;Testar conexão&quot;</strong> confirma que a credencial ainda funciona
            de verdade (chama a API real) — exceto Track N&apos;Me, que exige um navegador com
            captcha manual, então só é testável rodando o Painel Operador local.
          </li>
          <li>
            <strong>&quot;Editar&quot;</strong> abre os campos pra atualizar um valor (ex: token
            expirou, senha mudou).
          </li>
        </ol>
        <Callout tom="atencao">
          Editar uma credencial aqui grava no Vault central, mas{" "}
          <strong>só passa a valer nas máquinas do Painel Operador quando o Launcher existir</strong>
          {" "}(ainda não construído) — até lá, cada máquina continua com a credencial que já tem
          localmente, e uma atualização feita aqui não chega lá sozinha.
        </Callout>
      </Secao>

      <Secao id="dashboards" icone="▤" titulo="Dashboards">
        <div className="desc">
          Os números de negócio, com filtro De/Até no topo (a maioria respeita o filtro; algumas,
          marcadas &quot;Estado agora&quot;, mostram sempre o momento atual, ignorando o filtro —
          o rótulo da seção já avisa qual é qual). Cada métrica tem até 2 caixinhas de
          visibilidade:
        </div>
        <div className="manual-subcards">
          <Subcard titulo="Visível no Dashboard Cliente">
            Aparece pro link que a Puma usa pra acompanhar (sem login, sem acesso a mais nada do
            sistema).
          </Subcard>
          <Subcard titulo="Visível no Painel Operador">
            Aparece na aba &quot;Painel de apoio&quot; do Painel Operador local, pro atendimento
            acompanhar sem precisar abrir o Admin. Nem toda métrica tem essa opção (só as que o
            Painel Operador já sabe calcular/desenhar).
          </Subcard>
        </div>
        <div className="desc">
          Nenhuma das duas afeta o que você vê aqui no Admin — o Admin sempre mostra tudo, essas
          caixinhas só controlam o que os OUTROS 2 públicos enxergam.
        </div>
        <ol className="manual-steps">
          <li>
            <strong>Baixar PDF</strong> exporta a tela atual (com o filtro aplicado) num formato
            de impressão limpo, com o cabeçalho &quot;Relatório de pendências - Viver de
            Rastreamento&quot; — usa o &quot;Imprimir&quot; do próprio navegador (Ctrl+P/Cmd+P),
            não baixa um arquivo direto.
          </li>
          <li>Escolha &quot;Salvar como PDF&quot; na tela de impressão do navegador.</li>
        </ol>
      </Secao>

      <Secao id="operador" icone="🖥" titulo="Abrir Painel Operador">
        <div className="desc">
          Atalho pra abrir o Painel Operador já instalado nesta máquina, sem precisar procurar o
          ícone/pasta. Só funciona depois que o instalador do Painel Operador estiver pronto —
          até lá, clicar não faz nada.
        </div>
      </Secao>
    </>
  );
}
