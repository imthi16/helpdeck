import {
  BarChart3,
  BookOpenCheck,
  MessageSquareQuote,
  ServerCog,
  ShieldCheck,
  UserRoundCheck,
} from "lucide-react";
import Link from "next/link";

import { DemoWidget } from "@/components/demo-widget";
import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const GITHUB_URL = "https://github.com/imthi16/helpdeck";

const FEATURES = [
  {
    icon: BookOpenCheck,
    title: "Grounded answers",
    body: "Responses come only from your ingested docs — hybrid dense + full-text retrieval with reciprocal rank fusion.",
  },
  {
    icon: MessageSquareQuote,
    title: "Inline citations",
    body: "Every claim carries a [n] citation that opens the exact source passage, in the widget and the playground.",
  },
  {
    icon: ShieldCheck,
    title: "Hallucination guardrails",
    body: "A faithfulness judge scores every answer; anything unsupported is refused instead of invented.",
  },
  {
    icon: UserRoundCheck,
    title: "Human escalation",
    body: 'Out-of-scope questions and "talk to a human" hand off to your inbox with full context.',
  },
  {
    icon: BarChart3,
    title: "Analytics & quality",
    body: "Deflection, CSAT, top unanswered questions, and RAGAS-style eval scores — measured, not vibes.",
  },
  {
    icon: ServerCog,
    title: "Self-hostable",
    body: "Postgres + Redis + local Ollama models via Docker Compose. Your data can stay on your metal.",
  },
];

const FLOW = [
  ["Widget / API", "a customer asks a question"],
  ["Router", "intent: FAQ, chit-chat, or human request"],
  ["Hybrid retrieval", "pgvector + full-text → rank fusion"],
  ["Grounded answer", "answers ONLY from retrieved chunks, cites [n]"],
  ["Faithfulness judge", "every claim supported? score 0–1"],
  ["Respond or escalate", "low confidence → human handoff, never a guess"],
];

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-16 px-6 py-16">
      {/* Hero */}
      <section className="flex flex-col items-center gap-6 text-center">
        <p className="rounded-full border px-3 py-1 text-xs text-muted-foreground">
          Status: open-source MVP — live demo widget in the corner ↘
        </p>
        <h1 className="max-w-2xl text-4xl font-semibold tracking-tight sm:text-5xl">
          AI support that answers <span className="underline decoration-2">only</span> from
          your docs
        </h1>
        <p className="max-w-xl text-lg text-muted-foreground">
          HelpDeck ingests your knowledge base and answers customers with inline citations,
          deterministic guardrails against hallucination, and automatic human escalation when
          it isn&apos;t sure.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-3">
          <Link href="/signup" className={buttonVariants({ size: "lg" })}>
            Get started free
          </Link>
          <Link href="/login" className={buttonVariants({ variant: "outline", size: "lg" })}>
            Log in
          </Link>
          <a
            href={GITHUB_URL}
            className={buttonVariants({ variant: "ghost", size: "lg" })}
            rel="noreferrer"
          >
            GitHub ↗
          </a>
        </div>
        <p className="text-sm text-muted-foreground">
          Try it now: the chat bubble in the corner is a live HelpDeck widget backed by a
          fictional coffee company&apos;s docs. Ask about returns — then ask about its
          CEO&apos;s shoe size and watch it refuse.
        </p>
      </section>

      {/* Features */}
      <section aria-labelledby="features">
        <h2 id="features" className="mb-6 text-center text-2xl font-semibold">
          What you get
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <Card key={title}>
              <CardHeader className="pb-2">
                <Icon className="mb-1 size-5 text-muted-foreground" aria-hidden />
                <CardTitle className="text-base">{title}</CardTitle>
              </CardHeader>
              <CardContent className="text-sm text-muted-foreground">{body}</CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* Architecture */}
      <section aria-labelledby="architecture">
        <h2 id="architecture" className="mb-2 text-center text-2xl font-semibold">
          How a question flows
        </h2>
        <p className="mb-6 text-center text-sm text-muted-foreground">
          FastAPI · LangGraph · Postgres + pgvector · Redis · streamed over SSE
        </p>
        <ol className="mx-auto flex max-w-2xl flex-col gap-2">
          {FLOW.map(([step, detail], index) => (
            <li key={step} className="flex items-baseline gap-3 rounded-lg border p-3">
              <span className="text-sm font-semibold text-muted-foreground">{index + 1}</span>
              <span className="font-medium">{step}</span>
              <span className="ml-auto text-right text-sm text-muted-foreground">{detail}</span>
            </li>
          ))}
        </ol>
      </section>

      {/* Honest status + footer */}
      <section className="mx-auto max-w-2xl text-center">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Honest status</CardTitle>
            <CardDescription>
              HelpDeck is a working MVP: ingestion, grounded chat, widget, multi-tenant RLS,
              RBAC, analytics, and a CI-gated eval suite are real. WhatsApp/email channels and
              agentic actions are on the{" "}
              <a
                className="underline"
                href={`${GITHUB_URL}/blob/main/ROADMAP.md`}
                rel="noreferrer"
              >
                roadmap
              </a>
              .
            </CardDescription>
          </CardHeader>
        </Card>
        <p className="mt-8 text-xs text-muted-foreground">
          MIT licensed ·{" "}
          <a className="underline" href={GITHUB_URL} rel="noreferrer">
            Source on GitHub
          </a>
        </p>
      </section>

      <DemoWidget />
    </main>
  );
}
