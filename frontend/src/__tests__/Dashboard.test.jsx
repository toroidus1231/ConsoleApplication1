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

  it("renders each system-health row from /system/health", async () => {
    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    await waitFor(() => {
      // Component name + ok badge per row.
      expect(screen.getByText("netbox")).toBeInTheDocument();
      expect(screen.getByText("influxdb")).toBeInTheDocument();
      expect(screen.getByText("minio")).toBeInTheDocument();
      // At least one "ok" badge surfaces.
      expect(screen.getAllByText("ok").length).toBeGreaterThanOrEqual(2);
    });
  });

  it("renders zero state without crashing when summary is empty", async () => {
    fetch.mockReset();
    fetch.mockImplementation((url) => {
      if (url.includes("/system/health")) return Promise.resolve({ ok: true, json: async () => ({}) });
      if (url.includes("/punchlist/summary")) {
        return Promise.resolve({ ok: true, json: async () => ({ total: 0, by_severity: {}, by_category: {} }) });
      }
      if (url.includes("/tests/history")) return Promise.resolve({ ok: true, json: async () => ({ tests: [] }) });
      if (url.includes("/devices")) return Promise.resolve({ ok: true, json: async () => ({ devices: [] }) });
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    await waitFor(() => {
      // Component name + link both contain "Punch list" — assert count, not unique.
      expect(screen.getAllByText(/Punch list/i).length).toBeGreaterThan(0);
    });
  });
});
