// TestDetail (Contracts §6.4 "/tests/:id").
// GET /tests/status/:id  +  GET /tests/live/:id (SSE per-monitor_interval).
// Live register values stream in while the test runs; preconditions, acceptance
// criteria, and evidence hashes render from the TestResult.
import { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useApi, API_BASE, getApiKey } from "../hooks/useApi.js";
import { useFacility } from "../context/FacilityContext.jsx";
import {
  Card,
  StatusPill,
  DataTable,
  Loading,
  ErrorState,
  EmptyState,
  fmtTime,
  fmtNumber,
} from "../components/ui.jsx";

const ACTIVE_STATUSES = new Set(["queued", "running", "manual_pending", "", undefined, null]);

export default function TestDetail() {
  const { id } = useParams();
  const api = useApi();
  const { sse } = useFacility();
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [liveValues, setLiveValues] = useState({});
  const [aborting, setAborting] = useState(false);
  const liveRef = useRef(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get(`/tests/status/${id}`);
      setResult(data);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const isActive = result ? ACTIVE_STATUSES.has(result.status) : true;

  // Refresh the result when the global stream reports this test finished (§6.2).
  useEffect(() => {
    const offDone = sse.subscribe("test_completed", (d) => {
      if (d.test_id === id) load();
    });
    const offAbort = sse.subscribe("test_aborted", (d) => {
      if (d.test_id === id) load();
    });
    return () => {
      offDone();
      offAbort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, sse]);

  // Dedicated live register stream from GET /tests/live/:id while active.
  useEffect(() => {
    if (!isActive) return undefined;
    const key = getApiKey();
    const url = `${API_BASE}/tests/live/${id}${key ? `?api_key=${encodeURIComponent(key)}` : ""}`;
    const src = new EventSource(url);
    liveRef.current = src;
    const onPoint = (e) => {
      try {
        const d = JSON.parse(e.data);
        if (d.register_name != null) {
          setLiveValues((prev) => ({
            ...prev,
            [d.register_name]: { value: d.value, timestamp: d.timestamp },
          }));
        }
      } catch {
        /* ignore malformed frame */
      }
    };
    // Backend may emit default messages or a named event; handle both.
    src.onmessage = onPoint;
    src.addEventListener("poll_result", onPoint);
    src.addEventListener("test_value", onPoint);
    src.onerror = () => {};
    return () => src.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, isActive]);

  const abort = async () => {
    setAborting(true);
    try {
      await api.post(`/tests/abort/${id}`, {});
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setAborting(false);
    }
  };

  if (loading) return <div className="page"><Loading label="Loading test…" /></div>;
  if (error) return <div className="page"><ErrorState error={error} onRetry={load} /></div>;
  if (!result) return <div className="page"><EmptyState title="Test not found" /></div>;

  const liveNames = Object.keys(liveValues);

  return (
    <div className="page">
      <header className="page-head">
        <div className="breadcrumb">
          <Link to="/tests" className="link">Tests</Link> / {result.test_name || id}
        </div>
        <h1>{result.test_name || "Test"}</h1>
        <div className="meta-row">
          <StatusPill value={result.status} />
          <Link to={`/devices/${result.device_id}`} className="meta-item link">
            device {result.device_id}
          </Link>
          <span className="meta-item">{fmtTime(result.started_at)}</span>
          {result.duration_seconds != null && (
            <span className="meta-item">{Number(result.duration_seconds).toFixed(1)}s</span>
          )}
          {isActive && (
            <button className="btn btn-danger btn-sm" onClick={abort} disabled={aborting}>
              {aborting ? "Aborting…" : "Abort"}
            </button>
          )}
        </div>
      </header>

      {result.abort_triggered && (
        <div className="banner banner-danger">
          Aborted: {result.abort_reason || "unknown reason"}
          {result.restore_success === false && " — RESTORE FAILED"}
        </div>
      )}

      {isActive && (
        <Card title="Live Monitor" actions={<StatusPill value="running" />}>
          {liveNames.length === 0 ? (
            <EmptyState title="Awaiting live data" detail="Register values stream once monitoring begins." />
          ) : (
            <div className="register-grid">
              {liveNames.sort().map((name) => (
                <div className="register-tile pulse" key={name}>
                  <div className="register-name">{name}</div>
                  <div className="register-value">{fmtNumber(liveValues[name].value)}</div>
                  <div className="register-ts">{fmtTime(liveValues[name].timestamp)}</div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      <div className="grid-2">
        <Card title="Preconditions">
          <DataTable
            columns={[
              { key: "register", header: "Register" },
              { key: "expected", header: "Expected" },
              { key: "actual", header: "Actual", render: (r) => fmtNumber(r.actual) },
              { key: "passed", header: "Result", render: (r) => <StatusPill value={r.passed ? "passed" : "failed"} /> },
            ]}
            rows={result.precondition_results || []}
            rowKey={(r, i) => `${r.register}-${i}`}
            empty={<EmptyState title="No preconditions recorded." />}
          />
        </Card>

        <Card title="Acceptance Criteria">
          <DataTable
            columns={[
              { key: "register", header: "Register" },
              { key: "metric", header: "Metric" },
              { key: "expected", header: "Expected" },
              { key: "actual", header: "Actual", render: (r) => fmtNumber(r.actual) },
              { key: "passed", header: "Result", render: (r) => <StatusPill value={r.passed ? "passed" : "failed"} /> },
            ]}
            rows={result.acceptance_results || []}
            rowKey={(r, i) => `${r.register}-${i}`}
            empty={<EmptyState title="No acceptance criteria recorded." />}
          />
        </Card>
      </div>

      {result.manual_confirmations && result.manual_confirmations.length > 0 && (
        <Card title="Manual Confirmations">
          <DataTable
            columns={[
              { key: "prompt", header: "Prompt" },
              { key: "confirmed_by", header: "Confirmed by" },
              { key: "confirmed_at", header: "At", render: (r) => fmtTime(r.confirmed_at) },
            ]}
            rows={result.manual_confirmations}
            rowKey={(r, i) => i}
          />
        </Card>
      )}

      <Card
        title="Evidence (Attestation Hashes)"
        actions={
          result.evidence_hashes?.length > 0 && (
            <Link className="btn btn-ghost" to="/attestation">Verify in chain</Link>
          )
        }
      >
        {result.evidence_hashes && result.evidence_hashes.length > 0 ? (
          <ul className="hash-list">
            {result.evidence_hashes.map((h) => (
              <li key={h}>
                <Link to={`/attestation?hash=${h}`} className="mono link">{h}</Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No evidence hashes" detail="Generated once monitoring captures readings." />
        )}
      </Card>

      {(result.contact_wear_before != null || result.contact_wear_after != null) && (
        <Card title="Breaker Contact Wear">
          <div className="stat-row">
            <div className="stat">
              <div className="stat-value">{fmtNumber(result.contact_wear_before, 1)}%</div>
              <div className="stat-label">before</div>
            </div>
            <div className="stat">
              <div className="stat-value">{fmtNumber(result.contact_wear_after, 1)}%</div>
              <div className="stat-label">after</div>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
