import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const alertVariants = cva("rounded-md border px-3 py-2 text-sm", {
  variants: {
    variant: {
      default: "border-border bg-surface-2",
      warning: "border-warning/50 bg-warning/10 text-warning",
      danger: "border-loss/50 bg-loss/10 text-loss",
      info: "border-info/40 bg-info/10 text-info",
      success: "border-profit/40 bg-profit/10 text-profit",
    },
  },
  defaultVariants: { variant: "default" },
});

export function Alert({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof alertVariants>) {
  return (
    <div
      role={variant === "danger" ? "alert" : "status"}
      className={cn(alertVariants({ variant }), className)}
      {...props}
    />
  );
}
