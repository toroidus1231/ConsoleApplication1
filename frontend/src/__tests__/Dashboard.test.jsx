import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Dashboard from "../pages/Dashboard";

describe("Dashboard", () => {
  beforeEach(() => {
    localStorage.setItem("api_key", "TESTKEY");
    global.fetch = vi.fn().mockImplementation((url) => {
      if (url.includes("/system/health")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ netbox: "ok", influxdb: "ok", minio: "ok" }),
        });
      }
      if (url.includes("/punchlist/summary")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            total: 7,
            by_severity: { critical: 2, major: 4, minor: 1, info: 0 },
            by_category: { identity: 2, firmware: 1, power: 4 },
          }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders summary tiles after fetching /punchlist/summary", async () => {
    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("7")).toBeInTheDocument();      // total
      expect(screen.getByText("2")).toBeInTheDocument();      // critical
      expect(screen.getByText("4")).toBeInTheDocument();      // major
    });
  });

  it("renders system-health entries from /system/health", async () => {
    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText(/netbox: ok/i)).toBeInTheDocument();
      expect(screen.getByText(/influxdb: ok/i)).toBeInTheDocument();
      expect(screen.getByText(/minio: ok/i)).toBeInTheDocument();
    });
  });

  it("shows zero counts when summary missing categories", async () => {
    fetch.mockReset();
    fetch.mockImplementation((url) => {
      if (url.includes("/system/health")) return Promise.resolve({ ok: true, json: async () => ({}) });
      if (url.includes("/punchlist/summary")) {
        return Promise.resolve({ ok: true, json: async () => ({ total: 0, by_severity: {}, by_category: {} }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    await waitFor(() => {
      // four 0 tiles for four severities + the total tile
      expect(screen.getAllByText("0").length).toBeGreaterThanOrEqual(4);
    });
  });
});
