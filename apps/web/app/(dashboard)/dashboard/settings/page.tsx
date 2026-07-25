"use client";

import { useCallback, useEffect, useState } from "react";

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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  createApiKey,
  embedSnippet,
  fetchWidgetKey,
  listApiKeys,
  revokeApiKey,
  type ApiKey,
} from "@/lib/api-keys";
import {
  ADMIN_ROLES,
  ROLE_HELP,
  changeMemberRole,
  inviteMember,
  listInvites,
  listMembers,
  removeMember,
  revokeInvite,
  type Invite,
  type Member,
} from "@/lib/members";
import { completeOnboarding, useSession, type Role } from "@/lib/session";

const ROLES: Role[] = ["owner", "admin", "agent", "viewer"];

/** Native select: one control, keyboard-accessible, and easy to drive in tests. */
function RoleSelect({
  value,
  disabled,
  onChange,
  id,
  testId,
}: {
  value: Role;
  disabled?: boolean;
  onChange: (role: Role) => void;
  id?: string;
  testId?: string;
}) {
  return (
    <select
      id={id}
      data-testid={testId}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as Role)}
      className="h-9 rounded-md border border-input bg-transparent px-2 text-sm shadow-xs disabled:cursor-not-allowed disabled:opacity-50"
    >
      {ROLES.map((role) => (
        <option key={role} value={role}>
          {role}
        </option>
      ))}
    </select>
  );
}

function OrganizationTab({
  orgName,
  canEdit,
  onSaved,
}: {
  orgName: string;
  canEdit: boolean;
  onSaved: () => Promise<unknown>;
}) {
  const [name, setName] = useState(orgName);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSaved(false);
    try {
      await completeOnboarding(name.trim());
      await onSaved();
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Organization</CardTitle>
      </CardHeader>
      <CardContent>
        <form className="flex max-w-md items-end gap-2" onSubmit={onSubmit}>
          <div className="flex-1">
            <Label htmlFor="org-name">Organization name</Label>
            <Input
              id="org-name"
              value={name}
              required
              disabled={!canEdit}
              onChange={(e) => setName(e.target.value)}
              data-testid="org-name"
            />
          </div>
          <Button type="submit" disabled={!canEdit || name.trim() === orgName}>
            Save
          </Button>
        </form>
        {!canEdit && (
          <p className="mt-2 text-sm text-muted-foreground">
            Only owners and admins can change organization settings.
          </p>
        )}
        {saved && <p className="mt-2 text-sm text-muted-foreground">Saved.</p>}
        {error && (
          <p role="alert" className="mt-2 text-sm text-red-600">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function MembersTab({ myRole, myUserId }: { myRole: Role; myUserId: string }) {
  const canManage = ADMIN_ROLES.includes(myRole);
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [lastLink, setLastLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingRemoval, setPendingRemoval] = useState<Member | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextMembers, nextInvites] = await Promise.all([listMembers(), listInvites()]);
      setMembers(nextMembers);
      setInvites(nextInvites);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load members");
    }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([listMembers(), listInvites()])
      .then(([nextMembers, nextInvites]) => {
        if (!active) return;
        setMembers(nextMembers);
        setInvites(nextInvites);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Failed to load members");
      });
    return () => {
      active = false;
    };
  }, []);

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      // Re-read either way, so a rejected change (e.g. demoting the last owner)
      // leaves the table showing what the server actually holds.
      await refresh();
    }
  }

  async function onInvite(event: React.FormEvent) {
    event.preventDefault();
    setLastLink(null);
    setCopied(false);
    await run(async () => {
      const invite = await inviteMember(email.trim(), role);
      setLastLink(invite.invite_url ?? null);
      setEmail("");
    });
  }

  async function onCopy(link: string) {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {error && (
        <p role="alert" className="text-sm text-red-600" data-testid="members-error">
          {error}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Invite a teammate</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="flex flex-wrap items-end gap-2" onSubmit={onInvite}>
            <div className="min-w-56 flex-1">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                required
                disabled={!canManage}
                placeholder="teammate@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                data-testid="invite-email"
              />
            </div>
            <div>
              <Label htmlFor="invite-role">Role</Label>
              <div>
                <RoleSelect
                  id="invite-role"
                  testId="invite-role"
                  value={role}
                  disabled={!canManage}
                  onChange={setRole}
                />
              </div>
            </div>
            <Button type="submit" disabled={!canManage} data-testid="invite-submit">
              Send invite
            </Button>
          </form>
          <p className="mt-2 text-sm text-muted-foreground">{ROLE_HELP[role]}</p>
          {!canManage && (
            <p className="mt-2 text-sm text-muted-foreground">
              Only owners and admins can invite members.
            </p>
          )}
          {lastLink && (
            <div className="mt-4 rounded-md border bg-muted/40 p-3">
              <p className="mb-2 text-sm text-muted-foreground">
                Share this link with them — it is shown once and expires in 7 days.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <code
                  className="flex-1 overflow-x-auto rounded bg-background px-2 py-1 text-xs"
                  data-testid="invite-link"
                >
                  {lastLink}
                </code>
                <Button variant="outline" size="sm" onClick={() => void onCopy(lastLink)}>
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Members</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Role</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.map((member) => (
                <TableRow key={member.user_id} data-testid="member-row" data-email={member.email}>
                  <TableCell className="font-medium">
                    {member.email}
                    {member.user_id === myUserId && (
                      <Badge variant="secondary" className="ml-2">
                        you
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>{member.name || "—"}</TableCell>
                  <TableCell>
                    <RoleSelect
                      testId="member-role"
                      value={member.role}
                      disabled={!canManage}
                      onChange={(next) => void run(() => changeMemberRole(member.user_id, next))}
                    />
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!canManage}
                      onClick={() => setPendingRemoval(member)}
                      data-testid="member-remove"
                    >
                      Remove
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {invites.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Pending invites</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Expires</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invites.map((invite) => (
                  <TableRow key={invite.id} data-testid="invite-row" data-email={invite.email}>
                    <TableCell className="font-medium">{invite.email}</TableCell>
                    <TableCell>{invite.role}</TableCell>
                    <TableCell>{new Date(invite.expires_at).toLocaleDateString()}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!canManage}
                        onClick={() => void run(() => revokeInvite(invite.id))}
                        data-testid="invite-revoke"
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
      )}

      <AlertDialog
        open={pendingRemoval !== null}
        onOpenChange={(open) => !open && setPendingRemoval(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove member?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingRemoval?.email} will lose access to this organization.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="confirm-remove"
              onClick={() => {
                const member = pendingRemoval;
                setPendingRemoval(null);
                if (member) void run(() => removeMember(member.user_id));
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ApiKeysTab({ myRole }: { myRole: Role }) {
  const canManage = ADMIN_ROLES.includes(myRole);
  const isOwner = myRole === "owner";
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [widgetKey, setWidgetKey] = useState<ApiKey | null>(null);
  const [freshSecret, setFreshSecret] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pendingRevoke, setPendingRevoke] = useState<ApiKey | null>(null);

  // Widget key first, then the list: fetching the widget key re-issues one when
  // the previous was revoked, and the list must include that new row.
  const load = useCallback(async () => {
    const nextWidget = await fetchWidgetKey();
    const nextKeys = await listApiKeys();
    return { nextWidget, nextKeys };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const { nextWidget, nextKeys } = await load();
      setWidgetKey(nextWidget);
      setKeys(nextKeys);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load API keys");
    }
  }, [load]);

  useEffect(() => {
    let active = true;
    load()
      .then(({ nextWidget, nextKeys }) => {
        if (!active) return;
        setWidgetKey(nextWidget);
        setKeys(nextKeys);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Failed to load API keys");
      });
    return () => {
      active = false;
    };
  }, [load]);

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      await refresh();
    }
  }

  async function onCreateSecret(event: React.FormEvent) {
    event.preventDefault();
    setFreshSecret(null);
    await run(async () => {
      const created = await createApiKey("secret", name.trim());
      setFreshSecret(created.secret ?? null);
      setName("");
    });
  }

  return (
    <div className="flex flex-col gap-6">
      {error && (
        <p role="alert" className="text-sm text-red-600" data-testid="keys-error">
          {error}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Widget embed snippet</CardTitle>
        </CardHeader>
        <CardContent>
          {widgetKey?.value ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <code
                  className="flex-1 overflow-x-auto rounded bg-muted/60 px-2 py-1 text-xs"
                  data-testid="embed-snippet"
                >
                  {embedSnippet(widgetKey.value)}
                </code>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void navigator.clipboard.writeText(embedSnippet(widgetKey.value!))}
                >
                  Copy
                </Button>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                The public key identifies your organization and is safe to ship in page HTML.
                Revoke it below to rotate — a new one is issued automatically.
              </p>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">No widget key yet.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Create a secret key</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="flex flex-wrap items-end gap-2" onSubmit={onCreateSecret}>
            <div className="min-w-56 flex-1">
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                required
                disabled={!isOwner}
                placeholder="CI pipeline"
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="key-name"
              />
            </div>
            <Button type="submit" disabled={!isOwner} data-testid="key-create">
              Create secret key
            </Button>
          </form>
          <p className="mt-2 text-sm text-muted-foreground">
            Secret keys are for future server-to-server use — no endpoint accepts one yet.
            {!isOwner && " Only owners can issue them."}
          </p>
          {freshSecret && (
            <div className="mt-4 rounded-md border bg-muted/40 p-3">
              <p className="mb-2 text-sm text-muted-foreground">
                Copy this now — it is shown once and cannot be retrieved again.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <code
                  className="flex-1 overflow-x-auto rounded bg-background px-2 py-1 text-xs"
                  data-testid="fresh-secret"
                >
                  {freshSecret}
                </code>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void navigator.clipboard.writeText(freshSecret)}
                >
                  Copy
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Keys</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Prefix</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {keys.map((key) => (
                <TableRow key={key.id} data-testid="key-row" data-prefix={key.prefix}>
                  <TableCell className="font-medium">
                    {key.name}
                    {key.revoked_at && (
                      <Badge variant="destructive" className="ml-2">
                        revoked
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>{key.kind}</TableCell>
                  <TableCell className="font-mono text-xs">{key.prefix}…</TableCell>
                  <TableCell data-testid="key-last-used">
                    {key.last_used_at ? new Date(key.last_used_at).toLocaleString() : "never"}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={
                        !canManage || key.revoked_at !== null || (key.kind === "secret" && !isOwner)
                      }
                      onClick={() => setPendingRevoke(key)}
                      data-testid="key-revoke"
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

      <AlertDialog
        open={pendingRevoke !== null}
        onOpenChange={(open) => !open && setPendingRevoke(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke this key?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingRevoke?.kind === "public"
                ? "Any site using this widget key stops working immediately. A new widget key is issued automatically."
                : "Anything using this secret key stops working immediately."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="confirm-revoke"
              onClick={() => {
                const key = pendingRevoke;
                setPendingRevoke(null);
                if (key) void run(() => revokeApiKey(key.id));
              }}
            >
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default function SettingsPage() {
  const { user, refresh } = useSession();
  const membership = user?.memberships[0];

  if (!user || !membership) {
    return <PageHeader title="Settings" description="Organization, members, and API keys." />;
  }

  const canSeeKeys = ADMIN_ROLES.includes(membership.role);

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Settings" description="Organization, members, and API keys." />
      <Tabs defaultValue="organization">
        <TabsList>
          <TabsTrigger value="organization">Organization</TabsTrigger>
          <TabsTrigger value="members">Members</TabsTrigger>
          {/* Keys are admin+ even to read, so the tab is hidden for others. */}
          {canSeeKeys && <TabsTrigger value="keys">API keys</TabsTrigger>}
        </TabsList>
        <TabsContent value="organization" className="pt-4">
          <OrganizationTab
            orgName={membership.org_name}
            canEdit={ADMIN_ROLES.includes(membership.role)}
            onSaved={refresh}
          />
        </TabsContent>
        <TabsContent value="members" className="pt-4">
          <MembersTab myRole={membership.role} myUserId={user.id} />
        </TabsContent>
        {canSeeKeys && (
          <TabsContent value="keys" className="pt-4">
            <ApiKeysTab myRole={membership.role} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
