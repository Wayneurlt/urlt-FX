"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { CandlestickChart, Layers } from "lucide-react";
import { AppHeader } from "./app-header";
import { NAV_ITEMS, SHARED_NAV, isActive, workspaceNav, type NavItem } from "./nav";
import { BOT_IDENTITIES, botIdentity, workspaceFromPath, workspaceHref } from "@/lib/bots";
import { useMe, SessionProvider } from "@/hooks/use-auth";
import { onUnauthorized, isApiError } from "@/lib/api/client";
import { ErrorState, LoadingBlock } from "@/components/common/states";
import { cn } from "@/lib/utils";

function NavList({
  items,
  pathname,
  collapsed,
  onNavigate,
  accentText,
  exactFirst = false,
}: {
  items: NavItem[];
  pathname: string;
  collapsed: boolean;
  onNavigate: () => void;
  accentText?: string;
  exactFirst?: boolean;
}) {
  return (
    <ul className="space-y-0.5">
      {items.map((item, idx) => {
        const active = isActive(pathname, item.href, exactFirst && idx === 0);
        const Icon = item.icon;
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              title={collapsed ? item.label : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground lg:py-1.5",
                active && "bg-muted text-foreground",
                active && accentText,
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden="true" />
              <span className={cn(collapsed && "lg:sr-only")}>{item.label}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/** Choose what you are working on: the whole portfolio or exactly one bot. */
function WorkspaceSwitcher({ current, collapsed, onNavigate }: { current: string | null; collapsed: boolean; onNavigate: () => void }) {
  const options = [
    { id: null, href: "/dashboard", label: "Portfolio (both bots)", icon: Layers, chip: "border-border bg-muted text-foreground" },
    ...BOT_IDENTITIES.map((b) => ({ id: b.id, href: workspaceHref(b.id), label: b.short, icon: b.icon, chip: b.chip })),
  ];
  return (
    <div className="border-b border-border p-2">
      <p className={cn("px-1.5 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground", collapsed && "lg:sr-only")}>
        Workspace
      </p>
      <ul className="space-y-1" aria-label="Workspace">
        {options.map((o) => {
          const active = o.id === current;
          const Icon = o.icon;
          return (
            <li key={o.href}>
              <Link
                href={o.href}
                onClick={onNavigate}
                aria-current={active ? "true" : undefined}
                title={collapsed ? o.label : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-md border px-2 py-2 text-sm lg:py-1.5",
                  active ? cn(o.chip, "font-medium") : "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon className="size-4 shrink-0" aria-hidden="true" />
                <span className={cn("truncate", collapsed && "lg:sr-only")}>{o.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Sidebar({ collapsed, mobileOpen, onNavigate }: { collapsed: boolean; mobileOpen: boolean; onNavigate: () => void }) {
  const pathname = usePathname();
  const wsId = workspaceFromPath(pathname);
  const ws = wsId && BOT_IDENTITIES.some((b) => b.id === wsId) ? botIdentity(wsId) : null;
  return (
    <nav
      aria-label="Main navigation"
      className={cn(
        "fixed inset-y-0 left-0 z-40 flex flex-col border-r border-border bg-surface transition-[width,transform] duration-150 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0",
        collapsed ? "lg:w-14" : "lg:w-56",
        mobileOpen ? "w-64 translate-x-0" : "w-64 -translate-x-full",
      )}
    >
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
        <CandlestickChart className="size-5 shrink-0 text-primary" aria-hidden="true" />
        <span className={cn("text-sm font-semibold tracking-tight", collapsed && "lg:sr-only")}>urlt-FX</span>
      </div>
      <div className="flex-1 overflow-y-auto">
        <WorkspaceSwitcher current={ws?.id ?? null} collapsed={collapsed} onNavigate={onNavigate} />
        <div className="space-y-3 p-2">
          {ws ? (
            <>
              <div>
                <p className={cn("px-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider", ws.text, collapsed && "lg:sr-only")}>
                  {ws.short} bot
                </p>
                <NavList items={workspaceNav(ws.id)} pathname={pathname} collapsed={collapsed} onNavigate={onNavigate} accentText={ws.text} exactFirst />
              </div>
              <div>
                <p className={cn("px-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground", collapsed && "lg:sr-only")}>
                  Account (shared)
                </p>
                <NavList items={SHARED_NAV} pathname={pathname} collapsed={collapsed} onNavigate={onNavigate} />
              </div>
            </>
          ) : (
            <NavList items={NAV_ITEMS} pathname={pathname} collapsed={collapsed} onNavigate={onNavigate} />
          )}
        </div>
      </div>
    </nav>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const me = useMe();
  const [collapsed, setCollapsed] = React.useState(false);
  const [mobileOpen, setMobileOpen] = React.useState(false);

  React.useEffect(() => {
    onUnauthorized(() => router.replace(`/login?next=${encodeURIComponent(pathname)}`));
    return () => onUnauthorized(null);
  }, [router, pathname]);

  const unauthorized = isApiError(me.error) && me.error.status === 401;
  React.useEffect(() => {
    if (unauthorized) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [unauthorized, router, pathname]);

  if (me.isLoading || unauthorized) {
    return (
      <div className="mx-auto max-w-md p-10">
        <LoadingBlock rows={3} />
      </div>
    );
  }
  if (me.error || !me.data) {
    return (
      <div className="mx-auto max-w-md p-10">
        <ErrorState error={me.error ?? new Error("Session unavailable")} onRetry={() => void me.refetch()} />
      </div>
    );
  }

  function toggle() {
    if (typeof window !== "undefined" && window.matchMedia("(min-width: 1024px)").matches) setCollapsed((c) => !c);
    else setMobileOpen((o) => !o);
  }

  return (
    <SessionProvider user={me.data.user}>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-primary focus:px-3 focus:py-1"
      >
        Skip to content
      </a>
      <div className="flex min-h-screen">
        <Sidebar collapsed={collapsed} mobileOpen={mobileOpen} onNavigate={() => setMobileOpen(false)} />
        {mobileOpen ? (
          <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" aria-hidden="true" onClick={() => setMobileOpen(false)} />
        ) : null}
        <div className="flex min-w-0 flex-1 flex-col">
          <AppHeader onToggleSidebar={toggle} />
          <main id="main" className="min-w-0 flex-1 p-3 sm:p-4 lg:p-5">
            {children}
          </main>
        </div>
      </div>
    </SessionProvider>
  );
}
