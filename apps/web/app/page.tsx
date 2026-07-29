import Link from "next/link";

import { Wordmark } from "@/components/wordmark";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * A holding page, not the marketing site (that is plan task 7.4). It does one
 * thing: show the gesture the product is built on — an answer that points at the
 * passage it came from — instead of claiming it in a feature list.
 */
export default function Home() {
  return (
    <main className="flex min-h-screen flex-col bg-rail text-rail-foreground">
      <header className="flex items-center justify-between px-6 py-5 md:px-10">
        <Wordmark className="text-rail-foreground" size="lg" />
        <Link
          href="/login"
          className="text-sm text-rail-muted underline decoration-mark decoration-2 underline-offset-4 transition-colors hover:text-rail-foreground"
        >
          Log in
        </Link>
      </header>

      <div className="flex flex-1 items-center px-6 py-12 md:px-10">
        <div className="mx-auto grid w-full max-w-5xl gap-12 lg:grid-cols-[1.1fr_1fr] lg:items-center">
          <div>
            <p className="eyebrow text-rail-muted">Grounded support agent</p>
            <h1 className="mt-4 max-w-[22ch] text-4xl font-semibold leading-[1.06] text-balance md:text-[3.25rem]">
              It answers from your docs, or it says it doesn&apos;t know.
            </h1>
            <p className="mt-5 max-w-md text-[0.95rem] leading-relaxed text-rail-muted">
              HelpDeck reads the documentation you give it and answers customers from that alone —
              every claim carrying the passage it came from. When the evidence runs out, it hands the
              conversation to your team instead of inventing an answer.
            </p>
            {/* On the ink canvas the buttons carry their own colours: the shared
                variants inherit text colour, which would put white on white here. */}
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link
                href="/signup"
                className={cn(
                  buttonVariants({ size: "lg" }),
                  "bg-rail-foreground text-rail hover:bg-rail-foreground/90",
                )}
              >
                Get started
              </Link>
              <Link
                href="/login"
                className={cn(
                  buttonVariants({ variant: "outline", size: "lg" }),
                  "border-rail-muted/40 bg-transparent text-rail-foreground hover:bg-white/5 hover:text-rail-foreground",
                )}
              >
                Log in
              </Link>
            </div>
          </div>

          {/* The gesture itself: a reply, its marker, and the source it resolves to. */}
          <figure className="rounded-xl bg-card p-6 text-card-foreground shadow-2xl shadow-black/30">
            <figcaption className="eyebrow">A customer asks</figcaption>
            <p className="mt-2 text-[0.95rem] font-medium">
              How often should I descale my machine?
            </p>
            <div className="mt-5 border-l-2 border-mark pl-4">
              <p className="text-[0.95rem] leading-relaxed">
                Descale every three months with normal use and medium-hardness water.
                <sup className="mx-0.5 rounded-[3px] bg-mark-soft px-[3px] font-mono text-[10px] font-medium">
                  [1]
                </sup>
              </p>
            </div>
            <div className="mt-5 border-t pt-4">
              <p className="eyebrow">[1] descaling.md · retrieved 0.0164</p>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                “Descale the machine every three months with normal use and medium-hardness water.
                If you have hard water or use the machine several times a day, descale every four to
                six weeks.”
              </p>
            </div>
          </figure>
        </div>
      </div>

      <footer className="px-6 pb-8 md:px-10">
        <p className="eyebrow text-rail-muted">
          Status: MVP · self-hostable · Postgres + pgvector
        </p>
      </footer>
    </main>
  );
}
