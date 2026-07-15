"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/page-header";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api";
import {
  changeMemberRole,
  createInvite,
  listInvites,
  listMembers,
  removeMember,
  revokeInvite,
  type Invite,
  type Member,
} from "@/lib/members";
import { useSession, type Role } from "@/lib/session";

const ROLES: Role[] = ["owner", "admin", "agent", "viewer"];
const RANK: Record<Role, number> = { viewer: 0, agent: 1, admin: 2, owner: 3 };

function roleSelect(
  value: Role,
  onChange: (role: Role) => void,
  disabled: boolean,
  testId: string,
) {
  return (
    <select
      value={value}
      disabled={disabled}
      data-testid={testId}
      onChange={(e) => onChange(e.target.value as Role)}
      className="h-8 rounded-md border border-input bg-background px-2 text-sm disabled:opacity-60"
    >
      {ROLES.map((role) => (
        <option key={role} value={role}>
          {role}
        </option>
      ))}
    </select>
  );
}

export default function MembersPage() {
  const { user } = useSession();
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("agent");
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [removing, setRemoving] = useState<Member | null>(null);

  const myRole: Role = user?.memberships[0]?.role ?? "viewer";
  const canManage = RANK[myRole] >= RANK.admin;

  const refresh = useCallback(async () => {
    try {
      setMembers(await listMembers());
      if (RANK[myRole] >= RANK.admin) {
        setInvites(await listInvites());
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load members");
    }
  }, [myRole]);

  useEffect(() => {
    let active = true;
    const admin = RANK[myRole] >= RANK.admin;
    Promise.all([listMembers(), admin ? listInvites() : Promise.resolve([] as Invite[])])
      .then(([memberRows, inviteRows]) => {
        if (!active) return;
        setMembers(memberRows);
        setInvites(inviteRows);
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof ApiError ? err.message : "Failed to load members");
        }
      });
    return () => {
      active = false;
    };
  }, [myRole]);

  const manageable = useMemo(
    () => (member: Member) =>
      canManage && (myRole === "owner" || RANK[myRole] > RANK[member.role]),
    [canManage, myRole],
  );

  async function onInvite(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const created = await createInvite(inviteEmail, inviteRole);
      setInviteUrl(created.invite_url);
      setInviteEmail("");
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Invite failed");
    }
  }

  async function onChangeRole(member: Member, role: Role) {
    setError(null);
    try {
      await changeMemberRole(member.user_id, role);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Role change failed");
    }
  }

  async function onRemove() {
    if (!removing) return;
    setError(null);
    try {
      await removeMember(removing.user_id);
      setRemoving(null);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Remove failed");
      setRemoving(null);
    }
  }

  async function copyInviteUrl() {
    if (!inviteUrl) return;
    await navigator.clipboard.writeText(inviteUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Members"
        description="People with access to this organization."
        actions={
          canManage ? (
            <Button data-testid="invite-open" onClick={() => setInviteOpen(true)}>
              Invite member
            </Button>
          ) : undefined
        }
      />

      {error ? (
        <p className="text-sm text-destructive" data-testid="members-error">
          {error}
        </p>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Members</CardTitle>
        </CardHeader>
        <CardContent>
          <Table data-testid="members-table">
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead className="w-24" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.map((member) => (
                <TableRow key={member.user_id}>
                  <TableCell>{member.name || "—"}</TableCell>
                  <TableCell>{member.email}</TableCell>
                  <TableCell>
                    {manageable(member) ? (
                      roleSelect(
                        member.role,
                        (role) => void onChangeRole(member, role),
                        false,
                        `role-select-${member.email}`,
                      )
                    ) : (
                      <Badge variant="secondary">{member.role}</Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    {manageable(member) && member.user_id !== user?.id ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        data-testid={`remove-${member.email}`}
                        onClick={() => setRemoving(member)}
                      >
                        Remove
                      </Button>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {canManage && invites.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Pending invites</CardTitle>
          </CardHeader>
          <CardContent>
            <Table data-testid="invites-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Expires</TableHead>
                  <TableHead className="w-24" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {invites.map((invite) => (
                  <TableRow key={invite.id}>
                    <TableCell>{invite.email}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{invite.role}</Badge>
                    </TableCell>
                    <TableCell>{new Date(invite.expires_at).toLocaleDateString()}</TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => revokeInvite(invite.id).then(refresh)}
                      >
                        Revoke
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : null}

      <Dialog
        open={inviteOpen}
        onOpenChange={(open) => {
          setInviteOpen(open);
          if (!open) setInviteUrl(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Invite a member</DialogTitle>
            <DialogDescription>
              Invites are shared as a link — copy it and send it yourself (no email is sent).
            </DialogDescription>
          </DialogHeader>
          {inviteUrl ? (
            <div className="flex flex-col gap-3">
              <p className="text-sm font-medium">
                Invite created. Copy the link now — it won&apos;t be shown again:
              </p>
              <div className="flex items-center gap-2">
                <Input readOnly value={inviteUrl} data-testid="invite-url" />
                <Button type="button" onClick={copyInviteUrl}>
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
          ) : (
            <form onSubmit={onInvite} className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="invite-email">Email</Label>
                <Input
                  id="invite-email"
                  type="email"
                  required
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="invite-role">Role</Label>
                {roleSelect(inviteRole, setInviteRole, false, "invite-role")}
              </div>
              <DialogFooter>
                <Button type="submit" data-testid="invite-submit">
                  Create invite link
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={removing !== null} onOpenChange={(open) => !open && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {removing?.email}?</AlertDialogTitle>
            <AlertDialogDescription>
              They will immediately lose access to this organization.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void onRemove()}>Remove</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
