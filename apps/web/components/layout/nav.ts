import {
  Activity,
  BarChart3,
  Bell,
  Bot,
  FlaskConical,
  History,
  LayoutDashboard,
  ListChecks,
  Newspaper,
  Plug,
  Settings,
  ShieldAlert,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { workspaceHref } from "@/lib/bots";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

/** Portfolio view: everything across both bots. */
export const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/bots", label: "Trading Bots", icon: Bot },
  { href: "/positions", label: "Open Positions", icon: Activity },
  { href: "/trades", label: "Trade History", icon: History },
  { href: "/backtests", label: "Backtesting", icon: FlaskConical },
  { href: "/market", label: "Market & News", icon: Newspaper },
  { href: "/risk", label: "Risk Management", icon: ShieldAlert },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/integrations", label: "Integrations", icon: Plug },
  { href: "/notifications", label: "Notifications & Logs", icon: Bell },
  { href: "/settings", label: "Settings", icon: Settings },
];

/** Inside one bot's workspace: only that bot's data. */
export function workspaceNav(botId: string): NavItem[] {
  return [
    { href: workspaceHref(botId), label: "Overview", icon: LayoutDashboard },
    { href: workspaceHref(botId, "positions"), label: "Positions", icon: Activity },
    { href: workspaceHref(botId, "trades"), label: "Trades", icon: History },
    { href: workspaceHref(botId, "backtests"), label: "Backtests", icon: FlaskConical },
    { href: workspaceHref(botId, "configuration"), label: "Configuration", icon: SlidersHorizontal },
    { href: workspaceHref(botId, "activity"), label: "Activity", icon: ListChecks },
  ];
}

/** Account-wide pages shown under a bot workspace (risk is shared by both bots). */
export const SHARED_NAV: NavItem[] = [
  { href: "/dashboard", label: "Portfolio dashboard", icon: LayoutDashboard },
  { href: "/risk", label: "Risk Management", icon: ShieldAlert },
  { href: "/market", label: "Market & News", icon: Newspaper },
  { href: "/integrations", label: "Integrations", icon: Plug },
  { href: "/notifications", label: "Notifications & Logs", icon: Bell },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function isActive(pathname: string, href: string, exact = false): boolean {
  if (exact) return pathname === href;
  if (href === "/dashboard") return pathname === "/dashboard" || pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`) || (href === "/trades" && pathname.startsWith("/cycles"));
}
