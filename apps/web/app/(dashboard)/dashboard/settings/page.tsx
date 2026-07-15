"use client";

import { useEffect, useState } from "react";

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
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
  createApiKey,
  listApiKeys,
  revokeApiKey,
  type ApiKeyItem,
  type ApiKeyType,
} from "@/lib/apiKeys";
import { useSession } from "@/lib/session";

export default function SettingsPage() {
  const { user } = useSession();
  const isOwner = user?.memberships[0]?.role === "owner";
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [keyType, setKeyType] = useState<ApiKeyType>("secret");
  const [newToken, setNewToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [revoking, setRevoking] = useState<ApiKeyItem | null>(null);

  useEffect(() => {
    if (!isOwner) return;
    let active = true;
    listApiKeys()
      .then((rows) => {
        if (active) setKeys(rows);
      })
      .catch((err) => {
        if (active) setError(err instanceof ApiError ? err.message : "Failed to load keys");
      });
    return () => {
      active = false;
    };
  }, [isOwner]);

  async function refresh() {
    try {
      setKeys(await listApiKeys());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load keys");
    }
  }

  async function onCreate(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const created = await createApiKey(name, keyType);
      setNewToken(created.token);
      setName("");
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Key creation failed");
    }
  }

  async function onRevoke() {
    if (!revoking) return;
    try {
      await revokeApiKey(revoking.id);
      setRevoking(null);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Revoke failed");
      setRevoking(null);
    }
  }

  async function copyToken() {
    if (!newToken) return;
    await navigator.clipboard.writeText(newToken);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Settings" description="Organization settings and API keys." />

      {error ? (
        <p className="text-sm text-destructive" data-testid="settings-error">
          {error}
        </p>
      ) : null}

      <Card>
        <CardHeader className="flex-row items-start justify-between">
          <div>
            <CardTitle>API keys</CardTitle>
            <CardDescription>
              Widget keys are public and shown in your embed snippet. Secret keys are for
              server-to-server use and are revealed only once.
            </CardDescription>
          </div>
          {isOwner ? (
            <Button data-testid="key-create-open" onClick={() => setCreateOpen(true)}>
              Create key
            </Button>
          ) : null}
        </CardHeader>
        <CardContent>
          {!isOwner ? (
            <p className="text-sm text-muted-foreground">
              Only organization owners can manage API keys.
            </p>
          ) : (
            <Table data-testid="keys-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-24" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.map((key) => (
                  <TableRow key={key.id}>
                    <TableCell>{key.name}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{key.key_type}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {key.key_type === "widget" && key.public_value
                        ? key.public_value
                        : `${key.prefix}…`}
                    </TableCell>
                    <TableCell>
                      {key.last_used_at ? new Date(key.last_used_at).toLocaleString() : "never"}
                    </TableCell>
                    <TableCell>
                      {key.revoked_at ? (
                        <Badge variant="destructive">revoked</Badge>
                      ) : (
                        <Badge>active</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {!key.revoked_at ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          data-testid={`revoke-${key.name}`}
                          onClick={() => setRevoking(key)}
                        >
                          Revoke
                        </Button>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) setNewToken(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create API key</DialogTitle>
            <DialogDescription>
              Secret keys are shown once. Widget keys stay visible (they are public by design).
            </DialogDescription>
          </DialogHeader>
          {newToken ? (
            <div className="flex flex-col gap-3">
              <p className="text-sm font-medium">
                Copy your key now{keyType === "secret" ? " — it won't be shown again" : ""}:
              </p>
              <div className="flex items-center gap-2">
                <Input readOnly value={newToken} data-testid="new-key-token" />
                <Button type="button" onClick={copyToken}>
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
          ) : (
            <form onSubmit={onCreate} className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="key-name">Name</Label>
                <Input
                  id="key-name"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="key-type">Type</Label>
                <select
                  id="key-type"
                  value={keyType}
                  onChange={(e) => setKeyType(e.target.value as ApiKeyType)}
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                >
                  <option value="secret">secret (server-to-server)</option>
                  <option value="widget">widget (public)</option>
                </select>
              </div>
              <DialogFooter>
                <Button type="submit" data-testid="key-create-submit">
                  Create
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={revoking !== null} onOpenChange={(open) => !open && setRevoking(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke {revoking?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Requests using this key will be rejected immediately. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void onRevoke()}>Revoke</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
