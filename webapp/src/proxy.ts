import { NextResponse, type NextRequest } from "next/server";

import { createSupabaseServerClient } from "@/lib/supabase-server";

const ROLES_ADMIN = ["admin"];
const ROLES_DASHBOARD = ["admin", "cliente"];

/** Roles permitidas pra cada área — `null` = rota fora do escopo protegido
 * (não deveria acontecer, o `matcher` abaixo já restringe quais rotas
 * passam por aqui, mas nega por padrão em vez de deixar passar). */
function rolesPermitidas(pathname: string): string[] | null {
  if (pathname.startsWith("/admin")) return ROLES_ADMIN;
  if (pathname.startsWith("/dashboard")) return ROLES_DASHBOARD;
  return null;
}

export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createSupabaseServerClient({
    getAll: () => request.cookies.getAll(),
    setAll: (cookiesToSet, headers) => {
      cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
      response = NextResponse.next({ request });
      cookiesToSet.forEach(({ name, value, options }) => {
        response.cookies.set(name, value, options);
      });
      Object.entries(headers).forEach(([key, value]) => {
        response.headers.set(key, value);
      });
    },
  });

  const { data } = await supabase.auth.getUser();
  const permitidas = rolesPermitidas(request.nextUrl.pathname);
  const papel = data.user?.app_metadata?.role;

  if (!data.user || !permitidas || !permitidas.includes(papel)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return response;
}

export const config = {
  matcher: ["/admin/:path*", "/dashboard/:path*"],
};
