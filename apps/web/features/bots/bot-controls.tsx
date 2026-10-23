"use client";
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Power, PowerOff, RotateCcw, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, useConfirmRetry } from "@/components/common/confirm-dialog";
import { botsApi } from "@/lib/api/endpoints";
import { errorMessage } from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { BOT_ACTIONS, type Bot, type BotAction } from "@/types/api";

const ACTION_META: Record<BotAction, { label: string; icon: React.ComponentType; variant: "default" | "outline" | "secondary" | "destructive" }> = {
  enable: { label: "Enable", icon: Power, variant: "outline" },
  disable: { label: "Disable", icon: PowerOff, variant: "outline" },
  start: { label: "Start", icon: Play, variant: "default" },
  pause: { label: "Pause", icon: Pause, variant: "secondary" },
  resume: { label: "Resume", icon: RotateCcw, variant: "default" },
  stop: { label: "Stop", icon: Square, variant: "destructive" },
};

export const LIVE_START_PHRASE = "START LIVE";

/** Writes the backend's Bot into the caches. UI state only ever comes from server responses. */
export function useApplyBotUpdate() {
  const qc = useQueryClient();
  return React.useCallback(
    (bot: Bot) => {
      qc.setQueryData<Bot[]>(qk.bots, (list) => list?.map((b) => (b.id === bot.id ? bot : b)));
      qc.setQueryData<Bot>(qk.bot(bot.id), bot);
      void qc.invalidateQueries({ queryKey: qk.dashboardSummary });
      void qc.invalidateQueries({ queryKey: qk.status });
    },
    [qc],
  );
}

export function BotControls({ bot, liveEnvironment }: { bot: Bot; liveEnvironment: boolean }) {
  const apply = useApplyBotUpdate();
  const { run, dialog } = useConfirmRetry();
  const [error, setError] = React.useState<string | null>(null);
  const [liveConfirmOpen, setLiveConfirmOpen] = React.useState(false);

  const mutation = useMutation({
    mutationFn: ({ action, confirm }: { action: BotAction; confirm?: string }) => botsApi.action(bot.id, action, confirm),
    onSuccess: (updated) => apply(updated),
  });

  const allowed = BOT_ACTIONS.filter((a) => bot.allowed_actions.includes(a));

  async function perform(action: BotAction) {
    setError(null);
    try {
      await run(
        async (confirm) => {
          await mutation.mutateAsync({ action, confirm });
        },
        { title: `${ACTION_META[action].label} ${bot.name}`, destructive: action === "stop" || action === "start" },
      );
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  function onClick(action: BotAction) {
    if (action === "start" && (liveEnvironment || bot.environment === "live")) {
      setLiveConfirmOpen(true);
      return;
    }
    void perform(action);
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label={`Controls for ${bot.name}`}>
        {allowed.length === 0 ? <span className="text-xs text-muted-foreground">No actions available in this state</span> : null}
        {allowed.map((action) => {
          const meta = ACTION_META[action];
          const Icon = meta.icon;
          const busy = mutation.isPending && mutation.variables?.action === action;
          return (
            <Button
              key={action}
              size="xs"
              variant={meta.variant}
              onClick={() => onClick(action)}
              disabled={mutation.isPending}
              aria-busy={busy}
            >
              <Icon />
              {busy ? `${meta.label}…` : meta.label}
            </Button>
          );
        })}
      </div>
      {error ? (
        <p role="alert" className="text-xs text-loss">
          {error}
        </p>
      ) : null}
      <ConfirmDialog
        open={liveConfirmOpen}
        onOpenChange={setLiveConfirmOpen}
        title={`Start ${bot.name} in LIVE trading`}
        description="This bot will place real orders on the live broker account."
        phrase={LIVE_START_PHRASE}
        confirmLabel="Start live"
        destructive
        onConfirm={async (confirm) => {
          setError(null);
          await mutation.mutateAsync({ action: "start", confirm });
        }}
      />
      {dialog}
    </div>
  );
}
