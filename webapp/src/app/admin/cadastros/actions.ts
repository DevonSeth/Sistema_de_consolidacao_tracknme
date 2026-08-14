"use server";

import { revalidatePath } from "next/cache";

import { createSupabaseServiceClient } from "@/lib/supabase-server";

type ResultadoAction = { erro?: string };

export async function criarBaseAction(formData: FormData): Promise<ResultadoAction> {
  const nome = String(formData.get("nome") ?? "").trim();
  const endereco = String(formData.get("endereco") ?? "").trim();

  if (!nome) {
    return { erro: "Nome é obrigatório." };
  }

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase
    .from("bases")
    .insert({ nome, endereco, ativo: true });

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/cadastros");
  return {};
}

export async function atualizarBaseAction(formData: FormData): Promise<ResultadoAction> {
  const id = String(formData.get("id") ?? "");
  const nome = String(formData.get("nome") ?? "").trim();
  const endereco = String(formData.get("endereco") ?? "").trim();

  if (!id || !nome) {
    return { erro: "Nome é obrigatório." };
  }

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase
    .from("bases")
    .update({ nome, endereco })
    .eq("id", id);

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/cadastros");
  return {};
}

export async function alternarAtivoBaseAction(formData: FormData): Promise<ResultadoAction> {
  const id = String(formData.get("id") ?? "");
  const ativo = formData.get("ativo") === "true";

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase.from("bases").update({ ativo }).eq("id", id);

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/cadastros");
  return {};
}

export async function criarPontoAcaoAction(formData: FormData): Promise<ResultadoAction> {
  const nome_local = String(formData.get("nome_local") ?? "").trim();
  const endereco = String(formData.get("endereco") ?? "").trim();
  const data = String(formData.get("data") ?? "").trim();

  if (!nome_local) {
    return { erro: "Nome/Local é obrigatório." };
  }

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase
    .from("pontos_acao")
    .insert({ nome_local, endereco, data: data || null, ativo: true });

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/cadastros");
  return {};
}

export async function atualizarPontoAcaoAction(formData: FormData): Promise<ResultadoAction> {
  const id = String(formData.get("id") ?? "");
  const nome_local = String(formData.get("nome_local") ?? "").trim();
  const endereco = String(formData.get("endereco") ?? "").trim();
  const data = String(formData.get("data") ?? "").trim();

  if (!id || !nome_local) {
    return { erro: "Nome/Local é obrigatório." };
  }

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase
    .from("pontos_acao")
    .update({ nome_local, endereco, data: data || null })
    .eq("id", id);

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/cadastros");
  return {};
}

export async function alternarAtivoPontoAcaoAction(
  formData: FormData
): Promise<ResultadoAction> {
  const id = String(formData.get("id") ?? "");
  const ativo = formData.get("ativo") === "true";

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase
    .from("pontos_acao")
    .update({ ativo })
    .eq("id", id);

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/cadastros");
  return {};
}

export async function atualizarRegraAction(formData: FormData): Promise<ResultadoAction> {
  const id = String(formData.get("id") ?? "");
  const templateObservacao = String(formData.get("template_observacao") ?? "").trim();
  const templateAcao = String(formData.get("template_acao") ?? "").trim();
  const nivelUrgenciaRaw = String(formData.get("nivel_urgencia") ?? "").trim();

  if (!id) {
    return { erro: "Regra desconhecida." };
  }

  const payload: {
    template_observacao: string | null;
    template_acao: string | null;
    nivel_urgencia?: number;
  } = {
    template_observacao: templateObservacao || null,
    template_acao: templateAcao || null,
  };

  // nivel_urgencia só é editável quando a regra já tem um valor (não é
  // REGRA_4/REGRA_4_TIMESTAMP, dedup silencioso) — o form desabilita o
  // campo nesse caso e não envia o nome, então nivelUrgenciaRaw fica vazio.
  if (nivelUrgenciaRaw) {
    const nivel = Number(nivelUrgenciaRaw);
    if (!Number.isInteger(nivel) || nivel < 1 || nivel > 5) {
      return { erro: "Nível precisa ser um número inteiro de 1 a 5." };
    }
    payload.nivel_urgencia = nivel;
  }

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase.from("rule_templates").update(payload).eq("id", id);

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/cadastros");
  return {};
}

export async function alternarAtivoRegraAction(formData: FormData): Promise<ResultadoAction> {
  const id = String(formData.get("id") ?? "");
  const ativo = formData.get("ativo") === "true";

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase.from("rule_templates").update({ ativo }).eq("id", id);

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/cadastros");
  return {};
}
