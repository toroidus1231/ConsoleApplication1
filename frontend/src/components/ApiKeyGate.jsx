// ApiKeyGate — blocks the app until an API key is entered, then stores it in
// localStorage (Contracts §6.3 auth). Once a key is present, renders children.
import { useState } from "react";
import { useFacility } from "../context/FacilityContext.jsx";

export default function ApiKeyGate({ children }) {
  const { hasKey, saveApiKey } = useFacility();
  const [value, setValue] = useState("");

  if (hasKey) return children;

  const submit = (e) => {
    e.preventDefault();
    const key = value.trim();
    if (key) saveApiKey(key);
  };

  return (
    <div className="gate">
      <form className="gate-card" onSubmit={submit}>
        <div className="brand-mark gate-mark">CX</div>
        <h1 className="gate-title">Commissioning Platform</h1>
        <p className="gate-sub">
          Enter the facility API key to connect. It is stored locally in this
          browser and sent as the <code>X-API-Key</code> header on every request.
        </p>
        <input
          className="input"
          type="password"
          placeholder="API key"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
          aria-label="API key"
        />
        <button className="btn btn-primary gate-btn" type="submit" disabled={!value.trim()}>
          Connect
        </button>
        <p className="gate-note">
          On-prem tool — single shared key, no user accounts (§8).
        </p>
      </form>
    </div>
  );
}
