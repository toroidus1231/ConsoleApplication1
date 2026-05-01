"""DNP3 transport (pseudo-transport) layer per IEEE 1815-2012 §10.

The transport layer adds a single 1-byte header to each link frame:

    bit 7 : FIN  (1 = last segment of an APDU)
    bit 6 : FIR  (1 = first segment of an APDU)
    bits 5..0 : sequence (mod 64)

Application-layer fragments (APDUs) up to 2048 bytes are carried over
one or more 249-byte transport segments (250-byte link frame payload
minus the 1-byte transport header). The receiver concatenates all
segments from FIR=1 through FIN=1 to reconstruct the APDU.
"""

from __future__ import annotations


_MAX_TRANSPORT_PAYLOAD = 249  # link payload 250 - 1-byte header
_MAX_APDU = 2048              # IEEE 1815-2012 §10.2


class TransportError(RuntimeError):
    pass


class TransportSegmenter:
    """Outgoing direction: chops an APDU into segments and tags each
    with the FIR/FIN/sequence header. Holds an internal mod-64 sequence
    counter that increments per segment sent."""

    def __init__(self) -> None:
        self._seq = 0

    @property
    def sequence(self) -> int:
        return self._seq & 0x3F

    def reset(self) -> None:
        self._seq = 0

    def segment(self, apdu: bytes) -> list[bytes]:
        if len(apdu) > _MAX_APDU:
            raise TransportError(
                f"APDU {len(apdu)} bytes exceeds {_MAX_APDU}"
            )
        if not apdu:
            raise TransportError("cannot segment empty APDU")
        chunks = [
            apdu[i:i + _MAX_TRANSPORT_PAYLOAD]
            for i in range(0, len(apdu), _MAX_TRANSPORT_PAYLOAD)
        ]
        out = []
        for i, chunk in enumerate(chunks):
            fir = 1 if i == 0 else 0
            fin = 1 if i == len(chunks) - 1 else 0
            header = ((fin & 1) << 7) | ((fir & 1) << 6) | (self._seq & 0x3F)
            self._seq = (self._seq + 1) & 0x3F
            out.append(bytes([header]) + chunk)
        return out


class TransportReassembler:
    """Incoming direction: feed segments one at a time; whenever a
    complete APDU has been assembled it is returned, else None.

    Per IEEE 1815-2012 §10.3.2, sequence numbers must increment by 1
    (mod 64) within a single APDU; FIR=1 resets the expected sequence
    state.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._next_seq: int | None = None
        self._in_progress = False

    def feed(self, segment: bytes) -> bytes | None:
        if not segment:
            raise TransportError("empty segment")
        h = segment[0]
        fin = bool(h & 0x80)
        fir = bool(h & 0x40)
        seq = h & 0x3F
        body = segment[1:]
        if len(body) > _MAX_TRANSPORT_PAYLOAD:
            raise TransportError("segment payload exceeds 249 bytes")

        if fir:
            self._buf = bytearray(body)
            self._next_seq = (seq + 1) & 0x3F
            self._in_progress = True
        else:
            if not self._in_progress:
                raise TransportError("non-FIR segment with no APDU in progress")
            if seq != self._next_seq:
                raise TransportError(
                    f"out-of-order segment: got seq {seq}, expected {self._next_seq}"
                )
            self._buf += body
            self._next_seq = (self._next_seq + 1) & 0x3F

        if fin:
            apdu = bytes(self._buf)
            self._buf = bytearray()
            self._next_seq = None
            self._in_progress = False
            if len(apdu) > _MAX_APDU:
                raise TransportError(f"reassembled APDU {len(apdu)} > {_MAX_APDU}")
            return apdu
        return None
