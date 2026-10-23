import * as React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn, prettyJson } from "@/lib/utils";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Section({
  title,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <Card className={className}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <div className={cn("p-3", bodyClassName)}>{children}</div>
    </Card>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-2 pt-3 text-xs text-muted-foreground">
      <span className="num">
        {total === 0 ? "0 results" : `${(page - 1) * pageSize + 1}–${Math.min(total, page * pageSize)} of ${total}`}
      </span>
      <div className="flex items-center gap-1">
        <Button
          size="xs"
          variant="outline"
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          aria-label="Previous page"
        >
          <ChevronLeft />
        </Button>
        <span className="num px-1">
          {page} / {pages}
        </span>
        <Button
          size="xs"
          variant="outline"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= pages}
          aria-label="Next page"
        >
          <ChevronRight />
        </Button>
      </div>
    </nav>
  );
}

export function JsonView({ value, className }: { value: unknown; className?: string }) {
  return (
    <pre
      className={cn(
        "max-h-96 overflow-auto rounded-md border border-border bg-background p-3 text-xs leading-relaxed num",
        className,
      )}
    >
      {value === undefined || value === null ? "—" : prettyJson(value)}
    </pre>
  );
}

export function KeyValue({ items, className }: { items: [React.ReactNode, React.ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm", className)}>
      {items.map(([k, v], i) => (
        <React.Fragment key={i}>
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words num">{v ?? "—"}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function StatCard({
  label,
  value,
  hint,
  valueClassName,
  tag,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  valueClassName?: string;
  tag?: React.ReactNode;
}) {
  return (
    <Card className="px-3.5 py-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
        {tag}
      </div>
      <p className={cn("mt-1 text-lg font-semibold num", valueClassName)}>{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p> : null}
    </Card>
  );
}

/** Horizontal usage meter (value vs limit). */
export function Meter({
  label,
  value,
  max,
  display,
  warnAt = 0.7,
}: {
  label: string;
  value: number | null;
  max: number | null;
  display: React.ReactNode;
  warnAt?: number;
}) {
  const ratio = value !== null && max ? Math.min(1, Math.max(0, value / max)) : null;
  const color = ratio === null ? "bg-muted-foreground" : ratio >= 0.9 ? "bg-loss" : ratio >= warnAt ? "bg-warning" : "bg-profit";
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="num">{display}</span>
      </div>
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-muted"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max ?? undefined}
        aria-valuenow={value ?? undefined}
      >
        <div className={cn("h-full rounded-full", color)} style={{ width: `${(ratio ?? 0) * 100}%` }} />
      </div>
    </div>
  );
}

export function FieldError({ message, id }: { message?: string; id?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="text-xs text-loss" role="alert">
      {message}
    </p>
  );
}
