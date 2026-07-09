"use client";

import { Menu } from "lucide-react";
import { useState } from "react";

import { SidebarNav } from "@/components/sidebar-nav";
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
  const orgName = user.memberships[0]?.org_name ?? "HelpDeck";

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 border-r md:block">
        <div className="flex h-14 items-center border-b px-4 font-semibold">HelpDeck</div>
        <SidebarNav />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between gap-2 border-b px-4">
          <div className="flex items-center gap-2">
            {/* Mobile menu */}
            <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
              <SheetTrigger
                aria-label="Open navigation"
                className={cn(buttonVariants({ variant: "ghost", size: "icon" }), "md:hidden")}
              >
                <Menu className="size-5" />
              </SheetTrigger>
              <SheetContent side="left" className="w-64 p-0">
                <SheetTitle className="px-4 py-4 text-base font-semibold">HelpDeck</SheetTitle>
                <SidebarNav onNavigate={() => setMobileOpen(false)} />
              </SheetContent>
            </Sheet>
            <span className="truncate font-medium" data-testid="org-name">
              {orgName}
            </span>
          </div>

          <DropdownMenu>
            <DropdownMenuTrigger
              data-testid="user-menu"
              className={cn(buttonVariants({ variant: "ghost" }), "flex items-center gap-2")}
            >
              <Avatar className="size-7">
                <AvatarFallback>{initials(user)}</AvatarFallback>
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

        <main className="min-w-0 flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
