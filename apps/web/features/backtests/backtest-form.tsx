"use client";
import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { backtestApi } from "@/lib/api/endpoints";
import { errorMessage, isApiError } from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { FieldError } from "@/components/common/misc";
import { BotChip } from "@/components/common/bot-chip";
import { daysAgo, dayEndIso, dayStartIso, isoDay } from "@/lib/time";
import type { BacktestRequest, BacktestRun, BotKind } from "@/types/api";

const num = (msg = "Required") => z.number({ error: msg }).refine(Number.isFinite, msg);
const nonNeg = num().refine((v) => v >= 0, "Must be ≥ 0");
const prob = num().refine((v) => v >= 0 && v <= 1, "Must be between 0 and 1");

function isJsonObject(s: string): boolean {
  try {
    const v: unknown = JSON.parse(s || "{}");
    return !!v && typeof v === "object" && !Array.isArray(v);
  } catch {
    return false;
  }
}

export const backtestSchema = z
  .object({
    bot_kind: z.enum(["news_volatility", "daily_trading"]),
    symbol: z
      .string()
      .trim()
      .min(3, "Symbol is required")
      .max(20)
      .regex(/^[A-Za-z0-9._]+$/, "Letters/digits only"),
    start: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Start date required"),
    end: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "End date required"),
    initial_balance: num().refine((v) => v > 0, "Must be > 0"),
    account_currency: z
      .string()
      .trim()
      .regex(/^[A-Za-z]{3}$/, "3-letter currency code"),
    dataset_id: z.string(),
    seed: z.string().regex(/^\d*$/, "Whole number or empty"),
    strategy_json: z.string().refine(isJsonObject, "Must be a valid JSON object"),
    conversion_json: z.string().refine(isJsonObject, "Must be a valid JSON object"),
    costs: z.object({
      spread_mode: z.string().min(1),
      fixed_spread_pips: nonNeg,
      spread_multiplier: nonNeg,
      slippage_points: nonNeg,
      random_slippage_points: nonNeg,
      second_leg_extra_slippage_points: nonNeg,
      commission_per_lot_side: nonNeg,
      swap_long_per_lot_day: num(),
      swap_short_per_lot_day: num(),
    }),
    execution: z.object({
      latency_bars: num().refine((v) => Number.isInteger(v) && v >= 0, "Whole number ≥ 0"),
      reject_probability: prob,
      partial_fill_probability: prob,
      partial_fill_ratio: prob,
      leverage: num().refine((v) => v > 0, "Must be > 0"),
    }),
    risk: z.object({
      risk_per_entry_pct: num().refine((v) => v > 0 && v <= 100, "0–100"),
      max_daily_loss_pct: num().refine((v) => v > 0 && v <= 100, "0–100"),
      max_consecutive_losses: num().refine((v) => Number.isInteger(v) && v >= 0, "Whole number ≥ 0"),
      max_spread_pips: nonNeg,
    }),
  })
  .refine((v) => v.end >= v.start, { path: ["end"], message: "End must be on or after start" });

export type BacktestFormValues = z.infer<typeof backtestSchema>;

export function defaultBacktestValues(botKind: BotKind = "news_volatility"): BacktestFormValues {
  return {
    bot_kind: botKind,
    symbol: "EURUSD",
    start: isoDay(daysAgo(90)),
    end: isoDay(daysAgo(1)),
    initial_balance: 10000,
    account_currency: "USD",
    dataset_id: "",
    seed: "42",
    strategy_json: "{}",
    conversion_json: "{}",
    costs: {
      spread_mode: "data",
      fixed_spread_pips: 1,
      spread_multiplier: 1,
      slippage_points: 1,
      random_slippage_points: 2,
      second_leg_extra_slippage_points: 1,
      commission_per_lot_side: 3.5,
      swap_long_per_lot_day: -0.5,
      swap_short_per_lot_day: -0.3,
    },
    execution: { latency_bars: 0, reject_probability: 0, partial_fill_probability: 0, partial_fill_ratio: 1, leverage: 30 },
    risk: { risk_per_entry_pct: 0.5, max_daily_loss_pct: 1.5, max_consecutive_losses: 3, max_spread_pips: 3 },
  };
}

/** Maps validated form values onto the BacktestRequest contract. */
export function toBacktestRequest(v: BacktestFormValues): BacktestRequest {
  return {
    bot_kind: v.bot_kind,
    symbol: v.symbol.trim().toUpperCase(),
    base_timeframe: "M1",
    start: dayStartIso(v.start),
    end: dayEndIso(v.end),
    initial_balance: v.initial_balance,
    account_currency: v.account_currency.trim().toUpperCase(),
    dataset_id: v.dataset_id ? v.dataset_id : null,
    seed: v.seed === "" ? null : Number(v.seed),
    strategy: JSON.parse(v.strategy_json || "{}") as Record<string, unknown>,
    costs: { ...v.costs },
    execution: { ...v.execution },
    risk: { ...v.risk },
    conversion_rates: JSON.parse(v.conversion_json || "{}") as Record<string, number>,
  };
}

type NumPath =
  | `costs.${Exclude<keyof BacktestFormValues["costs"], "spread_mode">}`
  | `execution.${keyof BacktestFormValues["execution"]}`
  | `risk.${keyof BacktestFormValues["risk"]}`
  | "initial_balance";

function getErr(errors: Record<string, unknown>, path: string): string | undefined {
  let cur: unknown = errors;
  for (const k of path.split(".")) cur = cur && typeof cur === "object" ? (cur as Record<string, unknown>)[k] : undefined;
  const m = (cur as { message?: unknown } | undefined)?.message;
  return typeof m === "string" ? m : undefined;
}

export function BacktestForm({
  initialBotKind,
  lockBotKind = false,
  onCreated,
}: {
  initialBotKind?: BotKind;
  /** Inside a bot workspace the bot cannot be changed. */
  lockBotKind?: boolean;
  onCreated?: (run: BacktestRun) => void;
}) {
  const qc = useQueryClient();
  const datasets = useQuery({ queryKey: qk.datasets, queryFn: backtestApi.datasets });
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<BacktestFormValues>({
    resolver: zodResolver(backtestSchema),
    defaultValues: defaultBacktestValues(initialBotKind),
  });
  const create = useMutation({
    mutationFn: (req: BacktestRequest) => backtestApi.create(req),
    onSuccess: (run) => {
      void qc.invalidateQueries({ queryKey: ["backtests"] });
      onCreated?.(run);
    },
  });

  const errs = errors as unknown as Record<string, unknown>;

  const numField = (path: NumPath, label: string, step = "any") => {
    const id = `bt-${path.replace(/\./g, "-")}`;
    const err = getErr(errs, path);
    return (
      <div className="space-y-1" key={path}>
        <Label htmlFor={id}>{label}</Label>
        <Input
          id={id}
          type="number"
          step={step}
          aria-invalid={!!err}
          {...register(path, { valueAsNumber: true })}
        />
        <FieldError message={err} />
      </div>
    );
  };

  const onSubmit = handleSubmit((values) => create.mutate(toBacktestRequest(values)));
  const apiFieldErrors = isApiError(create.error) ? create.error.fieldErrors : {};

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4" aria-label="Backtest configuration">
      <fieldset className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <legend className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Run</legend>
        <div className="space-y-1">
          <Label htmlFor="bt-bot_kind">Bot</Label>
          {lockBotKind && initialBotKind ? (
            <>
              <input type="hidden" id="bt-bot_kind" {...register("bot_kind")} />
              <div className="flex h-9 items-center">
                <BotChip bot={initialBotKind} link={false} />
              </div>
            </>
          ) : (
            <Select id="bt-bot_kind" {...register("bot_kind")}>
              <option value="news_volatility">News &amp; Volatility</option>
              <option value="daily_trading">Daily Trading</option>
            </Select>
          )}
        </div>
        <div className="space-y-1">
          <Label htmlFor="bt-symbol">Symbol</Label>
          <Input id="bt-symbol" aria-invalid={!!errors.symbol} {...register("symbol")} />
          <FieldError message={errors.symbol?.message} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="bt-start">Start (UTC)</Label>
          <Input id="bt-start" type="date" aria-invalid={!!errors.start} {...register("start")} />
          <FieldError message={errors.start?.message} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="bt-end">End (UTC)</Label>
          <Input id="bt-end" type="date" aria-invalid={!!errors.end} {...register("end")} />
          <FieldError message={errors.end?.message} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="bt-timeframe">Base timeframe</Label>
          <Input id="bt-timeframe" value="M1" readOnly aria-readonly="true" />
        </div>
        {numField("initial_balance", "Initial balance")}
        <div className="space-y-1">
          <Label htmlFor="bt-ccy">Account currency</Label>
          <Input id="bt-ccy" aria-invalid={!!errors.account_currency} {...register("account_currency")} />
          <FieldError message={errors.account_currency?.message} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="bt-seed">Random seed</Label>
          <Input id="bt-seed" inputMode="numeric" aria-invalid={!!errors.seed} {...register("seed")} />
          <FieldError message={errors.seed?.message} />
        </div>
        <div className="col-span-2 space-y-1 md:col-span-4">
          <Label htmlFor="bt-dataset">Dataset</Label>
          <Select id="bt-dataset" {...register("dataset_id")}>
            <option value="">SYNTHETIC — generated prices (not real market data)</option>
            {datasets.data?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.symbol} {d.timeframe} · {d.rows.toLocaleString()} rows · {d.start.slice(0, 10)} → {d.end.slice(0, 10)} · {d.source}
              </option>
            ))}
          </Select>
          {datasets.error ? <p className="text-xs text-loss">Datasets unavailable: {errorMessage(datasets.error)}</p> : null}
        </div>
      </fieldset>

      <fieldset className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <legend className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Cost model</legend>
        <div className="space-y-1">
          <Label htmlFor="bt-spread_mode">Spread mode</Label>
          <Select id="bt-spread_mode" {...register("costs.spread_mode")}>
            <option value="data">From data (spread column)</option>
            <option value="fixed">Fixed</option>
          </Select>
        </div>
        {numField("costs.fixed_spread_pips", "Fixed spread (pips)")}
        {numField("costs.spread_multiplier", "Spread multiplier")}
        {numField("costs.slippage_points", "Slippage (points)")}
        {numField("costs.random_slippage_points", "Random slippage (points)")}
        {numField("costs.second_leg_extra_slippage_points", "2nd-leg extra slippage")}
        {numField("costs.commission_per_lot_side", "Commission / lot / side")}
        {numField("costs.swap_long_per_lot_day", "Swap long / lot / day")}
        {numField("costs.swap_short_per_lot_day", "Swap short / lot / day")}
      </fieldset>

      <fieldset className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <legend className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Execution model</legend>
        {numField("execution.latency_bars", "Latency (bars)", "1")}
        {numField("execution.reject_probability", "Reject probability")}
        {numField("execution.partial_fill_probability", "Partial fill probability")}
        {numField("execution.partial_fill_ratio", "Partial fill ratio")}
        {numField("execution.leverage", "Leverage")}
      </fieldset>

      <fieldset className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <legend className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Risk model</legend>
        {numField("risk.risk_per_entry_pct", "Risk per entry (%)")}
        {numField("risk.max_daily_loss_pct", "Max daily loss (%)")}
        {numField("risk.max_consecutive_losses", "Max consecutive losses", "1")}
        {numField("risk.max_spread_pips", "Max spread (pips)")}
      </fieldset>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="bt-strategy">Strategy parameters (JSON — bot config fields)</Label>
          <Textarea id="bt-strategy" rows={6} aria-invalid={!!errors.strategy_json} {...register("strategy_json")} />
          <FieldError message={errors.strategy_json?.message} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="bt-conv">Conversion rates (JSON, e.g. {`{"GBPUSD": 1.27}`})</Label>
          <Textarea id="bt-conv" rows={6} aria-invalid={!!errors.conversion_json} {...register("conversion_json")} />
          <FieldError message={errors.conversion_json?.message} />
        </div>
      </div>

      {create.error ? (
        <Alert variant="danger">
          {errorMessage(create.error)}
          {Object.keys(apiFieldErrors).length ? (
            <ul className="mt-1 list-inside list-disc text-xs">
              {Object.entries(apiFieldErrors).map(([k, v]) => (
                <li key={k}>
                  <span className="num">{k}</span>: {v}
                </li>
              ))}
            </ul>
          ) : null}
        </Alert>
      ) : null}
      {create.data ? <Alert variant="success">Backtest queued (run {create.data.id.slice(0, 8)}).</Alert> : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? "Queuing…" : "Queue backtest"}
        </Button>
      </div>
    </form>
  );
}
