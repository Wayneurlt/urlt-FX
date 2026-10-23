import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

export const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide whitespace-nowrap border",
  {
    variants: {
      variant: {
        default: "border-border bg-muted text-foreground",
        muted: "border-border bg-transparent text-muted-foreground",
        success: "border-profit/40 bg-profit/10 text-profit",
        danger: "border-loss/40 bg-loss/10 text-loss",
        warning: "border-warning/40 bg-warning/10 text-warning",
        info: "border-info/40 bg-info/10 text-info",
        live: "border-live bg-live text-white",
        paper: "border-paper/50 bg-paper/15 text-paper",
        sim: "border-sim/50 bg-sim/15 text-sim",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
