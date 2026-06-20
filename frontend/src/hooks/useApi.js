// useApi — thin fetch() wrapper that injects the X-API-Key header from
// localStorage on every request (Contracts §4 auth, §6.3).
//
// Production serves the frontend same-origin with the API under /api/v1, so a
// relative base works both in the built bundle and behind the Vite dev proxy.
import { useMemo } from "react";

export const API_BASE = "/api/v1";
export const API_KEY_STORAGE = "api_key";

export function getApiKey() {
  return localStorage.getItem(API_KEY_STORAGE) || "";
}

export function setApiKey(key) {
  if (key) localStorage.setItem(API_KEY_STORAGE, key);
  else localStorage.removeItem(API_KEY_STORAGE);
}

// Build a descriptive Error from a failed Response so pages can render the
// message in their error state.
async function toError(resp) {
  let detail = "";
  try {
    const body = await resp.json();
    detail = body?.detail || body?.message || JSON.stringify(body);
  } catch {
    try {
      detail = await resp.text();
    } catch {
      detail = "";
    }
  }
  const err = new Error(
    `${resp.status} ${resp.statusText}${detail ? ` — ${detail}` : ""}`
  );
  err.status = resp.status;
  return err;
}

function jsonHeaders() {
  return {
    "Content-Type": "application/json",
    "X-API-Key": getApiKey(),
  };
}

export function useApi() {
  // Methods are stable for a given key so they are safe in effect deps.
  return useMemo(() => {
    const get = async (path) => {
      const resp = await fetch(`${API_BASE}${path}`, {
        headers: { "X-API-Key": getApiKey() },
      });
      if (!resp.ok) throw await toError(resp);
      return resp.json();
    };

    const post = async (path, body) => {
      const resp = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify(body ?? {}),
      });
      if (!resp.ok) throw await toError(resp);
      return resp.json();
    };

    const patch = async (path, body) => {
      const resp = await fetch(`${API_BASE}${path}`, {
        method: "PATCH",
        headers: jsonHeaders(),
        body: JSON.stringify(body ?? {}),
      });
      if (!resp.ok) throw await toError(resp);
      return resp.json();
    };

    // multipart/form-data POST for file + photo uploads (checklist §4.6,
    // BIM import §4.8, config generate §4.9). Do NOT set Content-Type — the
    // browser must set the multipart boundary itself.
    const postForm = async (path, formData) => {
      const resp = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: { "X-API-Key": getApiKey() },
        body: formData,
      });
      if (!resp.ok) throw await toError(resp);
      return resp.json();
    };

    // Trigger a browser download for binary endpoints (report PDF §4.7,
    // attestation export bundle §4.5).
    const download = async (path, filename) => {
      const resp = await fetch(`${API_BASE}${path}`, {
        headers: { "X-API-Key": getApiKey() },
      });
      if (!resp.ok) throw await toError(resp);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename || path.split("/").pop() || "download";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    };

    return { get, post, patch, postForm, download };
  }, []);
}
