import * as React from "react";
import { cn } from "@/lib/utils";

export const inputClass =
  "flex h-9 w-full rounded-md border border-input bg-surface-2 px-2.5 py-1 text-sm placeholder:text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50 aria-[invalid=true]:border-loss";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(inputClass, className)} {...props} />,
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea ref={ref} className={cn(inputClass, "h-auto min-h-24 font-mono text-xs", className)} {...props} />
  ),
);
Textarea.displayName = "Textarea";

/** Native select, styled — accessible by default and simple to test. */
export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select ref={ref} className={cn(inputClass, "pr-7", className)} {...props}>
      {children}
    </select>
  ),
);
Select.displayName = "Select";
