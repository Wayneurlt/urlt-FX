import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RiskSettingsForm, buildRiskSchema, sectionOf } from "@/features/risk/risk-settings-form";
import { riskConfig } from "./fixtures";
import { apiError, json, mockFetch, renderWithProviders } from "./utils";

describe("Risk settings form", () => {
  it("builds Zod validation from field min/max", () => {
    const schema = buildRiskSchema(riskConfig.fields, riskConfig.config);
    expect(schema.safeParse({ f0: 0.5, f1: 2, f2: 3 }).success).toBe(true);
    expect(schema.safeParse({ f0: 5, f1: 2, f2: 3 }).success).toBe(false);
    expect(schema.safeParse({ f0: 0, f1: 2, f2: 3 }).success).toBe(false);
  });

  it("treats an empty nullable field (environment cap) as 'no cap' but still range-checks numbers", () => {
    const fields = [
      { path: "environments.live.max_cycle_risk_pct", section: "environments", label: "Live: max cycle risk %", description: "",
        unit: "%" as const, default: 0.5, min: 0.01, max: 2, nullable: true },
    ];
    const schema = buildRiskSchema(fields, { environments: { live: { max_cycle_risk_pct: null } } });
    const empty = schema.safeParse({ f0: Number.NaN });
    expect(empty.success && empty.data.f0).toBe(null);
    expect(schema.safeParse({ f0: 1.5 }).success).toBe(true);
    expect(schema.safeParse({ f0: 3 }).success).toBe(false);
    expect(sectionOf(fields[0])).toBe("environment");
  });

  it("groups fields into sections", () => {
    expect(sectionOf(riskConfig.fields[0])).toBe("global");
    expect(sectionOf(riskConfig.fields[2])).toBe("news_volatility");
    expect(sectionOf({ ...riskConfig.fields[0], path: "instruments.EURUSD.max_spread_pips" })).toBe("instruments");
    expect(sectionOf({ ...riskConfig.fields[0], section: "environment", path: "environments.live.max_risk" })).toBe("environment");
  });

  it("shows unit, default and range per field", () => {
    mockFetch({});
    renderWithProviders(<RiskSettingsForm data={riskConfig} />);
    expect(screen.getByText(/Default 0.5 · Range 0.01 – 2/)).toBeInTheDocument();
    expect(screen.getAllByText("%").length).toBeGreaterThan(0);
    expect(screen.getByText("pips")).toBeInTheDocument();
  });

  it("blocks out-of-range values without calling the API", async () => {
    const { calls } = mockFetch({});
    renderWithProviders(<RiskSettingsForm data={riskConfig} />);
    const input = screen.getByLabelText(/Risk per trade/);
    await userEvent.clear(input);
    await userEvent.type(input, "5");
    await userEvent.click(screen.getByRole("button", { name: "Save risk settings" }));
    expect(await screen.findByText(/Risk per trade: must be ≤ 2/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
  });

  it("opens a confirmation dialog on 409 and resubmits with the required phrase, then shows the diff", async () => {
    let attempt = 0;
    const { calls } = mockFetch({
      "PUT /risk/config": (call) => {
        attempt += 1;
        const body = call.body as { confirm_text?: string };
        if (!body.confirm_text) {
          return apiError(409, "confirmation_required", "Loosening risk limits requires confirmation", {
            required_confirm_text: "CONFIRM RISK INCREASE",
          });
        }
        return json({
          config: {},
          changes: [{ path: "global.max_daily_loss_pct", before: 2, after: 3 }],
          loosened: ["global.max_daily_loss_pct"],
        });
      },
      "GET /risk/config": riskConfig,
    });
    renderWithProviders(<RiskSettingsForm data={riskConfig} />);

    const input = screen.getByLabelText(/Max daily loss/);
    await userEvent.clear(input);
    await userEvent.type(input, "3");
    // Pending (before/after) diff is shown before saving.
    expect(screen.getByText(/Pending changes \(1\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save risk settings" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Loosening risk limits requires confirmation")).toBeInTheDocument();
    const apply = within(dialog).getByRole("button", { name: "Apply changes" });
    expect(apply).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/to confirm/), "CONFIRM RISK INCREASE");
    await userEvent.click(apply);

    await waitFor(() => expect(attempt).toBe(2));
    const puts = calls.filter((c) => c.method === "PUT");
    expect((puts[0].body as Record<string, unknown>).confirm_text).toBeUndefined();
    expect(puts[1].body).toMatchObject({
      confirm_text: "CONFIRM RISK INCREASE",
      config: { global: { max_daily_loss_pct: 3, risk_per_trade_pct: 0.5 } },
    });
    expect(await screen.findByText(/Risk configuration saved/)).toBeInTheDocument();
    expect(screen.getByText("Loosened")).toBeInTheDocument();
  });
});
