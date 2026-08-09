import assert from "node:assert/strict";
import test from "node:test";

import {
  hashActivationCode,
  issueActivationToken,
  verifyActivationCode,
  verifyActivationToken,
} from "../src/security.ts";

const secret = "test-only-signing-secret-with-enough-entropy";

test("activation codes are normalized, hashed, and never stored raw", async () => {
  const hash = await hashActivationCode("ABCD-EFGH", "USER@example.com", secret);
  assert.notEqual(hash, "ABCD-EFGH");
  assert.equal(await verifyActivationCode("abcd-efgh", "user@example.com", hash, secret), true);
  assert.equal(await verifyActivationCode("wrong-code", "user@example.com", hash, secret), false);
});

test("activation token is bound to machine and token version", async () => {
  const now = new Date("2026-08-09T10:00:00.000Z");
  const token = await issueActivationToken(
    { email: "user@example.com", machineId: "machine-a", tokenVersion: 2 },
    secret,
    now,
  );

  assert.equal(
    (await verifyActivationToken(token, "machine-a", 2, secret, now)).email,
    "user@example.com",
  );
  await assert.rejects(() => verifyActivationToken(token, "machine-b", 2, secret, now));
  await assert.rejects(() => verifyActivationToken(token, "machine-a", 3, secret, now));
});

test("activation token expires", async () => {
  const issuedAt = new Date("2026-08-09T10:00:00.000Z");
  const token = await issueActivationToken(
    { email: "user@example.com", machineId: "machine-a", tokenVersion: 1 },
    secret,
    issuedAt,
    60,
  );

  await assert.rejects(() =>
    verifyActivationToken(token, "machine-a", 1, secret, new Date("2026-08-09T10:01:01.000Z")),
  );
});
