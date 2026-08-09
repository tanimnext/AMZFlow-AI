import assert from "node:assert/strict";
import test from "node:test";

import { createApp } from "../src/app.ts";
import { hashActivationCode } from "../src/security.ts";
import type { LicenseRecord, LicenseStore } from "../src/store.ts";

const secret = "test-only-signing-secret-with-enough-entropy";

class FakeStore implements LicenseStore {
  user: LicenseRecord | null;
  constructor(user: LicenseRecord | null) { this.user = user; }
  async findByEmail(email: string) { return this.user?.email === email ? { ...this.user } : null; }
  async save(user: LicenseRecord) { this.user = { ...user }; }
}

async function fixture() {
  return new FakeStore({
    name: "Customer Name", email: "user@example.com", machineId: "", lastLogin: "",
    used: 1, quota: 5, expiryDate: "Lifetime", expiryTime: "00:00",
    activationCodeHash: await hashActivationCode("abcd-efgh", "user@example.com", secret),
    tokenVersion: 1,
  });
}

function post(path: string, body: unknown, token?: string) {
  return new Request(`https://license.test${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
}

test("health response is minimal and hardened", async () => {
  const response = await createApp({ store: new FakeStore(null), signingSecret: secret })(new Request("https://license.test/v1/health"));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { data: { status: "ok", apiVersion: "v1" } });
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
});

test("activation binds the machine, consumes code, and returns a token", async () => {
  const store = await fixture();
  const app = createApp({ store, signingSecret: secret });
  const response = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-123", activationCode: "abcd-efgh",
  }));
  const body = await response.json() as { data: { activationToken: string } };
  assert.equal(response.status, 200);
  assert.match(body.data.activationToken, /^v1\./);
  assert.equal(store.user?.machineId, "machine-id-123");
  assert.equal(store.user?.activationCodeHash, "");
});

test("invalid activation attempts have one generic response", async () => {
  const app = createApp({ store: await fixture(), signingSecret: secret });
  const unknown = await app(post("/v1/activations", {
    email: "nobody@example.com", name: "Nobody", machineId: "machine-id-123", activationCode: "wrong-code",
  }));
  const wrongCode = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-123", activationCode: "wrong-code",
  }));
  assert.equal(unknown.status, 401);
  assert.deepEqual(await unknown.json(), await wrongCode.json());
});

test("verification requires a valid token and current machine", async () => {
  const store = await fixture();
  const app = createApp({ store, signingSecret: secret });
  const activation = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-123", activationCode: "abcd-efgh",
  }));
  const token = ((await activation.json()) as { data: { activationToken: string } }).data.activationToken;
  const verified = await app(post("/v1/licenses/verify", { machineId: "machine-id-123" }, token));
  assert.equal(verified.status, 200);
  assert.equal(((await verified.json()) as { data: { license: { used: number } } }).data.license.used, 1);
  assert.equal((await app(post("/v1/licenses/verify", { machineId: "other-machine" }, token))).status, 401);
});

test("usage updates are authenticated, bounded, and persisted", async () => {
  const store = await fixture();
  const app = createApp({ store, signingSecret: secret });
  const activation = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-123", activationCode: "abcd-efgh",
  }));
  const token = ((await activation.json()) as { data: { activationToken: string } }).data.activationToken;
  assert.equal((await app(post("/v1/usage", { machineId: "machine-id-123", used: 2 }, token))).status, 200);
  assert.equal(store.user?.used, 2);
  assert.equal((await app(post("/v1/usage", { machineId: "machine-id-123", used: 6 }, token))).status, 403);
});
