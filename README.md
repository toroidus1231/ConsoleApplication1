# Commissioning Platform

Automated device discovery, testing, and cryptographic attestation for industrial
and data-center equipment. See `docs/` for the full specifications.

## Status

All 23 modules built, individually unit-tested, separate files in the repo.
**228+ tests passing.** See `tests/unit/` — one test file per module.

## Specs

- `docs/commissioning-spec-v5.pdf` — technical spec (the what)
- `docs/commissioning-contracts.pdf` — interface contracts and architecture
- `docs/implementation-guide.pdf` — code patterns and pinned dependencies
- `docs/README-FIRST.txt` — build rules

## Module map

Each module is built, tested, and committed independently. Tests use dependency
injection so the test environment never needs Modbus / BACnet / SNMP / NVML /
NetBox / InfluxDB / MinIO running.

| # | Module | File | Tests |
| -- | --- | --- | --- |
| 0 | Docker Compose + platform.yml | `docker-compose.yml`, `config/platform.yml` | — |
| — | Shared dataclasses | `src/types.py` | 7 |
| — | Config loader | `src/config.py` | 13 |
| 1 | Config Context Loader | `src/config_loader.py` | 5 |
| 2 | NetBox Device Reader | `src/netbox_reader.py` | 5 |
| 3 | Modbus TCP Poller (read + write) | `src/pollers/modbus.py` | 16 |
| 4 | InfluxDB Writer | `src/influx_writer.py` | 7 |
| 5 | Attestation Engine | `src/attestation.py` | 15 |
| 6 | Modbus Worker | `src/workers/modbus_worker.py` | 15 |
| 7 | BACnet/IP Poller | `src/pollers/bacnet.py` | 6 |
| 8 | SNMP Poller | `src/pollers/snmp.py` | 7 |
| 9 | NVML/DCGM Poller | `src/pollers/nvml_poller.py` | 8 |
| 10 | Active Test Engine | `src/test_engine.py` | 34 |
| 11 | Discovery Scanner | `src/discovery.py` | 15 |
| 12 | REST API Connectors | `src/connectors/{base,genetec,servicenow,maximo,milestone}.py` | 9 |
| 13 | Orchestrator | `src/orchestrator.py` | 12 |
| 14 | Reconciliation Engine | `src/reconciliation.py` | 16 |
| 15 | Report Generator | `src/reports.py` | 9 |
| 16 | BIM/IFC Import | `src/bim_import.py` | 6 |
| 17 | PDF-to-Config Pipeline | `src/pdf_pipeline.py` | 17 |
| 18 | Dashboard | `frontend/src/pages/{Dashboard,Devices,Tests,PunchList}.jsx` | (frontend) |
| 19 | Physical Verification Checklist | `frontend/src/pages/Checklist.jsx` | (frontend) |
| 20 | Attestation Viewer | `frontend/src/pages/Attestation.jsx` | (frontend) |
| 21 | FastAPI Server | `src/api/server.py` | 13 |
| 22 | Digital Twin Simulator | `simulator/sim.py` | 9 |

Frontend tests (Modules 18-20) are a documented gap — pytest can't drive React;
they need vitest/jest set up separately.

## Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## Docker stack

```bash
docker compose up --build
```

Then in another shell, create the MinIO WORM bucket (one-time setup):

```bash
mc alias set local http://localhost:9000 minioadmin minioadmin
mc mb local/attestation --with-lock
mc retention set --default COMPLIANCE 2555d local/attestation
```

## Frontend dev

```bash
cd frontend
npm install
npm run dev      # Vite dev server with API proxy at localhost:5173
npm run build    # production build → frontend/build/
```

## Spec edge-case coverage (§5)

| Edge case | Module | Status |
| --- | --- | --- |
| Float32 word order mismatch (§5.1) | 3 Modbus | ✓ NaN/Inf detection |
| Gateway unit_id misroute (§5.1) | 10 Test Engine | ✓ identity_register check |
| Timeout during active test (§5.1) | 10 | ✓ 3-fail watchdog |
| Stale reads / DGA (§5.1) | config | min_update_interval honored |
| FW-dependent register map (§5.1) | 6 Modbus Worker | ✓ firmware_mismatch event |
| Mechanical failure (§5.2) | 10 | ✓ abort_conditions + watchdog |
| Cascading power dependency (§5.2) | 13 Orchestrator | ✓ DAG conflict detection |
| Coolant spill (§5.2) | 10 | ✓ abort_conditions |
| GPU thermal runaway (§5.2) | 10 + 13 | ✓ abort + dependency ordering |
| Network partition mid-test (§5.2) | 5 Attestation | ✓ WAL fallback |
| WORM write failure (§5.3) | 5 | ✓ WAL + recovery loop |
| Hash chain break (§5.3) | 5 | ✓ verify_chain |
| Duplicate records (§5.3) | 5 | ✓ nonce dedup |
