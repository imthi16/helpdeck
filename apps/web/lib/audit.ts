import { api } from "@/lib/api";

export interface AuditEvent {
  id: string;
  action: string;
  actor_email: string | null;
  target_type: string | null;
  target_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

/** Human wording for each recorded action. */
export const ACTION_LABELS: Record<string, string> = {
  "auth.signup": "Signed up",
  "auth.login": "Logged in",
  "member.invited": "Invited a member",
  "member.invite_revoked": "Revoked an invite",
  "member.joined": "Joined the organization",
  "member.role_changed": "Changed a member's role",
  "member.removed": "Removed a member",
  "api_key.created": "Created an API key",
  "api_key.revoked": "Revoked an API key",
  "document.deleted": "Deleted a document",
  "settings.updated": "Updated settings",
};

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

/** The interesting bits of `metadata`, rendered compactly. */
export function describeEvent(event: AuditEvent): string {
  const parts = Object.entries(event.metadata).map(([key, value]) => `${key}: ${String(value)}`);
  return parts.join(" · ");
}

export function listAuditEvents(limit = 50): Promise<AuditEvent[]> {
  return api<AuditEvent[]>(`/api/v1/audit?limit=${limit}`);
}
