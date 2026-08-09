export interface ActivationRequest {
  email: string;
  name: string;
  machineId: string;
  activationCode: string;
}

export interface MachineRequest { machineId: string; }
export interface UsageRequest extends MachineRequest { used: number; }

const EMAIL_PATTERN = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$/;
const MACHINE_PATTERN = /^[A-Za-z0-9._:-]{8,200}$/;
const CODE_PATTERN = /^[a-z0-9-]{8,80}$/;
const ACTIVATION_FIELDS = new Set(["email", "name", "machineId", "activationCode"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function boundedString(value: unknown, maximum: number): string {
  if (typeof value !== "string") throw new Error("Invalid request");
  const normalized = value.trim();
  if (!normalized || normalized.length > maximum) throw new Error("Invalid request");
  return normalized;
}

export function parseActivationRequest(value: unknown): ActivationRequest {
  if (!isRecord(value) || Object.keys(value).some((key) => !ACTIVATION_FIELDS.has(key))) {
    throw new Error("Invalid request");
  }
  const email = boundedString(value.email, 254).toLowerCase();
  const name = boundedString(value.name, 120);
  const machineId = boundedString(value.machineId, 200);
  const activationCode = boundedString(value.activationCode, 80).toLowerCase();
  if (!EMAIL_PATTERN.test(email) || !MACHINE_PATTERN.test(machineId) || !CODE_PATTERN.test(activationCode)) {
    throw new Error("Invalid request");
  }
  return { email, name, machineId, activationCode };
}

export function parseMachineRequest(value: unknown): MachineRequest {
  if (!isRecord(value) || Object.keys(value).some((key) => key !== "machineId")) throw new Error("Invalid request");
  const machineId = boundedString(value.machineId, 200);
  if (!MACHINE_PATTERN.test(machineId)) throw new Error("Invalid request");
  return { machineId };
}

export function parseUsageRequest(value: unknown): UsageRequest {
  if (!isRecord(value) || Object.keys(value).some((key) => !new Set(["machineId", "used"]).has(key))) {
    throw new Error("Invalid request");
  }
  const { machineId } = parseMachineRequest({ machineId: value.machineId });
  if (!Number.isSafeInteger(value.used) || (value.used as number) < 0 || (value.used as number) > 1_000_000) {
    throw new Error("Invalid request");
  }
  return { machineId, used: value.used as number };
}
