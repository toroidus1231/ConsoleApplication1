import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Discovery from "../pages/Discovery";

const SCAN_STATUS = {
  scan_id: "scan-1",
  status: "completed",
  subnets: ["10.4.0.0/24"],
  devices_found: 28,
  devices_classified: 27,
  devices_unmatched: 1,
  errors: [],
};

const SCAN_RESULTS = {
  scan_id: "scan-1",
  devices: [
    { ip: "10.4.0.1", protocol: "snmp", identity: "utility-feed",
      device_type_slug: "utility", matched_exact: true },
    { ip: "10.4.0.20", protocol: "modbus_tcp", identity: "ats-main",
      device_type_slug: "asco-7000", matched_exact: true },
    { ip: "10.4.99.99", protocol: "snmp", identity: "Unknown Device",
      device_type_slug: "unknown", matched_exact: false },
  ],
};

describe("Discovery view", () => {
  beforeEach(() => {
    localStorage.setItem("api_key", "demo");
    global.fetch = vi.fn().mockImplementation((url) => {
      if (url.includes("/discovery/status/")) return Promise.resolve({ ok: true, json: async () => SCAN_STATUS });
      if (url.includes("/discovery/results/")) return Promise.resolve({ ok: true, json: async () => SCAN_RESULTS });
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });
  });
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("shows totals from /discovery/status", async () => {
    render(<MemoryRouter><Discovery /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText("28")).toBeInTheDocument();
      expect(screen.getByText("27")).toBeInTheDocument();
      expect(screen.getByText("1")).toBeInTheDocument();
    });
  });

  it("renders a card per discovered device", async () => {
    render(<MemoryRouter><Discovery /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText("utility-feed")).toBeInTheDocument();
      expect(screen.getByText("ats-main")).toBeInTheDocument();
      expect(screen.getByText("Unknown Device")).toBeInTheDocument();
    });
  });

  it("shows the progress percentage based on classified/found", async () => {
    render(<MemoryRouter><Discovery /></MemoryRouter>);
    await waitFor(() => {
      // 27 / 28 ≈ 96.4%
      expect(screen.getByText(/96\.\d%/)).toBeInTheDocument();
    });
  });
});
