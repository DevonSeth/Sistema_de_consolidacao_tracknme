export type Secao = "tracknme" | "newmo" | "supabase" | "google_sheets";

export type TipoCampo = "texto" | "numero" | "senha";

export type CampoMeta = {
  nome: string;
  label: string;
  tipo: TipoCampo;
};

export type CardMeta = {
  secao: Secao;
  emoji: string;
  titulo: string;
  descricao: string;
  testavel: boolean;
  notaFixa?: string;
};

export const CARDS: CardMeta[] = [
  {
    secao: "tracknme",
    emoji: "🚚",
    titulo: "Track N'Me",
    descricao: "Login automático — baixa relatórios e abre/fecha incidentes.",
    testavel: false,
    notaFixa: "Login manual (captcha) — só validável rodando o Painel Operador.",
  },
  {
    secao: "newmo",
    emoji: "💬",
    titulo: "Newmo (WhatsApp)",
    descricao: "Canal e setor usados pra disparar templates automáticos.",
    testavel: true,
  },
  {
    secao: "supabase",
    emoji: "🗄️",
    titulo: "Supabase",
    descricao: "Banco de dados — fonte única de verdade do sistema.",
    testavel: true,
  },
  {
    secao: "google_sheets",
    emoji: "📊",
    titulo: "Google Sheets",
    descricao: "Planilhas Administrador e Operacional.",
    testavel: true,
    notaFixa:
      "O arquivo da service account (.json) é gerenciado só por script de terminal — ver HANDOFF.",
  },
];

// Mesma forma de config/manager.py::CAMPOS_OBRIGATORIOS, mas só os campos
// editáveis por esta tela (newmo.templates fica de fora — não é
// credencial, é preservado automaticamente ao salvar).
export const CAMPOS_SECAO: Record<Secao, CampoMeta[]> = {
  tracknme: [
    { nome: "usuario", label: "Usuário", tipo: "texto" },
    { nome: "senha", label: "Senha", tipo: "senha" },
  ],
  newmo: [
    { nome: "canal_guid", label: "Canal (GUID)", tipo: "texto" },
    { nome: "setor_id", label: "Setor (ID)", tipo: "numero" },
    { nome: "token", label: "Token", tipo: "senha" },
  ],
  supabase: [
    { nome: "url", label: "URL", tipo: "texto" },
    { nome: "service_role_key", label: "Service Role Key", tipo: "senha" },
  ],
  google_sheets: [
    { nome: "credenciais_path", label: "Caminho do arquivo de credenciais", tipo: "texto" },
    { nome: "planilha_administrador_id", label: "ID da planilha Administrador", tipo: "texto" },
    { nome: "planilha_operacional_id", label: "ID da planilha Operacional", tipo: "texto" },
  ],
};

// Mesma lista de config/manager.py::CAMPOS_SECRETOS — nunca pré-carrega
// no formulário, em branco no submit significa "manter o valor atual".
export const CAMPOS_SECRETOS: Record<Secao, string[]> = {
  tracknme: ["senha"],
  newmo: ["token"],
  supabase: ["service_role_key"],
  google_sheets: [],
};

export function metaDoCard(secao: Secao): CardMeta {
  const card = CARDS.find((c) => c.secao === secao);
  if (!card) throw new Error(`Seção desconhecida: ${secao}`);
  return card;
}
