import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { Overview } from "@/features/dashboard/overview";
import { emptyCharts, makeSummary } from "./fixtures";
import { mockFetch, renderWithProviders } from "./utils";

describe("Overview dashboard", () => {
  it("renders the account summary with simulated labels and empty states", async () => {
    mockFetch({
      "GET /dashboard/summary": makeSummary(),
      "GET /dashboard/charts": emptyCharts,
      "GET /settings": { timezone: "UTC", display_name: null },
    });
    renderWithProviders(<Overview />);

    expect(await screen.findByText("10,000.00 USD")).toBeInTheDocument();
    expect(screen.getAllByText("Simulated").length).toBeGreaterThan(0);
    expect(screen.getByText("Synthetic prices")).toBeInTheDocument();

    // Performance metrics without history say so instead of showing 0.
    const winRateLabel = screen.getByText("Win rate");
    expect(within(winRateLabel.closest("div")!.parentElement!).getByText("Not enough history")).toBeInTheDocument();
    expect(screen.getAllByText("Not enough history")).toHaveLength(7);

    // Empty states
    expect(screen.getByText("No bots configured")).toBeInTheDocument();
    expect(screen.getByText("No active circuit breakers")).toBeInTheDocument();
    expect(screen.getByText("No recent executions")).toBeInTheDocument();
    expect(screen.getByText("No upcoming events")).toBeInTheDocument();
    expect(await screen.findByText("No equity history in this range")).toBeInTheDocument();
    expect(screen.getByText("No completed cycles in this range")).toBeInTheDocument();
  });

  it("shows the API error message when the summary fails", async () => {
    mockFetch({
      "GET /dashboard/summary": () =>
        new Response(JSON.stringify({ error: { code: "internal", message: "Database unavailable", details: null } }), { status: 500 }),
      "GET /dashboard/charts": emptyCharts,
    });
    renderWithProviders(<Overview />);
    expect(await screen.findByText("Database unavailable", {}, { timeout: 8000 })).toBeInTheDocument();
  }, 10000);
});
