"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { AppShell } from "@/components/app-shell";
import { logout, useSession } from "@/lib/session";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const { user, loading, refresh } = useSession();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/login");
    } else if (user.memberships[0] && !user.memberships[0].onboarded) {
      // A fresh org must finish onboarding before using the dashboard.
      router.replace("/onboarding");
    }
  }, [loading, user, router]);

  async function onLogout() {
    await logout();
    await refresh();
    router.replace("/login");
  }

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        Loading…
      </div>
    );
  }

  return (
    <AppShell user={user} onLogout={onLogout}>
      {children}
    </AppShell>
  );
}
