"use client";
import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { integrationsApi } from "@/lib/api/endpoints";
import { errorMessage, isApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Section } from "@/components/common/misc";
import { useTimeFormat } from "@/hooks/use-auth";
import type { AiAnalyzeRequest } from "@/types/api";

/** Optional AI commentary. A 409 means no AI provider is configured; shown as a neutral notice. */
export function AiAnalysis({ subject, id }: AiAnalyzeRequest) {
  const t = useTimeFormat();
  const m = useMutation({ mutationFn: () => integrationsApi.analyze({ subject, id }) });
  const notConfigured = isApiError(m.error) && m.error.status === 409;
  return (
    <Section
      title="AI analysis (optional)"
      actions={
        <Button size="sm" variant="outline" onClick={() => m.mutate()} disabled={m.isPending}>
          <Sparkles />
          {m.isPending ? "Analysing…" : m.data ? "Re-run analysis" : "AI analysis"}
        </Button>
      }
    >
      {notConfigured ? (
        <Alert variant="info">
          AI analysis is not configured. An administrator can add an AI provider under Integrations.
          {m.error && isApiError(m.error) && m.error.message ? <span className="block text-xs opacity-80">{m.error.message}</span> : null}
        </Alert>
      ) : m.error ? (
        <Alert variant="danger">{errorMessage(m.error)}</Alert>
      ) : m.data ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="info">AI commentary — not financial advice</Badge>
            {m.data.label ? <span>{m.data.label}</span> : null}
            <span>
              {m.data.provider} / {m.data.model} · {t.dt(m.data.generated_at)}
            </span>
          </div>
          <div className="whitespace-pre-wrap rounded-md border border-border bg-background p-3 text-sm leading-relaxed">{m.data.content}</div>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          Generates commentary from an external AI provider. Output is commentary only, not advice, and never drives trading.
        </p>
      )}
    </Section>
  );
}
