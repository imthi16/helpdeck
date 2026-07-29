import { cn } from "@/lib/utils";

/**
 * The product's mark is a citation marker. The brackets are the whole idea:
 * every answer HelpDeck gives points at the source it came from.
 */
export function Wordmark({
  className,
  size = "default",
}: {
  className?: string;
  size?: "default" | "lg";
}) {
  return (
    <span
      className={cn(
        "flex items-baseline gap-1.5 font-mono font-semibold tracking-tight",
        size === "lg" ? "text-lg" : "text-sm",
        className,
      )}
    >
      <span aria-hidden className="text-mark">
        [
      </span>
      HelpDeck
      <span aria-hidden className="text-mark">
        ]
      </span>
    </span>
  );
}
