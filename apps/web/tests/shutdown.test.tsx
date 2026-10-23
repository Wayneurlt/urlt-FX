import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ShutdownDialog } from "@/features/bots/shutdown-dialog";
import { makeBot } from "./fixtures";
import { json, mockFetch, renderWithProviders } from "./utils";

const preview = {
  cycles: [
    {
      id: "cyc-abcdef12",
      kind: "dual_entry",
      bot_id: "bot-1",
      bot_name: "News Breakout",
      strategy_version: "1.2.0",
      trigger_type: "news",
      event_id: null,
      symbol: "EURUSD",
      environment: "paper",
      state: "open",
      volume: 0.2,
      planned_risk: 50,
      realized_pnl: null,
      outcome: null,
      created_at: "2025-03-05T13:00:00+00:00",
      updated_at: null,
      closed_at: null,
      trailing_activated_at: null,
      simulated: true,
      legs: [],
      notes: [],
      error: null,
    },
  ],
  positions: [],
  policy: { default: "manage" },
};

describe("Orderly shutdown", () => {
  it("liquidation requires typing LIQUIDATE and sends it as confirm_text", async () => {
    const { calls } = mockFetch({
      "GET /bots/bot-1/shutdown-preview": preview,
      "POST /bots/bot-1/shutdown": () => json({ bot: makeBot({ status: "stopped" }), closed_cycles: ["cyc-abcdef12"] }),
    });
    renderWithProviders(<ShutdownDialog bot={makeBot()} open onOpenChange={vi.fn()} />);

    expect(await screen.findByText("EURUSD")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/Liquidate/));
    const submit = screen.getByRole("button", { name: "Liquidate and shut down" });
    expect(submit).toBeDisabled();

    const input = screen.getByLabelText(/to confirm/);
    await userEvent.type(input, "liquidate");
    expect(submit).toBeDisabled();

    await userEvent.clear(input);
    await userEvent.type(input, "LIQUIDATE");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);

    await waitFor(() => {
      const post = calls.find((c) => c.method === "POST");
      expect(post?.path).toBe("/bots/bot-1/shutdown");
      expect(post?.body).toEqual({ liquidate: true, confirm_text: "LIQUIDATE" });
    });
    expect(await screen.findByText(/Shutdown accepted/)).toBeInTheDocument();
  });

  it("stop-new-entries mode needs no phrase and does not liquidate", async () => {
    const { calls } = mockFetch({
      "GET /bots/bot-1/shutdown-preview": preview,
      "POST /bots/bot-1/shutdown": () => json({ bot: makeBot({ status: "stopped" }), closed_cycles: [] }),
    });
    renderWithProviders(<ShutdownDialog bot={makeBot()} open onOpenChange={vi.fn()} />);
    await screen.findByText("EURUSD");
    await userEvent.click(screen.getByRole("button", { name: "Shut down" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ liquidate: false }));
  });
});
