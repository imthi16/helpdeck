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
