import assert from "node:assert/strict";
import test from "node:test";

import { GoogleSheetsStore } from "../src/google_sheets_store.ts";

const row = ["Customer", "user@example.com", "machine-1", "2026-08-09", "2", "5", "Lifetime", "00:00", "hash", "3"];

test("Google Sheet rows are validated and mapped from A:J", async () => {
  const requests: Request[] = [];
  const store = new GoogleSheetsStore({
    spreadsheetId: "sheet-id-123",
    getAccessToken: async () => "access-token",
    fetcher: async (input, init) => {
      requests.push(new Request(input, init));
      return Response.json({ values: [["Name", "Email"], row] });
    },
  });
  const user = await store.findByEmail("user@example.com");
  assert.equal(user?.quota, 5);
  assert.equal(user?.tokenVersion, 3);
  assert.equal(user?.rowNumber, 2);
  assert.match(requests[0].url, /Sheet1!A%3AJ/);
  assert.equal(requests[0].headers.get("authorization"), "Bearer access-token");
});

test("save writes one complete A:J row using batchUpdate", async () => {
  let writeBody: unknown;
  const store = new GoogleSheetsStore({
    spreadsheetId: "sheet-id-123",
    getAccessToken: async () => "access-token",
    fetcher: async (_input, init) => {
      writeBody = JSON.parse(String(init?.body));
      return Response.json({ totalUpdatedRows: 1 });
    },
  });
  await store.save({
    name: "Customer", email: "user@example.com", machineId: "machine-1", lastLogin: "now",
    used: 3, quota: "Unlimited", expiryDate: "Lifetime", expiryTime: "00:00",
    activationCodeHash: "", tokenVersion: 3, rowNumber: 2,
  });
  assert.deepEqual(writeBody, {
    valueInputOption: "RAW",
    data: [{ range: "Sheet1!A2:J2", values: [["Customer", "user@example.com", "machine-1", "now", 3, "Unlimited", "Lifetime", "00:00", "", 3]] }],
  });
});

test("malformed Google responses are rejected", async () => {
  const store = new GoogleSheetsStore({
    spreadsheetId: "sheet-id-123",
    getAccessToken: async () => "access-token",
    fetcher: async () => Response.json({ values: "not-an-array" }),
  });
  await assert.rejects(() => store.findByEmail("user@example.com"));
});

test("Google HTTP errors do not expose response content", async () => {
  const store = new GoogleSheetsStore({
    spreadsheetId: "sheet-id-123",
    getAccessToken: async () => "access-token",
    fetcher: async () => new Response("private Google details", { status: 403 }),
  });
  await assert.rejects(() => store.findByEmail("user@example.com"), /^Error: Google Sheets request failed$/);
});
