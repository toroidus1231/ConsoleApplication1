import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Timeline from "../pages/Timeline";

const TWIN_RUNS = [
  {
    test_id: "t-0001", device_id: "ups-A", test_name: "ups_battery_transfer",
    status: "passed", started_at: "2026-04-29T08:00:00Z",
    completed_at: "2026-04-29T08:00:30Z", duration_seconds: 30, evidence_hashes: ["abc"],
  },
  {
    test_id: "t-0002", device_id: "ats-1", test_name: "ats_transfer",
    status: "failed", started_at: "2026-04-29T08:02:00Z",
    completed_at: "2026-04-29T08:03:00Z", duration_seconds: 60, evidence_hashes: ["def"],
  },
  {
    test_id: "t-0003", device_id: "srv-A1-01", test_name: "gpu_thermal_under_load",
    status: "aborted", started_at: "2026-04-29T08:05:00Z",
    completed_at: "2026-04-29T08:05:15Z", duration_seconds: 15, evidence_hashes: [],
  },
];

describe("Timeline (Module 18 — Gantt view)", () => {
  beforeEach(() => {
    localStorage.setItem("api_key", "demo");
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ tests: TWIN_RUNS }),
    });
  });
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders one row per test", async () => {
    render(<MemoryRouter><Timeline /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText("ups-A")).toBeInTheDocument();
      expect(screen.getByText("ats-1")).toBeInTheDocument();
      expect(screen.getByText("srv-A1-01")).toBeInTheDocument();
    });
  });

  it("shows status as the bar label", async () => {
    render(<MemoryRouter><Timeline /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText(/3 tests/)).toBeInTheDocument();
      // bars contain the status text
      expect(screen.getByText("passed")).toBeInTheDocument();
      expect(screen.getByText("failed")).toBeInTheDocument();
      expect(screen.getByText("aborted")).toBeInTheDocument();
    });
  });

  it("falls back to no-data message when history is empty", async () => {
    fetch.mockReset();
    fetch.mockResolvedValue({ ok: true, json: async () => ({ tests: [] }) });
    render(<MemoryRouter><Timeline /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText(/No tests recorded/i)).toBeInTheDocument();
    });
  });
});
