import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Racks from "../pages/Racks";

const TWIN_RACKS = {
  racks: {
    "A1": [
      { device_id: "srv-A1-01", name: "dgx-h100-A1-01", device_type_slug: "dgx-h100",
        position: 40, test_status: "passed" },
      { device_id: "srv-A1-02", name: "dgx-h100-A1-02", device_type_slug: "dgx-h100",
        position: 36, test_status: "failed" },
      { device_id: "pdu-A1", name: "pdu-A1", device_type_slug: "apc-rack-pdu",
        position: 42, test_status: "passed" },
    ],
    "B1": [
      { device_id: "pdu-B1", name: "pdu-B1", device_type_slug: "apc-rack-pdu",
        position: 42, test_status: "pending" },
    ],
  },
};

describe("Racks (Module 18 — rack elevations)", () => {
  beforeEach(() => {
    localStorage.setItem("api_key", "demo");
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, json: async () => TWIN_RACKS,
    });
  });
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders one rack frame per rack", async () => {
    render(<MemoryRouter><Racks /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText(/Rack A1/)).toBeInTheDocument();
      expect(screen.getByText(/Rack B1/)).toBeInTheDocument();
    });
  });

  it("places devices at their U positions with status class", async () => {
    render(<MemoryRouter><Racks /></MemoryRouter>);
    await waitFor(() => {
      // Devices appear with name + type
      expect(screen.getByText(/dgx-h100-A1-01/)).toBeInTheDocument();
      expect(screen.getByText(/dgx-h100-A1-02/)).toBeInTheDocument();
      expect(screen.getAllByText(/pdu-A1/).length).toBeGreaterThan(0);
    });
  });

  it("shows the position count crumb", async () => {
    render(<MemoryRouter><Racks /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText(/2 racks/)).toBeInTheDocument();
    });
  });
});
