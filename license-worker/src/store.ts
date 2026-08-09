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
}

export interface LicenseStore {
  findByEmail(email: string): Promise<LicenseRecord | null>;
  save(user: LicenseRecord): Promise<void>;
}
