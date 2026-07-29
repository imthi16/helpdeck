import type { Metadata } from "next";
import { Bricolage_Grotesque, IBM_Plex_Mono, Instrument_Sans } from "next/font/google";
import "./globals.css";

// Three roles, three faces. Bricolage carries titles and the measured numbers;
// Instrument Sans does the reading; Plex Mono is reserved for instrumentation —
// scores, token counts, document slugs, reference markers.
// The `--ff-*` names deliberately differ from Tailwind's `--font-*` theme keys:
// mapping a theme key to itself resolves to nothing and falls back to Times,
// which is what used to happen to every heading in this app.
const display = Bricolage_Grotesque({
  variable: "--ff-display",
  subsets: ["latin"],
  display: "swap",
});

const body = Instrument_Sans({
  variable: "--ff-body",
  subsets: ["latin"],
  display: "swap",
});

const mono = IBM_Plex_Mono({
  variable: "--ff-mono",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "HelpDeck",
  description:
    "AI customer support that answers only from your own docs — grounded, cited, and safe.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${body.variable} ${mono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
