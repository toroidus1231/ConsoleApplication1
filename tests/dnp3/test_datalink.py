"""Tests for the DNP3 data link layer.

CRC test vector comes from IEEE 1815-2012 §9.2.4.5 example: reset
link states header `0x05 0x64 0x05 0xC0 0x01 0x00 0x00 0x04` has
expected CRC `0xE920`. Frame round-trip tests cover header and
multi-block payloads.
"""

from __future__ import annotations

import pytest

from src.instruments.dnp3 import datalink as dl
from src.instruments.dnp3.datalink import LinkFrame


# ---------------------------------------------------------------------------
# CRC
# ---------------------------------------------------------------------------


def test_crc_reset_link_header():
    # IEEE 1815-2012 §9.2.4.5 — reset-link-states example header.
    # Wire order of CRC bytes is 0xE9, 0x21 → little-endian uint16 = 0x21E9.
    header = bytes.fromhex("056405C001000004")
    assert dl.crc16(header) == 0x21E9


def test_crc_empty_input():
    # Pure zero polynomial seed XOR-out 0xFFFF
    assert dl.crc16(b"") == 0xFFFF


def test_crc_two_distinct_inputs_distinct_results():
    assert dl.crc16(b"\x00") != dl.crc16(b"\x01")
    assert dl.crc16(b"\x05\x64") != dl.crc16(b"\x05\x65")


def test_crc_check_pass_and_fail():
    h = bytes.fromhex("056405C001000004")
    c = dl.crc16(h)
    assert dl.crc16_check(h, c)
    assert not dl.crc16_check(h, c ^ 1)


def test_crc_changes_with_each_byte():
    # Avalanche check: flipping any bit in the input should change the CRC
    base = b"\x05\x64\x05\xC0\x01\x00\x00\x04"
    base_crc = dl.crc16(base)
    for i in range(len(base)):
        for bit in range(8):
            flipped = bytearray(base)
            flipped[i] ^= 1 << bit
            assert dl.crc16(bytes(flipped)) != base_crc


# ---------------------------------------------------------------------------
# Block encoding
# ---------------------------------------------------------------------------


def test_encode_blocks_short_payload():
    payload = b"\xC0\xC1\x01\x3C\x02\x06"  # 6-byte app fragment
    blocks = dl.encode_blocks(payload)
    # one block: 6 user-data bytes + 2 CRC bytes = 8
    assert len(blocks) == 8
    assert blocks[:6] == payload


def test_encode_blocks_exactly_one_full_block():
    payload = bytes(range(16))
    blocks = dl.encode_blocks(payload)
    assert len(blocks) == 16 + 2  # one full block + CRC


def test_encode_blocks_two_blocks():
    payload = bytes(range(20))
    blocks = dl.encode_blocks(payload)
    # 16-byte block + CRC + 4-byte block + CRC
    assert len(blocks) == 16 + 2 + 4 + 2


def test_encode_blocks_max_payload():
    payload = bytes(250)
    blocks = dl.encode_blocks(payload)
    # 15 full 16-byte blocks + 1 final 10-byte block, each with CRC
    assert len(blocks) == 15 * 18 + 10 + 2


def test_encode_blocks_payload_too_large():
    with pytest.raises(ValueError, match="exceeds"):
        dl.encode_blocks(bytes(251))


def test_encode_decode_blocks_round_trip_random_lengths():
    import os
    for n in (1, 8, 16, 17, 32, 100, 250):
        payload = os.urandom(n)
        out = dl.decode_blocks(dl.encode_blocks(payload))
        assert out == payload


def test_decode_blocks_detects_corrupted_crc():
    payload = b"hello"
    blocks = bytearray(dl.encode_blocks(payload))
    blocks[-1] ^= 0xFF
    with pytest.raises(ValueError, match="CRC mismatch"):
        dl.decode_blocks(bytes(blocks))


def test_decode_blocks_short_block():
    with pytest.raises(ValueError, match="too short for CRC"):
        dl.decode_blocks(b"\x00")  # 1 byte: not enough for any block


# ---------------------------------------------------------------------------
# Frame round-trip
# ---------------------------------------------------------------------------


def test_frame_round_trip_request_no_payload():
    frame = LinkFrame(
        function_code=dl.PRI_RESET_LINK_STATES,
        dest_address=1, source_address=4,
        user_data=b"",
        direction=1, primary=1, fcb=0, fcv=0,
    )
    wire = dl.encode(frame)
    decoded, consumed = dl.decode(wire)
    assert consumed == len(wire)
    assert decoded.function_code == dl.PRI_RESET_LINK_STATES
    assert decoded.dest_address == 1
    assert decoded.source_address == 4
    assert decoded.user_data == b""
    assert decoded.direction == 1
    assert decoded.primary == 1


def test_frame_round_trip_request_with_payload():
    payload = bytes([0xC0, 0xC1, 0x01, 0x01, 0x02, 0x06, 0x00, 0x00, 0x00])
    frame = LinkFrame(
        function_code=dl.PRI_UNCONFIRMED_USER_DATA,
        dest_address=10, source_address=1,
        user_data=payload,
    )
    wire = dl.encode(frame)
    decoded, _ = dl.decode(wire)
    assert decoded.user_data == payload
    assert decoded.dest_address == 10
    assert decoded.source_address == 1


def test_frame_round_trip_secondary_response():
    frame = LinkFrame(
        function_code=dl.SEC_LINK_STATUS,
        dest_address=4, source_address=1,
        user_data=b"",
        direction=0, primary=0,
    )
    wire = dl.encode(frame)
    decoded, _ = dl.decode(wire)
    assert decoded.direction == 0
    assert decoded.primary == 0
    assert decoded.function_code == dl.SEC_LINK_STATUS


def test_decode_rejects_bad_sentinel():
    bad = bytes.fromhex("00640500" + "0000" + "0000" + "0000")
    with pytest.raises(ValueError, match="start sentinel"):
        dl.decode(bad)


def test_decode_rejects_short_header():
    with pytest.raises(ValueError, match="too short"):
        dl.decode(b"\x05\x64\x05")


def test_decode_rejects_bad_header_crc():
    frame = LinkFrame(function_code=0, dest_address=1, source_address=1)
    wire = bytearray(dl.encode(frame))
    wire[8] ^= 0xFF  # corrupt header CRC
    with pytest.raises(ValueError, match="header CRC mismatch"):
        dl.decode(bytes(wire))


def test_decode_rejects_truncated_blocks():
    payload = bytes(20)
    wire = dl.encode(LinkFrame(
        function_code=dl.PRI_CONFIRMED_USER_DATA,
        dest_address=1, source_address=2,
        user_data=payload,
    ))
    with pytest.raises(ValueError, match="too short"):
        dl.decode(wire[:-1])


def test_encoded_size_matches_actual_encoding():
    for n in (0, 1, 16, 17, 100, 250):
        size = dl.encoded_size(n)
        actual = len(dl.encode(LinkFrame(
            function_code=4, dest_address=1, source_address=2,
            user_data=bytes(n))))
        assert size == actual


def test_control_byte_flags_round_trip():
    cases = [
        (1, 1, 0, 0, dl.PRI_RESET_LINK_STATES),
        (1, 1, 1, 1, dl.PRI_CONFIRMED_USER_DATA),
        (0, 0, 0, 0, dl.SEC_ACK),
    ]
    for direction, primary, fcb, fcv, fc in cases:
        frame = LinkFrame(
            function_code=fc, dest_address=1, source_address=2,
            direction=direction, primary=primary, fcb=fcb, fcv=fcv,
        )
        decoded, _ = dl.decode(dl.encode(frame))
        assert decoded.direction == direction
        assert decoded.primary == primary
        assert decoded.fcb == fcb
        assert decoded.fcv == fcv
        assert decoded.function_code == fc
