"use client";
import * as React from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage, isApiError } from "@/lib/api/client";

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: React.ReactNode;
  /** When set, the user must type this exact phrase before confirming. */
  phrase?: string | null;
  confirmLabel?: string;
  destructive?: boolean;
  /** Called with the typed phrase (or undefined). Throwing keeps the dialog open and shows the error. */
  onConfirm: (confirmText: string | undefined) => Promise<void> | void;
  children?: React.ReactNode;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  phrase,
  confirmLabel = "Confirm",
  destructive,
  onConfirm,
  children,
}: ConfirmDialogProps) {
  const [typed, setTyped] = React.useState("");
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);
  const inputId = React.useId();

  React.useEffect(() => {
    if (!open) {
      setTyped("");
      setError(null);
      setPending(false);
    }
  }, [open]);

  const matches = !phrase || typed === phrase;

  async function handleConfirm(e: React.FormEvent) {
    e.preventDefault();
    // React events bubble through portals: don't let this submit reach an enclosing <form>.
    e.stopPropagation();
    if (!matches || pending) return;
    setPending(true);
    setError(null);
    try {
      await onConfirm(phrase ? typed : undefined);
      onOpenChange(false);
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !pending && onOpenChange(o)}>
      <DialogContent>
        <form onSubmit={handleConfirm} noValidate>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {description ? <DialogDescription asChild><div>{description}</div></DialogDescription> : (
              <DialogDescription className="sr-only">Confirm this action</DialogDescription>
            )}
          </DialogHeader>
          {children}
          {phrase ? (
            <div className="mt-3 space-y-1.5">
              <Label htmlFor={inputId}>
                Type <span className="num font-semibold text-foreground">{phrase}</span> to confirm
              </Label>
              <Input
                id={inputId}
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={typed.length > 0 && !matches}
                autoFocus
              />
            </div>
          ) : null}
          {error ? (
            <p role="alert" className="mt-3 text-sm text-loss">
              {errorMessage(error)}
              {isApiError(error) && error.requiredConfirmText && error.requiredConfirmText !== phrase
                ? ` (required phrase: ${error.requiredConfirmText})`
                : null}
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
              Cancel
            </Button>
            <Button type="submit" variant={destructive ? "destructive" : "default"} disabled={!matches || pending}>
              {pending ? "Working…" : confirmLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

interface PendingConfirm {
  title: string;
  description?: React.ReactNode;
  phrase: string;
  destructive?: boolean;
  confirmLabel?: string;
  run: (confirmText: string) => Promise<void>;
}

/**
 * Runs an action; if the API answers 409 with `required_confirm_text`, opens a confirmation
 * dialog requiring that phrase and re-submits with `confirm_text`.
 */
export function useConfirmRetry() {
  const [pending, setPending] = React.useState<PendingConfirm | null>(null);

  const run = React.useCallback(
    async (
      fn: (confirmText?: string) => Promise<void>,
      meta: { title: string; description?: React.ReactNode; destructive?: boolean; confirmLabel?: string },
    ) => {
      try {
        await fn();
      } catch (e) {
        if (isApiError(e) && e.status === 409 && e.requiredConfirmText) {
          setPending({
            ...meta,
            description: (
              <>
                <span className="block">{e.message}</span>
                {meta.description}
              </>
            ),
            phrase: e.requiredConfirmText,
            run: (t: string) => fn(t),
          });
          return;
        }
        throw e;
      }
    },
    [],
  );

  const dialog = pending ? (
    <ConfirmDialog
      open
      onOpenChange={(o) => !o && setPending(null)}
      title={pending.title}
      description={pending.description}
      phrase={pending.phrase}
      destructive={pending.destructive}
      confirmLabel={pending.confirmLabel}
      onConfirm={async (t) => {
        await pending.run(t ?? "");
      }}
    />
  ) : null;

  return { run, dialog };
}
