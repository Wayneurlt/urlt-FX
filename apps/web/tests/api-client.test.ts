import { describe, expect, it } from "vitest";
import { ApiError, api, buildQuery, setCsrfToken } from "@/lib/api/client";
import { apiError, mockFetch } from "./utils";

describe("API client", () => {
  it("sends X-CSRF-Token from the fx_csrf cookie on mutating requests only", async () => {
    document.cookie = "fx_csrf=cookie-token; path=/";
    const { calls } = mockFetch({ "GET /x": { ok: true }, "POST /x": { ok: true } });
    await api.get("/x");
    await api.post("/x", { a: 1 });
    expect(calls[0].headers["X-CSRF-Token"]).toBeUndefined();
    expect(calls[1].headers["X-CSRF-Token"]).toBe("cookie-token");
    expect(calls[1].url).toBe("/api/v1/x");
  });

  it("falls back to the in-memory token from login/me", async () => {
    setCsrfToken("mem-token");
    const { calls } = mockFetch({ "PUT /y": { ok: true } });
    await api.put("/y", {});
    expect(calls[0].headers["X-CSRF-Token"]).toBe("mem-token");
    setCsrfToken(null);
  });

  it("parses the error envelope and required_confirm_text", async () => {
    mockFetch({ "POST /z": () => apiError(409, "confirm", "Needs confirmation", { required_confirm_text: "LIQUIDATE" }) });
    const err = await api.post("/z").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).requiredConfirmText).toBe("LIQUIDATE");
  });

  it("buildQuery skips empty values", () => {
    expect(buildQuery({ a: "1", b: "", c: undefined, d: null, e: false, f: 0 })).toBe("?a=1&e=false&f=0");
    expect(buildQuery({})).toBe("");
  });
});
