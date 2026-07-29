"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS } from "@/lib/nav";
import { cn } from "@/lib/utils";

function isActive(pathname: string, href: string): boolean {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="flex flex-col py-2" aria-label="Main">
      {NAV_ITEMS.map((item) => {
        const active = isActive(pathname, item.href);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              // The current view is marked, not filled: a highlighter tick in the
              // margin, the same gesture as a citation.
              "group relative flex items-center gap-3 py-2.5 pl-5 pr-4 text-sm transition-colors",
              "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-mark",
              active
                ? "text-rail-foreground"
                : "text-rail-muted hover:bg-sidebar-accent hover:text-rail-foreground",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "absolute left-0 top-1/2 h-6 w-[3px] -translate-y-1/2 rounded-r-sm transition-all",
                active ? "bg-mark" : "bg-transparent group-hover:bg-rail-muted/40",
              )}
            />
            <Icon className={cn("size-4 shrink-0", active && "text-mark")} />
            <span className={cn(active && "font-medium")}>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
