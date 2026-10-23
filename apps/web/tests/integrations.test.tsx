import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IntegrationsPage, buildIntegrationUpdate } from "@/features/integrations/integrations-page";
import { brokerIntegration } from "./fixtures";
import { json, mockFetch, renderWithProviders } from "./utils";

describe("Integrations", () => {
  it("never displays stored secret values", async () => {
    mockFetch({ "GET /integrations": [brokerIntegration] });
    const { container } = renderWithProviders(<IntegrationsPage />);
    const card = await screen.findByTestId("integration-mt5");

    expect(container.innerHTML).not.toContain("hunter2-SECRET");
    const password = within(card).getByLabelText(/^Password/) as HTMLInputElement;
    expect(password.type).toBe("password");
    expect(password.value).toBe("");
    expect(within(card).getByText("Stored")).toBeInTheDocument();
    expect(within(card).getByText("Not set")).toBeInTheDocument();
    // Non-secret settings are shown.
    expect((within(card).getByLabelText(/^Login/) as HTMLInputElement).value).toBe("123456");
  });

  it("omits untouched secrets from the save payload and sends newly typed ones", async () => {
    const { calls } = mockFetch({
      "GET /integrations": [brokerIntegration],
      "PUT /integrations/mt5": () => json({ ...brokerIntegration, fields: brokerIntegration.fields.map((f) => (f.secret ? { ...f, value: null } : f)) }),
    });
    renderWithProviders(<IntegrationsPage />);
    const card = await screen.findByTestId("integration-mt5");
    await userEvent.type(within(card).getByLabelText(/^API token/), "new-token");
    await userEvent.click(within(card).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toEqual({ enabled: true, settings: { login: "123456" }, secrets: { api_token: "new-token" } });
    // After save the secret input is cleared again.
    await waitFor(() => expect((within(card).getByLabelText(/^API token/) as HTMLInputElement).value).toBe(""));
  });

  it("buildIntegrationUpdate never copies stored secret values", () => {
    const body = buildIntegrationUpdate(brokerIntegration, true, {}, {});
    expect(JSON.stringify(body)).not.toContain("hunter2");
    expect(body.secrets).toEqual({});
  });
});
