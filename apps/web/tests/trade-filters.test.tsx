import { describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EMPTY_TRADE_FILTERS, TradeHistory, toTradeFilters } from "@/features/trades/trade-history";
import { buildQuery } from "@/lib/api/client";
import { makeBot } from "./fixtures";
import { mockFetch, renderWithProviders } from "./utils";

const emptyPage = { items: [], total: 0, page: 1, page_size: 50 };

describe("Trade history filters", () => {
  it("drops empty filters from the query string", () => {
    expect(buildQuery(toTradeFilters(EMPTY_TRADE_FILTERS, 1, 50))).toBe("?sort=-fill_time&page=1&page_size=50");
  });

  it("builds the right query string from the filter form", async () => {
    const { calls } = mockFetch({
      "GET /bots": [makeBot()],
      "GET /trades": emptyPage,
    });
    renderWithProviders(<TradeHistory />);
    await screen.findByRole("option", { name: "News Breakout" });

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2025-03-01" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2025-03-05" } });
    await userEvent.selectOptions(screen.getByLabelText("Bot"), "bot-1");
    await userEvent.type(screen.getByLabelText("Strategy version"), "1.2.0");
    await userEvent.type(screen.getByLabelText("Instrument"), "eurusd");
    await userEvent.selectOptions(screen.getByLabelText("Direction"), "SELL");
    await userEvent.selectOptions(screen.getByLabelText("Environment"), "paper");
    await userEvent.type(screen.getByLabelText("Trigger"), "news");
    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "loss");
    await userEvent.type(screen.getByLabelText("Status"), "closed");
    await userEvent.type(screen.getByLabelText("Cycle ID"), "cyc-1");
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() => expect(calls.filter((c) => c.path === "/trades").length).toBeGreaterThanOrEqual(2));
    const last = calls.filter((c) => c.path === "/trades").at(-1)!;
    expect(Object.fromEntries(last.query.entries())).toEqual({
      start: "2025-03-01T00:00:00+00:00",
      end: "2025-03-05T23:59:59+00:00",
      bot_id: "bot-1",
      strategy_version: "1.2.0",
      symbol: "EURUSD",
      side: "SELL",
      environment: "paper",
      trigger: "news",
      outcome: "loss",
      status: "closed",
      cycle_id: "cyc-1",
      sort: "-fill_time",
      page: "1",
      page_size: "50",
    });

    // The CSV export link carries the same filters minus pagination.
    const link = screen.getByRole("link", { name: /Export CSV/ });
    const href = new URL(link.getAttribute("href")!, "http://localhost");
    expect(href.pathname).toBe("/api/v1/trades/export.csv");
    expect(href.searchParams.get("outcome")).toBe("loss");
    expect(href.searchParams.get("page")).toBeNull();
  });
});
