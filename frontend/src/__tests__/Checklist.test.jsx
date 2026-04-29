import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Checklist from "../pages/Checklist";


function setOnline(value) {
  Object.defineProperty(navigator, "onLine", {
    value,
    configurable: true,
    writable: true,
  });
}


describe("Checklist (Module 19)", () => {
  beforeEach(() => {
    localStorage.setItem("api_key", "TESTKEY");
    global.fetch = vi.fn();
    setOnline(true);
  });
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("loads items for a device id", async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            id: "bolts", description: "Verify rack bolts torqued",
            category: "structural", requires_photo: false,
            acceptance_criteria: "45 ft-lbs", completed: false,
          },
        ],
      }),
    });

    render(<MemoryRouter><Checklist /></MemoryRouter>);

    fireEvent.change(screen.getByPlaceholderText(/device_id/), {
      target: { value: "dev-1" },
    });
    fireEvent.click(screen.getByText("Load"));

    await waitFor(() => {
      expect(screen.getByText(/Verify rack bolts torqued/)).toBeInTheDocument();
      expect(screen.getByText(/45 ft-lbs/)).toBeInTheDocument();
    });
  });

  it("strips NETBOX: QR-code prefix from device id", async () => {
    fetch.mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });

    render(<MemoryRouter><Checklist /></MemoryRouter>);
    const input = screen.getByPlaceholderText(/device_id/);
    fireEvent.change(input, { target: { value: "NETBOX:dev-42" } });

    expect(input.value).toBe("dev-42");
  });

  it("shows offline banner when navigator.onLine is false", () => {
    setOnline(false);
    render(<MemoryRouter><Checklist /></MemoryRouter>);
    expect(screen.getByText(/offline \(queued\)/)).toBeInTheDocument();
  });
});
