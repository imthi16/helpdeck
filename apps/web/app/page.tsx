import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function Home() {
  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle className="text-2xl">HelpDeck</CardTitle>
          <CardDescription>
            AI customer support that answers only from your own docs — with
            citations, guardrails, and human escalation.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center gap-3">
          <Link href="/signup" className={buttonVariants()}>
            Get started
          </Link>
          <Link href="/login" className={buttonVariants({ variant: "outline" })}>
            Log in
          </Link>
        </CardContent>
      </Card>
    </main>
  );
}
