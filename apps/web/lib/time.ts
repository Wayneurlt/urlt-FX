/** All timestamps from the API are UTC ISO strings; render in the user's chosen timezone. */

export function isValidTimezone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

export function formatDateTime(iso: string | null | undefined, timeZone = "UTC", withSeconds = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const tz = isValidTimezone(timeZone) ? timeZone : "UTC";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: tz,
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
    hour12: false,
  }).format(d);
}

export function formatDate(iso: string | null | undefined, timeZone = "UTC"): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const tz = isValidTimezone(timeZone) ? timeZone : "UTC";
  return new Intl.DateTimeFormat("en-GB", { timeZone: tz, year: "numeric", month: "short", day: "2-digit" }).format(d);
}

export function formatTime(iso: string | null | undefined, timeZone = "UTC"): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const tz = isValidTimezone(timeZone) ? timeZone : "UTC";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(d);
}

/** "12s ago" / "4m ago" relative to `now` (defaults to Date.now()). */
export function formatAge(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "unknown";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** YYYY-MM-DD for <input type="date"> defaults, computed in UTC. */
export function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function daysAgo(n: number, from: Date = new Date()): Date {
  return new Date(from.getTime() - n * 86400_000);
}

/** Convert a date input (YYYY-MM-DD) to an ISO UTC start-of-day / end-of-day string. */
export function dayStartIso(day: string): string {
  return day ? `${day}T00:00:00+00:00` : "";
}
export function dayEndIso(day: string): string {
  return day ? `${day}T23:59:59+00:00` : "";
}

export function listTimezones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  const list = intl.supportedValuesOf?.("timeZone") ?? [];
  const base = list.length ? list : ["UTC", "Europe/London", "Europe/Berlin", "America/New_York", "Asia/Tokyo", "Africa/Nairobi"];
  return base.includes("UTC") ? base : ["UTC", ...base];
}
