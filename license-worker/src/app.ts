import {
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
  now?: () => Date;
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

export function createApp(deps: AppDependencies): (request: Request) => Promise<Response> {
  return async (request: Request) => {
    try {
      const { pathname } = new URL(request.url);
      if (request.method === "GET" && pathname === "/v1/health") {
        return json({ data: { status: "ok", apiVersion: "v1" } });
      }
      if (request.method !== "POST") throw new ApiError(404, "NOT_FOUND", "Resource not found.");

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
      return json({ error: { code: "INTERNAL_ERROR", message: "The service is temporarily unavailable." } }, 500);
    }
  };
}
