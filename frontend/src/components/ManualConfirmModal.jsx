// ManualConfirmModal — global modal driven by manual_confirmation_needed SSE
// events (Contracts §6.2). Shows the prompt from the test engine and lets the
// engineer confirm (POST /tests/confirm/{test_id}) or deny (abort the test via
// POST /tests/abort/{test_id}). Only the first pending confirmation is shown;
// the queue advances as each is handled.
import { useState } from "react";
import { useFacility } from "../context/FacilityContext.jsx";

export default function ManualConfirmModal() {
  const { api, confirmations, dismissConfirmation } = useFacility();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [name, setName] = useState("");

  const current = confirmations[0];
  if (!current) return null;

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/tests/confirm/${current.test_id}`, {
        confirmed_by: name.trim() || "engineer",
      });
      dismissConfirmation(current.test_id);
      setName("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const deny = async () => {
    setBusy(true);
    setError(null);
    try {
      // Denying a dangerous-test confirmation aborts it (executes restore).
      await api.post(`/tests/abort/${current.test_id}`, {});
      dismissConfirmation(current.test_id);
      setName("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true">
      <div className="modal modal-confirm">
        <div className="modal-badge">Manual confirmation required</div>
        <h2 className="modal-title">{current.prompt || "Confirm to proceed"}</h2>
        <p className="modal-meta">
          Test <code>{current.test_id}</code>
        </p>
        <input
          className="input"
          placeholder="Your name (recorded in attestation)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={busy}
        />
        {error && <div className="inline-error">{error}</div>}
        <div className="modal-actions">
          <button className="btn btn-danger" onClick={deny} disabled={busy}>
            Deny &amp; Abort
          </button>
          <button className="btn btn-primary" onClick={confirm} disabled={busy}>
            {busy ? "Working…" : "Confirm"}
          </button>
        </div>
        {confirmations.length > 1 && (
          <div className="modal-queue">
            {confirmations.length - 1} more pending
          </div>
        )}
      </div>
    </div>
  );
}
