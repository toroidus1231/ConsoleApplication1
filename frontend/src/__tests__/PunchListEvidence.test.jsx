import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PunchList from "../pages/PunchList";

const ITEMS = [
  {
    id: "i1", severity: "critical", category: "test_failure",
    device_id: "ups-A", device_name: "ups-A",
    rack: "UPS-A", expected: "passed", actual: "failed",
    remediation: "Investigate ups_battery_transfer.",
    evidence_hash: "deadbeef" + "0".repeat(56),
    test_id: "t-0001", status: "open",
  },
];

const RECORD = {
  hash: "deadbeef" + "0".repeat(56),
  previous_hash: "0".repeat(64),
  sequence: 1,
  device_id: "ups-A",
  measurement: "output_voltage",
  value: 460.1,
  raw_bytes: "01ce",
  protocol: "modbus_tcp",
  source_ip: "10.4.1.10",
  worker_id: "dev-server",
  timestamp_ns: 1_712_847_600_000_000_000,
  test_id: "t-0001",
  facility: "DC1-Ashburn",
};

describe("PunchList evidence modal", () => {
  beforeEach(() => {
    localStorage.setItem("api_key", "demo");
    global.fetch = vi.fn().mockImplementation((url) => {
      if (url.includes("/punchlist") && !url.includes("/attestation")) {
        return Promise.resolve({ ok: true, json: async () => ({ items: ITEMS }) });
      }
      if (url.includes("/attestation/")) {
        return Promise.resolve({ ok: true, json: async () => ({ record: RECORD }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });
  });
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("opens the evidence modal and renders the chained record", async () => {
    render(<MemoryRouter><PunchList /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText("ups-A")).toBeInTheDocument();
    });
    // The "Open" button is the only <button> with that text.
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    await waitFor(() => {
      expect(screen.getByText(/Attestation Evidence/i)).toBeInTheDocument();
      // Raw bytes are unique (hex), measurement appears in kv + JSON pre.
      expect(screen.getByText("01ce")).toBeInTheDocument();
      expect(screen.getAllByText(/output_voltage/).length).toBeGreaterThan(0);
    });
    const attCall = fetch.mock.calls.find((c) => c[0].includes("/attestation/"));
    expect(attCall).toBeTruthy();
    expect(attCall[0]).toContain("deadbeef");
  });

  it("closes the modal on backdrop click", async () => {
    render(<MemoryRouter><PunchList /></MemoryRouter>);
    await waitFor(() => screen.getByRole("button", { name: "Open" }));
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    await waitFor(() => {
      expect(screen.getByText(/Attestation Evidence/i)).toBeInTheDocument();
    });
    fireEvent.click(document.querySelector(".modal-backdrop"));
    await waitFor(() => {
      expect(screen.queryByText(/Attestation Evidence/i)).toBeNull();
    });
  });
});
