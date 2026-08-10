import type { LicenseRecord, LicenseStore } from "./store.ts";

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

interface GoogleSheetsStoreOptions {
  spreadsheetId: string;
  getAccessToken: () => Promise<string>;
  fetcher?: Fetcher;
}

function cell(row: unknown[], index: number): string {
  const value = row[index];
  if (value === undefined || value === null) return "";
  if (typeof value !== "string" && typeof value !== "number") throw new Error("Invalid Google Sheets response");
  return String(value).trim();
}

function integerCell(value: string, fallback?: number): number {
  if (!value && fallback !== undefined) return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0) throw new Error("Invalid Google Sheets response");
  return parsed;
}

function rowToLicense(row: unknown[], rowNumber: number): LicenseRecord {
  const email = cell(row, 1).toLowerCase();
  if (!email || !email.includes("@")) throw new Error("Invalid Google Sheets response");
  const quotaValue = cell(row, 5) || "Unlimited";
  const quota = quotaValue.toLowerCase() === "unlimited" ? "Unlimited" : integerCell(quotaValue);
  return {
    name: cell(row, 0), email, machineId: cell(row, 2), lastLogin: cell(row, 3),
    used: integerCell(cell(row, 4), 0), quota,
    expiryDate: cell(row, 6) || "Lifetime", expiryTime: cell(row, 7) || "00:00",
    activationCodeHash: cell(row, 8), tokenVersion: integerCell(cell(row, 9), 1), rowNumber,
  };
}

export class GoogleSheetsStore implements LicenseStore {
  private spreadsheetId: string;
  private getAccessToken: () => Promise<string>;
  private fetcher: Fetcher;

  constructor(options: GoogleSheetsStoreOptions) {
    if (!/^[A-Za-z0-9_-]{10,200}$/.test(options.spreadsheetId)) throw new Error("Invalid spreadsheet configuration");
    this.spreadsheetId = options.spreadsheetId;
    this.getAccessToken = options.getAccessToken;
    this.fetcher = options.fetcher ?? fetch;
  }

  private async request(path: string, init?: RequestInit): Promise<unknown> {
    const token = await this.getAccessToken();
    const response = await this.fetcher(`https://sheets.googleapis.com/v4/spreadsheets/${encodeURIComponent(this.spreadsheetId)}${path}`, {
      ...init,
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json", ...(init?.headers || {}) },
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error("Google Sheets request failed");
    }
    try { return await response.json(); } catch { throw new Error("Invalid Google Sheets response"); }
  }

  async findByEmail(email: string): Promise<LicenseRecord | null> {
    const range = encodeURIComponent("Sheet1!A:J");
    const payload = await this.request(`/values/${range}`);
    if (typeof payload !== "object" || payload === null || !("values" in payload) || !Array.isArray(payload.values)) {
      throw new Error("Invalid Google Sheets response");
    }
    const target = email.trim().toLowerCase();
    for (let index = 1; index < payload.values.length; index += 1) {
      const row = payload.values[index];
      if (!Array.isArray(row)) throw new Error("Invalid Google Sheets response");
      if (cell(row, 1).toLowerCase() === target) return rowToLicense(row, index + 1);
    }
    return null;
  }

  async save(user: LicenseRecord): Promise<void> {
    if (!Number.isSafeInteger(user.rowNumber) || user.rowNumber! < 2) throw new Error("Missing Google Sheet row number");
    const values = [[
      user.name, user.email, user.machineId, user.lastLogin, user.used, user.quota,
      user.expiryDate, user.expiryTime, user.activationCodeHash, user.tokenVersion,
    ]];
    const payload = await this.request("/values:batchUpdate", {
      method: "POST",
      body: JSON.stringify({ valueInputOption: "RAW", data: [{ range: `Sheet1!A${user.rowNumber}:J${user.rowNumber}`, values }] }),
    });
    if (typeof payload !== "object" || payload === null || !("totalUpdatedRows" in payload) || payload.totalUpdatedRows !== 1) {
      throw new Error("Invalid Google Sheets response");
    }
  }
}
