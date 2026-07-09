"use client";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useSession } from "@/lib/session";

export default function DashboardPage() {
  const { user } = useSession();
  const org = user?.memberships[0];

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-4 text-2xl font-semibold" data-testid="dashboard-heading">
        Dashboard
      </h1>
      <Card>
        <CardHeader>
          <CardTitle>{org?.org_name ?? "Your workspace"}</CardTitle>
          <CardDescription>
            You are signed in{org ? ` as ${org.role}` : ""}.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Knowledge base, playground, and conversations are coming next.
        </CardContent>
      </Card>
    </div>
  );
}
