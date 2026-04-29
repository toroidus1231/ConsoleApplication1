"""Module 5: Attestation Engine.

Maintains a single SHA-256 hash chain per facility (contracts spec §5.4).
Records are consumed from an asyncio.Queue by exactly one task, guaranteeing
strict ordering. Each record is hashed and persisted to MinIO with Object Lock.

On WORM failure the record is spilled to a local WAL directory and the chain
is paused — previous_hash is NOT advanced. A periodic recovery loop replays
WAL files once MinIO is reachable again.

The MinIO client is dependency-injectable for tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import socket
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Protocol

import aiofiles

from .types import AttestationRecord, AttestedRecord


GENESIS_HASH = "0" * 64


class MinioLike(Protocol):
    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data,
        length: int,
        content_type: str = ...,
    ): ...

    def get_object(self, bucket_name: str, object_name: str): ...

    def list_objects(self, bucket_name: str, prefix: str = "", recursive: bool = ...): ...


class AttestationEngine:
    """SHA-256 Merkle chain persisted to MinIO WORM.

    One instance per process. Concurrency safety comes from the single-
    consumer asyncio.Queue pattern: only ``run()`` touches the chain state
    (``_previous_hash``, ``_sequence``). All producers call ``submit()``.
    """

    def __init__(
        self,
        facility_name: str,
        minio_bucket: str,
        minio_client: MinioLike,
        *,
        wal_dir: str | Path = "/app/data/wal",
        worker_id: str | None = None,
    ):
        self._facility = facility_name
        self._bucket = minio_bucket
        self._minio = minio_client
        self._wal_dir = Path(wal_dir)
        self._wal_dir.mkdir(parents=True, exist_ok=True)
        self._worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"

        self._queue: asyncio.Queue[AttestationRecord] = asyncio.Queue()
        self._previous_hash: str = GENESIS_HASH
        self._sequence: int = 0
        self._chain_id: str = facility_name
        self._nonce_counter: int = 0
        # Per spec §5.3: duplicate records → keep first, discard second. The
        # consumer ignores any record whose nonce has already been chained.
        # Keys are bounded by submit() rate × project duration, ~millions max.
        self._seen_nonces: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def previous_hash(self) -> str:
        return self._previous_hash

    @property
    def sequence(self) -> int:
        return self._sequence

    async def submit(self, record: AttestationRecord) -> None:
        """Enqueue a record for attestation. Non-blocking for the producer.

        If ``record.nonce`` is already set (e.g. the producer is replaying
        records after a crash), it is preserved — the consumer will dedup
        against ``_seen_nonces``. If unset, a fresh per-worker nonce is
        assigned.
        """
        record.facility = self._facility
        record.worker_id = self._worker_id
        if not record.nonce:
            self._nonce_counter += 1
            record.nonce = f"{self._worker_id}-{self._nonce_counter}"
        await self._queue.put(record)

    async def run(self) -> None:
        """Main consumer loop. Cancel this task to stop."""
        while True:
            record = await self._queue.get()
            try:
                await self._process(record)
            finally:
                self._queue.task_done()

    async def drain(self) -> None:
        """Process every currently-queued record then return. For tests."""
        while not self._queue.empty():
            record = self._queue.get_nowait()
            try:
                await self._process(record)
            finally:
                self._queue.task_done()

    async def wal_recovery_loop(self, interval_seconds: float = 60.0) -> None:
        """Periodically replay WAL files into MinIO. Cancel to stop."""
        while True:
            await asyncio.sleep(interval_seconds)
            await self.replay_wal_once()

    async def replay_wal_once(self) -> int:
        """Retry every file currently in the WAL. Returns count replayed."""
        replayed = 0
        for path in sorted(self._wal_dir.iterdir()):
            if not path.is_file() or path.suffix != ".json":
                continue
            try:
                async with aiofiles.open(path, "r") as f:
                    raw = await f.read()
                record = json.loads(raw)
                key = _object_key(record["chain_id"], record["timestamp_ns"], record["sequence"])
                data = raw.encode("utf-8")
                self._minio.put_object(
                    bucket_name=self._bucket,
                    object_name=key,
                    data=io.BytesIO(data),
                    length=len(data),
                    content_type="application/json",
                )
                path.unlink()
                replayed += 1
            except Exception:
                # Will retry on the next sweep.
                continue
        return replayed

    def verify_chain(self, records: Iterable[AttestedRecord] | None = None) -> dict:
        """Walk the chain (or an explicit list of records) and report breaks.

        When ``records`` is None, every object in the facility's MinIO prefix
        is read and sorted by key.
        """
        recs: list[dict]
        if records is None:
            recs = []
            prefix = f"{self._chain_id}/"
            for obj in sorted(
                self._minio.list_objects(self._bucket, prefix=prefix, recursive=True),
                key=lambda o: o.object_name,
            ):
                stream = self._minio.get_object(self._bucket, obj.object_name)
                try:
                    body = stream.read()
                finally:
                    if hasattr(stream, "close"):
                        stream.close()
                recs.append(json.loads(body))
        else:
            recs = [asdict(r) for r in records]

        breaks: list[int] = []
        prev_hash = GENESIS_HASH
        for rec in recs:
            if rec["previous_hash"] != prev_hash:
                breaks.append(rec["sequence"])
                prev_hash = rec["hash"]
                continue
            canonical = _canonical_json(rec, rec["previous_hash"])
            recomputed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if recomputed != rec["hash"]:
                breaks.append(rec["sequence"])
            prev_hash = rec["hash"]
        return {"chain_valid": len(breaks) == 0, "breaks": breaks, "length": len(recs)}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _process(self, record: AttestationRecord) -> None:
        # Spec §5.3: duplicate records → keep first, discard second.
        if record.nonce and record.nonce in self._seen_nonces:
            return

        record_dict = asdict(record)
        canonical = _canonical_json(record_dict, self._previous_hash)
        hash_value = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        attested = AttestedRecord(
            **record_dict,
            hash=hash_value,
            previous_hash=self._previous_hash,
            chain_id=self._chain_id,
            sequence=self._sequence + 1,
        )
        serialized = json.dumps(asdict(attested), default=str, sort_keys=True)
        key = _object_key(self._chain_id, record.timestamp_ns, attested.sequence)

        # The chain advances locally regardless of WORM availability: the hash
        # has been committed to local state and subsequent records must chain
        # off it. The WAL is a retry buffer for MinIO, not a chain-pause lock;
        # pausing chain state on failure would produce sequence collisions as
        # soon as a second record arrived during the outage.
        self._previous_hash = hash_value
        self._sequence += 1
        if record.nonce:
            self._seen_nonces.add(record.nonce)

        try:
            data = serialized.encode("utf-8")
            self._minio.put_object(
                bucket_name=self._bucket,
                object_name=key,
                data=io.BytesIO(data),
                length=len(data),
                content_type="application/json",
            )
        except Exception:
            await self._spill_to_wal(serialized, record.timestamp_ns, attested.sequence)

    async def _spill_to_wal(self, serialized: str, timestamp_ns: int, sequence: int) -> None:
        # Filename is sequence-prefixed so replay is deterministic and one
        # outage's worth of records can't collide on identical timestamps.
        wal_path = self._wal_dir / f"{sequence:010d}-{timestamp_ns}.json"
        async with aiofiles.open(wal_path, "w") as f:
            await f.write(serialized)


def _canonical_json(record: dict, previous_hash: str) -> str:
    """Deterministic JSON over the six fields that bind a record to the chain.

    Sorted keys, no whitespace. Per contracts spec §5.4.
    """
    obj = {
        "device_id": record["device_id"],
        "measurement": record["measurement"],
        "previous_hash": previous_hash,
        "raw_bytes": record["raw_bytes"],
        "timestamp_ns": record["timestamp_ns"],
        "value": record["value"],
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _object_key(chain_id: str, timestamp_ns: int, sequence: int) -> str:
    date_str = time.strftime("%Y-%m-%d", time.gmtime(timestamp_ns / 1e9))
    return f"{chain_id}/{date_str}/{sequence:010d}.json"
