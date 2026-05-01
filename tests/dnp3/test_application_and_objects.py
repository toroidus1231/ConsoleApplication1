"""Tests for the DNP3 application layer + object encoders."""

from __future__ import annotations

import struct
import pytest

from src.instruments.dnp3 import application as app
from src.instruments.dnp3 import objects as obj


# ---------------------------------------------------------------------------
# Application layer framing
# ---------------------------------------------------------------------------


def test_request_encode_basic():
    req = app.Request(
        function_code=app.FC_READ,
        application_control=app.ApplicationControl(sequence=5),
        object_data=b"\x3C\x02\x06",  # group 60 var 2, all objects
    )
    out = req.encode()
    assert out[0] == 0xC5            # FIR | FIN | seq=5
    assert out[1] == app.FC_READ
    assert out[2:] == b"\x3C\x02\x06"


def test_response_round_trip():
    resp = app.Response(
        function_code=app.FC_RESPONSE,
        application_control=app.ApplicationControl(sequence=12, fir=True, fin=True),
        iin=app.IIN.NEED_TIME | app.IIN.CLASS_1_EVENTS,
        object_data=b"\x01\x02\x06",
    )
    decoded = app.Response.decode(resp.encode())
    assert decoded.function_code == app.FC_RESPONSE
    assert decoded.application_control.sequence == 12
    assert decoded.iin == app.IIN.NEED_TIME | app.IIN.CLASS_1_EVENTS
    assert decoded.has_iin(app.IIN.NEED_TIME)
    assert decoded.has_iin(app.IIN.CLASS_1_EVENTS)
    assert not decoded.has_iin(app.IIN.DEVICE_RESTART)
    assert decoded.object_data == b"\x01\x02\x06"


def test_response_decode_rejects_short_apdu():
    with pytest.raises(ValueError, match="too short"):
        app.Response.decode(b"\x80\x81\x00")


def test_response_decode_rejects_request_function_code():
    with pytest.raises(ValueError, match="not a response"):
        app.Response.decode(b"\xC5\x01\x00\x00")  # function code 0x01 = READ


def test_iin_decode_named_flags():
    flags = app.iin_decode(app.IIN.DEVICE_RESTART | app.IIN.NEED_TIME)
    assert flags["device_restart"] is True
    assert flags["need_time"] is True
    assert flags["broadcast"] is False


def test_application_control_round_trip():
    for seq in (0, 7, 15):
        for fir, fin, con, uns in [(True, True, False, False),
                                    (True, False, True, True),
                                    (False, True, False, True)]:
            ac = app.ApplicationControl(fir=fir, fin=fin, con=con,
                                          uns=uns, sequence=seq)
            decoded = app.ApplicationControl.decode(ac.encode())
            assert decoded.fir == fir
            assert decoded.fin == fin
            assert decoded.con == con
            assert decoded.uns == uns
            assert decoded.sequence == seq


# ---------------------------------------------------------------------------
# Object headers
# ---------------------------------------------------------------------------


def test_header_all_objects():
    h = obj.encode_header_all_objects(60, 1)
    assert h == b"\x3C\x01\x06"


def test_header_index_range_8():
    h = obj.encode_header_index_range_8(1, 2, 0, 7)
    # group 1, var 2, qualifier Q_8BIT_START_STOP, range bytes 0..7
    assert h == b"\x01\x02\x00\x00\x07"


# ---------------------------------------------------------------------------
# Group 1 — binary inputs with flag
# ---------------------------------------------------------------------------


def test_g1v2_round_trip():
    points = [
        {"state": True,  "online": True,  "restart": False},
        {"state": False, "online": True,  "comm_lost": True},
        {"state": True,  "online": False, "remote_forced": True},
    ]
    raw = obj.encode_g1v2(points)
    out = obj.decode_g1v2(raw, len(points))
    assert out[0]["state"] is True
    assert out[1]["state"] is False
    assert out[1]["comm_lost"] is True
    assert out[2]["state"] is True
    assert out[2]["remote_forced"] is True


def test_g1v2_short_payload_raises():
    with pytest.raises(ValueError, match="short"):
        obj.decode_g1v2(b"\x81", count=2)


# ---------------------------------------------------------------------------
# Group 2 — binary input events with absolute time
# ---------------------------------------------------------------------------


def test_g2v2_round_trip_single_event():
    e = obj.BinaryEvent(index=10, state=True, timestamp_ms=1_700_000_000_123)
    raw = obj.encode_g2v2([e])
    decoded = obj.decode_g2v2(raw, count=1, indices=[10])
    assert decoded[0].index == 10
    assert decoded[0].state is True
    assert decoded[0].timestamp_ms == 1_700_000_000_123


def test_g2v2_multiple_events_preserve_order():
    events = [
        obj.BinaryEvent(index=1, state=True, timestamp_ms=1000),
        obj.BinaryEvent(index=2, state=False, timestamp_ms=2000),
        obj.BinaryEvent(index=3, state=True, timestamp_ms=3000),
    ]
    raw = obj.encode_g2v2(events)
    decoded = obj.decode_g2v2(raw, count=3, indices=[1, 2, 3])
    assert [e.timestamp_ms for e in decoded] == [1000, 2000, 3000]
    assert [e.state for e in decoded] == [True, False, True]


def test_g2v2_index_count_mismatch_raises():
    e = obj.BinaryEvent(index=1, state=True, timestamp_ms=1)
    raw = obj.encode_g2v2([e])
    with pytest.raises(ValueError, match="index count"):
        obj.decode_g2v2(raw, count=1, indices=[1, 2])


# ---------------------------------------------------------------------------
# Group 12 — CROB
# ---------------------------------------------------------------------------


def test_g12v1_trip_round_trip():
    crob = obj.CROB(
        op_type=obj.CROB_OP_PULSE_ON,
        trip_close=obj.CROB_TC_TRIP,
        on_time_ms=200, off_time_ms=300,
    )
    raw = obj.encode_g12v1(crob)
    decoded = obj.decode_g12v1(raw)
    assert decoded.op_type == obj.CROB_OP_PULSE_ON
    assert decoded.trip_close == obj.CROB_TC_TRIP
    assert decoded.on_time_ms == 200
    assert decoded.off_time_ms == 300


def test_g12v1_close_round_trip():
    crob = obj.CROB(op_type=obj.CROB_OP_LATCH_ON, trip_close=obj.CROB_TC_CLOSE)
    raw = obj.encode_g12v1(crob)
    decoded = obj.decode_g12v1(raw)
    assert decoded.trip_close == obj.CROB_TC_CLOSE
    assert decoded.op_type == obj.CROB_OP_LATCH_ON


# ---------------------------------------------------------------------------
# Group 30 — analog input with float + flag
# ---------------------------------------------------------------------------


def test_g30v5_round_trip():
    points = [
        {"value": 13_807.0, "online": True, "over_range": False},
        {"value": 60.0234,  "online": True, "reference_check": True},
        {"value": 0.967,    "online": True},
    ]
    raw = obj.encode_g30v5(points)
    out = obj.decode_g30v5(raw, len(points))
    assert abs(out[0]["value"] - 13_807.0) < 1e-3
    assert abs(out[1]["value"] - 60.0234) < 1e-4
    assert out[1]["reference_check"] is True


def test_g30v5_handles_negative_value():
    out = obj.decode_g30v5(obj.encode_g30v5([{"value": -42.5}]), 1)
    assert abs(out[0]["value"] - (-42.5)) < 1e-6


# ---------------------------------------------------------------------------
# Group 50 — time
# ---------------------------------------------------------------------------


def test_g50v1_round_trip():
    ms = 1_700_001_234_567
    raw = obj.encode_g50v1(ms)
    assert len(raw) == 6
    assert obj.decode_g50v1(raw) == ms


def test_g50v1_zero_padding():
    raw = obj.encode_g50v1(0)
    assert raw == b"\x00\x00\x00\x00\x00\x00"
    assert obj.decode_g50v1(raw) == 0


# ---------------------------------------------------------------------------
# Group 70 — file transfer
# ---------------------------------------------------------------------------


def test_g70v3_filename_encoding():
    cmd = obj.FileCommand(
        filename="/EVENT/last.cfg",
        file_size=0, file_mode=obj.FILE_MODE_READ,
        max_block_size=512, request_id=42,
    )
    raw = obj.encode_g70v3(cmd)
    # name length stored in first 2 bytes
    name_len = int.from_bytes(raw[0:2], "little")
    assert name_len == len(cmd.filename.encode())
    # tail of payload is the name itself
    assert raw[-name_len:] == cmd.filename.encode()


def test_g70v3_rejects_long_filename():
    cmd = obj.FileCommand(filename="x" * 256)
    with pytest.raises(ValueError, match="too long"):
        obj.encode_g70v3(cmd)


def test_g70v4_status_decode():
    handle = 0x12345678
    file_size = 8192
    max_block = 256
    req_id = 7
    text = "OK"
    payload = (
        handle.to_bytes(4, "little")
        + file_size.to_bytes(4, "little")
        + max_block.to_bytes(2, "little")
        + req_id.to_bytes(2, "little")
        + bytes([obj.FILE_ST_SUCCESS])
        + text.encode()
    )
    s = obj.decode_g70v4(payload)
    assert s.handle == handle
    assert s.file_size == file_size
    assert s.max_block_size == max_block
    assert s.request_id == req_id
    assert s.status == obj.FILE_ST_SUCCESS
    assert s.text == "OK"


def test_g70v5_segment_round_trip():
    seg = obj.FileTransport(
        handle=0xDEADBEEF, block_number=3, file_data=b"COMTRADE",
    )
    raw = obj.encode_g70v5(seg)
    decoded = obj.decode_g70v5(raw)
    assert decoded.handle == 0xDEADBEEF
    assert decoded.sequence == 3
    assert decoded.is_last is False
    assert decoded.file_data == b"COMTRADE"


def test_g70v5_eof_flag_round_trip():
    seg = obj.FileTransport(
        handle=1, block_number=0x80000005, file_data=b"end",
    )
    decoded = obj.decode_g70v5(obj.encode_g70v5(seg))
    assert decoded.is_last is True
    assert decoded.sequence == 5
