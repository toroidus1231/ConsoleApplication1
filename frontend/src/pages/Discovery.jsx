// Discovery (Contracts §6.4 "/discovery").
// POST /discovery/scan, GET /discovery/status/{scan_id},
// GET /discovery/results/{scan_id}, plus device_discovered SSE (§6.2).
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useFacility } from "../context/FacilityContext.jsx";
import {
  Card,
  Stat,
  StatusPill,
  DataTable,
  ErrorState,
  EmptyState,
} from "../components/ui.jsx";

export default function Discovery() {
  const { api, sse, config } = useFacility();
  const [subnets, setSubnets] = useState("");
  const [scanId, setScanId] = useState(null);
  const [status, setStatus] = useState(null);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef(null);

  // device_discovered events bump a live counter while a scan runs (§6.2).
  const liveDiscovered = sse.counts.device_discovered || 0;

  // Prefill subnets from facility config if available.
  useEffect(() => {
    if (config?.scan_subnets && !subnets) {
      setSubnets(config.scan_subnets.join(", "));
    }
  }, [config, subnets]);

  const startScan = async () => {
    setStarting(true);
    setError(null);
    setResults(null);
    try {
      const list = subnets
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const body = list.length ? { subnets: list } : {};
      const resp = await api.post("/discovery/scan", body);
      setScanId(resp.scan_id);
      setStatus({ status: resp.status || "running" });
    } catch (err) {
      setError(err.message);
    } finally {
      setStarting(false);
    }
  };

  // Poll scan status until it leaves "running", then fetch results.
  useEffect(() => {
    if (!scanId) return undefined;
    let active = true;
    const tick = async () => {
      try {
        const st = await api.get(`/discovery/status/${scanId}`);
        if (!active) return;
        setStatus(st);
        if (st.status && st.status !== "running") {
          clearInterval(pollRef.current);
          const res = await api.get(`/discovery/results/${scanId}`);
          if (active) setResults(res.devices || []);
        }
      } catch (err) {
        if (active) setError(err.message);
        clearInterval(pollRef.current);
      }
    };
    tick();
    pollRef.current = setInterval(tick, 2000);
    return () => {
      active = false;
      clearInterval(pollRef.current);
    };
  }, [scanId, api]);

  const running = status?.status === "running";

  return (
    <div className="page">
      <header className="page-head">
        <h1>Discovery</h1>
        <p className="page-sub">
          Scan facility subnets, classify devices against NetBox device types (§5.5).
        </p>
      </header>

      <Card title="Run a scan">
        <div className="form-row">
          <input
            className="input grow"
            placeholder="CIDR subnets, comma-separated (defaults to platform config)"
            value={subnets}
            onChange={(e) => setSubnets(e.target.value)}
            disabled={running || starting}
          />
          <button
            className="btn btn-primary"
            onClick={startScan}
            disabled={running || starting}
          >
            {starting ? "Starting…" : running ? "Scanning…" : "Start scan"}
          </button>
        </div>
        {error && <ErrorState error={error} />}
      </Card>

      {status && (
        <Card
          title="Scan progress"
          actions={<StatusPill value={status.status} />}
        >
          <div className="stat-row">
            <Stat label="Found" value={status.devices_found ?? liveDiscovered} />
            <Stat label="Classified" value={status.devices_classified ?? 0} />
            <Stat label="Unmatched" value={status.devices_unmatched ?? 0} />
            <Stat label="Live discovered" value={liveDiscovered} />
          </div>
          {Array.isArray(status.errors) && status.errors.length > 0 && (
            <div className="inline-error">
              {status.errors.length} error(s): {status.errors.slice(0, 3).join("; ")}
            </div>
          )}
        </Card>
      )}

      <Card title="Discovered devices">
        {!results && !running && (
          <EmptyState
            title="No scan results yet"
            detail="Start a scan to enumerate devices on the facility network."
          />
        )}
        {running && !results && <div className="muted">Scan in progress…</div>}
        {results && (
          <DataTable
            columns={[
              {
                key: "name",
                header: "Device",
                sortable: true,
                render: (d) => (
                  <Link to={`/devices/${d.device_id}`} className="link">
                    {d.name || d.device_id}
                  </Link>
                ),
              },
              { key: "device_type_slug", header: "Type", sortable: true },
              { key: "protocol", header: "Protocol", render: (d) => <StatusPill value={d.protocol} /> },
              { key: "primary_ip", header: "IP" },
              { key: "site", header: "Site" },
              { key: "rack", header: "Rack" },
            ]}
            rows={results}
            rowKey={(d) => d.device_id}
            empty={<EmptyState title="Scan complete — no devices matched." />}
          />
        )}
      </Card>
    </div>
  );
}
