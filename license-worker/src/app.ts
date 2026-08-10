import {
  hashActivationCode,
  issueActivationToken,
  readUnverifiedTokenEmail,
  verifyActivationCode,
  verifyActivationToken,
} from "./security.ts";
import type { LicenseRecord, LicenseStore } from "./store.ts";
import { parseActivationRequest, parseMachineRequest, parseUsageRequest } from "./validation.ts";

interface AppDependencies {
  store: LicenseStore;
  signingSecret: string;
  adminToken?: string;
  now?: () => Date;
  logger?: (event: Record<string, unknown>) => void;
}

class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const INVALID_ACTIVATION = new ApiError(401, "INVALID_ACTIVATION", "Activation details are invalid.");
const INVALID_LICENSE = new ApiError(401, "INVALID_LICENSE", "License authorization is invalid.");
const SECURITY_HEADERS = {
  "cache-control": "no-store",
  "content-type": "application/json; charset=utf-8",
  "referrer-policy": "no-referrer",
  "strict-transport-security": "max-age=31536000; includeSubDomains",
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: SECURITY_HEADERS });
}

async function readJson(request: Request): Promise<unknown> {
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
    throw new ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json.");
  }
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > 8192) throw new ApiError(413, "REQUEST_TOO_LARGE", "Request is too large.");
  const text = await request.text();
  if (text.length > 8192) throw new ApiError(413, "REQUEST_TOO_LARGE", "Request is too large.");
  try { return JSON.parse(text); } catch { throw new ApiError(400, "INVALID_REQUEST", "Request body is invalid."); }
}

function licenseView(user: LicenseRecord) {
  return {
    email: user.email, name: user.name, used: user.used, quota: user.quota,
    expiryDate: user.expiryDate, expiryTime: user.expiryTime,
  };
}

function adminView(user: LicenseRecord) {
  return {
    ...licenseView(user), machineId: user.machineId, lastLogin: user.lastLogin,
    tokenVersion: user.tokenVersion,
  };
}

function isActive(user: LicenseRecord, now: Date): boolean {
  if (!user.expiryDate || user.expiryDate.toLowerCase() === "lifetime") return true;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(user.expiryDate) || !/^\d{2}:\d{2}$/.test(user.expiryTime)) return false;
  const expiry = new Date(`${user.expiryDate}T${user.expiryTime}:00+06:00`);
  return !Number.isNaN(expiry.getTime()) && now <= expiry;
}

function bearerToken(request: Request): string {
  const header = request.headers.get("authorization") || "";
  if (!header.startsWith("Bearer ") || header.length > 4103) throw INVALID_LICENSE;
  return header.slice(7);
}

async function authorizedUser(request: Request, machineId: string, deps: AppDependencies): Promise<LicenseRecord> {
  try {
    const token = bearerToken(request);
    const user = await deps.store.findByEmail(readUnverifiedTokenEmail(token));
    if (!user || !isActive(user, (deps.now ?? (() => new Date()))())) throw INVALID_LICENSE;
    await verifyActivationToken(token, machineId, user.tokenVersion, deps.signingSecret, (deps.now ?? (() => new Date()))());
    return user;
  } catch { throw INVALID_LICENSE; }
}

async function requireAdmin(request: Request, expected = ""): Promise<void> {
  try {
    if (expected.length < 32) throw new Error("Invalid admin configuration");
    const candidate = bearerToken(request);
    const key = await crypto.subtle.importKey(
      "raw", new TextEncoder().encode(expected), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"],
    );
    const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(expected));
    if (!await crypto.subtle.verify("HMAC", key, signature, new TextEncoder().encode(candidate))) throw new Error("Invalid admin token");
  } catch { throw new ApiError(401, "UNAUTHORIZED", "Admin authorization is required."); }
}

function activationCode(): string {
  const alphabet = "abcdefghjkmnpqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const value = Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join("");
  return value.match(/.{4}/g)!.join("-");
}

function parseNewUser(value: unknown) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new ApiError(400, "INVALID_REQUEST", "Request body is invalid.");
  const input = value as Record<string, unknown>;
  const allowed = new Set(["name", "email", "quota", "expiryDate", "expiryTime"]);
  if (Object.keys(input).some((key) => !allowed.has(key)) || typeof input.name !== "string" || typeof input.email !== "string") {
    throw new ApiError(400, "INVALID_REQUEST", "Request body is invalid.");
  }
  const name = input.name.trim();
  const email = input.email.trim().toLowerCase();
  const quota = input.quota === "Unlimited" ? "Unlimited" : input.quota;
  const expiryDate = input.expiryDate ?? "Lifetime";
  const expiryTime = input.expiryTime ?? "00:00";
  if (!name || name.length > 120 || !/^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$/.test(email) ||
      (quota !== "Unlimited" && (!Number.isSafeInteger(quota) || (quota as number) < 1)) ||
      typeof expiryDate !== "string" || typeof expiryTime !== "string" ||
      (expiryDate !== "Lifetime" && !/^\d{4}-\d{2}-\d{2}$/.test(expiryDate)) || !/^\d{2}:\d{2}$/.test(expiryTime)) {
    throw new ApiError(400, "INVALID_REQUEST", "Request body is invalid.");
  }
  return { name, email, quota: quota as number | "Unlimited", expiryDate, expiryTime };
}

function parseUserPatch(value: unknown): Partial<Pick<LicenseRecord, "name" | "quota" | "expiryDate" | "expiryTime">> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new ApiError(400, "INVALID_REQUEST", "Request body is invalid.");
  const input = value as Record<string, unknown>;
  const allowed = new Set(["name", "quota", "expiryDate", "expiryTime"]);
  if (!Object.keys(input).length || Object.keys(input).some((key) => !allowed.has(key))) {
    throw new ApiError(400, "INVALID_REQUEST", "Request body is invalid.");
  }
  const patch: Partial<Pick<LicenseRecord, "name" | "quota" | "expiryDate" | "expiryTime">> = {};
  if (input.name !== undefined) {
    if (typeof input.name !== "string" || !input.name.trim() || input.name.trim().length > 120) throw new ApiError(400, "INVALID_REQUEST", "Request body is invalid.");
    patch.name = input.name.trim();
  }
  if (input.quota !== undefined) {
    if (input.quota !== "Unlimited" && (!Number.isSafeInteger(input.quota) || (input.quota as number) < 1)) throw new ApiError(400, "INVALID_REQUEST", "Request body is invalid.");
    patch.quota = input.quota as number | "Unlimited";
  }
  if (input.expiryDate !== undefined) {
    if (typeof input.expiryDate !== "string" || (input.expiryDate !== "Lifetime" && !/^\d{4}-\d{2}-\d{2}$/.test(input.expiryDate))) throw new ApiError(400, "INVALID_REQUEST", "Request body is invalid.");
    patch.expiryDate = input.expiryDate;
  }
  if (input.expiryTime !== undefined) {
    if (typeof input.expiryTime !== "string" || !/^\d{2}:\d{2}$/.test(input.expiryTime)) throw new ApiError(400, "INVALID_REQUEST", "Request body is invalid.");
    patch.expiryTime = input.expiryTime;
  }
  return patch;
}

function adminEmail(pathname: string, suffix = ""): string | null {
  const expression = new RegExp(`^/v1/admin/users/([^/]+)${suffix}$`);
  const match = pathname.match(expression);
  if (!match) return null;
  try { return decodeURIComponent(match[1]).trim().toLowerCase(); }
  catch { throw new ApiError(400, "INVALID_REQUEST", "Request path is invalid."); }
}

export function createApp(deps: AppDependencies): (request: Request) => Promise<Response> {
  return async (request: Request) => {
    const requestId = request.headers.get("cf-ray") || crypto.randomUUID();
    let route = "unknown";
    try {
      const { pathname } = new URL(request.url);
      route = pathname.startsWith("/v1/admin/users/") ? "/v1/admin/users/:action" : pathname;
      if (request.method === "GET" && pathname === "/v1/health") {
        return json({ data: { status: "ok", apiVersion: "v1" } });
      }
      const isAdmin = pathname === "/v1/admin/users" || pathname.startsWith("/v1/admin/users/");
      if (isAdmin) await requireAdmin(request, deps.adminToken);
      if (request.method === "GET" && pathname === "/v1/admin/users") {
        const url = new URL(request.url);
        const page = Math.max(1, Number.parseInt(url.searchParams.get("page") || "1", 10) || 1);
        const pageSize = Math.min(100, Math.max(1, Number.parseInt(url.searchParams.get("pageSize") || "50", 10) || 50));
        const result = await deps.store.list(page, pageSize);
        return json({ data: result.users.map(adminView), pagination: { page, pageSize, totalItems: result.total } });
      }
      const directAdminEmail = adminEmail(pathname);
      if (request.method === "PATCH" && directAdminEmail) {
        const user = await deps.store.findByEmail(directAdminEmail);
        if (!user) throw new ApiError(404, "USER_NOT_FOUND", "User not found.");
        Object.assign(user, parseUserPatch(await readJson(request)));
        await deps.store.save(user);
        return json({ data: { user: adminView(user) } });
      }
      if (request.method === "DELETE" && directAdminEmail) {
        if (!await deps.store.findByEmail(directAdminEmail)) throw new ApiError(404, "USER_NOT_FOUND", "User not found.");
        await deps.store.delete(directAdminEmail);
        return new Response(null, { status: 204, headers: SECURITY_HEADERS });
      }
      const resetUsageEmail = adminEmail(pathname, "/reset-usage");
      if (request.method === "POST" && resetUsageEmail) {
        const user = await deps.store.findByEmail(resetUsageEmail);
        if (!user) throw new ApiError(404, "USER_NOT_FOUND", "User not found.");
        user.used = 0;
        await deps.store.save(user);
        return json({ data: { user: adminView(user) } });
      }
      const activationCodeEmail = adminEmail(pathname, "/activation-code");
      if (request.method === "POST" && activationCodeEmail) {
        const user = await deps.store.findByEmail(activationCodeEmail);
        if (!user) throw new ApiError(404, "USER_NOT_FOUND", "User not found.");
        const code = activationCode();
        user.tokenVersion += 1;
        user.activationCodeHash = await hashActivationCode(code, user.email, deps.signingSecret);
        await deps.store.save(user);
        return json({ data: { user: adminView(user), activationCode: code } });
      }
      if (request.method !== "POST") throw new ApiError(404, "NOT_FOUND", "Resource not found.");

      if (pathname === "/v1/admin/users") {
        const input = parseNewUser(await readJson(request));
        if (await deps.store.findByEmail(input.email)) throw new ApiError(409, "USER_EXISTS", "A user with this email already exists.");
        const code = activationCode();
        const user: LicenseRecord = {
          ...input, machineId: "", lastLogin: "", used: 0, tokenVersion: 1,
          activationCodeHash: await hashActivationCode(code, input.email, deps.signingSecret),
        };
        await deps.store.create(user);
        return json({ data: { user: adminView(user), activationCode: code } }, 201);
      }

      const resetMachineMatch = pathname.match(/^\/v1\/admin\/users\/([^/]+)\/reset-machine$/);
      if (resetMachineMatch) {
        const email = decodeURIComponent(resetMachineMatch[1]).trim().toLowerCase();
        const user = await deps.store.findByEmail(email);
        if (!user) throw new ApiError(404, "USER_NOT_FOUND", "User not found.");
        const code = activationCode();
        user.machineId = "";
        user.tokenVersion += 1;
        user.activationCodeHash = await hashActivationCode(code, email, deps.signingSecret);
        await deps.store.save(user);
        return json({ data: { user: adminView(user), activationCode: code } });
      }

      if (pathname === "/v1/activations") {
        const input = parseActivationRequest(await readJson(request));
        const user = await deps.store.findByEmail(input.email);
        const validCode = user && await verifyActivationCode(input.activationCode, input.email, user.activationCodeHash, deps.signingSecret);
        const validName = user && (!user.name || user.name.toLowerCase() === input.name.toLowerCase());
        if (!user || !validCode || !validName || !isActive(user, (deps.now ?? (() => new Date()))()) ||
            (user.machineId && user.machineId.toLowerCase() !== input.machineId.toLowerCase())) throw INVALID_ACTIVATION;
        user.name ||= input.name;
        user.machineId = input.machineId;
        user.lastLogin = (deps.now ?? (() => new Date()))().toISOString();
        user.activationCodeHash = "";
        await deps.store.save(user);
        const activationToken = await issueActivationToken(
          { email: user.email, machineId: user.machineId, tokenVersion: user.tokenVersion },
          deps.signingSecret,
          (deps.now ?? (() => new Date()))(),
        );
        return json({ data: { activationToken, license: licenseView(user) } });
      }

      if (pathname === "/v1/licenses/verify") {
        const input = parseMachineRequest(await readJson(request));
        const user = await authorizedUser(request, input.machineId, deps);
        return json({ data: { license: licenseView(user) } });
      }

      if (pathname === "/v1/usage") {
        const input = parseUsageRequest(await readJson(request));
        const user = await authorizedUser(request, input.machineId, deps);
        if (input.used < user.used) {
          throw new ApiError(409, "USAGE_CONFLICT", "Usage cannot be lower than the current value.");
        }
        if (user.quota !== "Unlimited" && input.used > user.quota) {
          throw new ApiError(403, "QUOTA_EXCEEDED", "License quota has been reached.");
        }
        user.used = input.used;
        user.lastLogin = (deps.now ?? (() => new Date()))().toISOString();
        await deps.store.save(user);
        return json({ data: { license: licenseView(user) } });
      }
      throw new ApiError(404, "NOT_FOUND", "Resource not found.");
    } catch (error) {
      if (error instanceof ApiError) return json({ error: { code: error.code, message: error.message } }, error.status);
      const event = {
        event: "license_api_error", requestId, route,
        errorType: error instanceof Error ? error.name : "UnknownError",
        errorMessage: error instanceof Error ? error.message.slice(0, 160) : "Unknown failure",
      };
      (deps.logger ?? ((value) => console.error(JSON.stringify(value))))(event);
      return json({ error: { code: "INTERNAL_ERROR", message: "The service is temporarily unavailable." } }, 500);
    }
  };
}
