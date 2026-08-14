"use server";

import { revalidatePath } from "next/cache";

import { createSupabaseServiceClient } from "@/lib/supabase-server";

import { metaDe, TIER_REGEX } from "./meta";

type ResultadoAction = { erro?: string };

export async function atualizarParametroAction(
  formData: FormData
): Promise<ResultadoAction> {
  const chave = String(formData.get("chave") ?? "");
  const valor = String(formData.get("valor") ?? "");

  if (!chave) {
    return { erro: "Parâmetro desconhecido." };
  }

  const { tipo } = metaDe(chave);

  if (tipo === "numero" && (valor.trim() === "" || !Number.isFinite(Number(valor)))) {
    return { erro: "Precisa ser um número." };
  }

  if (tipo === "tier" && valor && !TIER_REGEX.test(valor)) {
    return {
      erro: "Formato inválido — use dias=NOME,dias=NOME,... (ex: 31=CRITICO,11=ATRASO,1=NORMAL).",
    };
  }

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase
    .from("system_parameters")
    .update({ valor })
    .eq("chave", chave);

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/parametros");
  return {};
}
