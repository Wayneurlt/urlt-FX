import { describe, expect, it } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BotsPage } from "@/features/bots/bots-page";
import { makeBot, status } from "./fixtures";
import { apiError, deferred, json, mockFetch, renderWithProviders } from "./utils";

describe("Bot controls", () => {
  it("renders only allowed actions and updates status only after the backend responds", async () => {
    const pending = deferred<Response>();
    const { calls } = mockFetch({
      "GET /bots": [makeBot()],
      "GET /system/status": status,
      "GET /settings": { timezone: "UTC", display_name: null },
      "POST /bots/bot-1/actions/pause": () => pending.promise,
    });
    renderWithProviders(<BotsPage />);

    const card = await screen.findByTestId("bot-card-bot-1");
    expect(within(card).getByTestId("bot-status")).toHaveTextContent("Running");
    expect(within(card).queryByRole("button", { name: /^Start/ })).not.toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /^Enable/ })).not.toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "Pause" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/bots/bot-1/actions/pause")).toBe(true));
    // No optimistic update while the request is in flight.
    expect(within(card).getByTestId("bot-status")).toHaveTextContent("Running");

    await act(async () => {
      pending.resolve(json(makeBot({ status: "paused", allowed_actions: ["resume", "stop"] })));
    });

    await waitFor(() => expect(within(card).getByTestId("bot-status")).toHaveTextContent("Paused"));
    expect(within(card).getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("keeps the previous status and shows the error when the transition is refused", async () => {
    mockFetch({
      "GET /bots": [makeBot()],
      "GET /system/status": status,
      "POST /bots/bot-1/actions/stop": () => apiError(409, "invalid_transition", "Cannot stop while starting"),
    });
    renderWithProviders(<BotsPage />);
    const card = await screen.findByTestId("bot-card-bot-1");
    await userEvent.click(within(card).getByRole("button", { name: "Stop" }));
    expect(await within(card).findByText("Cannot stop while starting")).toBeInTheDocument();
    expect(within(card).getByTestId("bot-status")).toHaveTextContent("Running");
  });

  it("requires typing START LIVE before starting in a live environment", async () => {
    const { calls } = mockFetch({
      "GET /bots": [makeBot({ status: "stopped", environment: "live", allowed_actions: ["start", "disable"] })],
      "GET /system/status": { ...status, environment: "live" },
      "POST /bots/bot-1/actions/start": () => json(makeBot({ status: "running", environment: "live" })),
    });
    renderWithProviders(<BotsPage />);
    const card = await screen.findByTestId("bot-card-bot-1");
    await userEvent.click(within(card).getByRole("button", { name: "Start" }));

    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Start live" });
    expect(confirm).toBeDisabled();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await userEvent.type(within(dialog).getByLabelText(/to confirm/), "START LIVE");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);

    await waitFor(() => {
      const post = calls.find((c) => c.path === "/bots/bot-1/actions/start");
      expect(post?.body).toEqual({ confirm_text: "START LIVE" });
    });
  });
});
