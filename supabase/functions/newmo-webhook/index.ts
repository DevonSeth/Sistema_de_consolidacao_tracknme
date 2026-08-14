// Recebe o evento `mensagem_recebida` do Newmo/Zapio (webhook cadastrado
// manualmente no painel do Newmo, por canal — sem campo de segredo/
// assinatura própria, por isso a autenticação é um `?token=` na própria
// URL, comparado contra o secret WEBHOOK_TOKEN).
//
// Payload real de referência: referencia_legado/Formato_webhook_newmo.txt

import "@supabase/functions-js/edge-runtime.d.ts";
import { withSupabase } from "@supabase/server";

const STATUS_PENDENTE = "pendente";
const STATUS_AGUARDANDO_RESPOSTA = "aguardando_resposta";
const STATUS_RESPONDIDO = "respondido";
const ORIGEM_REMOCAO = "remocao";

const WEBHOOK_TOKEN = Deno.env.get("WEBHOOK_TOKEN");
const NEWMO_CANAL_GUID = Deno.env.get("NEWMO_CANAL_GUID");

interface Tratativa {
  id: string;
  status: string;
  origem: string;
}

// Mesma regra de core/normalizacao.normalizar_telefone_e164 (Python) —
// mantida em sincronia manualmente, é a única duplicação aceita porque
// Deno/TS não importa código Python.
function normalizarTelefoneE164(telefoneBruto: string | null | undefined): string | null {
  let digitos = (telefoneBruto ?? "").replace(/\D/g, "");
  if (!digitos) return null;

  if (digitos.startsWith("0")) digitos = digitos.slice(1);

  if ((digitos.length === 12 || digitos.length === 13) && digitos.startsWith("55")) {
    digitos = digitos.slice(2);
  }

  if (digitos.length !== 10 && digitos.length !== 11) return null;

  const ddd = digitos.slice(0, 2);
  let numeroLocal = digitos.slice(2);

  if (numeroLocal.length === 8) {
    if ("6789".includes(numeroLocal[0])) {
      numeroLocal = "9" + numeroLocal;
    } else if (!"2345".includes(numeroLocal[0])) {
      return null;
    }
  } else if (numeroLocal.length === 9) {
    if (numeroLocal[0] !== "9") return null;
  } else {
    return null;
  }

  return `+55${ddd}${numeroLocal}`;
}

function retornoAssociadoPorOrigem(origem: string): string {
  return origem === ORIGEM_REMOCAO
    ? "Retirado — associado confirma, revisar"
    : "Instalado — associado confirma, revisar";
}

export default {
  fetch: withSupabase({ auth: "none" }, async (req, ctx) => {
    const url = new URL(req.url);
    const tokenRecebido = url.searchParams.get("token");

    if (req.method !== "POST" || !WEBHOOK_TOKEN || tokenRecebido !== WEBHOOK_TOKEN) {
      return Response.json({ error: "unauthorized" }, { status: 401 });
    }

    let payload: any;
    try {
      payload = await req.json();
    } catch {
      // Corpo ilegível não é erro de autenticação — sempre 200 pra não
      // entrar em retry loop do Newmo.
      return Response.json({ ok: true }, { status: 200 });
    }

    // Defensivo, sem downside: só loga, nunca bloqueia (o token já é a
    // autenticação real).
    if (NEWMO_CANAL_GUID && payload?.Canal?.GUID && payload.Canal.GUID !== NEWMO_CANAL_GUID) {
      console.warn(`newmo-webhook: Canal.GUID inesperado (${payload.Canal.GUID})`);
    }

    if (payload?.Evento !== "mensagem_recebida") {
      return Response.json({ ok: true }, { status: 200 });
    }

    const atendimentoId: number | null = payload?.Atendimento?.Id ?? null;
    const telefoneContato: string | null = payload?.Contato?.Numero ?? null;
    const mensagem = payload?.Mensagem ?? {};

    let tratativa: Tratativa | null = null;

    if (atendimentoId !== null) {
      const { data } = await ctx.supabaseAdmin
        .from("tratativas")
        .select("id, status, origem")
        .eq("atendimento_id", atendimentoId)
        .limit(1);
      tratativa = (data?.[0] as Tratativa) ?? null;
    }

    if (!tratativa && telefoneContato) {
      const e164 = normalizarTelefoneE164(telefoneContato);
      if (e164) {
        const { data } = await ctx.supabaseAdmin
          .from("tratativas")
          .select("id, status, origem")
          .eq("telefone", e164)
          .limit(1);
        tratativa = (data?.[0] as Tratativa) ?? null;
      }
    }

    if (!tratativa) {
      return Response.json({ ok: true }, { status: 200 });
    }

    const agora = new Date().toISOString();
    const campos: Record<string, unknown> = { updated_at: agora };

    // Guarda de status: nunca sobrescreve uma tratativa que já saiu do
    // ciclo de mensagens (senão ela reapareceria duplicada em
    // "Tratativas" no próximo ciclo do orchestrator).
    const podeMarcarRespondido =
      tratativa.status === STATUS_PENDENTE || tratativa.status === STATUS_AGUARDANDO_RESPOSTA;

    if (mensagem.Tipo && mensagem.Tipo !== "Texto") {
      campos.resposta = `[${mensagem.Tipo}]`;
      campos.data_resposta = agora;
      if (podeMarcarRespondido) campos.status = STATUS_RESPONDIDO;
    } else {
      const texto = String(mensagem.Texto ?? "").trim();
      campos.resposta = texto;
      campos.data_resposta = agora;
      if (podeMarcarRespondido) campos.status = STATUS_RESPONDIDO;

      if (texto === "Já foi realizado") {
        campos.retorno_associado = retornoAssociadoPorOrigem(tratativa.origem);
      } else if (texto === "Confirmar") {
        campos.situacao_manual = "Agendado";
      }
    }

    await ctx.supabaseAdmin.from("tratativas").update(campos).eq("id", tratativa.id);

    return Response.json({ ok: true }, { status: 200 });
  }),
};
