"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ThresholdGauge } from "@/components/threshold-gauge";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatPercent, fetchOverview, type AnalyticsOverview } from "@/lib/analytics";
import { listConversations, type ConversationSummary } from "@/lib/conversations";
import { listDocuments, type DocumentItem } from "@/lib/documents";
import { useSession } from "@/lib/session";

const RANGE_DAYS = 30;

function relativeDay(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return `${days}d ago`;
}

export default function DashboardPage() {
  const { user } = useSession();
  const org = user?.memberships[0];

  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[] | null>(null);
  const [escalated, setEscalated] = useState<ConversationSummary[] | null>(null);

  useEffect(() => {
    let active = true;
    const settle = <T,>(set: (value: T) => void) => (value: T) => {
      if (active) set(value);
    };
    // Each panel fails independently: a missing scope or an empty section should
    // not blank the whole page.
    fetchOverview(RANGE_DAYS).then(settle(setOverview), () => settle(setOverview)(null));
    listDocuments().then(settle(setDocuments), () => settle(setDocuments)([]));
    listConversations({ status: "escalated" }).then(settle(setEscalated), () =>
      settle(setEscalated)([]),
    );
    return () => {
      active = false;
    };
  }, []);

  const ready = documents?.filter((d) => d.status === "ready") ?? [];
  const indexing = documents?.filter((d) => d.status === "pending" || d.status === "processing") ?? [];
  const failed = documents?.filter((d) => d.status === "failed") ?? [];
  const chunks = ready.reduce((sum, d) => sum + d.chunk_count, 0);
  const pendingHandoffs = escalated ?? [];
  const noSources = documents !== null && documents.length === 0;
  const needsAttention = failed.length > 0 || indexing.length > 0;
  const votes = overview ? overview.thumbs_up + overview.thumbs_down : 0;

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-7">
        <p className="eyebrow mb-2">
          {org ? `${org.org_name} · ${org.role}` : "Workspace"}
        </p>
        <h1 className="text-[1.75rem] font-semibold leading-none" data-testid="dashboard-heading">
          Overview
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          What your assistant answered, what it refused, and what needs you.
        </p>
      </div>

      {noSources ? (
        // An empty knowledge base is the one state where nothing else matters.
        <Card>
          <CardContent className="flex flex-col items-start gap-4 py-8">
            <div>
              <p className="eyebrow mb-2">Nothing indexed yet</p>
              <h2 className="text-xl font-semibold">Add your first source</h2>
              <p className="mt-2 max-w-lg text-sm text-muted-foreground">
                The assistant answers only from documents you give it. Upload a PDF, paste a help
                article, or point it at a URL, and it starts citing them immediately.
              </p>
            </div>
            <Link href="/dashboard/knowledge-base" className={buttonVariants()}>
              Add a source
            </Link>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* The thesis: of everything asked, how much the docs could answer. */}
          <Card className="overflow-visible">
            <CardContent className="py-6">
              <div className="flex flex-wrap items-end justify-between gap-6">
                <div>
                  <p className="eyebrow mb-3">Answered from your docs · last {RANGE_DAYS} days</p>
                  <p className="font-display text-[3.5rem] font-semibold leading-none tracking-tight">
                    {overview ? formatPercent(overview.deflection_rate) : "—"}
                  </p>
                </div>
                {/* Single-word labels keep the three values on one baseline when
                    the row wraps to a narrow screen. */}
                <dl className="grid grid-cols-3 gap-x-8">
                  <dt className="eyebrow">Conversations</dt>
                  <dt className="eyebrow">Handoffs</dt>
                  <dt className="eyebrow">CSAT</dt>
                  <dd className="data mt-1.5 text-lg">
                    {overview ? overview.total_conversations : "—"}
                  </dd>
                  <dd className="data mt-1.5 text-lg text-handoff">
                    {overview ? overview.escalated_conversations : "—"}
                  </dd>
                  <dd className="data mt-1.5 text-lg">
                    {overview?.csat_average != null ? `${overview.csat_average.toFixed(1)}/5` : "—"}
                  </dd>
                </dl>
              </div>

              <div className="mt-5">
                <ThresholdGauge
                  value={overview?.deflection_rate ?? 0}
                  tone="grounded"
                  label={`${overview ? formatPercent(overview.deflection_rate) : "0%"} of conversations answered without a handoff`}
                />
                <div className="mt-2 flex justify-between">
                  <span className="eyebrow">
                    {overview ? overview.deflected_conversations : 0} grounded
                  </span>
                  <span className="eyebrow">
                    {overview ? overview.escalated_conversations : 0} to a human
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <Card size="sm">
              <CardContent>
                <p className="eyebrow">Sources ready</p>
                <p className="data mt-1.5 text-2xl">{documents ? ready.length : "—"}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {documents ? `of ${documents.length} uploaded` : "Loading…"}
                </p>
              </CardContent>
            </Card>
            {/* This tile earns its place only when something needs doing;
                otherwise it reports the size of the index. */}
            {needsAttention ? (
              <Card size="sm">
                <CardContent>
                  <p className="eyebrow">{failed.length > 0 ? "Failed to index" : "Indexing now"}</p>
                  <p className="data mt-1.5 text-2xl text-handoff">
                    {failed.length > 0 ? failed.length : indexing.length}
                  </p>
                  <Link
                    href="/dashboard/knowledge-base"
                    className="mt-1 inline-block text-xs underline decoration-mark decoration-2 underline-offset-2"
                  >
                    {failed.length > 0 ? "Re-index in Knowledge Base" : "Watch progress"}
                  </Link>
                </CardContent>
              </Card>
            ) : (
              <Card size="sm">
                <CardContent>
                  <p className="eyebrow">Indexed passages</p>
                  <p className="data mt-1.5 text-2xl">{documents ? chunks : "—"}</p>
                  <p className="mt-1 text-xs text-muted-foreground">Searchable right now</p>
                </CardContent>
              </Card>
            )}
            <Card size="sm">
              <CardContent>
                <p className="eyebrow">Rated helpful</p>
                <p className="data mt-1.5 text-2xl">
                  {overview ? overview.thumbs_up : "—"}
                  {overview && votes > 0 && (
                    <span className="text-base text-muted-foreground"> of {votes}</span>
                  )}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {overview && votes === 0 ? "No votes yet" : "Thumbs from visitors"}
                </p>
              </CardContent>
            </Card>
          </div>

          <div className="mt-4 grid items-start gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader className="border-b">
                <CardTitle className="flex items-center justify-between gap-3">
                  Waiting on a human
                  {pendingHandoffs.length > 0 && (
                    <Badge variant="handoff" className="data">
                      {pendingHandoffs.length}
                    </Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                {pendingHandoffs.length === 0 ? (
                  <p className="px-(--card-spacing) py-5 text-sm text-muted-foreground">
                    Nothing escalated. Handoffs land here the moment the assistant is unsure.
                  </p>
                ) : (
                  <ul>
                    {pendingHandoffs.slice(0, 5).map((conversation) => (
                      <li key={conversation.id} className="border-b last:border-0">
                        <Link
                          href="/dashboard/conversations"
                          className="flex items-center justify-between gap-3 px-(--card-spacing) py-3 transition-colors hover:bg-muted"
                        >
                          <span className="min-w-0">
                            <span className="block truncate text-sm">
                              {conversation.user_identifier ?? "Anonymous visitor"}
                            </span>
                            <span className="eyebrow">
                              {conversation.channel} · {conversation.message_count} messages
                            </span>
                          </span>
                          <span className="eyebrow shrink-0">
                            {relativeDay(conversation.created_at)}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="border-b">
                <CardTitle>Gaps in your knowledge base</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                {!overview || overview.top_unanswered.length === 0 ? (
                  <p className="px-(--card-spacing) py-5 text-sm text-muted-foreground">
                    No unanswered questions in this period.
                  </p>
                ) : (
                  <ul>
                    {overview.top_unanswered.slice(0, 5).map((cluster) => (
                      <li
                        key={cluster.question}
                        className="flex items-baseline justify-between gap-3 border-b px-(--card-spacing) py-3 last:border-0"
                      >
                        <span className="text-sm">{cluster.question}</span>
                        <span className="data shrink-0 text-xs text-muted-foreground">
                          ×{cluster.count}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
