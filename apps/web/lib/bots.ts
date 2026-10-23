import { CalendarClock, Zap, type LucideIcon } from "lucide-react";
import type { BotKind } from "@/types/api";

/**
 * Visual identity of each bot. Every place that shows bot-specific data uses this,
 * so the two bots are never confused: same colour, icon and short name everywhere.
 * Class strings are written out in full so Tailwind can see them.
 */
export interface BotIdentity {
  id: string;
  kind: BotKind;
  short: string;
  /** very short label for phone-width headers */
  abbr: string;
  tagline: string;
  icon: LucideIcon;
  /** text colour */
  text: string;
  /** chip: border + tinted background + text */
  chip: string;
  /** solid accent (bars, dots) */
  solid: string;
  /** left accent border for workspace header / cards */
  border: string;
  /** subtle tinted panel background */
  panel: string;
}

export const BOT_IDENTITIES: BotIdentity[] = [
  {
    id: "news-volatility",
    kind: "news_volatility",
    short: "News & Volatility",
    abbr: "N&V",
    tagline: "Dual BUY + SELL entries around news and volatility bursts; trails the surviving leg.",
    icon: Zap,
    text: "text-bot-nv",
    chip: "border-bot-nv/40 bg-bot-nv/10 text-bot-nv",
    solid: "bg-bot-nv",
    border: "border-l-bot-nv",
    panel: "bg-bot-nv/5",
  },
  {
    id: "daily-trading",
    kind: "daily_trading",
    short: "Daily Trading",
    abbr: "Daily",
    tagline: "Experimental intraday trend-following baseline: one position at a time with ATR stop and target.",
    icon: CalendarClock,
    text: "text-bot-daily",
    chip: "border-bot-daily/40 bg-bot-daily/10 text-bot-daily",
    solid: "bg-bot-daily",
    border: "border-l-bot-daily",
    panel: "bg-bot-daily/5",
  },
];

const FALLBACK: Omit<BotIdentity, "id" | "kind" | "short" | "abbr"> = {
  tagline: "",
  icon: Zap,
  text: "text-muted-foreground",
  chip: "border-border bg-muted text-muted-foreground",
  solid: "bg-muted-foreground",
  border: "border-l-border",
  panel: "",
};

/** Look up a bot by id ("news-volatility") or kind ("news_volatility"). */
export function botIdentity(idOrKind: string | null | undefined): BotIdentity {
  const found = BOT_IDENTITIES.find((b) => b.id === idOrKind || b.kind === idOrKind);
  if (found) return found;
  const label = idOrKind ? idOrKind.replace(/[-_]/g, " ") : "Unknown bot";
  return { ...FALLBACK, id: idOrKind ?? "", kind: (idOrKind ?? "news_volatility") as BotKind, short: label, abbr: label };
}

/** Sub-pages of a bot workspace, in display order. */
export const WORKSPACE_TABS = [
  { slug: "", label: "Overview" },
  { slug: "positions", label: "Positions" },
  { slug: "trades", label: "Trades" },
  { slug: "backtests", label: "Backtests" },
  { slug: "configuration", label: "Configuration" },
  { slug: "activity", label: "Activity" },
] as const;

export function workspaceHref(botId: string, slug = ""): string {
  return `/bots/${encodeURIComponent(botId)}${slug ? `/${slug}` : ""}`;
}

/** The bot workspace the current path belongs to, if any (e.g. "/bots/daily-trading/trades"). */
export function workspaceFromPath(pathname: string): string | null {
  const m = /^\/bots\/([^/]+)/.exec(pathname);
  return m ? decodeURIComponent(m[1]) : null;
}
