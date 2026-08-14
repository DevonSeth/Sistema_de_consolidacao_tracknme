import { createSign } from "node:crypto";

const TOKEN_URL = "https://oauth2.googleapis.com/token";
const GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer";

type ServiceAccount = {
  client_email: string;
  private_key: string;
};

function base64url(input: Buffer | string): string {
  const buf = typeof input === "string" ? Buffer.from(input) : input;
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function assinarJwt(conta: ServiceAccount, scope: string): string {
  const agora = Math.floor(Date.now() / 1000);
  const header = { alg: "RS256", typ: "JWT" };
  const claims = {
    iss: conta.client_email,
    scope,
    aud: TOKEN_URL,
    exp: agora + 3600,
    iat: agora,
  };

  const cabecalho = `${base64url(JSON.stringify(header))}.${base64url(JSON.stringify(claims))}`;
  const assinatura = createSign("RSA-SHA256").update(cabecalho).sign(conta.private_key);
  return `${cabecalho}.${base64url(assinatura)}`;
}

/**
 * Troca o JSON da service account por um access token OAuth2 (JWT Bearer
 * flow, RFC 7523) — sem depender de `googleapis`/`google-auth-library`,
 * mesmo espírito de usar `httpx` puro em vez de SDK grande no lado Python
 * (`integrations/newmo_client.py`).
 */
export async function obterAccessToken(
  serviceAccountJson: string,
  scope: string
): Promise<string> {
  const conta: ServiceAccount = JSON.parse(serviceAccountJson);
  const jwt = assinarJwt(conta, scope);

  const resposta = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: GRANT_TYPE, assertion: jwt }),
  });

  const corpo = await resposta.json();
  if (!resposta.ok || !corpo.access_token) {
    throw new Error(corpo.error_description || corpo.error || "Falha ao obter access token do Google");
  }
  return corpo.access_token as string;
}
