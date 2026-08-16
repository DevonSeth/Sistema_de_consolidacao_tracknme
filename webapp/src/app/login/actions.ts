"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { createSupabaseServerClient } from "@/lib/supabase-server";

const DESTINO_POR_ROLE: Record<string, string> = {
  admin: "/admin",
  cliente: "/dashboard",
};

function clienteComCookies(cookieStore: Awaited<ReturnType<typeof cookies>>) {
  return createSupabaseServerClient({
    getAll: () => cookieStore.getAll(),
    setAll: (cookiesToSet) => {
      cookiesToSet.forEach(({ name, value, options }) => {
        cookieStore.set(name, value, options);
      });
    },
  });
}

export async function loginAction(formData: FormData) {
  const email = String(formData.get("email") ?? "");
  const senha = String(formData.get("senha") ?? "");

  const cookieStore = await cookies();
  const supabase = clienteComCookies(cookieStore);

  const { data, error } = await supabase.auth.signInWithPassword({
    email,
    password: senha,
  });

  if (error) {
    redirect("/login?erro=1");
  }

  const destino = DESTINO_POR_ROLE[data.user?.app_metadata?.role ?? ""];
  if (!destino) {
    // Conta válida no Supabase Auth, mas sem papel autorizado pro webapp
    // (ex: operador@... tentando logar aqui por engano) — nega e não deixa
    // sessão autenticada sem destino.
    await supabase.auth.signOut();
    redirect("/login?erro=1");
  }

  redirect(destino);
}

export async function signOutAction() {
  const cookieStore = await cookies();
  const supabase = clienteComCookies(cookieStore);
  await supabase.auth.signOut();
  redirect("/login");
}
