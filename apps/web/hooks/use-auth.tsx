"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { authApi, settingsApi } from "@/lib/api/endpoints";
import { qk } from "@/lib/query-keys";
import type { User } from "@/types/api";
import { formatDate, formatDateTime, formatTime, isValidTimezone } from "@/lib/time";

interface SessionContextValue {
  user: User;
  isAdmin: boolean;
  timezone: string;
}

const SessionContext = React.createContext<SessionContextValue | null>(null);

export function SessionProvider({ user, children }: { user: User; children: React.ReactNode }) {
  // Timezone is a per-user server setting (GET /settings); fall back to the user's record, then UTC.
  const settings = useQuery({ queryKey: qk.settings, queryFn: settingsApi.get, staleTime: 60_000 });
  const tzCandidate = settings.data?.timezone ?? user.timezone ?? "UTC";
  const timezone = isValidTimezone(tzCandidate) ? tzCandidate : "UTC";
  const value = React.useMemo(() => ({ user, isAdmin: user.role === "admin", timezone }), [user, timezone]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const ctx = React.useContext(SessionContext);
  if (!ctx) {
    // Outside the authenticated shell (e.g. tests rendering a feature in isolation).
    return { user: { id: "anon", username: "anonymous", role: "viewer", timezone: "UTC" }, isAdmin: false, timezone: "UTC" };
  }
  return ctx;
}

/** Date formatters bound to the user's configured timezone. */
export function useTimeFormat() {
  const { timezone } = useSession();
  return React.useMemo(
    () => ({
      timezone,
      dt: (iso: string | null | undefined, seconds = false) => formatDateTime(iso, timezone, seconds),
      date: (iso: string | null | undefined) => formatDate(iso, timezone),
      time: (iso: string | null | undefined) => formatTime(iso, timezone),
    }),
    [timezone],
  );
}

export function useMe() {
  return useQuery({ queryKey: qk.me, queryFn: authApi.me, staleTime: 60_000, retry: false });
}
