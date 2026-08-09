const encoder = new TextEncoder();
const TOKEN_LIFETIME_SECONDS = 30 * 24 * 60 * 60;

export interface ActivationTokenClaims {
  email: string;
  machineHash: string;
  tokenVersion: number;
  issuedAt: number;
  expiresAt: number;
}

interface TokenInput {
  email: string;
  machineId: string;
  tokenVersion: number;
}

function toBase64Url(value: ArrayBuffer | Uint8Array): string {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function fromBase64Url(value: string): ArrayBuffer {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error("Invalid token encoding");
  const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0)).buffer;
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  if (secret.length < 32) throw new Error("Signing secret is too short");
  return crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

async function sign(value: string, secret: string): Promise<ArrayBuffer> {
  return crypto.subtle.sign("HMAC", await hmacKey(secret), encoder.encode(value));
}

async function machineHash(machineId: string, secret: string): Promise<string> {
  return toBase64Url(await sign(`machine\0${machineId}`, secret));
}

export async function hashActivationCode(code: string, email: string, secret: string): Promise<string> {
  return toBase64Url(await sign(`code\0${email.trim().toLowerCase()}\0${code.trim().toLowerCase()}`, secret));
}

export async function verifyActivationCode(code: string, email: string, expectedHash: string, secret: string): Promise<boolean> {
  try {
    return crypto.subtle.verify(
      "HMAC",
      await hmacKey(secret),
      fromBase64Url(expectedHash),
      encoder.encode(`code\0${email.trim().toLowerCase()}\0${code.trim().toLowerCase()}`),
    );
  } catch {
    return false;
  }
}

export async function issueActivationToken(
  input: TokenInput,
  secret: string,
  now = new Date(),
  lifetimeSeconds = TOKEN_LIFETIME_SECONDS,
): Promise<string> {
  const issuedAt = Math.floor(now.getTime() / 1000);
  const claims: ActivationTokenClaims = {
    email: input.email.trim().toLowerCase(),
    machineHash: await machineHash(input.machineId, secret),
    tokenVersion: input.tokenVersion,
    issuedAt,
    expiresAt: issuedAt + lifetimeSeconds,
  };
  const payload = toBase64Url(encoder.encode(JSON.stringify(claims)));
  return `v1.${payload}.${toBase64Url(await sign(`v1.${payload}`, secret))}`;
}

export async function verifyActivationToken(
  token: string,
  machineId: string,
  tokenVersion: number,
  secret: string,
  now = new Date(),
): Promise<ActivationTokenClaims> {
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== "v1") throw new Error("Invalid activation token");
  const signed = `${parts[0]}.${parts[1]}`;
  const isAuthentic = await crypto.subtle.verify("HMAC", await hmacKey(secret), fromBase64Url(parts[2]), encoder.encode(signed));
  if (!isAuthentic) throw new Error("Invalid activation token");
  const claims = JSON.parse(new TextDecoder().decode(fromBase64Url(parts[1]))) as Partial<ActivationTokenClaims>;
  const currentTime = Math.floor(now.getTime() / 1000);
  if (
    typeof claims.email !== "string" || typeof claims.machineHash !== "string" ||
    !Number.isInteger(claims.tokenVersion) || !Number.isInteger(claims.issuedAt) ||
    !Number.isInteger(claims.expiresAt) || claims.expiresAt! <= currentTime ||
    claims.tokenVersion !== tokenVersion || claims.machineHash !== await machineHash(machineId, secret)
  ) throw new Error("Invalid activation token");
  return claims as ActivationTokenClaims;
}
