import { api } from "@/lib/api";

export type ApiKeyKind = "public" | "secret";

export interface ApiKey {
  id: string;
  name: string;
  kind: ApiKeyKind;
  prefix: string;
  /** Public keys carry their value (they are not secrets); secrets do not. */
  value: string | null;
  scopes: string[];
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
  /** Returned exactly once, in the response to creating a secret key. */
  secret?: string | null;
}

export function listApiKeys(): Promise<ApiKey[]> {
  return api<ApiKey[]>("/api/v1/keys");
}

export function fetchWidgetKey(): Promise<ApiKey> {
  return api<ApiKey>("/api/v1/keys/widget");
}

export function createApiKey(kind: ApiKeyKind, name: string): Promise<ApiKey> {
  return api<ApiKey>("/api/v1/keys", {
    method: "POST",
    body: JSON.stringify({ kind, name }),
  });
}

export function revokeApiKey(id: string): Promise<void> {
  return api<void>(`/api/v1/keys/${id}`, { method: "DELETE" });
}

export function embedSnippet(publicKey: string): string {
  return `<script src="https://cdn.helpdeck.example/helpdeck.js" data-public-key="${publicKey}" defer></script>`;
}
