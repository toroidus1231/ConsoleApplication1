import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PowerGraph from "../pages/PowerGraph";

// Reactflow needs a `ResizeObserver`; jsdom doesn't ship one.
beforeEach(() => {
  if (!global.ResizeObserver) {
    global.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} };
  }
});

const TWIN_GRAPH = {
  nodes: [
    { id: "utility-1", label: "utility-feed", device_type: "utility",   rack: "MV1", position: 0,  test_status: "pending" },
    { id: "ats-1",     label: "ats-main",     device_type: "asco-7000", rack: "MV1", position: 8,  test_status: "passed" },
    { id: "ups-A",     label: "ups-A",        device_type: "apc-symmetra", rack: "UPS-A", position: 10, test_status: "failed" },
    { id: "pdu-A1",    label: "pdu-A1",       device_type: "apc-rack-pdu", rack: "A1", position: 42, test_status: "passed" },
    { id: "srv-A1-01", label: "dgx-h100-A1-01", device_type: "dgx-h100", rack: "A1", position: 40, test_status: "running" },
  ],
  edges: [
    { id: "ats-1->utility-1", source: "utility-1", target: "ats-1" },
    { id: "ups-A->ats-1",     source: "ats-1",     target: "ups-A" },
    { id: "pdu-A1->ups-A",    source: "ups-A",     target: "pdu-A1" },
    { id: "srv-A1-01->pdu-A1", source: "pdu-A1",   target: "srv-A1-01" },
  ],
};

describe("PowerGraph (Module 18 — Power DAG view)", () => {
  beforeEach(() => {
    localStorage.setItem("api_key", "demo");
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => TWIN_GRAPH,
    });
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders the device + feed counts from /power-graph", async () => {
    render(<MemoryRouter><PowerGraph /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText(/5 devices/)).toBeInTheDocument();
      expect(screen.getByText(/4 feeds/)).toBeInTheDocument();
    });
  });

  it("aggregates node statuses into the four tiles", async () => {
    render(<MemoryRouter><PowerGraph /></MemoryRouter>);
    // 2 passed (ats-1, pdu-A1), 1 failed (ups-A), 1 pending (utility-1),
    // 1 running (srv-A1-01).
    await waitFor(() => {
      const labels = Array.from(document.querySelectorAll(".tile .label"));
      const tileFor = (label) =>
        labels.find((el) => el.textContent === label).closest(".tile");
      expect(tileFor("Passed").querySelector(".value").textContent.trim()).toBe("2");
      expect(tileFor("Failed").querySelector(".value").textContent.trim()).toBe("1");
      expect(tileFor("Running").querySelector(".value").textContent.trim()).toBe("1");
    });
  });

  it("renders the tier legend", async () => {
    render(<MemoryRouter><PowerGraph /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText(/Utility \/ Gen/)).toBeInTheDocument();
      expect(screen.getByText(/PDU/)).toBeInTheDocument();
      expect(screen.getByText(/Branch/)).toBeInTheDocument();
    });
  });
});
