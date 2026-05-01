"""Tests for the DNP3 master class.

Uses a fake LinkChannel that:
  - Buffers bytes the master writes and decodes them as a link frame
    (so test code can assert what the master sent).
  - Has a `queue_response_apdu(apdu)` method that wraps the APDU in
    transport segmentation + a link frame and queues it to be read
    next.
"""

from __future__ import annotations

import pytest

from src.instruments.dnp3 import datalink as dl
from src.instruments.dnp3 import application as app
from src.instruments.dnp3 import objects as obj
from src.instruments.dnp3 import transport as tx
from src.instruments.dnp3.master import (
    DNP3Master, DNP3MasterConfig, DNP3Error,
)


class FakeLinkChannel:
    def __init__(self, master_addr: int = 1, outstation_addr: int = 4):
        self._master_addr = master_addr
        self._outstation_addr = outstation_addr
        self._sent_frames: list[dl.LinkFrame] = []
        self._read_buf = bytearray()
        self._segmenter = tx.TransportSegmenter()
        self._closed = False

    async def write_bytes(self, data: bytes) -> None:
        # Decode the link frame the master sent
        frame, _ = dl.decode(data)
        self._sent_frames.append(frame)

    async def read_bytes(self, n: int, timeout_s: float) -> bytes:
        if len(self._read_buf) < n:
            raise AssertionError(
                f"read_bytes({n}) starved (buf has {len(self._read_buf)})"
            )
        out = bytes(self._read_buf[:n])
        del self._read_buf[:n]
        return out

    async def close(self) -> None:
        self._closed = True

    def queue_response_apdu(self, apdu: bytes) -> None:
        for segment in self._segmenter.segment(apdu):
            frame = dl.LinkFrame(
                function_code=dl.PRI_UNCONFIRMED_USER_DATA,
                dest_address=self._master_addr,
                source_address=self._outstation_addr,
                user_data=segment,
                direction=0, primary=1,
            )
            self._read_buf += dl.encode(frame)

    def get_sent_apdus(self) -> list[bytes]:
        # Each sent frame's user_data is a single transport segment;
        # for the simple cases here, those segments are also full APDUs.
        return [bytes(f.user_data[1:]) for f in self._sent_frames]


# ---------------------------------------------------------------------------
# Helpers to build outstation responses
# ---------------------------------------------------------------------------


def _make_response(seq: int, iin: int, object_data: bytes = b"") -> bytes:
    return app.Response(
        function_code=app.FC_RESPONSE,
        application_control=app.ApplicationControl(
            fir=True, fin=True, sequence=seq,
        ),
        iin=iin, object_data=object_data,
    ).encode()


# ---------------------------------------------------------------------------
# Integrity poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integrity_poll_round_trip():
    ch = FakeLinkChannel()
    master = DNP3Master(ch)
    ch.queue_response_apdu(_make_response(seq=0, iin=app.IIN.NEED_TIME))
    out = await master.integrity_poll()
    assert out["iin"] == app.IIN.NEED_TIME
    assert out["iin_flags"]["need_time"] is True
    # Master sent exactly one APDU with FC_READ
    apdus = ch.get_sent_apdus()
    assert len(apdus) == 1
    assert apdus[0][1] == app.FC_READ


@pytest.mark.asyncio
async def test_integrity_poll_includes_class_0_1_2_3_object_headers():
    ch = FakeLinkChannel()
    master = DNP3Master(ch)
    ch.queue_response_apdu(_make_response(seq=0, iin=0))
    await master.integrity_poll()
    apdu = ch.get_sent_apdus()[0]
    # APDU body after the 2-byte header (control + fc) is 4 object headers,
    # each 3 bytes (group, var, qualifier 0x06)
    body = apdu[2:]
    assert len(body) == 4 * 3
    # Group 60, vars 2, 3, 4, 1 in that order
    assert (body[0], body[1], body[2]) == (60, 2, 0x06)
    assert (body[3], body[4], body[5]) == (60, 3, 0x06)
    assert (body[6], body[7], body[8]) == (60, 4, 0x06)
    assert (body[9], body[10], body[11]) == (60, 1, 0x06)


# ---------------------------------------------------------------------------
# Time write (clock sync)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_time_sends_g50v1():
    ch = FakeLinkChannel()
    master = DNP3Master(ch)
    ch.queue_response_apdu(_make_response(seq=0, iin=0))
    iin = await master.write_time(1_700_000_000_000)
    assert iin == 0
    apdu = ch.get_sent_apdus()[0]
    # function code WRITE
    assert apdu[1] == app.FC_WRITE
    # group=50 var=1 follows the application-layer 2-byte header
    assert apdu[2] == 50 and apdu[3] == 1


# ---------------------------------------------------------------------------
# CROB direct-operate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operate_crob_trip_command():
    ch = FakeLinkChannel()
    master = DNP3Master(ch)
    # Echo back the same CROB body with status=SUCCESS
    crob = obj.CROB(
        op_type=obj.CROB_OP_PULSE_ON,
        trip_close=obj.CROB_TC_TRIP,
        on_time_ms=100, off_time_ms=100,
    )
    body = bytearray()
    body += bytes([12, 1, obj.Q_8BIT_INDEX_PREFIX, 1])
    body += bytes([7])  # point index
    body += obj.encode_g12v1(crob)
    ch.queue_response_apdu(_make_response(seq=0, iin=0, object_data=bytes(body)))
    out = await master.operate_crob(7, crob)
    assert out["iin"] == 0
    apdu = ch.get_sent_apdus()[0]
    assert apdu[1] == app.FC_DIRECT_OPERATE


# ---------------------------------------------------------------------------
# Class 1 events (SOE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_class1_events_returns_object_data():
    ch = FakeLinkChannel()
    master = DNP3Master(ch)
    events = [
        obj.BinaryEvent(index=10, state=True,  timestamp_ms=1_700_000_001_000),
        obj.BinaryEvent(index=11, state=False, timestamp_ms=1_700_000_001_002),
    ]
    obj_body = (
        bytes([2, 2, obj.Q_8BIT_INDEX_PREFIX])
        + bytes([2])  # qty
        + bytes([10, 11])  # indices
        + obj.encode_g2v2(events)
    )
    ch.queue_response_apdu(_make_response(
        seq=0, iin=app.IIN.CLASS_1_EVENTS, object_data=obj_body,
    ))
    out = await master.read_class1_events()
    assert out["iin"] & app.IIN.CLASS_1_EVENTS
    assert b"\x02\x02" in out["object_data"]


# ---------------------------------------------------------------------------
# File transfer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_concatenates_all_blocks():
    ch = FakeLinkChannel()
    master = DNP3Master(ch, DNP3MasterConfig(file_block_size=8))

    # Open file → status SUCCESS, handle 0xCAFE, file_size=20
    handle = 0xCAFE
    open_status = (
        handle.to_bytes(4, "little")
        + (20).to_bytes(4, "little")
        + (8).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + bytes([obj.FILE_ST_SUCCESS])
    )
    ch.queue_response_apdu(_make_response(
        seq=0, iin=0,
        object_data=(bytes([70, 4, obj.Q_16BIT_LIMITED_QTY])
                     + (1).to_bytes(2, "little") + open_status),
    ))

    # Three transport segments: 8 + 8 + 4 bytes; last has EOF flag
    segs = [
        obj.FileTransport(handle=handle, block_number=0,
                          file_data=b"COMTRADE"),
        obj.FileTransport(handle=handle, block_number=1,
                          file_data=b"FILE-DAT"),
        obj.FileTransport(handle=handle, block_number=0x80000002,
                          file_data=b"END!"),
    ]
    for i, seg in enumerate(segs):
        ch.queue_response_apdu(_make_response(
            seq=i + 1, iin=0,
            object_data=(bytes([70, 5, obj.Q_16BIT_LIMITED_QTY])
                         + (1).to_bytes(2, "little") + obj.encode_g70v5(seg)),
        ))

    # Close
    ch.queue_response_apdu(_make_response(seq=4, iin=0))

    data = await master.read_file("/EVENT/2026-05-01_fault.cfg")
    assert data == b"COMTRADEFILE-DATEND!"


@pytest.mark.asyncio
async def test_read_file_open_failure_raises():
    ch = FakeLinkChannel()
    master = DNP3Master(ch)
    open_status = (
        (0).to_bytes(4, "little")     # handle
        + (0).to_bytes(4, "little")    # file_size
        + (0).to_bytes(2, "little")    # max_block
        + (1).to_bytes(2, "little")    # request_id
        + bytes([obj.FILE_ST_NOT_FOUND])
    )
    ch.queue_response_apdu(_make_response(
        seq=0, iin=0,
        object_data=(bytes([70, 4, obj.Q_16BIT_LIMITED_QTY])
                     + (1).to_bytes(2, "little") + open_status),
    ))
    with pytest.raises(DNP3Error, match="status 3"):
        await master.read_file("/EVENT/missing.cfg")


# ---------------------------------------------------------------------------
# Sequence handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_master_increments_app_sequence():
    ch = FakeLinkChannel()
    master = DNP3Master(ch)
    ch.queue_response_apdu(_make_response(seq=0, iin=0))
    ch.queue_response_apdu(_make_response(seq=1, iin=0))
    await master.integrity_poll()
    await master.integrity_poll()
    apdus = ch.get_sent_apdus()
    assert (apdus[0][0] & 0x0F) == 0
    assert (apdus[1][0] & 0x0F) == 1


@pytest.mark.asyncio
async def test_master_skips_stale_sequence_response():
    ch = FakeLinkChannel()
    master = DNP3Master(ch)
    # Stale response at seq=99 followed by the right one at seq=0
    ch.queue_response_apdu(_make_response(seq=9, iin=0))
    ch.queue_response_apdu(_make_response(seq=0, iin=0))
    out = await master.integrity_poll()
    assert out["iin"] == 0


@pytest.mark.asyncio
async def test_disconnect_closes_channel():
    ch = FakeLinkChannel()
    master = DNP3Master(ch)
    await master.disconnect()
    assert ch._closed is True
