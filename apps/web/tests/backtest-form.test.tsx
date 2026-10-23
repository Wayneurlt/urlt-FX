import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BacktestForm } from "@/features/backtests/backtest-form";
import { json, mockFetch, renderWithProviders } from "./utils";

const datasets = [
  {
    id: "ds-1",
    symbol: "EURUSD",
    timeframe: "M1",
    rows: 1000,
    start: "2024-01-01T00:00:00+00:00",
    end: "2024-02-01T00:00:00+00:00",
    source: "dukascopy",
    quality: "ok",
    created_at: "2024-03-01T00:00:00+00:00",
  },
];

describe("Backtest form", () => {
  it("submits a BacktestRequest payload matching the contract", async () => {
    const { calls } = mockFetch({
      "GET /datasets": datasets,
      "POST /backtests": () =>
        json({ id: "run-12345678", status: "queued", progress: 0, error: null, created_at: "", finished_at: null, bot_kind: "daily_trading", symbol: "GBPUSD", start: "", end: "", data_source: "historical", summary: null }),
    });
    const onCreated = vi.fn();
    renderWithProviders(<BacktestForm initialBotKind="daily_trading" onCreated={onCreated} />);

    await screen.findByRole("option", { name: /EURUSD M1/ });
    await userEvent.clear(screen.getByLabelText("Symbol"));
    await userEvent.type(screen.getByLabelText("Symbol"), "gbpusd");
    fireEvent.change(screen.getByLabelText("Start (UTC)"), { target: { value: "2024-01-01" } });
    fireEvent.change(screen.getByLabelText("End (UTC)"), { target: { value: "2024-01-31" } });
    await userEvent.clear(screen.getByLabelText("Initial balance"));
    await userEvent.type(screen.getByLabelText("Initial balance"), "25000");
    await userEvent.selectOptions(screen.getByLabelText("Dataset"), "ds-1");
    await userEvent.clear(screen.getByLabelText(/Strategy parameters/));
    fireEvent.change(screen.getByLabelText(/Strategy parameters/), { target: { value: '{"entry_hour": 8}' } });
    await userEvent.clear(screen.getByLabelText("Fixed spread (pips)"));
    await userEvent.type(screen.getByLabelText("Fixed spread (pips)"), "1.5");

    await userEvent.click(screen.getByRole("button", { name: "Queue backtest" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    const post = calls.find((c) => c.method === "POST" && c.path === "/backtests");
    expect(post?.body).toEqual({
      bot_kind: "daily_trading",
      symbol: "GBPUSD",
      base_timeframe: "M1",
      start: "2024-01-01T00:00:00+00:00",
      end: "2024-01-31T23:59:59+00:00",
      initial_balance: 25000,
      account_currency: "USD",
      dataset_id: "ds-1",
      seed: 42,
      strategy: { entry_hour: 8 },
      costs: {
        spread_mode: "data",
        fixed_spread_pips: 1.5,
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
      conversion_rates: {},
    });
  });

  it("uses dataset_id null (synthetic) by default and rejects invalid strategy JSON", async () => {
    const { calls } = mockFetch({ "GET /datasets": [] });
    renderWithProviders(<BacktestForm />);
    expect(screen.getByRole("option", { name: /SYNTHETIC/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Strategy parameters/), { target: { value: "{not json" } });
    await userEvent.click(screen.getByRole("button", { name: "Queue backtest" }));
    expect(await screen.findByText("Must be a valid JSON object")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });
});
