import type { ComponentChildren } from "preact";

import type { Citation } from "./api";

function renderInline(text: string): ComponentChildren[] {
  const nodes: ComponentChildren[] = [];
  const pattern = /\*\*([^*]+)\*\*|`([^`]+)`/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    if (match[1] !== undefined) nodes.push(<strong>{match[1]}</strong>);
    else if (match[2] !== undefined) nodes.push(<code>{match[2]}</code>);
    last = pattern.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function MessageContent({
  text,
  citations,
  onCitation,
}: {
  text: string;
  citations: Citation[];
  onCitation: (c: Citation) => void;
}) {
  const byN = new Map(citations.map((c) => [c.n, c]));
  const segments = text.split(/(\[\d+\])/g);

  return (
    <span>
      {segments.map((segment) => {
        const m = segment.match(/^\[(\d+)\]$/);
        if (m) {
          const n = Number(m[1]);
          const citation = byN.get(n);
          return (
            <button
              type="button"
              class="chip"
              data-testid="citation-chip"
              disabled={!citation}
              onClick={() => citation && onCitation(citation)}
            >
              {n}
            </button>
          );
        }
        return <span>{renderInline(segment)}</span>;
      })}
    </span>
  );
}
