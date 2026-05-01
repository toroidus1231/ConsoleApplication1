"""DNP3 object groups per IEEE 1815-2012 §11 + the IEEE 1815 Subset
Definitions Doc, the bits the master needs for relay commissioning:

  Group 1   - Binary Input (g1v1 packed bits, g1v2 with flags)
  Group 2   - Binary Input Event (g2v1 no time, g2v2 absolute time,
              g2v3 relative time)
  Group 12  - Control Relay Output Block (CROB) — for trip/close
  Group 30  - Analog Input (g30v1 32-bit int + flag, g30v2 16-bit int + flag,
              g30v5 32-bit float + flag)
  Group 32  - Analog Input Event (g32v3 with absolute time)
  Group 50  - Time and Date (g50v1 absolute time)
  Group 70  - File-control objects (g70v3 file command, g70v4 file
              command status, g70v5 file transport, g70v6 file transport
              status)

Variations are grouped here as encoders/decoders that map to/from
Python dicts. The qualifier code (Q) and prefix tags identify which
range/index encoding is used in the object header.
"""

from __future__ import annotations

import dataclasses
import struct


# Qualifier code (octet 2 of object header) — IEEE 1815-2012 Table 11-9
Q_8BIT_START_STOP = 0x00       # 1-octet start, 1-octet stop indices
Q_16BIT_START_STOP = 0x01
Q_ALL_OBJECTS = 0x06
Q_8BIT_LIMITED_QTY = 0x07
Q_16BIT_LIMITED_QTY = 0x08
Q_8BIT_INDEX_PREFIX = 0x17     # each object preceded by 1-octet index
Q_16BIT_INDEX_PREFIX = 0x28


@dataclasses.dataclass
class ObjectHeader:
    group: int
    variation: int
    qualifier: int
    range_field: bytes  # encoded range field, format depends on qualifier

    def encode(self) -> bytes:
        return bytes([self.group, self.variation, self.qualifier]) + self.range_field


def encode_header_all_objects(group: int, variation: int) -> bytes:
    """Header for 'read all instances of this object'."""
    return ObjectHeader(group, variation, Q_ALL_OBJECTS, b"").encode()


def encode_header_index_range_8(group: int, variation: int,
                                 start: int, stop: int) -> bytes:
    return ObjectHeader(
        group, variation, Q_8BIT_START_STOP,
        bytes([start & 0xFF, stop & 0xFF]),
    ).encode()


# ---------------------------------------------------------------------------
# Group 1 — Binary Input (with flag, 1 byte per point)
# ---------------------------------------------------------------------------


def decode_g1v2(payload: bytes, count: int) -> list[dict]:
    """Decode `count` Group 1 Var 2 objects (1 byte each: bit 7 = state,
    lower bits = quality flags per IEEE 1815-2012 Table 11-7)."""
    if len(payload) < count:
        raise ValueError(f"g1v2 short: need {count} bytes, have {len(payload)}")
    out = []
    for i in range(count):
        b = payload[i]
        out.append({
            "state": bool(b & 0x80),
            "online": bool(b & 0x01),
            "restart": bool(b & 0x02),
            "comm_lost": bool(b & 0x04),
            "remote_forced": bool(b & 0x08),
            "local_forced": bool(b & 0x10),
            "chatter_filter": bool(b & 0x20),
        })
    return out


def encode_g1v2(points: list[dict]) -> bytes:
    out = bytearray()
    for p in points:
        b = 0
        if p.get("state"):
            b |= 0x80
        if p.get("online", True):
            b |= 0x01
        if p.get("restart"):
            b |= 0x02
        if p.get("comm_lost"):
            b |= 0x04
        if p.get("remote_forced"):
            b |= 0x08
        if p.get("local_forced"):
            b |= 0x10
        if p.get("chatter_filter"):
            b |= 0x20
        out.append(b)
    return bytes(out)


# ---------------------------------------------------------------------------
# Group 2 — Binary Input Events
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class BinaryEvent:
    index: int
    state: bool
    online: bool = True
    timestamp_ms: int | None = None  # absolute UTC ms since epoch (g2v2)


def encode_g2v2(events: list[BinaryEvent]) -> bytes:
    """Encode Group 2 Var 2 (binary input event with absolute time).
    Each object: 1 byte flags + 6 bytes 48-bit time (little-endian)."""
    out = bytearray()
    for e in events:
        b = 0
        if e.state:
            b |= 0x80
        if e.online:
            b |= 0x01
        out.append(b)
        ts = (e.timestamp_ms or 0) & 0xFFFFFFFFFFFF  # 48-bit
        out += ts.to_bytes(6, "little")
    return bytes(out)


def decode_g2v2(payload: bytes, count: int,
                indices: list[int]) -> list[BinaryEvent]:
    """Decode `count` Group 2 Var 2 events, pairing with `indices` from
    the object header's prefix tags."""
    obj_size = 7
    if len(payload) < count * obj_size:
        raise ValueError(
            f"g2v2 short: need {count * obj_size} bytes, have {len(payload)}"
        )
    if len(indices) != count:
        raise ValueError(
            f"index count mismatch: {len(indices)} vs {count}"
        )
    out = []
    for i in range(count):
        off = i * obj_size
        b = payload[off]
        ts = int.from_bytes(payload[off + 1:off + 7], "little")
        out.append(BinaryEvent(
            index=indices[i],
            state=bool(b & 0x80),
            online=bool(b & 0x01),
            timestamp_ms=ts,
        ))
    return out


# ---------------------------------------------------------------------------
# Group 12 — Control Relay Output Block (CROB)
# ---------------------------------------------------------------------------


# CROB control codes (op-type field) per IEEE 1815-2012 Table 11-10
CROB_OP_NUL = 0x0
CROB_OP_PULSE_ON = 0x1
CROB_OP_PULSE_OFF = 0x2
CROB_OP_LATCH_ON = 0x3
CROB_OP_LATCH_OFF = 0x4
# Trip/close are added to the op_type
CROB_TC_CLOSE = 0x40
CROB_TC_TRIP = 0x80


@dataclasses.dataclass
class CROB:
    op_type: int           # one of CROB_OP_*
    trip_close: int = 0    # CROB_TC_CLOSE / CROB_TC_TRIP / 0
    count: int = 1
    on_time_ms: int = 100
    off_time_ms: int = 100
    status: int = 0        # response only (0 = success per Table 11-12)


def encode_g12v1(c: CROB) -> bytes:
    """Encode a single CROB (group 12 var 1, 11 bytes)."""
    control_code = (c.trip_close & 0xC0) | (c.op_type & 0x0F)
    return (
        bytes([control_code, c.count & 0xFF])
        + c.on_time_ms.to_bytes(4, "little")
        + c.off_time_ms.to_bytes(4, "little")
        + bytes([c.status & 0xFF])
    )


def decode_g12v1(payload: bytes) -> CROB:
    if len(payload) < 11:
        raise ValueError(f"g12v1 short: {len(payload)} bytes")
    control = payload[0]
    return CROB(
        op_type=control & 0x0F,
        trip_close=control & 0xC0,
        count=payload[1],
        on_time_ms=int.from_bytes(payload[2:6], "little"),
        off_time_ms=int.from_bytes(payload[6:10], "little"),
        status=payload[10],
    )


# ---------------------------------------------------------------------------
# Group 30 / 32 — Analog Input (and Event)
# ---------------------------------------------------------------------------


def decode_g30v5(payload: bytes, count: int) -> list[dict]:
    """Decode `count` Group 30 Var 5 (single-precision float + flag,
    5 bytes per point)."""
    if len(payload) < count * 5:
        raise ValueError(f"g30v5 short: need {count * 5}, have {len(payload)}")
    out = []
    for i in range(count):
        off = i * 5
        b = payload[off]
        v = struct.unpack("<f", payload[off + 1:off + 5])[0]
        out.append({
            "value": v,
            "online": bool(b & 0x01),
            "restart": bool(b & 0x02),
            "over_range": bool(b & 0x20),
            "reference_check": bool(b & 0x40),
        })
    return out


def encode_g30v5(points: list[dict]) -> bytes:
    out = bytearray()
    for p in points:
        b = 0
        if p.get("online", True):
            b |= 0x01
        if p.get("restart"):
            b |= 0x02
        if p.get("over_range"):
            b |= 0x20
        if p.get("reference_check"):
            b |= 0x40
        out.append(b)
        out += struct.pack("<f", float(p["value"]))
    return bytes(out)


# ---------------------------------------------------------------------------
# Group 50 — Time and Date
# ---------------------------------------------------------------------------


def encode_g50v1(timestamp_ms: int) -> bytes:
    """Encode Group 50 Var 1 — 48-bit absolute UTC ms since epoch."""
    return (timestamp_ms & 0xFFFFFFFFFFFF).to_bytes(6, "little")


def decode_g50v1(payload: bytes) -> int:
    if len(payload) < 6:
        raise ValueError(f"g50v1 short: {len(payload)} bytes")
    return int.from_bytes(payload[:6], "little")


# ---------------------------------------------------------------------------
# Group 70 — File transfer
#
# Master initiates a file read by sending a g70v3 (file command) with
# operation = OPEN. Outstation responds with a g70v4 status + handle.
# Master then issues g70v5 (file transport) reads using the handle;
# outstation returns g70v5 segments terminated by the EOF flag.
# ---------------------------------------------------------------------------


# File operation codes per IEEE 1815-2012 §11.27
FILE_OP_OPEN = 1
FILE_OP_DELETE = 2
FILE_OP_NULL = 0
# File mode flags
FILE_MODE_READ = 1
FILE_MODE_WRITE = 2
FILE_MODE_APPEND = 3
# File status codes (g70v4)
FILE_ST_SUCCESS = 0
FILE_ST_DENIED = 1
FILE_ST_INVALID_MODE = 2
FILE_ST_NOT_FOUND = 3
FILE_ST_LOCKED = 4
FILE_ST_TOO_MANY_OPEN = 5
FILE_ST_INVALID_HANDLE = 6
FILE_ST_WRITE_BLOCK_SIZE = 7
FILE_ST_COMM_LOST = 8
FILE_ST_CANNOT_ABORT = 9
FILE_ST_NOT_OPENED = 16
FILE_ST_HANDLE_EXPIRED = 17
FILE_ST_BUFFER_OVERRUN = 18
FILE_ST_FATAL = 19
FILE_ST_BLOCK_SEQ = 20


@dataclasses.dataclass
class FileCommand:
    """g70v3 file command issued by master to open/delete a file."""
    filename: str
    file_size: int = 0
    permissions: int = 0
    auth_key: int = 0
    file_mode: int = FILE_MODE_READ
    max_block_size: int = 1024
    request_id: int = 0
    operational_mode: int = FILE_OP_OPEN
    file_type: int = 0  # 0 = simple file


def encode_g70v3(cmd: FileCommand) -> bytes:
    name = cmd.filename.encode("utf-8")
    if len(name) > 255:
        raise ValueError("filename too long")
    body = bytearray()
    body += len(name).to_bytes(2, "little")
    body += cmd.file_type.to_bytes(2, "little")
    body += cmd.file_size.to_bytes(4, "little")
    body += cmd.permissions.to_bytes(2, "little")
    body += cmd.auth_key.to_bytes(4, "little")
    body += cmd.file_mode.to_bytes(4, "little")
    body += cmd.max_block_size.to_bytes(2, "little")
    body += cmd.request_id.to_bytes(2, "little")
    body += cmd.operational_mode.to_bytes(1, "little")
    body += name
    return bytes(body)


@dataclasses.dataclass
class FileCommandStatus:
    """g70v4 reply: handle the master uses for subsequent transport
    reads, plus the negotiated block size and a status code."""
    handle: int
    file_size: int
    max_block_size: int
    request_id: int
    status: int
    text: str = ""


def decode_g70v4(payload: bytes) -> FileCommandStatus:
    if len(payload) < 13:
        raise ValueError(f"g70v4 short: {len(payload)} bytes")
    handle = int.from_bytes(payload[0:4], "little")
    file_size = int.from_bytes(payload[4:8], "little")
    max_block = int.from_bytes(payload[8:10], "little")
    req_id = int.from_bytes(payload[10:12], "little")
    status = payload[12]
    text = payload[13:].decode("utf-8", errors="replace")
    return FileCommandStatus(
        handle=handle, file_size=file_size,
        max_block_size=max_block, request_id=req_id,
        status=status, text=text.rstrip("\x00"),
    )


@dataclasses.dataclass
class FileTransport:
    """g70v5 segment carrying a chunk of file data."""
    handle: int
    block_number: int  # bit 31 = EOF flag
    file_data: bytes

    @property
    def is_last(self) -> bool:
        return bool(self.block_number & 0x80000000)

    @property
    def sequence(self) -> int:
        return self.block_number & 0x7FFFFFFF


def encode_g70v5(seg: FileTransport) -> bytes:
    return (
        seg.handle.to_bytes(4, "little")
        + seg.block_number.to_bytes(4, "little")
        + seg.file_data
    )


def decode_g70v5(payload: bytes) -> FileTransport:
    if len(payload) < 8:
        raise ValueError(f"g70v5 short: {len(payload)} bytes")
    handle = int.from_bytes(payload[0:4], "little")
    block = int.from_bytes(payload[4:8], "little")
    return FileTransport(
        handle=handle, block_number=block,
        file_data=bytes(payload[8:]),
    )
