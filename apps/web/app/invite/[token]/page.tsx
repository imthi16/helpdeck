"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { acceptInvite, previewInvite, type InvitePreview } from "@/lib/members";
import { fetchMe } from "@/lib/session";

export default function AcceptInvitePage() {
  const router = useRouter();
  const token = String(useParams().token ?? "");

  const [invite, setInvite] = useState<InvitePreview | null>(null);
  const [signedInAs, setSignedInAs] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ name: "", password: "" });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const preview = await previewInvite(token);
        if (active) setInvite(preview);
      } catch (err) {
        if (active) {
          setError(
            err instanceof ApiError ? err.message : "This invite link is no longer valid.",
          );
        }
      }
      // A signed-in session can accept directly; otherwise we ask for a password.
      try {
        const me = await fetchMe();
        if (active) setSignedInAs(me.email);
      } catch {
        if (active) setSignedInAs(null);
      }
      if (active) setLoading(false);
    })();
    return () => {
      active = false;
    };
  }, [token]);

  async function accept(withPassword: boolean) {
    setError(null);
    setSubmitting(true);
    try {
      await acceptInvite(
        token,
        withPassword ? { name: form.name, password: form.password } : {},
      );
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not accept this invite");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>
            {invite ? `Join ${invite.org_name}` : "Invitation"}
          </CardTitle>
          <CardDescription>
            {loading
              ? "Checking your invite…"
              : invite
                ? `${invite.email} · ${invite.role}`
                : "This invite cannot be used."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error && (
            <p role="alert" className="mb-4 text-sm text-red-600" data-testid="invite-error">
              {error}
            </p>
          )}

          {!loading && invite && (
            <>
              {signedInAs === invite.email ? (
                <Button
                  className="w-full"
                  disabled={submitting}
                  onClick={() => void accept(false)}
                  data-testid="accept-invite"
                >
                  {submitting ? "Joining…" : `Join as ${signedInAs}`}
                </Button>
              ) : invite.needs_account ? (
                <form
                  className="flex flex-col gap-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void accept(true);
                  }}
                >
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="name">Your name</Label>
                    <Input
                      id="name"
                      value={form.name}
                      onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="password">Choose a password</Label>
                    <Input
                      id="password"
                      type="password"
                      required
                      minLength={8}
                      value={form.password}
                      onChange={(e) => setForm((p) => ({ ...p, password: e.target.value }))}
                    />
                  </div>
                  <Button type="submit" disabled={submitting} data-testid="accept-invite">
                    {submitting ? "Joining…" : "Accept invitation"}
                  </Button>
                </form>
              ) : (
                <p className="text-sm text-muted-foreground">
                  This invite is for <span className="font-medium">{invite.email}</span>.{" "}
                  <Link href="/login" className="underline">
                    Log in as that user
                  </Link>{" "}
                  to accept it.
                </p>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
