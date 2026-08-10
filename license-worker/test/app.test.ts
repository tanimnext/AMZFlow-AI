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
  async list() { return { users: this.user ? [{ ...this.user }] : [], total: this.user ? 1 : 0 }; }
  async create(user: LicenseRecord) { this.user = { ...user }; }
  async delete(email: string) { if (this.user?.email === email) this.user = null; }
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

test("internal failures emit safe structured diagnostics", async () => {
  const events: unknown[] = [];
  const store = new FakeStore(null);
  store.findByEmail = async () => { throw new Error("Google authentication failed"); };
  const app = createApp({ store, signingSecret: secret, logger: (event) => events.push(event) });
  const response = await app(post("/v1/activations", {
    email: "private@example.com", name: "Private Name", machineId: "machine-id-123", activationCode: "private-code",
  }));
  assert.equal(response.status, 500);
  const serialized = JSON.stringify(events);
  assert.match(serialized, /license_api_error|Google authentication failed/);
  assert.doesNotMatch(serialized, /private@example|Private Name|private-code/);
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
  assert.equal((await app(post("/v1/usage", { machineId: "machine-id-123", used: 1 }, token))).status, 409);
  assert.equal(store.user?.used, 2);
  assert.equal((await app(post("/v1/usage", { machineId: "machine-id-123", used: 6 }, token))).status, 403);
});

test("admin endpoints reject missing or incorrect bearer tokens", async () => {
  const app = createApp({ store: await fixture(), signingSecret: secret, adminToken: "test-admin-token-with-enough-entropy" });
  assert.equal((await app(new Request("https://license.test/v1/admin/users"))).status, 401);
  assert.equal((await app(new Request("https://license.test/v1/admin/users", { headers: { authorization: "Bearer wrong-token" } }))).status, 401);
});

test("admin list includes machine metadata but never activation hashes", async () => {
  const store = await fixture();
  store.user!.machineId = "machine-id-123";
  const adminToken = "test-admin-token-with-enough-entropy";
  const app = createApp({ store, signingSecret: secret, adminToken });
  const response = await app(new Request("https://license.test/v1/admin/users", {
    headers: { authorization: `Bearer ${adminToken}` },
  }));
  const text = await response.text();
  assert.match(text, /machine-id-123/);
  assert.doesNotMatch(text, /activationCodeHash|test-only-signing/);
});

test("admin can create a user and receives the activation code only once", async () => {
  const store = new FakeStore(null);
  const adminToken = "test-admin-token-with-enough-entropy";
  const app = createApp({ store, signingSecret: secret, adminToken });
  const response = await app(post("/v1/admin/users", {
    name: "New Customer", email: "new@example.com", quota: 10,
    expiryDate: "Lifetime", expiryTime: "00:00",
  }, adminToken));
  const body = await response.json() as { data: { activationCode: string } };
  assert.equal(response.status, 201);
  assert.match(body.data.activationCode, /^[a-z0-9]{4}(?:-[a-z0-9]{4}){3}$/);
  assert.notEqual(store.user?.activationCodeHash, body.data.activationCode);
  assert.equal(store.user?.tokenVersion, 1);
});

test("admin machine reset revokes tokens and creates a new activation code", async () => {
  const store = await fixture();
  store.user!.machineId = "machine-id-123";
  store.user!.activationCodeHash = "";
  const adminToken = "test-admin-token-with-enough-entropy";
  const app = createApp({ store, signingSecret: secret, adminToken });
  const response = await app(post("/v1/admin/users/user%40example.com/reset-machine", {}, adminToken));
  assert.equal(response.status, 200);
  assert.equal(store.user?.machineId, "");
  assert.equal(store.user?.tokenVersion, 2);
  assert.ok(store.user?.activationCodeHash);
});

test("admin can update, reset usage, regenerate code, and delete a user", async () => {
  const store = await fixture();
  const adminToken = "test-admin-token-with-enough-entropy";
  const app = createApp({ store, signingSecret: secret, adminToken });
  const patchResponse = await app(new Request("https://license.test/v1/admin/users/user%40example.com", {
    method: "PATCH", headers: { authorization: `Bearer ${adminToken}`, "content-type": "application/json" },
    body: JSON.stringify({ quota: 20 }),
  }));
  assert.equal(patchResponse.status, 200);
  assert.equal(store.user?.quota, 20);
  assert.equal((await app(post("/v1/admin/users/user%40example.com/reset-usage", {}, adminToken))).status, 200);
  assert.equal(store.user?.used, 0);
  assert.equal((await app(post("/v1/admin/users/user%40example.com/activation-code", {}, adminToken))).status, 200);
  assert.equal(store.user?.tokenVersion, 2);
  const deleted = await app(new Request("https://license.test/v1/admin/users/user%40example.com", {
    method: "DELETE", headers: { authorization: `Bearer ${adminToken}` },
  }));
  assert.equal(deleted.status, 204);
  assert.equal(store.user, null);
});
