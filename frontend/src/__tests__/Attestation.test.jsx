import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Attestation from "../pages/Attestation";


describe("Attestation viewer (Module 20)", () => {
  beforeEach(() => {
    localStorage.setItem("api_key", "TESTKEY");
    global.fetch = vi.fn();
  });
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("shows chain valid + length when /verify says so", async () => {
    fetch.mockImplementation((url) => {
      if (url.includes("/verify")) {
        return Promise.resolve({ ok: true, json: async () => ({ chain_valid: true, length: 1234, breaks: [] }) });
      }
      if (url.includes("/certificate")) {
        return Promise.resolve({ ok: true, json: async () => ({
          facility: "DC1-A", chain_length: 1234, last_hash: "abc123",
        }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<MemoryRouter><Attestation /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText(/Chain valid/)).toBeInTheDocument();
      expect(screen.getByText(/Length:/)).toBeInTheDocument();
      // The certificate table renders facility name.
      expect(screen.getByText("DC1-A")).toBeInTheDocument();
    });
  });

  it("highlights broken chain with sequence numbers", async () => {
    fetch.mockImplementation((url) => {
      if (url.includes("/verify")) {
        return Promise.resolve({ ok: true, json: async () => ({
          chain_valid: false, length: 100, breaks: [42, 73],
        }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({ facility: "x", chain_length: 100, last_hash: "y" }) });
    });

    render(<MemoryRouter><Attestation /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText(/BROKEN/)).toBeInTheDocument();
      expect(screen.getByText(/42, 73/)).toBeInTheDocument();
    });
  });
});
