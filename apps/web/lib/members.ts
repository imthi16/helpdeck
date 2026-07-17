import { api } from "@/lib/api";
import type { Role } from "@/lib/session";

export interface Member {
  user_id: string;
  email: string;
  name: string;
  role: Role;
  created_at: string;
}

export interface Invite {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  created_at: string;
}

export interface CreatedInvite extends Invite {
  invite_url: string;
}

export function listMembers(): Promise<Member[]> {
  return api<Member[]>("/api/v1/members");
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

export function listInvites(): Promise<Invite[]> {
  return api<Invite[]>("/api/v1/members/invites");
}

export function createInvite(email: string, role: Role): Promise<CreatedInvite> {
  return api<CreatedInvite>("/api/v1/members/invites", {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
}

export function revokeInvite(inviteId: string): Promise<void> {
  return api<void>(`/api/v1/members/invites/${inviteId}`, { method: "DELETE" });
}

export function acceptInvite(token: string): Promise<Member> {
  return api<Member>("/api/v1/members/invites/accept", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}
