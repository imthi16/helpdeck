"use client";

import Script from "next/script";

/**
 * Embeds the live HelpDeck widget against the public demo org (task 7.4).
 * Renders nothing unless NEXT_PUBLIC_DEMO_WIDGET_KEY is configured, so the
 * landing page works in every environment.
 */
export function DemoWidget() {
  const publicKey = process.env.NEXT_PUBLIC_DEMO_WIDGET_KEY;
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const src = process.env.NEXT_PUBLIC_WIDGET_URL ?? "/helpdeck.js";
  if (!publicKey) return null;
  return (
    <Script
      src={src}
      strategy="lazyOnload"
      data-public-key={publicKey}
      data-api-url={apiUrl}
    />
  );
}
