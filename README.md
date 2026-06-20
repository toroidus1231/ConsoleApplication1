

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
| — | Entry point (composition root) | `src/main.py` | built |
| 1 | Config Context Loader | `src/config_loader.py` | built |
| 2 | NetBox Device Reader | `src/netbox_reader.py` | built |
| 3 | Modbus TCP Poller | `src/pollers/modbus.py` | built |
| 4 | InfluxDB Writer | `src/influx_writer.py` | built |
| 5 | Attestation Engine | `src/attestation.py` | built |
| 6 | Modbus Worker | `src/workers/base.py`, `src/workers/modbus_worker.py` | built |
| 7 | BACnet/IP Poller | `src/pollers/bacnet.py` | built |
| 8 | SNMP Poller | `src/pollers/snmp.py` | built |
| 9 | NVML/DCGM Poller | `src/pollers/nvml_poller.py` | built |
| 10 | Active Test Engine | `src/test_engine.py` | built |
| 11 | Discovery Scanner | `src/discovery.py` | built |
| 12 | REST API Connectors | `src/connectors/*.py` | built |
| 13 | Orchestrator | `src/orchestrator.py` | built |
| 14 | Reconciliation Engine | `src/reconciliation.py` | built |
| 15 | Report Generator | `src/reports.py` | built |
| 16 | BIM/IFC Import | `src/bim_import.py` | built |
| 17 | PDF-to-Config Pipeline | `src/pdf_pipeline.py` | built |
| 18 | Dashboard | `frontend/src/pages/Dashboard.jsx` | built |
| 19 | Physical Verification Checklist | `frontend/src/pages/Checklist.jsx` | built |
| 20 | Attestation Viewer | `frontend/src/pages/Attestation.jsx` | built |
| 21 | FastAPI Server | `src/api/server.py` | built |
| 22 | Digital Twin Simulator | `simulator/sim.py` | built |

All backend modules ship with no live infrastructure required for their unit tests:
heavy/native protocol clients (BAC0, pysnmp, pynvml, ifcopenshell, pdfplumber,
anthropic) are lazy-imported behind dependency-injected seams, so `pytest` runs
clean without a GPU, a BACnet stack, or an Anthropic key.

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
