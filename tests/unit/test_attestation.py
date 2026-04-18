"""Tests for Module 5 — Attestation Engine.

Uses a fake MinIO client that stores objects in a dict. Covers: chain
construction, hash canonicalization, chain verification, tamper detection,
WORM-failure WAL spill + replay, genesis record, and delete immutability
(the fake refuses delete because real MinIO with Object Lock would).
"""

import hashlib
import io
import json

import pytest

from src.attestation import AttestationEngine, _canonical_json, _object_key
from src.types import AttestationRecord


class FakeS3Error(Exception):
    pass


class FakeObject:
    def __init__(self, name: str):
        self.object_name = name


class FakeStream:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def close(self):
        pass


class FakeMinio:
    """In-memory MinIO stand-in.

    Object Lock semantics: once written, objects cannot be deleted or
    overwritten (put_object raises on conflict in 'compliance' mode). We don't
    emulate overwrite blocking because the attestation key is always unique
    by sequence; we do block delete to enforce that tests can't cheat.
    """

    def __init__(self, fail_count: int = 0):
        self.store: dict[str, bytes] = {}
        self.fail_count = fail_count
        self.put_calls = 0

    def put_object(self, bucket_name, object_name, data, length, content_type="application/octet-stream"):
        self.put_calls += 1
        if self.put_calls <= self.fail_count:
            raise FakeS3Error("simulated MinIO outage")
        body = data.read() if hasattr(data, "read") else bytes(data)
        assert len(body) == length
        # Object Lock compliance: refuse overwrites.
        if object_name in self.store:
            raise FakeS3Error(f"Object Lock: {object_name} already exists")
        self.store[object_name] = body

    def get_object(self, bucket_name, object_name):
        return FakeStream(self.store[object_name])

    def list_objects(self, bucket_name, prefix="", recursive=True):
        return [FakeObject(k) for k in self.store if k.startswith(prefix)]

    def remove_object(self, bucket_name, object_name):
        raise FakeS3Error("Object Lock: cannot delete attested record")


def _make_engine(tmp_path, fake_minio=None):
    minio = fake_minio or FakeMinio()
    return AttestationEngine(
        facility_name="DC1",
        minio_bucket="attestation",
        minio_client=minio,
        wal_dir=tmp_path,
        worker_id="test-worker",
    )


def _record(value=480.0, measurement="voltage", ts=1_712_847_600_000_000_000):
    return AttestationRecord(
        timestamp_ns=ts,
        device_id="dev-1",
        measurement=measurement,
        value=value,
        raw_bytes="01e0",
        protocol="modbus_tcp",
        source_ip="10.0.0.5",
        worker_id="",
    )


# --- Canonical JSON / key format ---------------------------------------------


def test_canonical_json_is_sorted_and_dense():
    rec = {
        "device_id": "d",
        "measurement": "m",
        "raw_bytes": "01",
        "timestamp_ns": 1,
        "value": 2.0,
    }
    out = _canonical_json(rec, "prev")
    assert out == '{"device_id":"d","measurement":"m","previous_hash":"prev","raw_bytes":"01","timestamp_ns":1,"value":2.0}'


def test_object_key_is_sequence_padded_and_dated():
    key = _object_key("DC1", 1_712_847_600_000_000_000, 42)
    assert key.startswith("DC1/")
    assert key.endswith("/0000000042.json")
    # date portion is a valid YYYY-MM-DD
    date = key.split("/")[1]
    assert len(date) == 10 and date.count("-") == 2


# --- Chain construction ------------------------------------------------------


async def test_single_record_advances_chain_from_genesis(tmp_path):
    minio = FakeMinio()
    eng = _make_engine(tmp_path, minio)

    assert eng.sequence == 0
    assert eng.previous_hash == "0" * 64

    await eng.submit(_record())
    await eng.drain()

    assert eng.sequence == 1
    assert eng.previous_hash != "0" * 64
    assert len(minio.store) == 1


async def test_chain_links_across_multiple_records(tmp_path):
    minio = FakeMinio()
    eng = _make_engine(tmp_path, minio)

    for i in range(3):
        await eng.submit(_record(value=float(i), ts=1_712_847_600_000_000_000 + i))
    await eng.drain()

    assert eng.sequence == 3
    # Read back and verify each record's previous_hash matches the prior's hash
    recs = []
    for key in sorted(minio.store):
        recs.append(json.loads(minio.store[key]))
    assert recs[0]["previous_hash"] == "0" * 64
    assert recs[1]["previous_hash"] == recs[0]["hash"]
    assert recs[2]["previous_hash"] == recs[1]["hash"]
    assert [r["sequence"] for r in recs] == [1, 2, 3]


async def test_submit_fills_facility_worker_id_and_nonce(tmp_path):
    minio = FakeMinio()
    eng = _make_engine(tmp_path, minio)

    for _ in range(2):
        await eng.submit(_record())
    await eng.drain()

    recs = [json.loads(minio.store[k]) for k in sorted(minio.store)]
    assert recs[0]["facility"] == "DC1"
    assert recs[0]["worker_id"] == "test-worker"
    assert recs[0]["nonce"] == "test-worker-1"
    assert recs[1]["nonce"] == "test-worker-2"


# --- Hash correctness --------------------------------------------------------


async def test_hash_matches_independent_sha256(tmp_path):
    minio = FakeMinio()
    eng = _make_engine(tmp_path, minio)
    rec = _record()
    await eng.submit(rec)
    await eng.drain()

    stored = json.loads(next(iter(minio.store.values())))
    expected_canonical = _canonical_json(
        {"device_id": rec.device_id, "measurement": rec.measurement,
         "raw_bytes": rec.raw_bytes, "timestamp_ns": rec.timestamp_ns,
         "value": rec.value},
        "0" * 64,
    )
    expected = hashlib.sha256(expected_canonical.encode()).hexdigest()
    assert stored["hash"] == expected


# --- Chain verification ------------------------------------------------------


async def test_verify_chain_valid_on_happy_path(tmp_path):
    minio = FakeMinio()
    eng = _make_engine(tmp_path, minio)
    for i in range(5):
        await eng.submit(_record(value=float(i), ts=1_712_847_600_000_000_000 + i))
    await eng.drain()

    result = eng.verify_chain()
    assert result == {"chain_valid": True, "breaks": [], "length": 5}


async def test_verify_chain_detects_tamper(tmp_path):
    minio = FakeMinio()
    eng = _make_engine(tmp_path, minio)
    for i in range(3):
        await eng.submit(_record(value=float(i), ts=1_712_847_600_000_000_000 + i))
    await eng.drain()

    # Tamper: flip the value of the middle record in-place.
    keys = sorted(minio.store)
    tampered = json.loads(minio.store[keys[1]])
    tampered["value"] = 9999.9
    minio.store[keys[1]] = json.dumps(tampered, sort_keys=True).encode()

    result = eng.verify_chain()
    assert result["chain_valid"] is False
    assert 2 in result["breaks"]  # sequence 2 is the middle record


# --- WORM failure / WAL ------------------------------------------------------


async def test_worm_failure_spills_to_wal_but_chain_still_advances(tmp_path):
    minio = FakeMinio(fail_count=1)  # fail first put
    eng = _make_engine(tmp_path, minio)

    await eng.submit(_record())
    await eng.drain()

    # Chain advances locally — a second record arriving during the outage must
    # still have a unique sequence and a linked previous_hash.
    assert eng.sequence == 1
    assert eng.previous_hash != "0" * 64
    assert len(minio.store) == 0
    wal_files = list(tmp_path.glob("*.json"))
    assert len(wal_files) == 1


async def test_wal_recovery_replays_spilled_records(tmp_path):
    minio = FakeMinio(fail_count=1)
    eng = _make_engine(tmp_path, minio)

    await eng.submit(_record())
    await eng.drain()
    assert len(list(tmp_path.glob("*.json"))) == 1

    replayed = await eng.replay_wal_once()
    assert replayed == 1
    assert len(minio.store) == 1
    assert list(tmp_path.glob("*.json")) == []


async def test_chain_is_valid_across_outage_and_recovery(tmp_path):
    minio = FakeMinio(fail_count=2)  # records 1 and 2 hit the WAL
    eng = _make_engine(tmp_path, minio)

    for i in range(4):
        await eng.submit(_record(value=float(i), ts=1_712_847_600_000_000_000 + i))
    await eng.drain()

    # After the outage: 2 records went to WAL, 2 landed in MinIO directly.
    assert eng.sequence == 4
    assert len(minio.store) == 2
    assert len(list(tmp_path.glob("*.json"))) == 2

    # Replay drains WAL into MinIO — chain must verify end-to-end.
    replayed = await eng.replay_wal_once()
    assert replayed == 2
    assert len(minio.store) == 4
    assert list(tmp_path.glob("*.json")) == []

    result = eng.verify_chain()
    assert result == {"chain_valid": True, "breaks": [], "length": 4}


# --- Immutability ------------------------------------------------------------


async def test_minio_object_lock_prevents_delete(tmp_path):
    minio = FakeMinio()
    eng = _make_engine(tmp_path, minio)
    await eng.submit(_record())
    await eng.drain()

    key = next(iter(minio.store))
    with pytest.raises(FakeS3Error):
        minio.remove_object("attestation", key)
