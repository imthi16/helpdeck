"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * The console's signature: a measured value on a track, with the line the
 * product actually acts on marked on it.
 *
 * A bare number ("0.62") does not say what happens next. HelpDeck's whole
 * promise is a boundary — above the faithfulness threshold it answers, below it
 * it hands the conversation to a human — so wherever a real boundary exists we
 * draw it, and the fill takes the colour of the side it lands on. Where there is
 * no boundary (a share of traffic, say), `threshold` is omitted and the track is
 * just a share.
 */
export function ThresholdGauge({
  value,
  threshold,
  tone = "auto",
  size = "default",
  label,
  className,
}: {
  /** 0–1. */
  value: number;
  /** 0–1. Omit when the value has no decision boundary. */
  threshold?: number;
  tone?: "auto" | "grounded" | "handoff" | "ink";
  size?: "default" | "sm";
  /** Announced to screen readers, e.g. "faithfulness 0.91 of threshold 0.40". */
  label: string;
  className?: string;
}) {
  const clamped = Math.max(0, Math.min(1, value));
  const [fill, setFill] = useState(0);

  // One orchestrated moment: the fill grows to its value on mount, so the eye
  // lands on where it stopped relative to the line. Respects reduced motion via
  // the transition class below.
  useEffect(() => {
    const frame = requestAnimationFrame(() => setFill(clamped));
    return () => cancelAnimationFrame(frame);
  }, [clamped]);

  const passes = threshold == null || clamped >= threshold;
  // Written out rather than interpolated: Tailwind only sees literal classes.
  const TONES = {
    grounded: "bg-grounded",
    handoff: "bg-handoff",
    ink: "bg-foreground",
  } as const;
  const resolved = tone === "auto" ? (passes ? TONES.grounded : TONES.handoff) : TONES[tone];

  return (
    <div
      role="meter"
      aria-valuenow={Math.round(clamped * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className={cn(
        "relative w-full overflow-hidden rounded-full bg-secondary",
        size === "sm" ? "h-1.5" : "h-2.5",
        className,
      )}
    >
      <div
        className={cn(
          "h-full rounded-full motion-safe:transition-[width] motion-safe:duration-700 motion-safe:ease-out",
          resolved,
        )}
        style={{ width: `${fill * 100}%` }}
      />
      {threshold != null && (
        <span
          aria-hidden
          className="absolute top-0 h-full w-0.5 -translate-x-1/2 bg-foreground/70"
          style={{ left: `${Math.max(0, Math.min(1, threshold)) * 100}%` }}
        />
      )}
    </div>
  );
}
