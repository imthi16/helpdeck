"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { MessageContent } from "@/components/message-content";
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
import { Textarea } from "@/components/ui/textarea";
import { streamChat, type Citation } from "@/lib/chat";
import { addText } from "@/lib/documents";
import { completeOnboarding, useSession } from "@/lib/session";

const STEPS = ["Name your workspace", "Add a document", "Ask a question", "Embed the widget"];

export default function OnboardingPage() {
  const router = useRouter();
  const { user, loading, refresh } = useSession();
  const orgId = user?.memberships[0]?.org_id;

  const [step, setStep] = useState(0);
  const [orgName, setOrgName] = useState("");
  const [docTitle, setDocTitle] = useState("Getting Started");
  const [docBody, setDocBody] = useState(
    "# Getting Started\n\nOur support team is available Monday to Friday, 9am to 5pm.",
  );
  const [docAdded, setDocAdded] = useState(false);
  const [question, setQuestion] = useState("What are your support hours?");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [busy, setBusy] = useState(false);
  const nameInitialized = useRef(false);

  // Prefill the org name once the session loads.
  useEffect(() => {
    if (!nameInitialized.current && user?.memberships[0]) {
      setOrgName(user.memberships[0].org_name);
      nameInitialized.current = true;
    }
    // If already onboarded, skip the wizard.
    if (!loading && user?.memberships[0]?.onboarded) {
      router.replace("/dashboard");
    }
  }, [user, loading, router]);

  async function addFirstDoc() {
    setBusy(true);
    try {
      await addText(docTitle, docBody);
      setDocAdded(true);
      setStep(2);
    } finally {
      setBusy(false);
    }
  }

  async function ask() {
    if (!orgId) return;
    setAnswer("");
    setCitations([]);
    setBusy(true);
    await streamChat(
      { org_id: orgId, message: question, bypass_cache: true },
      {
        onToken: (text) => setAnswer((prev) => prev + text),
        onCitation: (c) => setCitations((prev) => [...prev, c]),
        onDone: () => setBusy(false),
        onError: () => setBusy(false),
      },
    );
  }

  async function finish() {
    setBusy(true);
    try {
      await completeOnboarding(orgName);
      await refresh();
      router.replace("/dashboard");
    } finally {
      setBusy(false);
    }
  }

  const publicKey = orgId ? `pk_${orgId.replace(/-/g, "").slice(0, 24)}` : "pk_your_key";
  const snippet = `<script src="https://cdn.helpdeck.example/helpdeck.js" data-public-key="${publicKey}" defer></script>`;

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-xl">
        <CardHeader>
          <CardTitle data-testid="wizard-step">{STEPS[step]}</CardTitle>
          <CardDescription>
            Step {step + 1} of {STEPS.length}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {step === 0 && (
            <>
              <div className="space-y-2">
                <Label htmlFor="org">Workspace name</Label>
                <Input id="org" value={orgName} onChange={(e) => setOrgName(e.target.value)} />
              </div>
              <Button onClick={() => setStep(1)} disabled={!orgName.trim()}>
                Continue
              </Button>
            </>
          )}

          {step === 1 && (
            <>
              <div className="space-y-2">
                <Label htmlFor="doc-title">Title</Label>
                <Input
                  id="doc-title"
                  value={docTitle}
                  onChange={(e) => setDocTitle(e.target.value)}
                />
                <Label htmlFor="doc-body">Content</Label>
                <Textarea
                  id="doc-body"
                  rows={5}
                  value={docBody}
                  onChange={(e) => setDocBody(e.target.value)}
                />
              </div>
              <Button onClick={addFirstDoc} disabled={busy} data-testid="add-first-doc">
                {busy ? "Adding…" : "Add document"}
              </Button>
            </>
          )}

          {step === 2 && (
            <>
              <div className="flex gap-2">
                <Input value={question} onChange={(e) => setQuestion(e.target.value)} />
                <Button onClick={ask} disabled={busy || !docAdded} data-testid="ask-test">
                  Ask
                </Button>
              </div>
              {answer && (
                <div className="rounded bg-muted p-3" data-testid="wizard-answer">
                  <MessageContent text={answer} citations={citations} />
                </div>
              )}
              <Button variant="outline" onClick={() => setStep(3)} disabled={!answer}>
                Continue
              </Button>
            </>
          )}

          {step === 3 && (
            <>
              <p className="text-sm text-muted-foreground">
                Drop this snippet into any website to embed your assistant.
              </p>
              <pre
                className="overflow-x-auto rounded bg-muted p-3 text-xs"
                data-testid="embed-snippet"
              >
                {snippet}
              </pre>
              <Button onClick={finish} disabled={busy} data-testid="finish-onboarding">
                {busy ? "Finishing…" : "Go to dashboard"}
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
