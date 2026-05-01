"""DNP3 data link layer per IEEE 1815-2012 §9.

Frame format (max 292 bytes):

    Bytes 0-1  : Start sentinel 0x05 0x64 (little-endian on wire)
    Byte 2     : Length — count of bytes from byte 3 inclusive through
                 the last user-data byte exclusive of CRCs (max 255)
    Byte 3     : Control octet (DIR, PRM, FCB, FCV, FUNCTION_CODE)
    Bytes 4-5  : Destination address (little-endian)
    Bytes 6-7  : Source address (little-endian)
    Bytes 8-9  : Header CRC over bytes 0-7

Then up to 16 user-data blocks: each block is up to 16 bytes of payload
followed by a 2-byte CRC (little-endian on wire) computed over just
that block's payload. The final block may be short.

CRC: CRC-16-DNP polynomial 0x3D65, initial value 0x0000, input and
output reflected, xor-out 0xFFFF (per IEEE 1815-2012 §9.2.4.5).
"""

from __future__ import annotations

import dataclasses
from typing import Iterable


# Control-octet bits per IEEE 1815-2012 §9.2.4.1.4 Table 9-2
CTRL_DIR = 0x80   # 1 = master→outstation
CTRL_PRM = 0x40   # 1 = primary frame (request); 0 = secondary (response)
CTRL_FCB = 0x20   # frame count bit (alternates on retried PRI frames)
CTRL_FCV = 0x10   # FCB valid

# Primary function codes (PRM=1) per Table 9-3
PRI_RESET_LINK_STATES = 0x00
PRI_TEST_LINK_STATES  = 0x02
PRI_CONFIRMED_USER_DATA = 0x03
PRI_UNCONFIRMED_USER_DATA = 0x04
PRI_REQUEST_LINK_STATUS = 0x09

# Secondary function codes (PRM=0)
SEC_ACK = 0x00
SEC_NACK = 0x01
SEC_LINK_STATUS = 0x0B
SEC_NOT_SUPPORTED = 0x0F


# ---------------------------------------------------------------------------
# CRC-16-DNP
# ---------------------------------------------------------------------------


def _build_crc_table() -> list[int]:
    poly = 0xA6BC  # bit-reflected 0x3D65
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
        table.append(crc & 0xFFFF)
    return table


_CRC_TABLE = _build_crc_table()


def crc16(data: bytes) -> int:
    """Compute the DNP3 CRC-16 over `data`. The XOR-out value 0xFFFF is
    applied so the returned 16-bit value can be appended directly to the
    wire stream."""
    crc = 0x0000
    for b in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]
    return crc ^ 0xFFFF


def crc16_check(data: bytes, expected: int) -> bool:
    return crc16(data) == (expected & 0xFFFF)


# ---------------------------------------------------------------------------
# Block encoding / decoding
# ---------------------------------------------------------------------------


_MAX_USER_DATA_PER_BLOCK = 16
_MAX_USER_DATA_PER_FRAME = 250


def encode_blocks(user_data: bytes) -> bytes:
    """Chunk `user_data` into 16-byte blocks, append a CRC-16 after each."""
    if len(user_data) > _MAX_USER_DATA_PER_FRAME:
        raise ValueError(
            f"user data {len(user_data)} bytes exceeds "
            f"{_MAX_USER_DATA_PER_FRAME}"
        )
    out = bytearray()
    for i in range(0, len(user_data), _MAX_USER_DATA_PER_BLOCK):
        block = user_data[i:i + _MAX_USER_DATA_PER_BLOCK]
        out += block
        out += crc16(block).to_bytes(2, "little")
    return bytes(out)


def decode_blocks(blocks: bytes) -> bytes:
    """Strip the per-block CRCs from `blocks`. Raises if any CRC fails."""
    out = bytearray()
    pos = 0
    while pos < len(blocks):
        # Each block is up to 16 user-data bytes + 2 CRC bytes
        remaining = len(blocks) - pos
        block_len = min(_MAX_USER_DATA_PER_BLOCK, remaining - 2)
        if block_len <= 0:
            raise ValueError("trailing block too short for CRC")
        block = blocks[pos:pos + block_len]
        crc_bytes = blocks[pos + block_len:pos + block_len + 2]
        if len(crc_bytes) != 2:
            raise ValueError("missing block CRC")
        expected = int.from_bytes(crc_bytes, "little")
        if not crc16_check(block, expected):
            raise ValueError(
                f"block CRC mismatch at offset {pos}: "
                f"got {expected:#06x}, expected {crc16(block):#06x}"
            )
        out += block
        pos += block_len + 2
    return bytes(out)


# ---------------------------------------------------------------------------
# Frame
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class LinkFrame:
    """One data-link frame ready to encode or just decoded.

    Use the higher-level encode/decode functions; this is just the
    parsed shape.
    """
    function_code: int
    dest_address: int
    source_address: int
    user_data: bytes = b""
    direction: int = 1   # 1 = master→outstation
    primary: int = 1     # 1 = request
    fcb: int = 0
    fcv: int = 0

    @property
    def control(self) -> int:
        c = self.function_code & 0x0F
        if self.direction:
            c |= CTRL_DIR
        if self.primary:
            c |= CTRL_PRM
        if self.fcb:
            c |= CTRL_FCB
        if self.fcv:
            c |= CTRL_FCV
        return c & 0xFF


def encode(frame: LinkFrame) -> bytes:
    """Encode a LinkFrame to bytes ready for the transport layer."""
    payload_len = len(frame.user_data)
    if payload_len > _MAX_USER_DATA_PER_FRAME:
        raise ValueError(
            f"user data {payload_len} bytes exceeds "
            f"{_MAX_USER_DATA_PER_FRAME}"
        )
    # Length field counts: control + dest + source + user_data
    # = 1 + 2 + 2 + N = N + 5
    length = payload_len + 5
    header = bytearray()
    header += b"\x05\x64"
    header.append(length & 0xFF)
    header.append(frame.control)
    header += frame.dest_address.to_bytes(2, "little")
    header += frame.source_address.to_bytes(2, "little")
    header_crc = crc16(bytes(header)).to_bytes(2, "little")
    blocks = encode_blocks(frame.user_data)
    return bytes(header) + header_crc + blocks


def decode(buf: bytes) -> tuple[LinkFrame, int]:
    """Decode the first frame in `buf`. Returns (frame, bytes_consumed).

    Raises ValueError on bad start sentinel, length, or CRC.
    """
    if len(buf) < 10:
        raise ValueError("buffer too short for header")
    if buf[0] != 0x05 or buf[1] != 0x64:
        raise ValueError(f"bad start sentinel {buf[:2].hex()}")
    length = buf[2]
    if length < 5:
        raise ValueError(f"length {length} below minimum 5")
    control = buf[3]
    dest = int.from_bytes(buf[4:6], "little")
    src = int.from_bytes(buf[6:8], "little")
    header = bytes(buf[0:8])
    header_crc = int.from_bytes(buf[8:10], "little")
    if not crc16_check(header, header_crc):
        raise ValueError(
            f"header CRC mismatch: got {header_crc:#06x}, "
            f"expected {crc16(header):#06x}"
        )
    user_data_len = length - 5
    n_full = user_data_len // _MAX_USER_DATA_PER_BLOCK
    rem = user_data_len % _MAX_USER_DATA_PER_BLOCK
    blocks_total = n_full * (_MAX_USER_DATA_PER_BLOCK + 2)
    if rem:
        blocks_total += rem + 2
    end = 10 + blocks_total
    if len(buf) < end:
        raise ValueError(
            f"buffer too short: need {end} bytes, have {len(buf)}"
        )
    user_data = decode_blocks(buf[10:end])
    return (LinkFrame(
        function_code=control & 0x0F,
        dest_address=dest,
        source_address=src,
        user_data=user_data,
        direction=1 if control & CTRL_DIR else 0,
        primary=1 if control & CTRL_PRM else 0,
        fcb=1 if control & CTRL_FCB else 0,
        fcv=1 if control & CTRL_FCV else 0,
    ), end)


def encoded_size(user_data_len: int) -> int:
    """Size in bytes of an encoded frame carrying `user_data_len` bytes
    of payload."""
    if user_data_len > _MAX_USER_DATA_PER_FRAME:
        raise ValueError("user data exceeds 250 bytes")
    n_full = user_data_len // _MAX_USER_DATA_PER_BLOCK
    rem = user_data_len % _MAX_USER_DATA_PER_BLOCK
    blocks_total = n_full * (_MAX_USER_DATA_PER_BLOCK + 2)
    if rem:
        blocks_total += rem + 2
    return 10 + blocks_total
