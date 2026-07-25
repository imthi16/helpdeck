import { api } from "@/lib/api";
import type { Role, SessionUser } from "@/lib/session";

export interface Member {
  user_id: string;
  email: string;
  name: string;
  role: Role;
  joined_at: string;
}

export interface Invite {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  created_at: string;
  /** Only returned when the invite is created — the raw token lives in the link. */
  invite_url?: string | null;
}

export interface InvitePreview {
  org_name: string;
  email: string;
  role: Role;
  needs_account: boolean;
}

/** Roles that may manage the knowledge base, members, and settings. */
export const ADMIN_ROLES: Role[] = ["owner", "admin"];
/** Roles that may act on conversations and use the playground. */
export const AGENT_ROLES: Role[] = ["owner", "admin", "agent"];

export const ROLE_HELP: Record<Role, string> = {
  owner: "Full access, including ownership and billing.",
  admin: "Knowledge base, members, and settings.",
  agent: "Conversations, escalations, and the playground.",
  viewer: "Read-only access.",
};

export function listMembers(): Promise<Member[]> {
  return api<Member[]>("/api/v1/members");
}

export function listInvites(): Promise<Invite[]> {
  return api<Invite[]>("/api/v1/members/invites");
}

export function inviteMember(email: string, role: Role): Promise<Invite> {
  return api<Invite>("/api/v1/members/invites", {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
}

export function revokeInvite(id: string): Promise<void> {
  return api<void>(`/api/v1/members/invites/${id}`, { method: "DELETE" });
}

export function changeMemberRole(userId: string, role: Role): Promise<Member> {
  return api<Member>(`/api/v1/members/${userId}`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

export function removeMember(userId: string): Promise<void> {
  return api<void>(`/api/v1/members/${userId}`, { method: "DELETE" });
}

export function previewInvite(token: string): Promise<InvitePreview> {
  return api<InvitePreview>(`/auth/invites/${token}`);
}

export function acceptInvite(
  token: string,
  input: { name?: string; password?: string } = {},
): Promise<SessionUser> {
  return api<SessionUser>(`/auth/invites/${token}/accept`, {
    method: "POST",
    body: JSON.stringify({ name: input.name ?? "", password: input.password ?? null }),
  });
}
