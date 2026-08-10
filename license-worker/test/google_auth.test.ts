import assert from "node:assert/strict";
import test from "node:test";

import { createGoogleAccessTokenProvider } from "../src/google_auth.ts";

function base64UrlJson(part: string): Record<string, unknown> {
  return JSON.parse(Buffer.from(part, "base64url").toString("utf8"));
}

test("service-account JWT uses RS256 and access tokens are cached", async () => {
  const keys = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  );
  const pkcs8 = Buffer.from(await crypto.subtle.exportKey("pkcs8", keys.privateKey)).toString("base64");
  const credentials = JSON.stringify({
    client_email: "worker-test@example.iam.gserviceaccount.com",
    private_key_id: "test-key-id",
    private_key: `-----BEGIN PRIVATE KEY-----\n${pkcs8}\n-----END PRIVATE KEY-----\n`,
    token_uri: "https://oauth2.googleapis.com/token",
  });
  let calls = 0;
  const provider = createGoogleAccessTokenProvider(credentials, async (_input, init) => {
    calls += 1;
    assert.equal(init?.redirect, "manual");
    const form = new URLSearchParams(String(init?.body));
    assert.equal(form.get("grant_type"), "urn:ietf:params:oauth:grant-type:jwt-bearer");
    const parts = form.get("assertion")!.split(".");
    assert.equal(base64UrlJson(parts[0]).alg, "RS256");
    assert.equal(base64UrlJson(parts[1]).scope, "https://www.googleapis.com/auth/spreadsheets");
    assert.equal(
      await crypto.subtle.verify(
        "RSASSA-PKCS1-v1_5", keys.publicKey, Buffer.from(parts[2], "base64url"),
        new TextEncoder().encode(`${parts[0]}.${parts[1]}`),
      ),
      true,
    );
    return Response.json({ access_token: "google-access-token", expires_in: 3600, token_type: "Bearer" });
  }, () => new Date("2026-08-09T10:00:00Z"));

  assert.equal(await provider(), "google-access-token");
  assert.equal(await provider(), "google-access-token");
  assert.equal(calls, 1);
});

test("service-account configuration rejects alternate token hosts", () => {
  assert.throws(() => createGoogleAccessTokenProvider(JSON.stringify({
    client_email: "worker-test@example.iam.gserviceaccount.com",
    private_key: "private",
    token_uri: "https://attacker.example/token",
  })));
});
