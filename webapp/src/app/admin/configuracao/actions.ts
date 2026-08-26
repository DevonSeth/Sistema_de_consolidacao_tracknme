"use server";

import { randomBytes } from "node:crypto";

import { revalidatePath } from "next/cache";

import { obterAccessToken } from "@/lib/google-auth";
import { sha256 } from "@/lib/provisionamento";
import { createSupabaseServiceClient } from "@/lib/supabase-server";
import { lerSegredo, lerSegredoRaw } from "@/lib/vault-credenciais";

import { CAMPOS_SECAO, CAMPOS_SECRETOS, type Secao } from "./meta";

type ResultadoAction = { erro?: string };
type ResultadoTeste = { ok: boolean; mensagem: string };
type ResultadoToken = { token?: string; expiraEm?: string; erro?: string };

const VALIDADE_TOKEN_HORAS = 2;

async function gravarSegredo(secao: string, valores: Record<string, unknown>): Promise<void> {
  const supabase = createSupabaseServiceClient();
  const { error } = await supabase.rpc("credenciais_definir", {
    p_secao: secao,
    p_valor_json: JSON.stringify(valores),
  });
  if (error) throw new Error(error.message);
}

export async function salvarCredencialAction(formData: FormData): Promise<ResultadoAction> {
  const secao = String(formData.get("secao") ?? "") as Secao;
  const campos = CAMPOS_SECAO[secao];
  if (!campos) {
    return { erro: "Seção desconhecida." };
  }

  const secretos = new Set(CAMPOS_SECRETOS[secao]);

  try {
    const atual = await lerSegredo(secao);
    const novo: Record<string, unknown> = { ...atual };

    for (const campo of campos) {
      const valor = String(formData.get(campo.nome) ?? "").trim();

      if (secretos.has(campo.nome)) {
        if (valor) novo[campo.nome] = valor; // em branco = mantém o valor atual
        continue;
      }

      if (!valor) {
        return { erro: `${campo.label} é obrigatório.` };
      }
      if (campo.tipo === "numero") {
        const numero = Number(valor);
        if (!Number.isInteger(numero)) {
          return { erro: `${campo.label} precisa ser um número inteiro.` };
        }
        novo[campo.nome] = numero;
      } else {
        novo[campo.nome] = valor;
      }
    }

    await gravarSegredo(secao, novo);
  } catch (e) {
    return { erro: e instanceof Error ? e.message : "Erro desconhecido ao gravar no Vault." };
  }

  revalidatePath("/admin/configuracao");
  return {};
}

export async function testarConexaoAction(secao: Secao): Promise<ResultadoTeste> {
  try {
    if (secao === "tracknme") {
      return { ok: false, mensagem: "Não aplicável — login sempre manual (captcha)." };
    }

    if (secao === "supabase") {
      const valores = await lerSegredo("supabase");
      const url = String(valores.url ?? "");
      const key = String(valores.service_role_key ?? "");
      if (!url || !key) return { ok: false, mensagem: "Credencial incompleta no Vault." };

      const { createClient } = await import("@supabase/supabase-js");
      const client = createClient(url, key, { auth: { persistSession: false } });
      const { error } = await client.from("system_parameters").select("chave").limit(1);
      if (error) return { ok: false, mensagem: error.message };
      return { ok: true, mensagem: "Conectado." };
    }

    if (secao === "newmo") {
      const valores = await lerSegredo("newmo");
      const token = String(valores.token ?? "");
      if (!token) return { ok: false, mensagem: "Credencial incompleta no Vault." };

      const resposta = await fetch("https://model.newmo.com.br/api/v2/canal?status=todos", {
        headers: { Authorization: `Bearer ${token}` },
      });
      const corpo = await resposta.json().catch(() => null);
      if (!resposta.ok || !corpo || corpo.erro) {
        return { ok: false, mensagem: corpo?.descricao ?? `HTTP ${resposta.status}` };
      }
      return { ok: true, mensagem: "Conectado." };
    }

    if (secao === "google_sheets") {
      const valores = await lerSegredo("google_sheets");
      const adminId = String(valores.planilha_administrador_id ?? "");
      const operId = String(valores.planilha_operacional_id ?? "");
      const arquivo = await lerSegredoRaw("google_sheets_arquivo_credenciais");
      if (!adminId || !operId || !arquivo) {
        return { ok: false, mensagem: "Credencial incompleta no Vault." };
      }

      const token = await obterAccessToken(
        arquivo,
        "https://www.googleapis.com/auth/spreadsheets.readonly"
      );

      for (const [nome, id] of [
        ["Administrador", adminId],
        ["Operacional", operId],
      ] as const) {
        const resp = await fetch(
          `https://sheets.googleapis.com/v4/spreadsheets/${id}?fields=spreadsheetId`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        if (!resp.ok) {
          const corpo = await resp.json().catch(() => null);
          return {
            ok: false,
            mensagem: `Planilha ${nome}: ${corpo?.error?.message ?? `HTTP ${resp.status}`}`,
          };
        }
      }
      return { ok: true, mensagem: "Conectado (2 planilhas)." };
    }

    return { ok: false, mensagem: "Seção desconhecida." };
  } catch (e) {
    return { ok: false, mensagem: e instanceof Error ? e.message : "Erro desconhecido." };
  }
}

/**
 * Gera um token de uso único pra provisionar uma máquina nova do Painel
 * Operador — consumido por `POST /api/operador/provisionar`
 * (`webapp/src/app/api/operador/provisionar/route.ts`). Só o hash
 * (`sha256`) é gravado em `provisioning_tokens.token_hash`; o token em
 * claro só existe nesta resposta, exibido uma única vez pro Admin (mesmo
 * princípio de `chave_maquina`, não é recuperável depois).
 *
 * Mesmo formato/contrato já validado em produção via
 * `_handoff/verificar_provisionamento.py::_inserir_token`.
 */
export async function gerarTokenProvisionamentoAction(rotuloMaquina: string): Promise<ResultadoToken> {
  const rotulo = rotuloMaquina.trim();
  if (!rotulo) {
    return { erro: "Rótulo da máquina é obrigatório." };
  }

  const token = randomBytes(32).toString("base64url");
  const expiraEm = new Date(Date.now() + VALIDADE_TOKEN_HORAS * 60 * 60 * 1000).toISOString();

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase.from("provisioning_tokens").insert({
    token_hash: sha256(token),
    rotulo_maquina: rotulo,
    expira_em: expiraEm,
  });
  if (error) {
    return { erro: error.message };
  }

  return { token, expiraEm };
}
