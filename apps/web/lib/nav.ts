import {
  BarChart3,
  BookOpen,
  Inbox,
  LayoutDashboard,
  MessageSquare,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
}

export const NAV_ITEMS: NavItem[] = [
  { label: "Overview", href: "/dashboard", icon: LayoutDashboard },
  { label: "Knowledge Base", href: "/dashboard/knowledge-base", icon: BookOpen },
  { label: "Playground", href: "/dashboard/playground", icon: MessageSquare },
  { label: "Conversations", href: "/dashboard/conversations", icon: Inbox },
  { label: "Analytics", href: "/dashboard/analytics", icon: BarChart3 },
  { label: "Settings", href: "/dashboard/settings", icon: Settings },
];
