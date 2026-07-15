"use client";

import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  getConversation,
  listConversations,
  replyToConversation,
  resolveConversation,
  type ConversationDetail,
  type ConversationStatus,
  type ConversationSummary,
} from "@/lib/conversations";

const STATUS_VARIANT: Record<ConversationStatus, "default" | "secondary" | "destructive"> = {
  open: "secondary",
  escalated: "destructive",
  closed: "default",
};

export default function ConversationsPage() {
  const [tab, setTab] = useState<"all" | "escalated">("all");
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [selected, setSelected] = useState<ConversationDetail | null>(null);
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setItems(
        await listConversations(tab === "escalated" ? { status: "escalated" } : {}),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load conversations");
    }
  }, [tab]);

  useEffect(() => {
    let active = true;
    listConversations(tab === "escalated" ? { status: "escalated" } : {})
      .then((rows) => {
        if (active) setItems(rows);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Failed to load conversations");
      });
    return () => {
      active = false;
    };
  }, [tab]);

  async function open(id: string) {
    setSelected(await getConversation(id));
  }

  async function onResolve() {
    if (!selected) return;
    const updated = await resolveConversation(selected.id);
    setSelected(updated);
    await refresh();
  }

  async function onReply(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !reply.trim()) return;
    const updated = await replyToConversation(selected.id, reply.trim());
    setSelected(updated);
    setReply("");
  }

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Conversations" description="Review chats and handle escalations." />

      <Tabs value={tab} onValueChange={(v) => setTab(v as "all" | "escalated")}>
        <TabsList>
          <TabsTrigger value="all">All</TabsTrigger>
          <TabsTrigger value="escalated">Escalated</TabsTrigger>
        </TabsList>
      </Tabs>

      {error && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          {error}
        </p>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_22rem]">
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Channel</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Messages</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={3} className="text-center text-muted-foreground">
                      No conversations.
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map((conversation) => (
                    <TableRow
                      key={conversation.id}
                      data-testid="conversation-row"
                      className="cursor-pointer"
                      onClick={() => void open(conversation.id)}
                    >
                      <TableCell className="capitalize">{conversation.channel}</TableCell>
                      <TableCell>
                        <Badge variant={STATUS_VARIANT[conversation.status]}>
                          {conversation.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right">{conversation.message_count}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card data-testid="transcript-panel">
          <CardContent className="space-y-3 p-4 text-sm">
            {!selected ? (
              <p className="text-muted-foreground">Select a conversation to view its transcript.</p>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <Badge variant={STATUS_VARIANT[selected.status]} data-testid="transcript-status">
                    {selected.status}
                  </Badge>
                  {selected.status !== "closed" && (
                    <Button size="sm" variant="outline" onClick={onResolve} data-testid="resolve">
                      Mark resolved
                    </Button>
                  )}
                </div>

                {selected.escalations.length > 0 && (
                  <div className="rounded border border-destructive/40 p-2 text-xs">
                    <p className="font-medium">Escalation</p>
                    {selected.escalations.map((e) => (
                      <p key={e.id} className="text-muted-foreground">
                        {e.reason} — {e.status}
                      </p>
                    ))}
                  </div>
                )}

                <div className="max-h-72 space-y-2 overflow-y-auto" data-testid="transcript">
                  {selected.messages.map((message) => (
                    <div
                      key={message.id}
                      className={
                        message.role === "user"
                          ? "rounded bg-primary px-2 py-1 text-primary-foreground"
                          : "rounded bg-muted px-2 py-1"
                      }
                    >
                      <span className="mr-1 text-[10px] uppercase opacity-70">{message.role}</span>
                      {message.content}
                      {message.feedback === 1 && (
                        <span className="ml-2" title="Rated helpful" data-testid="thumb-up">
                          👍
                        </span>
                      )}
                      {message.feedback === -1 && (
                        <span className="ml-2" title="Rated not helpful" data-testid="thumb-down">
                          👎
                        </span>
                      )}
                    </div>
                  ))}
                </div>

                <form onSubmit={onReply} className="flex gap-2">
                  <Input
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                    placeholder="Internal reply…"
                    data-testid="reply-input"
                  />
                  <Button type="submit" size="sm">
                    Reply
                  </Button>
                </form>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
