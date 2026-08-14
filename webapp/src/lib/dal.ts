import { cache } from "react";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { createSupabaseServerClient } from "@/lib/supabase-server";

export const verifySession = cache(async () => {
  const cookieStore = await cookies();
  const supabase = createSupabaseServerClient({
    getAll: () => cookieStore.getAll(),
    // Sem escrita aqui de propósito: verifySession() pode ser chamada durante
    // o render de um Server Component, onde escrever cookie lança erro
    // (regra do next/headers — cookies só graváveis em Server Action/Route
    // Handler). O proxy já cobre o refresh de token.
    setAll: () => {},
  });

  const { data, error } = await supabase.auth.getUser();

  if (error || !data.user) {
    redirect("/login");
  }

  return { user: data.user };
});
