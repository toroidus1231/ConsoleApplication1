

## Specs

- `docs/commissioning-spec-v5.pdf` — technical spec (the what)
- `docs/commissioning-contracts.pdf` — interface contracts and architecture
- `docs/implementation-guide.pdf` — code patterns and pinned dependencies
- `docs/README-FIRST.txt` — build rules

## Module map

Each module is built, tested, and committed independently. The 23 modules are laid
out in `src/` per the implementation guide:

| # | Module | File | Status |
| -- | --- | --- | --- |
| 0 | Docker Compose + platform.yml | `docker-compose.yml`, `config/platform.yml` | built |
| — | Shared dataclasses | `src/types.py` | built |
| — | Config loader | `src/config.py` | built |
| 1 | Config Context Loader | `src/config_loader.py` | pending |
| 2 | NetBox Device Reader | `src/netbox_reader.py` | pending |
| 3 | Modbus TCP Poller | `src/pollers/modbus.py` | pending |
| 4 | InfluxDB Writer | `src/influx_writer.py` | pending |
| 5 | Attestation Engine | `src/attestation.py` | pending |
| 6 | Modbus Worker | `src/workers/modbus_worker.py` | pending |
| 7 | BACnet/IP Poller | `src/pollers/bacnet.py` | pending |
| 8 | SNMP Poller | `src/pollers/snmp.py` | pending |
| 9 | NVML/DCGM Poller | `src/pollers/nvml_poller.py` | pending |
| 10 | Active Test Engine | `src/test_engine.py` | pending |
| 11 | Discovery Scanner | `src/discovery.py` | pending |
| 12 | REST API Connectors | `src/connectors/*.py` | pending |
| 13 | Orchestrator | `src/orchestrator.py` | pending |
| 14 | Reconciliation Engine | `src/reconciliation.py` | pending |
| 15 | Report Generator | `src/reports.py` | pending |
| 16 | BIM/IFC Import | `src/bim_import.py` | pending |
| 17 | PDF-to-Config Pipeline | `src/pdf_pipeline.py` | pending |
| 18 | Dashboard | `frontend/src/pages/Dashboard.jsx` | pending |
| 19 | Physical Verification Checklist | `frontend/src/pages/Checklist.jsx` | pending |
| 20 | Attestation Viewer | `frontend/src/pages/Attestation.jsx` | pending |
| 21 | FastAPI Server | `src/api/server.py` | pending |
| 22 | Digital Twin Simulator | `simulator/sim.py` | pending |

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
