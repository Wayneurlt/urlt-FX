"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Bell, LogOut, Menu, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DataSourceBadge, Dot, EnvironmentBadge } from "@/components/common/labels";
import { useSystemStatus } from "@/hooks/use-system-status";
import { useSession } from "@/hooks/use-auth";
import { authApi } from "@/lib/api/endpoints";
import { formatAge } from "@/lib/time";
import { cn } from "@/lib/utils";
import { BOT_IDENTITIES, botIdentity, workspaceFromPath } from "@/lib/bots";

function StatusItem({ label, children, title }: { label: string; children: React.ReactNode; title?: string }) {
  return (
    <div className="flex items-center gap-1.5 whitespace-nowrap text-xs" title={title}>
      <span className="text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

export function AppHeader({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const status = useSystemStatus();
  const { user } = useSession();
  const router = useRouter();
  const qc = useQueryClient();
  const [loggingOut, setLoggingOut] = React.useState(false);
  const s = status.data;
  const live = s?.environment === "live";
  const pathname = usePathname();
  const wsId = workspaceFromPath(pathname);
  const ws = wsId && BOT_IDENTITIES.some((b) => b.id === wsId) ? botIdentity(wsId) : null;
  const WsIcon = ws?.icon;

  async function logout() {
    setLoggingOut(true);
    try {
      await authApi.logout();
    } catch {
      // Even if the call fails, drop client state and go to login.
    } finally {
      qc.clear();
      router.replace("/login");
    }
  }

  return (
    <header
      className={cn(
        "sticky top-0 z-30 border-b bg-surface/95 backdrop-blur",
        live ? "border-live/70 shadow-[inset_0_2px_0_0_var(--color-live)]" : "border-border",
      )}
    >
      <div className="flex h-12 items-center gap-2 px-2 sm:gap-3 sm:px-3">
        <Button variant="ghost" size="icon" onClick={onToggleSidebar} aria-label="Toggle navigation">
          <Menu />
        </Button>
        <EnvironmentBadge env={s?.environment} className={live ? "px-2 py-1 text-xs" : undefined} />
        {ws && WsIcon ? (
          <span
            className={cn("inline-flex min-w-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium", ws.chip)}
            aria-label={`Working on: ${ws.short} bot`}
            title={`You are in the ${ws.short} workspace`}
          >
            <WsIcon className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="sm:hidden">{ws.abbr}</span>
            <span className="hidden truncate sm:inline">{ws.short}</span>
          </span>
        ) : null}
        {s?.market_data.data_source === "synthetic" ? (
          <Badge variant="warning" title="Prices are generated, not real market data">
            <span className="sm:hidden">SYNTH</span>
            <span className="hidden sm:inline">SYNTHETIC PRICES</span>
          </Badge>
        ) : null}

        {s ? (
          <div className="flex items-center gap-1.5 md:hidden" aria-label="System status">
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground" title={`Broker: ${s.broker.connected ? "connected" : "disconnected"}`}>
              <Dot ok={s.broker.connected} />B
            </span>
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground" title={`Market data: ${s.market_data.fresh ? "fresh" : "stale"}`}>
              <Dot ok={s.market_data.fresh} />D
            </span>
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground" title={`Worker: ${s.worker.alive ? "alive" : "down"}`}>
              <Dot ok={s.worker.alive} />W
            </span>
            {s.active_breakers > 0 ? (
              <Link href="/risk" aria-label={`${s.active_breakers} circuit breakers active`} className="text-loss">
                <ShieldAlert className="size-3.5" aria-hidden="true" />
              </Link>
            ) : null}
          </div>
        ) : null}
        <div className="hidden min-w-0 flex-1 items-center gap-4 overflow-x-auto md:flex" aria-live="polite">
          {status.error ? (
            <span className="text-xs text-loss">Status unavailable</span>
          ) : s ? (
            <>
              <StatusItem label="Broker" title={s.broker.detail ?? undefined}>
                <Dot ok={s.broker.connected} />
                <span>{s.broker.name}</span>
                <span className={s.broker.connected ? "text-profit" : "text-loss"}>
                  {s.broker.connected ? "connected" : "disconnected"}
                </span>
                {s.broker.simulated ? <Badge variant="sim">Simulated</Badge> : null}
              </StatusItem>
              <StatusItem label="Data">
                <Dot ok={s.market_data.fresh} />
                <span>{s.market_data.provider}</span>
                {s.market_data.data_source !== "synthetic" ? <DataSourceBadge source={s.market_data.data_source} /> : null}
                <span className={cn("num", s.market_data.fresh ? "text-muted-foreground" : "text-warning")}>
                  {s.market_data.fresh ? "" : "stale · "}
                  {formatAge(s.market_data.last_update)}
                </span>
              </StatusItem>
              <StatusItem label="Worker">
                <Dot ok={s.worker.alive} />
                <span className={cn("num", s.worker.alive ? "text-muted-foreground" : "text-loss")}>
                  {s.worker.alive ? formatAge(s.worker.last_heartbeat) : `down · ${formatAge(s.worker.last_heartbeat)}`}
                </span>
              </StatusItem>
              {s.active_breakers > 0 ? (
                <Link href="/risk" className="flex items-center gap-1 text-xs text-loss hover:underline">
                  <ShieldAlert className="size-3.5" aria-hidden="true" />
                  {s.active_breakers} breaker{s.active_breakers === 1 ? "" : "s"} active
                </Link>
              ) : null}
            </>
          ) : (
            <span className="text-xs text-muted-foreground">Loading status…</span>
          )}
        </div>

        <div className="ml-auto flex items-center gap-1">
          <Button asChild variant="ghost" size="icon" className="relative">
            <Link
              href="/notifications"
              aria-label={`Notifications${s ? `, ${s.unread_notifications} unread` : ""}`}
            >
              <Bell />
              {s && s.unread_notifications > 0 ? (
                <span className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-loss px-1 text-center text-[10px] font-semibold leading-4 text-white num">
                  {s.unread_notifications > 99 ? "99+" : s.unread_notifications}
                </span>
              ) : null}
            </Link>
          </Button>
          <span className="hidden text-xs text-muted-foreground sm:inline">
            {user.username} <span className="opacity-70">({user.role})</span>
          </span>
          <Button variant="ghost" size="sm" onClick={logout} disabled={loggingOut} aria-label="Log out">
            <LogOut />
            <span className="hidden sm:inline">Logout</span>
          </Button>
        </div>
      </div>
      {live ? (
        <div className="border-t border-live/40 bg-live/10 px-3 py-1 text-xs font-medium text-live" role="status">
          LIVE TRADING — orders are sent to the real broker account
          {s?.broker.account_id ? <span className="num"> ({s.broker.account_id})</span> : null}.
        </div>
      ) : null}
    </header>
  );
}
