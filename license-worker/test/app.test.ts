import assert from "node:assert/strict";
import test from "node:test";

import { createApp } from "../src/app.ts";
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

function fixture(maxDevices = 1) {
  return new FakeStore({
    name: "Customer Name", email: "user@example.com", machineIds: [], maxDevices, lastLogin: "",
    used: 1, quota: 5, expiryDate: "Lifetime", expiryTime: "00:00",
    activationCodeHash: "", tokenVersion: 1,
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

test("activation binds the machine by email alone and returns a token", async () => {
  const store = fixture();
  const app = createApp({ store, signingSecret: secret });
  const response = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-123",
  }));
  const body = await response.json() as { data: { activationToken: string } };
  assert.equal(response.status, 200);
  assert.match(body.data.activationToken, /^v1\./);
  assert.deepEqual(store.user?.machineIds, ["machine-id-123"]);
});

test("activation is rejected for an unknown email or once the device limit is reached", async () => {
  const store = fixture(1);
  const app = createApp({ store, signingSecret: secret });
  const unknown = await app(post("/v1/activations", {
    email: "nobody@example.com", name: "Nobody", machineId: "machine-id-123",
  }));
  assert.equal(unknown.status, 401);

  await app(post("/v1/activations", { email: "user@example.com", name: "Customer Name", machineId: "machine-id-123" }));
  const otherMachine = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-456",
  }));
  assert.equal(otherMachine.status, 401);
  assert.deepEqual(await unknown.json(), await otherMachine.json());
});

test("activation is allowed on a second device once maxDevices raises the limit", async () => {
  const store = fixture(2);
  const app = createApp({ store, signingSecret: secret });
  await app(post("/v1/activations", { email: "user@example.com", name: "Customer Name", machineId: "machine-id-123" }));
  const second = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-456",
  }));
  assert.equal(second.status, 200);
  assert.deepEqual(store.user?.machineIds, ["machine-id-123", "machine-id-456"]);

  const third = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-789",
  }));
  assert.equal(third.status, 401);
});

test("re-activating an already-bound device does not add a duplicate entry", async () => {
  const store = fixture(1);
  const app = createApp({ store, signingSecret: secret });
  await app(post("/v1/activations", { email: "user@example.com", name: "Customer Name", machineId: "machine-id-123" }));
  const again = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-123",
  }));
  assert.equal(again.status, 200);
  assert.deepEqual(store.user?.machineIds, ["machine-id-123"]);
});

test("internal failures emit safe structured diagnostics", async () => {
  const events: unknown[] = [];
  const store = new FakeStore(null);
  store.findByEmail = async () => { throw new Error("Google authentication failed"); };
  const app = createApp({ store, signingSecret: secret, logger: (event) => events.push(event) });
  const response = await app(post("/v1/activations", {
    email: "private@example.com", name: "Private Name", machineId: "machine-id-123",
  }));
  assert.equal(response.status, 500);
  const serialized = JSON.stringify(events);
  assert.match(serialized, /license_api_error|Google authentication failed/);
  assert.doesNotMatch(serialized, /private@example|Private Name/);
});

test("verification requires a valid token and current machine", async () => {
  const store = fixture();
  const app = createApp({ store, signingSecret: secret });
  const activation = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-123",
  }));
  const token = ((await activation.json()) as { data: { activationToken: string } }).data.activationToken;
  const verified = await app(post("/v1/licenses/verify", { machineId: "machine-id-123" }, token));
  assert.equal(verified.status, 200);
  assert.equal(((await verified.json()) as { data: { license: { used: number } } }).data.license.used, 1);
  assert.equal((await app(post("/v1/licenses/verify", { machineId: "other-machine" }, token))).status, 401);
});

test("usage updates are authenticated, bounded, and persisted", async () => {
  const store = fixture();
  const app = createApp({ store, signingSecret: secret });
  const activation = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-123",
  }));
  const token = ((await activation.json()) as { data: { activationToken: string } }).data.activationToken;
  assert.equal((await app(post("/v1/usage", { machineId: "machine-id-123", used: 2 }, token))).status, 200);
  assert.equal(store.user?.used, 2);
  assert.equal((await app(post("/v1/usage", { machineId: "machine-id-123", used: 1 }, token))).status, 409);
  assert.equal(store.user?.used, 2);
  assert.equal((await app(post("/v1/usage", { machineId: "machine-id-123", used: 6 }, token))).status, 403);
});

test("admin endpoints reject missing or incorrect bearer tokens", async () => {
  const app = createApp({ store: fixture(), signingSecret: secret, adminToken: "test-admin-token-with-enough-entropy" });
  assert.equal((await app(new Request("https://license.test/v1/admin/users"))).status, 401);
  assert.equal((await app(new Request("https://license.test/v1/admin/users", { headers: { authorization: "Bearer wrong-token" } }))).status, 401);
});

test("admin list includes device metadata but never activation hashes", async () => {
  const store = fixture();
  store.user!.machineIds = ["machine-id-123"];
  const adminToken = "test-admin-token-with-enough-entropy";
  const app = createApp({ store, signingSecret: secret, adminToken });
  const response = await app(new Request("https://license.test/v1/admin/users", {
    headers: { authorization: `Bearer ${adminToken}` },
  }));
  const text = await response.text();
  assert.match(text, /machine-id-123/);
  assert.match(text, /maxDevices/);
  assert.doesNotMatch(text, /activationCodeHash|test-only-signing/);
});

test("admin can create a user with a device limit who can activate immediately by email", async () => {
  const store = new FakeStore(null);
  const adminToken = "test-admin-token-with-enough-entropy";
  const app = createApp({ store, signingSecret: secret, adminToken });
  const response = await app(post("/v1/admin/users", {
    name: "New Customer", email: "new@example.com", quota: 10,
    expiryDate: "Lifetime", expiryTime: "00:00", maxDevices: 3,
  }, adminToken));
  assert.equal(response.status, 201);
  assert.equal(store.user?.tokenVersion, 1);
  assert.deepEqual(store.user?.machineIds, []);
  assert.equal(store.user?.maxDevices, 3);

  const activation = await app(post("/v1/activations", {
    email: "new@example.com", name: "New Customer", machineId: "machine-id-999",
  }));
  assert.equal(activation.status, 200);
  assert.deepEqual(store.user?.machineIds, ["machine-id-999"]);
});

test("admin user creation without maxDevices defaults to a single device", async () => {
  const store = new FakeStore(null);
  const adminToken = "test-admin-token-with-enough-entropy";
  const app = createApp({ store, signingSecret: secret, adminToken });
  await app(post("/v1/admin/users", {
    name: "New Customer", email: "new@example.com", quota: 10,
    expiryDate: "Lifetime", expiryTime: "00:00",
  }, adminToken));
  assert.equal(store.user?.maxDevices, 1);
});

test("admin machine reset clears every device and revokes tokens account-wide", async () => {
  const store = fixture(2);
  store.user!.machineIds = ["machine-id-123", "machine-id-456"];
  const adminToken = "test-admin-token-with-enough-entropy";
  const app = createApp({ store, signingSecret: secret, adminToken });
  const response = await app(post("/v1/admin/users/user%40example.com/reset-machine", {}, adminToken));
  assert.equal(response.status, 200);
  assert.deepEqual(store.user?.machineIds, []);
  assert.equal(store.user?.tokenVersion, 2);
});

test("admin can remove one device without signing the other devices out", async () => {
  const store = fixture(2);
  const adminToken = "test-admin-token-with-enough-entropy";
  const app = createApp({ store, signingSecret: secret, adminToken });
  const firstActivation = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-123",
  }));
  const firstToken = ((await firstActivation.json()) as { data: { activationToken: string } }).data.activationToken;
  const secondActivation = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-456",
  }));
  const secondToken = ((await secondActivation.json()) as { data: { activationToken: string } }).data.activationToken;

  const removed = await app(post("/v1/admin/users/user%40example.com/remove-device", { machineId: "machine-id-123" }, adminToken));
  assert.equal(removed.status, 200);
  assert.deepEqual(store.user?.machineIds, ["machine-id-456"]);
  assert.equal(store.user?.tokenVersion, 1, "tokenVersion must not bump -- that would sign out every device, not just the removed one");

  // The removed device's own token can no longer verify...
  const removedDeviceCheck = await app(post("/v1/licenses/verify", { machineId: "machine-id-123" }, firstToken));
  assert.equal(removedDeviceCheck.status, 401);
  // ...but the still-bound device's own token keeps working.
  const remainingDeviceCheck = await app(post("/v1/licenses/verify", { machineId: "machine-id-456" }, secondToken));
  assert.equal(remainingDeviceCheck.status, 200);

  // The freed slot can now be used by a new device.
  const reactivated = await app(post("/v1/activations", {
    email: "user@example.com", name: "Customer Name", machineId: "machine-id-789",
  }));
  assert.equal(reactivated.status, 200);
});

test("admin can update, reset usage, and delete a user", async () => {
  const store = fixture();
  const adminToken = "test-admin-token-with-enough-entropy";
  const app = createApp({ store, signingSecret: secret, adminToken });
  const patchResponse = await app(new Request("https://license.test/v1/admin/users/user%40example.com", {
    method: "PATCH", headers: { authorization: `Bearer ${adminToken}`, "content-type": "application/json" },
    body: JSON.stringify({ quota: 20, maxDevices: 5 }),
  }));
  assert.equal(patchResponse.status, 200);
  assert.equal(store.user?.quota, 20);
  assert.equal(store.user?.maxDevices, 5);
  assert.equal((await app(post("/v1/admin/users/user%40example.com/reset-usage", {}, adminToken))).status, 200);
  assert.equal(store.user?.used, 0);
  const deleted = await app(new Request("https://license.test/v1/admin/users/user%40example.com", {
    method: "DELETE", headers: { authorization: `Bearer ${adminToken}` },
  }));
  assert.equal(deleted.status, 204);
  assert.equal(store.user, null);
});
