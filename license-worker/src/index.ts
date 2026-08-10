import { createApp } from "./app.ts";
import { createGoogleAccessTokenProvider } from "./google_auth.ts";
import { GoogleSheetsStore } from "./google_sheets_store.ts";

interface RateLimiter {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

interface Env {
  GOOGLE_SERVICE_ACCOUNT_JSON: string;
  GOOGLE_SHEET_ID: string;
  LICENSE_SIGNING_SECRET: string;
  ADMIN_API_TOKEN: string;
  ACTIVATION_RATE_LIMITER: RateLimiter;
  ADMIN_RATE_LIMITER: RateLimiter;
}

type Handler = (request: Request) => Promise<Response>;
let app: Handler | undefined;

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json(
    { error: { code, message } },
    { status, headers: { "cache-control": "no-store", "x-content-type-options": "nosniff" } },
  );
}

function createProductionApp(env: Env): Handler {
  if (env.LICENSE_SIGNING_SECRET.length < 32 || env.ADMIN_API_TOKEN.length < 32) {
    throw new Error("Invalid Worker secret configuration");
  }
  const store = new GoogleSheetsStore({
    spreadsheetId: env.GOOGLE_SHEET_ID,
    getAccessToken: createGoogleAccessTokenProvider(env.GOOGLE_SERVICE_ACCOUNT_JSON),
  });
  return createApp({ store, signingSecret: env.LICENSE_SIGNING_SECRET, adminToken: env.ADMIN_API_TOKEN });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      const path = new URL(request.url).pathname;
      const clientKey = request.headers.get("cf-connecting-ip") || "missing-client-ip";
      if (request.method === "POST" && path === "/v1/activations") {
        const { success } = await env.ACTIVATION_RATE_LIMITER.limit({ key: clientKey });
        if (!success) return errorResponse(429, "RATE_LIMITED", "Too many activation attempts. Try again later.");
      }
      if (path.startsWith("/v1/admin/")) {
        const { success } = await env.ADMIN_RATE_LIMITER.limit({ key: clientKey });
        if (!success) return errorResponse(429, "RATE_LIMITED", "Too many admin requests. Try again later.");
      }
      app ??= createProductionApp(env);
      return await app(request);
    } catch {
      return errorResponse(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.");
    }
  },
};
