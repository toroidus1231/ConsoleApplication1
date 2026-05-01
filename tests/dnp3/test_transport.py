"""Tests for the DNP3 transport (pseudo-transport) layer."""

from __future__ import annotations

import pytest

from src.instruments.dnp3.transport import (
    TransportSegmenter, TransportReassembler, TransportError,
)


def test_segment_short_apdu_single_segment():
    seg = TransportSegmenter()
    out = seg.segment(b"hello")
    assert len(out) == 1
    h = out[0][0]
    assert h & 0xC0 == 0xC0  # FIN + FIR both set
    assert out[0][1:] == b"hello"


def test_segment_apdu_at_boundary():
    # 249 bytes → still single segment; 250 → two segments
    seg = TransportSegmenter()
    one = seg.segment(b"x" * 249)
    assert len(one) == 1

    seg2 = TransportSegmenter()
    two = seg2.segment(b"x" * 250)
    assert len(two) == 2
    assert (two[0][0] & 0xC0) == 0x40   # FIR only
    assert (two[1][0] & 0xC0) == 0x80   # FIN only


def test_segment_three_segments():
    seg = TransportSegmenter()
    out = seg.segment(b"x" * (249 * 2 + 100))
    assert len(out) == 3
    assert (out[0][0] & 0xC0) == 0x40
    assert (out[1][0] & 0xC0) == 0x00   # neither FIR nor FIN
    assert (out[2][0] & 0xC0) == 0x80


def test_segment_sequence_increments():
    seg = TransportSegmenter()
    out = seg.segment(b"x" * (249 * 4))
    seqs = [s[0] & 0x3F for s in out]
    assert seqs == [0, 1, 2, 3]


def test_segment_sequence_wraps_at_64():
    seg = TransportSegmenter()
    seg._seq = 62
    out = seg.segment(b"x" * (249 * 5))
    seqs = [s[0] & 0x3F for s in out]
    assert seqs == [62, 63, 0, 1, 2]


def test_segment_rejects_empty_apdu():
    with pytest.raises(TransportError, match="empty"):
        TransportSegmenter().segment(b"")


def test_segment_rejects_oversize_apdu():
    with pytest.raises(TransportError, match="exceeds"):
        TransportSegmenter().segment(b"x" * 2049)


# ---------------------------------------------------------------------------
# Reassembly
# ---------------------------------------------------------------------------


def test_reassemble_single_segment():
    rasm = TransportReassembler()
    out = rasm.feed(bytes([0xC0]) + b"hello")
    assert out == b"hello"


def test_reassemble_round_trip_short():
    seg = TransportSegmenter()
    rasm = TransportReassembler()
    apdu = b"abcdefghij"
    out = None
    for s in seg.segment(apdu):
        out = rasm.feed(s)
    assert out == apdu


def test_reassemble_round_trip_long():
    import os
    seg = TransportSegmenter()
    rasm = TransportReassembler()
    apdu = os.urandom(2000)
    out = None
    for s in seg.segment(apdu):
        out = rasm.feed(s)
    assert out == apdu


def test_reassemble_returns_none_until_fin():
    rasm = TransportReassembler()
    # FIR only, seq=0
    assert rasm.feed(bytes([0x40]) + b"first") is None
    # middle, seq=1
    assert rasm.feed(bytes([0x01]) + b"middle") is None
    # FIN only, seq=2
    out = rasm.feed(bytes([0x82]) + b"last")
    assert out == b"first" + b"middle" + b"last"


def test_reassemble_rejects_non_fir_with_no_state():
    rasm = TransportReassembler()
    # First segment lacks FIR
    with pytest.raises(TransportError, match="no APDU in progress"):
        rasm.feed(bytes([0x80]) + b"data")  # FIN only, no FIR


def test_reassemble_rejects_out_of_order_seq():
    rasm = TransportReassembler()
    rasm.feed(bytes([0x40]) + b"first")  # FIR, seq=0
    with pytest.raises(TransportError, match="out-of-order"):
        rasm.feed(bytes([0x82]) + b"jumped")  # FIN, seq=2 (skipped 1)


def test_reassemble_fir_resets_state():
    rasm = TransportReassembler()
    rasm.feed(bytes([0x40]) + b"abandoned")
    # New FIR drops the in-progress fragment and starts fresh
    out = rasm.feed(bytes([0xC1]) + b"fresh")
    assert out == b"fresh"


def test_reassemble_rejects_empty_segment():
    with pytest.raises(TransportError, match="empty"):
        TransportReassembler().feed(b"")


def test_reassemble_rejects_oversize_segment():
    rasm = TransportReassembler()
    big = bytes([0xC0]) + b"x" * 250  # body 250 bytes (limit is 249)
    with pytest.raises(TransportError, match="exceeds"):
        rasm.feed(big)
