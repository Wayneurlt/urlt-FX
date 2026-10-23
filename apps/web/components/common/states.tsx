import * as React from "react";
import { AlertTriangle, Inbox } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage, isApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export function LoadingBlock({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)} role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-7 w-full" />
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const code = isApiError(error) ? `${error.status} ${error.code}` : null;
  return (
    <div role="alert" className={cn("flex items-start gap-2 rounded-md border border-loss/40 bg-loss/5 p-3 text-sm", className)}>
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-loss" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-loss">{errorMessage(error)}</p>
        {code ? <p className="mt-0.5 text-xs text-muted-foreground num">{code}</p> : null}
      </div>
      {onRetry ? (
        <Button size="xs" variant="outline" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  className,
  icon,
}: {
  title: string;
  description?: React.ReactNode;
  className?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-1 py-8 text-center", className)}>
      <span className="text-muted-foreground" aria-hidden="true">
        {icon ?? <Inbox className="size-5" />}
      </span>
      <p className="text-sm font-medium">{title}</p>
      {description ? <p className="max-w-md text-xs text-muted-foreground">{description}</p> : null}
    </div>
  );
}

/** Renders loading / error / content for a TanStack Query result. */
export function QueryState<T>({
  query,
  children,
  loading,
  rows,
}: {
  query: { data: T | undefined; isLoading: boolean; error: unknown; refetch: () => unknown };
  children: (data: T) => React.ReactNode;
  loading?: React.ReactNode;
  rows?: number;
}) {
  if (query.isLoading) return <>{loading ?? <LoadingBlock rows={rows} />}</>;
  if (query.error) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  if (query.data === undefined) return <LoadingBlock rows={rows} />;
  return <>{children(query.data)}</>;
}
