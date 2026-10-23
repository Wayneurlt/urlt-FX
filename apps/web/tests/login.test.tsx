import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoginForm, safeNext } from "@/features/auth/login-form";
import { apiError, json, mockFetch, renderWithProviders } from "./utils";

describe("LoginForm", () => {
  it("submits credentials and redirects on success", async () => {
    const { calls } = mockFetch({
      "POST /auth/login": () => json({ user: { id: 1, username: "ops", role: "admin", timezone: "UTC" }, csrf_token: "tok-123" }),
    });
    const onSuccess = vi.fn();
    renderWithProviders(<LoginForm next="/bots" onSuccess={onSuccess} />, { user: null });

    await userEvent.type(screen.getByLabelText("Username"), "ops");
    await userEvent.type(screen.getByLabelText("Password"), "s3cret!");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith("/bots"));
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toBe("/api/v1/auth/login");
    expect(calls[0].body).toEqual({ username: "ops", password: "s3cret!" });
  });

  it("goes to the dashboard by default and shows progress while it opens", async () => {
    mockFetch({
      "POST /auth/login": () => json({ user: { id: 1, username: "ops", role: "admin", timezone: "UTC" }, csrf_token: "t" }),
    });
    const onSuccess = vi.fn();
    const onPrefetch = vi.fn();
    renderWithProviders(<LoginForm onSuccess={onSuccess} onPrefetch={onPrefetch} />, { user: null });
    expect(onPrefetch).toHaveBeenCalledWith("/dashboard");
    await userEvent.type(screen.getByLabelText("Username"), "ops");
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith("/dashboard"));
    expect(screen.getByRole("status")).toHaveTextContent("Opening your dashboard");
  });

  it("shows the API error message on bad credentials", async () => {
    mockFetch({ "POST /auth/login": () => apiError(401, "invalid_credentials", "Invalid username or password") });
    const onSuccess = vi.fn();
    renderWithProviders(<LoginForm onSuccess={onSuccess} />, { user: null });

    await userEvent.type(screen.getByLabelText("Username"), "ops");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password");
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("shows retry_after_seconds on 429", async () => {
    mockFetch({ "POST /auth/login": () => apiError(429, "rate_limited", "Too many attempts", { retry_after_seconds: 30 }) });
    renderWithProviders(<LoginForm onSuccess={vi.fn()} />, { user: null });
    await userEvent.type(screen.getByLabelText("Username"), "ops");
    await userEvent.type(screen.getByLabelText("Password"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 30s");
  });

  it("validates required fields without calling the API", async () => {
    const { fn } = mockFetch({});
    renderWithProviders(<LoginForm onSuccess={vi.fn()} />, { user: null });
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Username is required")).toBeInTheDocument();
    expect(fn).not.toHaveBeenCalled();
  });

  it("only allows relative same-site redirects", () => {
    expect(safeNext("https://evil.example")).toBe("/dashboard");
    expect(safeNext("//evil.example")).toBe("/dashboard");
    expect(safeNext("/risk")).toBe("/risk");
    expect(safeNext(null)).toBe("/dashboard");
    expect(safeNext("/")).toBe("/dashboard");
  });
});
