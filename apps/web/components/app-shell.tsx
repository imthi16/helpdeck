"use client";

import { Menu } from "lucide-react";
import { useState } from "react";

import { SidebarNav } from "@/components/sidebar-nav";
import { Wordmark } from "@/components/wordmark";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import type { SessionUser } from "@/lib/session";
import { cn } from "@/lib/utils";

function initials(user: SessionUser): string {
  const source = user.name?.trim() || user.email;
  return source.slice(0, 2).toUpperCase();
}

export function AppShell({
  user,
  onLogout,
  children,
}: {
  user: SessionUser;
  onLogout: () => void;
  children: React.ReactNode;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const membership = user.memberships[0];
  const orgName = membership?.org_name ?? "HelpDeck";

  return (
    <div className="flex min-h-screen">
      {/* Desktop rail */}
      <aside className="hidden w-[15rem] shrink-0 flex-col bg-rail md:flex">
        <div className="flex h-14 items-center px-5">
          <Wordmark className="text-rail-foreground" />
        </div>
        <SidebarNav />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between gap-2 border-b bg-card px-4 md:px-6">
          <div className="flex min-w-0 items-center gap-2">
            {/* Mobile menu */}
            <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
              <SheetTrigger
                aria-label="Open navigation"
                className={cn(buttonVariants({ variant: "ghost", size: "icon" }), "md:hidden")}
              >
                <Menu className="size-5" />
              </SheetTrigger>
              <SheetContent side="left" className="w-64 border-0 bg-rail p-0">
                <SheetTitle className="flex h-14 items-center px-5">
                  <Wordmark className="text-rail-foreground" />
                </SheetTitle>
                <SidebarNav onNavigate={() => setMobileOpen(false)} />
              </SheetContent>
            </Sheet>
            <span
              className="truncate font-display text-[0.95rem] font-semibold"
              data-testid="org-name"
            >
              {orgName}
            </span>
            {membership && (
              <span className="eyebrow hidden shrink-0 border-l pl-2 sm:inline">
                {membership.role}
              </span>
            )}
          </div>

          <DropdownMenu>
            <DropdownMenuTrigger
              data-testid="user-menu"
              className={cn(buttonVariants({ variant: "ghost" }), "flex items-center gap-2")}
            >
              <Avatar className="size-7 rounded-md">
                <AvatarFallback className="rounded-md bg-secondary font-mono text-[0.7rem]">
                  {initials(user)}
                </AvatarFallback>
              </Avatar>
              <span className="hidden text-sm sm:inline" data-testid="user-email">
                {user.email}
              </span>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <div className="px-2 py-1.5 text-sm font-medium">{user.email}</div>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={onLogout}>Log out</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>

        <main className="min-w-0 flex-1 p-6 md:p-8">{children}</main>
      </div>
    </div>
  );
}
