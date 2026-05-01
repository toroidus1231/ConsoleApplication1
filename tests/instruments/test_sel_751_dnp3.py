"""Tests for the SEL-751 DNP3 driver.

Uses the FakeLinkChannel from tests/dnp3/test_master.py to script
DNP3 outstation responses.
"""

from __future__ import annotations

import json
import pytest

from src.instruments import _eager_register, get_driver
from src.instruments.dnp3 import application as app
from src.instruments.dnp3 import objects as obj
from src.instruments.megger_mit525 import InstrumentError
from src.instruments.sel_751_dnp3 import SEL751DNP3, SEL751DNP3Config

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "dnp3"))
from test_master import FakeLinkChannel, _make_response  # noqa: E402


def _queue_integrity_ok(ch: FakeLinkChannel, seq: int = 0):
    ch.queue_response_apdu(_make_response(seq=seq, iin=0))


# ---------------------------------------------------------------------------
# Registry / connect lifecycle
# ---------------------------------------------------------------------------


def test_dnp3_driver_registered_with_dnp3_transport():
    _eager_register()
    cls_modbus = get_driver("SEL", "SEL-751", transport="modbus_tcp")
    cls_dnp3   = get_driver("SEL", "SEL-751", transport="dnp3")
    assert cls_dnp3 is SEL751DNP3
    assert cls_dnp3 is not cls_modbus


@pytest.mark.asyncio
async def test_connect_does_initial_integrity_poll():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch)
    _queue_integrity_ok(ch)
    await inst.connect()
    assert inst._connected is True
    # Master sent an FC_READ
    apdus = ch.get_sent_apdus()
    assert len(apdus) == 1
    assert apdus[0][1] == app.FC_READ


@pytest.mark.asyncio
async def test_connect_raises_on_device_restart():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch)
    ch.queue_response_apdu(_make_response(seq=0, iin=app.IIN.DEVICE_RESTART))
    with pytest.raises(InstrumentError, match="restart"):
        await inst.connect()


@pytest.mark.asyncio
async def test_connect_raises_on_device_trouble():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch)
    ch.queue_response_apdu(_make_response(seq=0, iin=app.IIN.DEVICE_TROUBLE))
    with pytest.raises(InstrumentError, match="trouble"):
        await inst.connect()


# ---------------------------------------------------------------------------
# sync_clock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_clock_writes_g50v1():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch)
    _queue_integrity_ok(ch, seq=0)
    await inst.connect()
    ch.queue_response_apdu(_make_response(seq=1, iin=0))
    out = await inst.execute({"operation": "sync_clock",
                                "timestamp_ms": 1_700_000_000_000})
    assert out["timestamp_ms"] == 1_700_000_000_000
    assert out["iin"] == 0
    write_apdu = ch.get_sent_apdus()[1]
    assert write_apdu[1] == app.FC_WRITE
    assert write_apdu[2] == 50 and write_apdu[3] == 1


# ---------------------------------------------------------------------------
# collect_soe
# ---------------------------------------------------------------------------


def _build_g2v2_response_body(events: list[obj.BinaryEvent]) -> bytes:
    indices = [e.index for e in events]
    return (
        bytes([2, 2, obj.Q_8BIT_INDEX_PREFIX])
        + bytes([len(events)])
        + bytes(indices)
        + obj.encode_g2v2(events)
    )


@pytest.mark.asyncio
async def test_collect_soe_returns_indexed_events():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch)
    _queue_integrity_ok(ch, seq=0)
    await inst.connect()

    events = [
        # 51 phase TOC pickup at relay-stamped time
        obj.BinaryEvent(index=2, state=True,  timestamp_ms=1_700_000_001_005),
        # 50 phase IOC pickup 12 ms later
        obj.BinaryEvent(index=3, state=True,  timestamp_ms=1_700_000_001_017),
        # 51 dropout
        obj.BinaryEvent(index=2, state=False, timestamp_ms=1_700_000_001_055),
    ]
    ch.queue_response_apdu(_make_response(
        seq=1, iin=app.IIN.CLASS_1_EVENTS,
        object_data=_build_g2v2_response_body(events),
    ))

    out = await inst.execute({"operation": "collect_soe"})
    assert out["event_count"] == 3
    assert out["iin"] & app.IIN.CLASS_1_EVENTS
    assert out["events"][0]["element"] == "51"
    assert out["events"][0]["state"] is True
    assert out["events"][0]["timestamp_ms"] == 1_700_000_001_005
    assert out["events"][1]["element"] == "50"
    assert out["events"][2]["state"] is False


@pytest.mark.asyncio
async def test_collect_soe_handles_empty_event_buffer():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch)
    _queue_integrity_ok(ch, seq=0)
    await inst.connect()
    ch.queue_response_apdu(_make_response(seq=1, iin=0, object_data=b""))
    out = await inst.execute({"operation": "collect_soe"})
    assert out["event_count"] == 0
    assert out["events"] == []


@pytest.mark.asyncio
async def test_collect_soe_preserves_index_for_unknown_points():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch)
    _queue_integrity_ok(ch, seq=0)
    await inst.connect()
    events = [
        obj.BinaryEvent(index=99, state=True, timestamp_ms=10),
    ]
    ch.queue_response_apdu(_make_response(
        seq=1, iin=0, object_data=_build_g2v2_response_body(events),
    ))
    out = await inst.execute({"operation": "collect_soe"})
    assert out["events"][0]["index"] == 99
    assert out["events"][0]["element"] is None


# ---------------------------------------------------------------------------
# retrieve_comtrade
# ---------------------------------------------------------------------------


def _queue_file_open(ch: FakeLinkChannel, seq: int, handle: int,
                      file_size: int, max_block: int = 1024,
                      status: int = obj.FILE_ST_SUCCESS):
    open_status = (
        handle.to_bytes(4, "little")
        + file_size.to_bytes(4, "little")
        + max_block.to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + bytes([status])
    )
    ch.queue_response_apdu(_make_response(
        seq=seq, iin=0,
        object_data=(bytes([70, 4, obj.Q_16BIT_LIMITED_QTY])
                     + (1).to_bytes(2, "little") + open_status),
    ))


def _queue_file_block(ch: FakeLinkChannel, seq: int, handle: int,
                       block_no: int, data: bytes):
    seg = obj.FileTransport(handle=handle, block_number=block_no, file_data=data)
    ch.queue_response_apdu(_make_response(
        seq=seq, iin=0,
        object_data=(bytes([70, 5, obj.Q_16BIT_LIMITED_QTY])
                     + (1).to_bytes(2, "little") + obj.encode_g70v5(seg)),
    ))


def _queue_file_close(ch: FakeLinkChannel, seq: int):
    ch.queue_response_apdu(_make_response(seq=seq, iin=0))


@pytest.mark.asyncio
async def test_retrieve_comtrade_concatenates_file():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch, SEL751DNP3Config(file_block_size=8))
    _queue_integrity_ok(ch, seq=0)
    await inst.connect()

    handle = 0xCAFE
    payload = b"COMTRADE-2026-05-01-fault-record"
    # file_size is informational; block size 8 → 4 segments
    _queue_file_open(ch, seq=1, handle=handle, file_size=len(payload),
                      max_block=8)
    _queue_file_block(ch, seq=2, handle=handle, block_no=0,
                       data=payload[:8])
    _queue_file_block(ch, seq=3, handle=handle, block_no=1,
                       data=payload[8:16])
    _queue_file_block(ch, seq=4, handle=handle, block_no=2,
                       data=payload[16:24])
    _queue_file_block(ch, seq=5, handle=handle, block_no=0x80000003,
                       data=payload[24:])
    _queue_file_close(ch, seq=6)

    out = await inst.execute({
        "operation": "retrieve_comtrade",
        "filename": "/EVENT/2026-05-01_fault.cfg",
    })
    assert out["filename"] == "/EVENT/2026-05-01_fault.cfg"
    assert out["size_bytes"] == len(payload)
    import hashlib
    assert out["content_sha256"] == hashlib.sha256(payload).hexdigest()
    assert out["raw"] == payload


# ---------------------------------------------------------------------------
# CROB trip / close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trip_issues_g12_direct_operate():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch)
    _queue_integrity_ok(ch, seq=0)
    await inst.connect()

    # Echo back a successful CROB
    crob = obj.CROB(op_type=obj.CROB_OP_PULSE_ON,
                    trip_close=obj.CROB_TC_TRIP,
                    on_time_ms=100, off_time_ms=100)
    body = (
        bytes([12, 1, obj.Q_8BIT_INDEX_PREFIX, 1])
        + bytes([0])
        + obj.encode_g12v1(crob)
    )
    ch.queue_response_apdu(_make_response(
        seq=1, iin=0, object_data=body,
    ))
    out = await inst.execute({"operation": "trip"})
    assert out["action"] == "trip"
    assert out["iin"] == 0


# ---------------------------------------------------------------------------
# verify_calibration via /CONFIG/cal-cert.json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_calibration_via_file():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch)
    _queue_integrity_ok(ch, seq=0)
    await inst.connect()

    cal_payload = json.dumps({
        "instrument_serial": "SEL751-A8472301",
        "cert_id": "CAL-2026-SEL-Q1",
        "issued_at": "2026-01-15",
        "expires_at": "2026-12-31",
    }).encode()
    handle = 0xBEEF
    _queue_file_open(ch, seq=1, handle=handle, file_size=len(cal_payload))
    _queue_file_block(ch, seq=2, handle=handle, block_no=0x80000000,
                       data=cal_payload)
    _queue_file_close(ch, seq=3)

    cert = await inst.verify_calibration()
    assert cert.instrument_serial == "SEL751-A8472301"
    assert cert.cert_id == "CAL-2026-SEL-Q1"
    assert cert.expires_at == "2026-12-31"
    assert cert.cert_hash.startswith("sha256:sel-751-")


# ---------------------------------------------------------------------------
# Unknown operation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_unknown_operation_raises():
    ch = FakeLinkChannel()
    inst = SEL751DNP3(ch)
    _queue_integrity_ok(ch, seq=0)
    await inst.connect()
    with pytest.raises(InstrumentError, match="unsupported operation"):
        await inst.execute({"operation": "make_coffee"})
