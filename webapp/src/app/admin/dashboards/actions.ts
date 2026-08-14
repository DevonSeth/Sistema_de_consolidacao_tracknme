"use server";

import { revalidatePath } from "next/cache";

import { createSupabaseServiceClient } from "@/lib/supabase-server";

type ResultadoAction = { erro?: string };

export async function alternarVisibilidadeMetricaAction(formData: FormData): Promise<ResultadoAction> {
  const chave = String(formData.get("chave") ?? "");
  const visivel = formData.get("visivel") === "true";

  if (!chave) {
    return { erro: "Métrica desconhecida." };
  }

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase
    .from("dashboard_metricas_cliente")
    .update({ visivel })
    .eq("chave", chave);

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/dashboards");
  revalidatePath("/dashboard");
  return {};
}

export async function alternarVisibilidadeOperadorAction(formData: FormData): Promise<ResultadoAction> {
  const chave = String(formData.get("chave") ?? "");
  const visivelOperador = formData.get("visivel_operador") === "true";

  if (!chave) {
    return { erro: "Métrica desconhecida." };
  }

  const supabase = createSupabaseServiceClient();
  const { error } = await supabase
    .from("dashboard_metricas_cliente")
    .update({ visivel_operador: visivelOperador })
    .eq("chave", chave);

  if (error) {
    return { erro: error.message };
  }

  revalidatePath("/admin/dashboards");
  return {};
}
