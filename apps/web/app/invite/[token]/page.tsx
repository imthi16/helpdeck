"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import { acceptInvite } from "@/lib/members";
import { useSession } from "@/lib/session";

export default function InvitePage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { user, loading } = useSession();
  const [error, setError] = useState<string | null>(null);
  const attempted = useRef(false);

  useEffect(() => {
    if (loading || !user || attempted.current) return;
    attempted.current = true;
    acceptInvite(token)
      .then(() => router.replace("/dashboard"))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 409) {
          // Already a member — nothing to redeem.
          router.replace("/dashboard");
          return;
        }
        setError(err instanceof ApiError ? err.message : "Could not accept the invite");
      });
  }, [loading, user, token, router]);

  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Organization invite</CardTitle>
          <CardDescription>
            {loading
              ? "Checking your session…"
              : user
                ? error
                  ? "This invite could not be accepted."
                  : "Joining the organization…"
                : "Create an account (or log in) to accept this invite."}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
          {!loading && !user && (
            <>
              <Link href={`/signup?invite=${token}`} className={buttonVariants()}>
                Create account &amp; join
              </Link>
              <Link
                href={`/login?next=/invite/${token}`}
                className={buttonVariants({ variant: "outline" })}
              >
                Log in instead
              </Link>
            </>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
