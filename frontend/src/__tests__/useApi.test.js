import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../hooks/useApi";

describe("useApi", () => {
  beforeEach(() => {
    localStorage.setItem("api_key", "TESTKEY");
    global.fetch = vi.fn();
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("GET attaches X-API-Key from localStorage", async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ok: 1 }),
    });

    const result = await api.get("/devices");

    expect(result).toEqual({ ok: 1 });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/devices",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-API-Key": "TESTKEY" }),
      }),
    );
  });

  it("POST stringifies body", async () => {
    fetch.mockResolvedValueOnce({ ok: true, json: async () => ({}) });

    await api.post("/tests/run", { device_id: "d1", test_name: "x" });

    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/tests/run",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ device_id: "d1", test_name: "x" }),
      }),
    );
  });

  it("PATCH includes JSON content-type and body", async () => {
    fetch.mockResolvedValueOnce({ ok: true, json: async () => ({}) });

    await api.patch("/punchlist/1", { status: "resolved" });

    const [, init] = fetch.mock.calls[0];
    expect(init.method).toBe("PATCH");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ status: "resolved" }));
  });

  it("non-2xx responses throw", async () => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      statusText: "Unauthorized",
      text: async () => "bad key",
    });

    await expect(api.get("/devices")).rejects.toThrow("401");
  });

  it("postForm uses multipart and skips JSON content-type", async () => {
    fetch.mockResolvedValueOnce({ ok: true, json: async () => ({ id: 1 }) });

    const fd = new FormData();
    fd.append("file", new Blob(["x"]), "f.bin");
    await api.postForm("/bim/import", fd);

    const [, init] = fetch.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(init.body).toBe(fd);
    // No JSON content-type — fetch sets multipart automatically.
    expect(init.headers["Content-Type"]).toBeUndefined();
    expect(init.headers["X-API-Key"]).toBe("TESTKEY");
  });
});
