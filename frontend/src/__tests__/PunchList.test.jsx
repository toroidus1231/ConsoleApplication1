import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PunchList from "../pages/PunchList";

const SAMPLE = [
  {
    id: "i1", severity: "critical", category: "identity",
    device_id: "d1", device_name: "cm2000-A3",
    expected: "cm2000", actual: "NOT FOUND",
    remediation: "Verify power.", status: "open",
  },
  {
    id: "i2", severity: "major", category: "firmware",
    device_id: "d2", device_name: "ex4300",
    expected: "3.2.1", actual: "2.0.0",
    remediation: "Update firmware.", status: "open",
  },
];


describe("PunchList", () => {
  beforeEach(() => {
    localStorage.setItem("api_key", "TESTKEY");
    global.fetch = vi.fn();
  });
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders rows from /punchlist", async () => {
    fetch.mockResolvedValue({ ok: true, json: async () => ({ items: SAMPLE }) });

    render(<MemoryRouter><PunchList /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("cm2000-A3")).toBeInTheDocument();
      expect(screen.getByText("ex4300")).toBeInTheDocument();
      expect(screen.getByText("Verify power.")).toBeInTheDocument();
    });
  });

  it("applies severity + category filters as query string", async () => {
    fetch.mockResolvedValue({ ok: true, json: async () => ({ items: SAMPLE }) });
    render(<MemoryRouter><PunchList /></MemoryRouter>);

    await waitFor(() => expect(fetch).toHaveBeenCalled());

    const sevSelect = screen.getByDisplayValue("All severities");
    fireEvent.change(sevSelect, { target: { value: "critical" } });

    await waitFor(() => {
      const calls = fetch.mock.calls.map((c) => c[0]);
      expect(calls.some((u) => u.includes("severity=critical"))).toBe(true);
    });
  });

  it("PATCH'es resolve and removes the item from the table", async () => {
    fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ items: SAMPLE }) })  // initial GET
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: 1 }) });          // PATCH

    render(<MemoryRouter><PunchList /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("cm2000-A3")).toBeInTheDocument());

    const resolveButtons = screen.getAllByText("Resolve");
    fireEvent.click(resolveButtons[0]);

    await waitFor(() => {
      expect(screen.queryByText("cm2000-A3")).toBeNull();
    });

    const patchCall = fetch.mock.calls.find((c) => (c[1]?.method || "") === "PATCH");
    expect(patchCall).toBeTruthy();
    expect(patchCall[0]).toContain("/punchlist/i1");
  });
});
