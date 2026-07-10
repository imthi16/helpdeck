"use client";

import { Fragment, type ReactNode } from "react";

import type { Citation } from "@/lib/chat";

// Minimal inline markdown: **bold** and `code`. Enough for grounded answers.
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /\*\*([^*]+)\*\*|`([^`]+)`/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    if (match[1] !== undefined) {
      nodes.push(<strong key={`${keyPrefix}-b-${i}`}>{match[1]}</strong>);
    } else if (match[2] !== undefined) {
      nodes.push(
        <code key={`${keyPrefix}-c-${i}`} className="rounded bg-muted px-1 py-0.5 text-xs">
          {match[2]}
        </code>,
      );
    }
    last = pattern.lastIndex;
    i += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Renders assistant text, turning [n] markers into citation chips. */
export function MessageContent({
  text,
  citations,
}: {
  text: string;
  citations: Citation[];
}) {
  const byN = new Map(citations.map((c) => [c.n, c]));
  const segments = text.split(/(\[\d+\])/g);

  return (
    <div className="whitespace-pre-wrap text-sm leading-relaxed">
      {segments.map((segment, index) => {
        const citeMatch = segment.match(/^\[(\d+)\]$/);
        if (citeMatch) {
          const n = Number(citeMatch[1]);
          const citation = byN.get(n);
          return (
            <sup
              key={`cite-${index}`}
              data-testid="citation-chip"
              title={
                citation
                  ? `${citation.document_title}: ${citation.snippet}`
                  : `Source ${n}`
              }
              className="mx-0.5 cursor-help rounded bg-primary/10 px-1 text-[10px] font-medium text-primary"
            >
              {n}
            </sup>
          );
        }
        return <Fragment key={`seg-${index}`}>{renderInline(segment, `seg-${index}`)}</Fragment>;
      })}
    </div>
  );
}
