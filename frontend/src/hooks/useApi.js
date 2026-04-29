// Single API key stored in localStorage per spec §6.3.

const API_BASE = "/api/v1";

function headers() {
  const apiKey = localStorage.getItem("api_key") || "";
  return { "Content-Type": "application/json", "X-API-Key": apiKey };
}

async function check(resp) {
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status}: ${text || resp.statusText}`);
  }
  return resp.json();
}

export const api = {
  get: (path) => fetch(`${API_BASE}${path}`, { headers: headers() }).then(check),
  post: (path, body) =>
    fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: headers(),
      body: body ? JSON.stringify(body) : undefined,
    }).then(check),
  patch: (path, body) =>
    fetch(`${API_BASE}${path}`, {
      method: "PATCH",
      headers: headers(),
      body: JSON.stringify(body),
    }).then(check),
  postForm: (path, formData) =>
    fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "X-API-Key": localStorage.getItem("api_key") || "" },
      body: formData,
    }).then(check),
};
