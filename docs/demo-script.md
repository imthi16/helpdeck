# Demo Video Script (3 minutes)

Target: recorded screen capture, ~420 narrated words ≈ 3:00 at a natural
pace. Screen prep: dashboard logged into a fresh org, a PDF on the desktop,
the landing page with the live widget in a second tab, Langfuse in a third.

| # | Time | Screen | Narration |
|---|------|--------|-----------|
| 1 | 0:00–0:20 | Landing page, cursor idle | "Most AI support bots will happily invent your refund policy. HelpDeck is built the opposite way — it answers only from your own docs, cites its sources, and hands off to a human instead of guessing. Let me show you, live." |
| 2 | 0:20–0:50 | Dashboard → Knowledge Base → drag-drop the PDF; status flips pending → processing → ready; chunk count appears | "I'm starting with an empty workspace. I drop in our product manual… HelpDeck extracts it, splits it into heading-aware chunks, embeds them, and indexes them for hybrid search — dense vectors plus full-text, fused. Twenty seconds later the knowledge base is live." |
| 3 | 0:50–1:30 | Playground → ask "How often should I descale the machine?" — answer streams with `[1]`; click the citation chip; open the debug panel | "Now I ask a real customer question. The answer streams in — and notice the citation. Click it and you see the exact passage it came from. The debug panel shows what happened under the hood: intent routing, the retrieved chunks with scores, and a faithfulness score — a separate judge that checks every claim against the sources before the answer ships." |
| 4 | 1:30–2:05 | Same playground → ask "What is your CEO's shoe size?" → refusal + escalation banner; switch to Conversations inbox, escalated tab | "Here's the part that matters. I ask something the docs can't answer. No improvisation — HelpDeck says it doesn't know and escalates. And that escalation is already sitting in the team inbox with the full transcript. It would rather hand off than hallucinate. That's a design guarantee, not a prompt suggestion." |
| 5 | 2:05–2:35 | Landing page tab → open the widget bubble → ask a question → cited answer → thumbs-up → 1–5 rating | "The same agent embeds on any website with one script tag. Same grounded answers, same citations, in an isolated iframe about a kilobyte of loader. Visitors rate answers, and…" |
| 6 | 2:35–3:00 | Analytics page (deflection/CSAT/unanswered) → Langfuse trace tree → README eval table | "…everything is measured. Deflection rate, CSAT, the exact questions your docs couldn't answer — clustered so you know what to write next. Every turn is a full Langfuse trace, and a golden-set eval gate runs in CI, so quality regressions can't merge. Grounded, cited, escalated, measured — that's HelpDeck. The repo link is below; it runs locally with zero API keys." |

## Table-read check

Narration is ~415 words. At a measured 140 wpm that's 2:58 — read it aloud
once before recording; if you land over 3:00, trim scene 5's second
sentence first.

## Recording notes

- Seed beforehand: `uv run python scripts/seed.py` gives the Northwind KB if
  you'd rather skip the live upload (scene 2 still reads fine over a
  pre-seeded re-index).
- Use `bypass_cache` in the playground so both questions run the live agent.
- Scene 4's question is golden-set item `g106` — guaranteed refusal.
- Keep the cursor still during narration-only beats; motion draws the eye.
