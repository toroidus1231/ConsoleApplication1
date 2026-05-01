"""DNP3 master implementation per IEEE 1815-2012.

Ties datalink, transport, and application together. Speaks to the
outstation through an injected `LinkChannel` so production code uses
asyncio.open_connection(host, 20000) and tests use a scripted stub.

The master holds the application-layer sequence counter, transport
segmentation state, and the file-transfer handle table.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Protocol

from . import datalink as dl
from . import transport as tx
from . import application as app
from . import objects as obj


class LinkChannel(Protocol):
    """Bytes-in / bytes-out abstraction over the data-link transport.

    For TCP/IP DNP3 (IEEE 1815-2012 §7.1) the underlying connection is
    a single TCP stream on port 20000; multiple link frames may be
    concatenated in either direction.
    """
    async def write_bytes(self, data: bytes) -> None: ...
    async def read_bytes(self, n: int, timeout_s: float) -> bytes: ...
    async def close(self) -> None: ...


class DNP3Error(RuntimeError):
    pass


@dataclasses.dataclass
class DNP3MasterConfig:
    master_address: int = 1
    outstation_address: int = 4
    response_timeout_s: float = 5.0
    file_block_size: int = 1024


class DNP3Master:
    """High-level DNP3 master.

    Each public method does one logical operation (integrity poll, read
    class events, write time, read file). All of them go through
    `_request_response` which handles framing, segmentation, and
    sequence checking.
    """

    def __init__(self, channel: LinkChannel,
                 config: DNP3MasterConfig | None = None):
        self._ch = channel
        self._cfg = config or DNP3MasterConfig()
        self._segmenter = tx.TransportSegmenter()
        self._app_seq = 0  # application-layer mod-16 sequence
        self._next_request_id = 1

    # ------------------------------------------------------------------
    # Public master operations
    # ------------------------------------------------------------------

    async def integrity_poll(self) -> dict:
        """Class 0+1+2+3 read, returns dict with iin + raw object_data."""
        # Group 60 var 1 = Class 0 (static data); var 2/3/4 = events.
        body = (
            obj.encode_header_all_objects(60, 2)
            + obj.encode_header_all_objects(60, 3)
            + obj.encode_header_all_objects(60, 4)
            + obj.encode_header_all_objects(60, 1)
        )
        resp = await self._request_response(app.FC_READ, body)
        return {
            "iin": resp.iin,
            "iin_flags": app.iin_decode(resp.iin),
            "object_data": resp.object_data,
        }

    async def read_class1_events(self) -> dict:
        """Read all Class 1 events (g60v2). Used by SOE collection."""
        body = obj.encode_header_all_objects(60, 2)
        resp = await self._request_response(app.FC_READ, body)
        return {"iin": resp.iin, "object_data": resp.object_data}

    async def write_time(self, timestamp_ms: int) -> int:
        """Synchronize outstation clock by writing g50v1. Returns IIN."""
        body = obj.encode_header_index_range_8(50, 1, 0, 0) + obj.encode_g50v1(timestamp_ms)
        resp = await self._request_response(app.FC_WRITE, body)
        return resp.iin

    async def operate_crob(self, point_index: int, crob: obj.CROB) -> dict:
        """Direct-operate a CROB at `point_index` (group 12 var 1).
        Used to issue trip/close commands during commissioning verification."""
        # Qualifier 0x17 (8-bit index prefix) — one prefix byte then the object
        body = bytearray()
        body += bytes([12, 1, obj.Q_8BIT_INDEX_PREFIX, 1])
        body += bytes([point_index & 0xFF])
        body += obj.encode_g12v1(crob)
        resp = await self._request_response(app.FC_DIRECT_OPERATE, bytes(body))
        return {"iin": resp.iin, "object_data": resp.object_data}

    async def open_file(self, filename: str) -> obj.FileCommandStatus:
        cmd = obj.FileCommand(
            filename=filename,
            file_mode=obj.FILE_MODE_READ,
            max_block_size=self._cfg.file_block_size,
            request_id=self._alloc_request_id(),
            operational_mode=obj.FILE_OP_OPEN,
        )
        body = (
            bytes([70, 3, obj.Q_16BIT_LIMITED_QTY])
            + (1).to_bytes(2, "little")
            + obj.encode_g70v3(cmd)
        )
        resp = await self._request_response(app.FC_OPEN_FILE, body)
        # The outstation reply to FC_OPEN_FILE includes a g70v4 object.
        # Skip its 3-byte header, the 2-byte qty, then decode the body.
        if len(resp.object_data) < 5:
            raise DNP3Error("open_file: response too short")
        body_off = 5
        return obj.decode_g70v4(resp.object_data[body_off:])

    async def read_file_block(self, handle: int) -> obj.FileTransport:
        # Read next g70v5 segment for `handle`
        body = (
            bytes([70, 5, obj.Q_16BIT_LIMITED_QTY])
            + (1).to_bytes(2, "little")
            + handle.to_bytes(4, "little")
            + (0).to_bytes(4, "little")  # block_number=0 → "next"
        )
        resp = await self._request_response(app.FC_READ, body)
        if len(resp.object_data) < 5:
            raise DNP3Error("read_file_block: response too short")
        body_off = 5
        return obj.decode_g70v5(resp.object_data[body_off:])

    async def close_file(self, handle: int) -> int:
        cmd = obj.FileCommand(
            filename="", file_mode=0, max_block_size=0,
            request_id=self._alloc_request_id(),
            operational_mode=obj.FILE_OP_NULL,
        )
        cmd_bytes = obj.encode_g70v3(cmd)
        body = (
            bytes([70, 3, obj.Q_16BIT_LIMITED_QTY])
            + (1).to_bytes(2, "little")
            + handle.to_bytes(4, "little")
            + cmd_bytes
        )
        resp = await self._request_response(app.FC_CLOSE_FILE, body)
        return resp.iin

    async def read_file(self, filename: str) -> bytes:
        """Convenience wrapper: open, read all blocks until EOF, close.
        Used to retrieve a COMTRADE record from the relay."""
        status = await self.open_file(filename)
        if status.status != obj.FILE_ST_SUCCESS:
            raise DNP3Error(
                f"open file {filename!r}: status {status.status}"
            )
        out = bytearray()
        try:
            while True:
                seg = await self.read_file_block(status.handle)
                out += seg.file_data
                if seg.is_last:
                    break
        finally:
            await self.close_file(status.handle)
        return bytes(out)

    async def disconnect(self) -> None:
        await self._ch.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _alloc_request_id(self) -> int:
        rid = self._next_request_id
        self._next_request_id = (self._next_request_id + 1) & 0xFFFF
        return rid

    def _next_app_seq(self) -> int:
        s = self._app_seq
        self._app_seq = (self._app_seq + 1) & app.AC_SEQ_MASK
        return s

    async def _request_response(self, fc: int, body: bytes) -> app.Response:
        seq = self._next_app_seq()
        req = app.Request(
            function_code=fc,
            application_control=app.ApplicationControl(
                fir=True, fin=True, sequence=seq,
            ),
            object_data=body,
        )
        apdu = req.encode()
        await self._send_apdu(apdu)
        return await self._receive_response_apdu(expected_seq=seq)

    async def _send_apdu(self, apdu: bytes) -> None:
        for segment in self._segmenter.segment(apdu):
            frame = dl.LinkFrame(
                function_code=dl.PRI_UNCONFIRMED_USER_DATA,
                dest_address=self._cfg.outstation_address,
                source_address=self._cfg.master_address,
                user_data=segment,
                direction=1, primary=1,
            )
            await self._ch.write_bytes(dl.encode(frame))

    async def _receive_response_apdu(self, expected_seq: int) -> app.Response:
        rasm = tx.TransportReassembler()
        deadline = asyncio.get_event_loop().time() + self._cfg.response_timeout_s
        while True:
            timeout = max(0.01, deadline - asyncio.get_event_loop().time())
            frame, _ = await self._read_one_link_frame(timeout)
            apdu = rasm.feed(frame.user_data)
            if apdu is None:
                continue
            resp = app.Response.decode(apdu)
            if resp.application_control.sequence != expected_seq:
                # Out-of-order or stale response: keep reading
                continue
            return resp

    async def _read_one_link_frame(self, timeout_s: float):
        # Header is exactly 10 bytes; once we have those we know the
        # block-encoded user-data length and can read the rest.
        header = await self._ch.read_bytes(10, timeout_s)
        if len(header) < 10:
            raise DNP3Error("link frame header truncated")
        length = header[2]
        user_data_len = max(0, length - 5)
        n_full = user_data_len // 16
        rem = user_data_len % 16
        blocks_total = n_full * (16 + 2)
        if rem:
            blocks_total += rem + 2
        rest = b""
        if blocks_total:
            rest = await self._ch.read_bytes(blocks_total, timeout_s)
            if len(rest) < blocks_total:
                raise DNP3Error("link frame body truncated")
        return dl.decode(header + rest)
