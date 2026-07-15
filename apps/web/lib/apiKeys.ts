import { api } from "@/lib/api";

export type ApiKeyType = "widget" | "secret";

export interface ApiKeyItem {
  id: string;
  name: string;
  key_type: ApiKeyType;
  prefix: string;
  // Plaintext for widget keys (public by design); null for secret keys.
  public_value: string | null;
  scopes: string[];
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface CreatedApiKey extends ApiKeyItem {
  token: string;
}

export function listApiKeys(): Promise<ApiKeyItem[]> {
  return api<ApiKeyItem[]>("/api/v1/keys");
}

export function createApiKey(name: string, keyType: ApiKeyType): Promise<CreatedApiKey> {
  return api<CreatedApiKey>("/api/v1/keys", {
    method: "POST",
    body: JSON.stringify({ name, key_type: keyType }),
  });
}

export function revokeApiKey(id: string): Promise<ApiKeyItem> {
  return api<ApiKeyItem>(`/api/v1/keys/${id}`, { method: "DELETE" });
}

export interface AuditLogEntry {
  id: number;
  actor_user_id: string | null;
  actor_type: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  payload: Record<string, unknown>;
  ip: string | null;
  created_at: string;
}

export function listAuditLogs(limit = 50): Promise<AuditLogEntry[]> {
  return api<AuditLogEntry[]>(`/api/v1/audit-logs?limit=${limit}`);
}
