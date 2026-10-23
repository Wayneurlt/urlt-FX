import * as React from "react";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { humanize } from "@/lib/utils";
import type { BotStatus, Environment, HealthState } from "@/types/api";

export function EnvironmentBadge({ env, className }: { env: Environment | string | null | undefined; className?: string }) {
  if (env === "live")
    return (
      <Badge variant="live" className={className} aria-label="Environment: Live Trading">
        Live Trading
      </Badge>
    );
  if (env === "paper")
    return (
      <Badge variant="paper" className={className} aria-label="Environment: Paper Trading">
        Paper Trading
      </Badge>
    );
  if (env === "simulation")
    return (
      <Badge variant="sim" className={className} aria-label="Environment: Simulation">
        Simulation
      </Badge>
    );
  return (
    <Badge variant="muted" className={className} aria-label={env ? `Environment: ${humanize(env)}` : "Loading environment"}>
      {env ? humanize(env) : "…"}
    </Badge>
  );
}

/** Must be shown wherever the API says `simulated: true` — simulated fills are not broker executions. */
export function SimulatedTag({ simulated, className }: { simulated: boolean | null | undefined; className?: string }) {
  if (!simulated) return null;
  return (
    <Badge variant="sim" className={className} title="Simulated — not a real broker execution">
      Simulated
    </Badge>
  );
}

export function DataSourceBadge({ source, className }: { source: string | null | undefined; className?: string }) {
  if (!source) return null;
  if (source === "synthetic")
    return (
      <Badge variant="warning" className={className} title="Prices are generated, not real market data">
        Synthetic prices
      </Badge>
    );
  const variant: BadgeProps["variant"] = source === "live" ? "success" : source === "delayed" ? "warning" : "muted";
  return (
    <Badge variant={variant} className={className}>
      {humanize(source)} data
    </Badge>
  );
}

export function OriginBadge({ origin }: { origin: string | null | undefined }) {
  if (origin === "sample")
    return (
      <Badge variant="warning" title="Sample data">
        SAMPLE DATA – not real news
      </Badge>
    );
  if (origin === "mock") return <Badge variant="warning">Mock data</Badge>;
  if (origin === "provider") return <Badge variant="success">Provider</Badge>;
  return <Badge variant="muted">{origin ?? "unknown"}</Badge>;
}

const BOT_STATUS_VARIANT: Record<BotStatus, BadgeProps["variant"]> = {
  disabled: "muted",
  stopped: "default",
  starting: "info",
  running: "success",
  paused: "warning",
  risk_halted: "danger",
  error: "danger",
};

export function BotStatusBadge({ status }: { status: BotStatus | string }) {
  return (
    <Badge variant={BOT_STATUS_VARIANT[status as BotStatus] ?? "default"} data-testid="bot-status">
      {humanize(status)}
    </Badge>
  );
}

const HEALTH_VARIANT: Record<HealthState, BadgeProps["variant"]> = {
  ok: "success",
  degraded: "warning",
  down: "danger",
  unknown: "muted",
  not_configured: "muted",
};

export function HealthBadge({ state }: { state: HealthState | string }) {
  return <Badge variant={HEALTH_VARIANT[state as HealthState] ?? "default"}>{humanize(state)}</Badge>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  const v: BadgeProps["variant"] =
    severity === "critical" || severity === "error"
      ? "danger"
      : severity === "warning"
        ? "warning"
        : severity === "info"
          ? "info"
          : "muted";
  return <Badge variant={v}>{severity}</Badge>;
}

export function GenericStatusBadge({ status }: { status: string | null | undefined }) {
  const s = (status ?? "").toLowerCase();
  const v: BadgeProps["variant"] = ["succeeded", "connected", "approved", "ok", "closed", "win", "filled"].includes(s)
    ? "success"
    : ["failed", "dead", "error", "rejected", "loss", "disconnected", "down"].includes(s)
      ? "danger"
      : ["running", "queued", "open", "active", "pending"].includes(s)
        ? "info"
        : ["unverified", "degraded", "unavailable", "partial"].includes(s)
          ? "warning"
          : "muted";
  return <Badge variant={v}>{status ? humanize(status) : "—"}</Badge>;
}

export function SideLabel({ side }: { side: string | null | undefined }) {
  const s = (side ?? "").toUpperCase();
  return <span className={s === "BUY" ? "font-semibold text-profit" : s === "SELL" ? "font-semibold text-loss" : ""}>{s || "—"}</span>;
}

export function Dot({ ok, className }: { ok: boolean | null; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block size-2 rounded-full ${ok === null ? "bg-muted-foreground" : ok ? "bg-profit" : "bg-loss"} ${className ?? ""}`}
    />
  );
}
