type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

interface ServiceAccountCredentials {
  clientEmail: string;
  privateKey: string;
  privateKeyId?: string;
}

const TOKEN_URL = "https://oauth2.googleapis.com/token";
const encoder = new TextEncoder();

function base64Url(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function parseCredentials(raw: string): ServiceAccountCredentials {
  let value: unknown;
  try { value = JSON.parse(raw); } catch { throw new Error("Invalid Google service-account configuration"); }
  if (typeof value !== "object" || value === null) throw new Error("Invalid Google service-account configuration");
  const input = value as Record<string, unknown>;
  if (
    typeof input.client_email !== "string" || !input.client_email.endsWith(".gserviceaccount.com") ||
    typeof input.private_key !== "string" || !input.private_key.includes("BEGIN PRIVATE KEY") ||
    input.token_uri !== TOKEN_URL ||
    (input.private_key_id !== undefined && typeof input.private_key_id !== "string")
  ) throw new Error("Invalid Google service-account configuration");
  return { clientEmail: input.client_email, privateKey: input.private_key, privateKeyId: input.private_key_id as string | undefined };
}

function pemBytes(pem: string): ArrayBuffer {
  const encoded = pem.replace("-----BEGIN PRIVATE KEY-----", "").replace("-----END PRIVATE KEY-----", "").replace(/\s/g, "");
  if (!encoded || !/^[A-Za-z0-9+/=]+$/.test(encoded)) throw new Error("Invalid Google service-account configuration");
  try { return Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0)).buffer; }
  catch { throw new Error("Invalid Google service-account configuration"); }
}

async function assertion(credentials: ServiceAccountCredentials, now: Date): Promise<string> {
  const issuedAt = Math.floor(now.getTime() / 1000) - 30;
  const header = base64Url(encoder.encode(JSON.stringify({ alg: "RS256", typ: "JWT", ...(credentials.privateKeyId ? { kid: credentials.privateKeyId } : {}) })));
  const claims = base64Url(encoder.encode(JSON.stringify({
    iss: credentials.clientEmail,
    scope: "https://www.googleapis.com/auth/spreadsheets",
    aud: TOKEN_URL,
    iat: issuedAt,
    exp: issuedAt + 3600,
  })));
  const key = await crypto.subtle.importKey(
    "pkcs8", pemBytes(credentials.privateKey),
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"],
  );
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, encoder.encode(`${header}.${claims}`));
  return `${header}.${claims}.${base64Url(new Uint8Array(signature))}`;
}

export function createGoogleAccessTokenProvider(
  rawCredentials: string,
  fetcher: Fetcher = fetch,
  now: () => Date = () => new Date(),
): () => Promise<string> {
  const credentials = parseCredentials(rawCredentials);
  let cachedToken = "";
  let refreshAt = 0;
  return async () => {
    if (cachedToken && now().getTime() < refreshAt) return cachedToken;
    const body = new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: await assertion(credentials, now()),
    });
    const response = await fetcher(TOKEN_URL, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(10_000),
      headers: { "content-type": "application/x-www-form-urlencoded" }, body,
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error("Google authentication failed");
    }
    let payload: unknown;
    try { payload = await response.json(); } catch { throw new Error("Invalid Google authentication response"); }
    if (
      typeof payload !== "object" || payload === null ||
      !("access_token" in payload) || typeof payload.access_token !== "string" || payload.access_token.length > 4096 ||
      !("expires_in" in payload) || !Number.isInteger(payload.expires_in) || (payload.expires_in as number) < 120
    ) throw new Error("Invalid Google authentication response");
    cachedToken = payload.access_token;
    refreshAt = now().getTime() + ((payload.expires_in as number) - 60) * 1000;
    return cachedToken;
  };
}
