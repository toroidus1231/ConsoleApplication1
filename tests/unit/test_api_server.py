"""Tests for Module 21 — FastAPI Server.

Drives the app in-process via httpx ASGITransport against a Platform built
entirely from fakes (no NetBox/InfluxDB/MinIO). Covers auth, every §4 section's
representative endpoints, multipart upload jobs, and the background-job stores.
The SSE broadcaster is tested directly in test_api_events.py.
"""

import asyncio

import httpx

from src.api.server import create_app
from src.api.state import Platform
from src.config import PlatformConfig
from src.types import DeviceInfo, PollResult, PunchListItem, TestResult

API_KEY = "secret-key"


def _config():
    return PlatformConfig(
        facility_name="DC1-Ashburn", scan_subnets=["10.0.0.0/24"],
        netbox_url="http://nb", netbox_token="NB-SECRET",
        influxdb_url="http://influx", influxdb_token="INFLUX-SECRET", influxdb_org="org",
        influxdb_bucket="commissioning", minio_endpoint="minio:9000",
        minio_access_key="AK", minio_secret_key="SK-SECRET", minio_bucket="attestation",
        minio_retention_days=2555, max_concurrent_polls=50, max_concurrent_tests=5,
        api_port=8080, api_key=API_KEY, cors_origins=["http://localhost:5173"],
        ntp_server="pool.ntp.org", log_level="INFO",
    )


def _device(device_id="dev-1"):
    return DeviceInfo(
        device_id=device_id, name=f"ups-{device_id}", primary_ip="10.0.0.5",
        device_type_slug="ups", protocol="modbus_tcp", site="DC1", rack="A1", position=1,
        config_context={
            "protocol": "modbus_tcp",
            "active_tests": [{"name": "battery_transfer", "description": "UPS transfer",
                             "preconditions": [{"register": "battery_percent", "operator": "gte", "value": 80}]}],
            "checklist": [{"id": "bolts", "description": "Torque bolts", "category": "structural", "requires_photo": False}],
        },
    )


class FakeOrchestrator:
    def __init__(self):
        self.submitted = []
        self.results = {}

    async def submit(self, request):
        self.submitted.append(request)


class FakeEngine:
    def __init__(self):
        self.confirmed = []

    def confirm_manual(self, test_id, confirmed_by):
        self.confirmed.append((test_id, confirmed_by))
        return True


class FakeInflux:
    async def query_register_history(self, device_id, register, start, stop):
        return [{"timestamp": "2026-04-12T00:00:00Z", "value": 480.0}]


class FakeReader:
    def __init__(self):
        self._rec = {"hash": "abc123", "sequence": 1, "device_id": "dev-1", "value": 480.0}

    def get_by_hash(self, h):
        return self._rec if h == "abc123" else None

    def chain(self, from_sequence, count):
        return [self._rec]

    def verify(self, h):
        return {"valid": True, "chain_position": 1, "chain_length": 1, "breaks": []}

    def verify_chain(self):
        return {"chain_valid": True, "breaks": [], "length": 1,
                "first": self._rec, "last": self._rec}


def _platform(**overrides):
    p = Platform(config=_config())
    p.get_devices = _overrides_get_devices(overrides.pop("devices", [_device()]))
    p.orchestrator = overrides.pop("orchestrator", FakeOrchestrator())
    p.test_engine = overrides.pop("test_engine", FakeEngine())
    p.influx = FakeInflux()
    p.attestation_reader = FakeReader()
    p.workers = {"modbus_tcp": _FakeWorker()}

    async def discovery_fn(subnets):
        from src.discovery import DiscoveryResult
        return DiscoveryResult(scan_id="s", devices_found=2, devices_classified=1,
                               devices_unmatched=1, errors=[],
                               devices=[{"ip": "10.0.0.5", "protocol": "modbus_tcp",
                                         "identity": "CM2000", "device_type_slug": "cm2000",
                                         "classified": True}])
    p.discovery_fn = discovery_fn

    async def reconcile_fn():
        return [PunchListItem(severity="critical", category="identity", device_id="dev-9",
                              expected="ups", actual="NOT FOUND", source="reconciliation")]
    p.reconcile_fn = reconcile_fn

    async def report_fn(sections):
        return b"%PDF-1.4 fake report"
    p.report_fn = report_fn

    async def bim_preview_fn(data):
        return [{"name": "pdu-1", "device_type_slug": "pdu", "rack": "A1"}]
    p.bim_preview_fn = bim_preview_fn

    async def bim_commit_fn(devices):
        return {"devices_committed": len(devices)}
    p.bim_commit_fn = bim_commit_fn

    async def pdf_fn(data, slug):
        return {"config_context": {"protocol": "modbus_tcp", "registers": []},
                "validation_errors": [], "warnings": ["missing connection"]}
    p.pdf_fn = pdf_fn

    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def _overrides_get_devices(devices):
    async def get_devices():
        return devices
    return get_devices


class _FakeWorker:
    devices = [1, 2, 3]
    unreachable = {2}


def _client(platform):
    app = create_app(platform)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test",
                             headers={"X-API-Key": API_KEY})


# --- Auth --------------------------------------------------------------------

async def test_missing_api_key_rejected():
    app = create_app(_platform())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/system/config")
        assert r.status_code == 401


async def test_wrong_api_key_rejected():
    async with _client(_platform()) as c:
        r = await c.get("/api/v1/system/config", headers={"X-API-Key": "nope"})
        assert r.status_code == 401


# --- System ------------------------------------------------------------------

async def test_system_config_redacts_secrets():
    async with _client(_platform()) as c:
        r = await c.get("/api/v1/system/config")
        assert r.status_code == 200
        body = r.json()
        blob = str(body)
        assert "NB-SECRET" not in blob and "SK-SECRET" not in blob and "INFLUX-SECRET" not in blob
        assert body["facility_name"] == "DC1-Ashburn"


async def test_system_health_reports_workers():
    async with _client(_platform()) as c:
        r = await c.get("/api/v1/system/health")
        assert r.json()["workers"]["modbus_tcp"] == {"devices": 3, "errors": 1}


# --- Devices -----------------------------------------------------------------

async def test_devices_list_and_filter():
    async with _client(_platform(devices=[_device("a"), _device("b")])) as c:
        r = await c.get("/api/v1/devices")
        assert len(r.json()["devices"]) == 2
        r = await c.get("/api/v1/devices", params={"protocol": "snmp"})
        assert r.json()["devices"] == []


async def test_device_detail_and_history():
    p = _platform()
    p.last_poll["dev-1"] = PollResult("dev-1", 1, {"v": 1.0}, {}, "modbus_tcp", "10.0.0.5", True)
    p.test_results["t1"] = TestResult(test_id="t1", device_id="dev-1", status="passed")
    async with _client(p) as c:
        r = await c.get("/api/v1/devices/dev-1")
        body = r.json()
        assert body["device"]["device_id"] == "dev-1"
        assert body["last_poll"]["measurements"] == {"v": 1.0}
        assert body["tests_available"] == ["battery_transfer"]
        assert len(body["tests_run"]) == 1
        h = await c.get("/api/v1/devices/dev-1/history", params={"register": "v", "from": "-1h", "to": "now()"})
        assert h.json()["points"][0]["value"] == 480.0
        assert (await c.get("/api/v1/devices/ghost")).status_code == 404


# --- Tests -------------------------------------------------------------------

async def test_run_and_confirm_test():
    orch = FakeOrchestrator()
    engine = FakeEngine()
    async with _client(_platform(orchestrator=orch, test_engine=engine)) as c:
        r = await c.post("/api/v1/tests/run", json={"device_id": "dev-1", "test_name": "battery_transfer"})
        assert r.json()["status"] == "queued"
        assert len(orch.submitted) == 1
        tid = r.json()["test_id"]
        r2 = await c.post(f"/api/v1/tests/confirm/{tid}", json={"confirmed_by": "jane"})
        assert r2.json()["status"] == "confirmed"
        assert engine.confirmed == [(tid, "jane")]


async def test_tests_available_and_status_and_history():
    p = _platform()
    p.test_results["t1"] = TestResult(test_id="t1", device_id="dev-1", test_name="x", status="failed")
    async with _client(p) as c:
        assert (await c.get("/api/v1/tests/available/dev-1")).json()["tests"][0]["name"] == "battery_transfer"
        assert (await c.get("/api/v1/tests/status/t1")).json()["status"] == "failed"
        hist = await c.get("/api/v1/tests/history", params={"status": "failed"})
        assert len(hist.json()["tests"]) == 1


# --- Punch list --------------------------------------------------------------

async def test_punchlist_crud_and_summary():
    p = _platform()
    p.punchlist = [
        PunchListItem(severity="critical", category="power", status="open", device_id="d1"),
        PunchListItem(severity="minor", category="firmware", status="open", device_id="d2"),
    ]
    pid = p.punchlist[0].id
    async with _client(p) as c:
        assert len(((await c.get("/api/v1/punchlist")).json())["items"]) == 2
        filt = await c.get("/api/v1/punchlist", params={"severity": "critical"})
        assert len(filt.json()["items"]) == 1
        patched = await c.patch(f"/api/v1/punchlist/{pid}", json={"status": "resolved", "resolved_by": "bob"})
        assert patched.json()["item"]["status"] == "resolved"
        assert patched.json()["item"]["resolved_by"] == "bob"
        summary = (await c.get("/api/v1/punchlist/summary")).json()
        assert summary["total"] == 2
        assert summary["by_severity"]["critical"] == 1
        assert summary["by_status"]["resolved"] == 1


# --- Attestation -------------------------------------------------------------

async def test_attestation_endpoints():
    async with _client(_platform()) as c:
        assert (await c.get("/api/v1/attestation/abc123")).json()["record"]["hash"] == "abc123"
        chain = (await c.get("/api/v1/attestation/chain")).json()
        assert chain["chain_valid"] is True and len(chain["records"]) == 1
        assert (await c.get("/api/v1/attestation/verify/abc123")).json()["valid"] is True
        cert = (await c.get("/api/v1/attestation/certificate")).json()
        assert cert["facility"] == "DC1-Ashburn" and cert["hash_algorithm"] == "SHA-256"
        assert (await c.get("/api/v1/attestation/nope")).status_code == 404


# --- Checklist ---------------------------------------------------------------

async def test_checklist_get_submit_summary():
    p = _platform()
    async with _client(p) as c:
        items = (await c.get("/api/v1/checklist/dev-1")).json()["items"]
        assert items[0]["id"] == "bolts" and items[0]["completed"] is False
        sub = await c.post("/api/v1/checklist/dev-1/bolts", data={"completed": "true", "notes": "ok"})
        assert "attestation_hash" in sub.json()
        items2 = (await c.get("/api/v1/checklist/dev-1")).json()["items"]
        assert items2[0]["completed"] is True
        summary = (await c.get("/api/v1/checklist/summary")).json()
        assert summary["completed"] == 1


# --- Async jobs (discovery / reconciliation / reports / bim / config) --------

async def _wait_status(client, url, key="status", target="complete", tries=50):
    for _ in range(tries):
        r = await client.get(url)
        if r.json().get(key) in (target, "parsed", "committed"):
            return r.json()
        await asyncio.sleep(0)
    return r.json()


async def test_discovery_scan_job():
    async with _client(_platform()) as c:
        scan_id = (await c.post("/api/v1/discovery/scan", json={})).json()["scan_id"]
        status = await _wait_status(c, f"/api/v1/discovery/status/{scan_id}")
        assert status["status"] == "complete"
        assert status["devices_found"] == 2
        results = (await c.get(f"/api/v1/discovery/results/{scan_id}")).json()
        assert results["devices"][0]["device_type_slug"] == "cm2000"


async def test_reconciliation_job_populates_punchlist():
    p = _platform()
    async with _client(p) as c:
        job_id = (await c.post("/api/v1/reconciliation/run")).json()["job_id"]
        status = await _wait_status(c, f"/api/v1/reconciliation/status/{job_id}")
        assert status["status"] == "complete"
        assert status["punch_items_generated"] == 1
        assert len(p.punchlist) == 1


async def test_report_generate_and_download():
    async with _client(_platform()) as c:
        report_id = (await c.post("/api/v1/reports/generate", json={})).json()["report_id"]
        await _wait_status(c, f"/api/v1/reports/{report_id}".replace("/reports/", "/reports/"), tries=1)
        for _ in range(50):
            r = await c.get(f"/api/v1/reports/{report_id}")
            if r.status_code == 200:
                break
            await asyncio.sleep(0)
        assert r.status_code == 200
        assert r.content.startswith(b"%PDF")
        assert r.headers["content-type"] == "application/pdf"


async def test_bim_import_preview_commit():
    async with _client(_platform()) as c:
        job_id = (await c.post("/api/v1/bim/import",
                               files={"file": ("design.ifc", b"IFCDATA")})).json()["job_id"]
        status = await _wait_status(c, f"/api/v1/bim/status/{job_id}")
        assert status["devices_parsed"] == 1
        preview = (await c.get(f"/api/v1/bim/preview/{job_id}")).json()
        assert preview["devices"][0]["name"] == "pdu-1"
        commit = (await c.post(f"/api/v1/bim/commit/{job_id}")).json()
        assert commit["devices_committed"] == 1


async def test_config_generate_job():
    async with _client(_platform()) as c:
        job_id = (await c.post("/api/v1/config/generate",
                               files={"pdf": ("cm2000.pdf", b"PDFDATA")},
                               data={"device_type_slug": "cm2000"})).json()["job_id"]
        await _wait_status(c, f"/api/v1/config/result/{job_id}", tries=1)
        for _ in range(50):
            res = (await c.get(f"/api/v1/config/result/{job_id}")).json()
            if res.get("config_context"):
                break
            await asyncio.sleep(0)
        assert res["config_context"]["protocol"] == "modbus_tcp"
        assert res["warnings"] == ["missing connection"]
