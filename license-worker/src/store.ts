export interface LicenseRecord {
  name: string;
  email: string;
  machineId: string;
  lastLogin: string;
  used: number;
  quota: number | "Unlimited";
  expiryDate: string;
  expiryTime: string;
  activationCodeHash: string;
  tokenVersion: number;
  rowNumber?: number;
}

export interface LicenseStore {
  findByEmail(email: string): Promise<LicenseRecord | null>;
  save(user: LicenseRecord): Promise<void>;
  list(page: number, pageSize: number): Promise<{ users: LicenseRecord[]; total: number }>;
  create(user: LicenseRecord): Promise<void>;
  delete(email: string): Promise<void>;
}
