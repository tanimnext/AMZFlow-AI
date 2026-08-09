import assert from "node:assert/strict";
import test from "node:test";

import { parseActivationRequest } from "../src/validation.ts";

test("activation input is normalized and strictly bounded", () => {
  assert.deepEqual(
    parseActivationRequest({
      email: " User@Example.COM ",
      name: "  Customer Name ",
      machineId: "machine-id-123",
      activationCode: " ABCD-EFGH ",
    }),
    {
      email: "user@example.com",
      name: "Customer Name",
      machineId: "machine-id-123",
      activationCode: "abcd-efgh",
    },
  );
});

test("activation input rejects missing, extra, and oversized values", () => {
  assert.throws(() => parseActivationRequest({ email: "user@example.com" }));
  assert.throws(() =>
    parseActivationRequest({
      email: "user@example.com",
      name: "Customer",
      machineId: "machine-id-123",
      activationCode: "abcd-efgh",
      unexpected: "field",
    }),
  );
  assert.throws(() =>
    parseActivationRequest({
      email: "user@example.com",
      name: "x".repeat(121),
      machineId: "machine-id-123",
      activationCode: "abcd-efgh",
    }),
  );
});
