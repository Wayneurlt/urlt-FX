"use client";
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { Alert } from "@/components/ui/alert";
import { ErrorState, LoadingBlock } from "@/components/common/states";
import { JsonView } from "@/components/common/misc";
import { SideLabel, SimulatedTag } from "@/components/common/labels";
import { botsApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { fmtNumber, fmtSigned, pnlClass } from "@/lib/utils";
import type { Bot } from "@/types/api";
import { useApplyBotUpdate } from "./bot-controls";

export const LIQUIDATE_PHRASE = "LIQUIDATE";

export function ShutdownDialog({ bot, open, onOpenChange }: { bot: Bot; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const apply = useApplyBotUpdate();
  const [mode, setMode] = React.useState<"manage" | "liquidate">("manage");
  const [typed, setTyped] = React.useState("");
  const [result, setResult] = React.useState<string[] | null>(null);

  React.useEffect(() => {
    if (!open) {
      setMode("manage");
      setTyped("");
      setResult(null);
    }
  }, [open]);

  const preview = useQuery({
    queryKey: qk.shutdownPreview(bot.id),
    queryFn: () => botsApi.shutdownPreview(bot.id),
    enabled: open,
    staleTime: 0,
  });

  const mutation = useMutation({
    mutationFn: () =>
      botsApi.shutdown(bot.id, mode === "liquidate" ? { liquidate: true, confirm_text: typed } : { liquidate: false }),
    onSuccess: (res) => {
      apply(res.bot);
      setResult(res.closed_cycles);
      void qc.invalidateQueries({ queryKey: qk.positions });
      void qc.invalidateQueries({ queryKey: ["cycles"] });
    },
  });

  const canSubmit = mode === "manage" || typed === LIQUIDATE_PHRASE;

  return (
    <Dialog open={open} onOpenChange={(o) => !mutation.isPending && onOpenChange(o)}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Orderly shutdown — {bot.name}</DialogTitle>
          <DialogDescription>Review what is affected, then choose how open exposure is handled.</DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3">
            <Alert variant="success">
              Shutdown accepted. Bot status is now <strong>{mutation.data?.bot.status}</strong>.
              {result.length ? ` Closed cycles: ${result.length}.` : " No cycles were closed."}
            </Alert>
            {result.length ? (
              <ul className="space-y-1 text-xs">
                {result.map((id) => (
                  <li key={id}>
                    <Link className="num text-primary hover:underline" href={`/cycles/${encodeURIComponent(id)}`}>
                      {id}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)}>Close</Button>
            </DialogFooter>
          </div>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (canSubmit) mutation.mutate();
            }}
          >
            {preview.isLoading ? (
              <LoadingBlock rows={3} />
            ) : preview.error ? (
              <ErrorState error={preview.error} onRetry={() => void preview.refetch()} />
            ) : preview.data ? (
              <div className="space-y-3 text-sm">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">
                      Affected cycles ({preview.data.cycles.length})
                    </p>
                    {preview.data.cycles.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No active cycles.</p>
                    ) : (
                      <ul className="space-y-1 text-xs">
                        {preview.data.cycles.map((c) => (
                          <li key={c.id} className="flex items-center gap-2">
                            <span className="num">{c.id.slice(0, 8)}</span>
                            <span>{c.symbol}</span>
                            <span className="text-muted-foreground">{c.state}</span>
                            <SimulatedTag simulated={c.simulated} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">
                      Open positions ({preview.data.positions.length})
                    </p>
                    {preview.data.positions.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No open positions.</p>
                    ) : (
                      <ul className="space-y-1 text-xs">
                        {preview.data.positions.map((p) => (
                          <li key={p.leg_id} className="flex items-center gap-2">
                            <span>{p.symbol}</span>
                            <SideLabel side={p.side} />
                            <span className="num">{fmtNumber(p.volume, 2)}</span>
                            <span className={`num ${pnlClass(p.floating_pnl)}`}>{fmtSigned(p.floating_pnl)}</span>
                            <SimulatedTag simulated={p.simulated} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
                {preview.data.policy ? (
                  <details>
                    <summary className="cursor-pointer text-xs text-muted-foreground">Shutdown policy</summary>
                    <JsonView value={preview.data.policy} className="mt-1 max-h-40" />
                  </details>
                ) : null}

                <fieldset className="space-y-2">
                  <legend className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Shutdown mode</legend>
                  <label className="flex cursor-pointer items-start gap-2 rounded-md border border-border p-2">
                    <input
                      type="radio"
                      name="shutdown-mode"
                      value="manage"
                      checked={mode === "manage"}
                      onChange={() => setMode("manage")}
                      className="mt-1"
                    />
                    <span>
                      <span className="font-medium">Stop new entries, keep managing</span>
                      <span className="block text-xs text-muted-foreground">
                        Existing cycles keep their stops/trailing logic until they close naturally.
                      </span>
                    </span>
                  </label>
                  <label className="flex cursor-pointer items-start gap-2 rounded-md border border-loss/40 p-2">
                    <input
                      type="radio"
                      name="shutdown-mode"
                      value="liquidate"
                      checked={mode === "liquidate"}
                      onChange={() => setMode("liquidate")}
                      className="mt-1"
                    />
                    <span>
                      <span className="font-medium text-loss">Liquidate</span>
                      <span className="block text-xs text-muted-foreground">
                        Close all open positions of this bot at market now.
                      </span>
                    </span>
                  </label>
                </fieldset>

                {mode === "liquidate" ? (
                  <div className="space-y-1.5">
                    <Label htmlFor="liquidate-confirm">
                      Type <span className="num font-semibold text-foreground">{LIQUIDATE_PHRASE}</span> to confirm
                    </Label>
                    <Input
                      id="liquidate-confirm"
                      value={typed}
                      onChange={(e) => setTyped(e.target.value)}
                      autoComplete="off"
                      spellCheck={false}
                    />
                  </div>
                ) : null}
              </div>
            ) : null}

            {mutation.error ? (
              <p role="alert" className="mt-3 text-sm text-loss">
                {errorMessage(mutation.error)}
              </p>
            ) : null}

            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={mutation.isPending}>
                Cancel
              </Button>
              <Button
                type="submit"
                variant="destructive"
                disabled={!canSubmit || mutation.isPending || !preview.data}
              >
                {mutation.isPending ? "Working…" : mode === "liquidate" ? "Liquidate and shut down" : "Shut down"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
