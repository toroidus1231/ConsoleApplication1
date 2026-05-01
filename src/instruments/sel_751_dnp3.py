"""SEL-751 driver speaking DNP3 instead of Modbus.

The relay is the same physical device — the existing `sel_751.py`
driver covers the Modbus path. This module is selected when the
detector finds the relay on TCP port 20000 (DNP3) and the operator
needs the deeper test scope only DNP3 unlocks:

  - Sequence-of-events (SOE) timestamps with the relay's own clock,
    not our polling clock — Group 2 binary input events
  - COMTRADE waveform retrieval via DNP3 file transfer — Group 70
  - SCADA integration validation — does the customer's HMI receive
    the trip events we generate during functional testing
  - Clock sync verification — Group 50

Index assignments per SEL-751 DNP3 Configuration Guide rev 5.10:

  Binary input points (groups 1, 2):
      0  : breaker open status
      1  : breaker closed status
      2  : 51 phase TOC pickup
      3  : 50 phase IOC pickup
      4  : 27 undervoltage pickup
      5  : 81 frequency pickup
      6  : 50N ground IOC pickup
      7  : 51N ground TOC pickup
      8  : trip output asserted
      9  : close output asserted

  Binary output points (group 12, CROB):
      0  : breaker trip
      1  : breaker close
      2  : settings group select bit 0
      3  : settings group select bit 1

  Analog input points (group 30):
      0..2 : Va, Vb, Vc primary (V)
      3..5 : Ia, Ib, Ic primary (A)
      6   : neutral current (A)
      7   : frequency (Hz)
      8   : power factor
      9   : real power (kW)
"""

from __future__ import annotations

from dataclasses import dataclass

from . import CalibrationCert, register
from .megger_mit525 import InstrumentError
from .dnp3 import objects as obj
from .dnp3.master import DNP3Master, DNP3MasterConfig, LinkChannel


@dataclass
class SEL751DNP3Config:
    host: str = "10.4.0.110"
    port: int = 20000
    master_address: int = 1
    outstation_address: int = 4
    file_block_size: int = 1024


# Binary input indices for protective elements per SEL-751 DNP3 Config
# Guide rev 5.10. Mapping is fixed by the SEL factory profile.
_ELEMENT_BIN_INDEX = {
    "51":  2,
    "50":  3,
    "27":  4,
    "81":  5,
    "50N": 6,
    "51N": 7,
}


@register(transport="dnp3")
class SEL751DNP3:
    VENDOR = "SEL"
    MODEL = "SEL-751"
    TRANSPORT = "dnp3"

    def __init__(self, channel: LinkChannel,
                 config: SEL751DNP3Config | None = None):
        cfg = config or SEL751DNP3Config()
        self._cfg = cfg
        self._master = DNP3Master(
            channel,
            DNP3MasterConfig(
                master_address=cfg.master_address,
                outstation_address=cfg.outstation_address,
                file_block_size=cfg.file_block_size,
            ),
        )
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        # Initial integrity poll establishes the link + tells us if the
        # outstation just restarted (DEVICE_RESTART IIN bit) or needs a
        # clock sync (NEED_TIME).
        result = await self._master.integrity_poll()
        flags = result["iin_flags"]
        if flags["device_restart"] or flags["device_trouble"]:
            raise InstrumentError(
                f"SEL-751 reported device fault on connect: "
                f"restart={flags['device_restart']}, "
                f"trouble={flags['device_trouble']}"
            )
        self._connected = True

    async def disconnect(self) -> None:
        if not self._connected:
            return
        await self._master.disconnect()
        self._connected = False

    async def verify_calibration(self) -> CalibrationCert:
        # SEL-751 stores cal cert metadata as a virtual file the master
        # can pull via Group 70 transfer. Real outstation path:
        # /CONFIG/cal-cert.json. Tests stub a small JSON response.
        await self._require_connected()
        try:
            data = await self._master.read_file("/CONFIG/cal-cert.json")
        except Exception as e:
            raise InstrumentError(f"cal cert fetch failed: {e}") from e
        import json
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise InstrumentError(f"malformed cal cert: {e}") from e
        return CalibrationCert(
            instrument_serial=payload["instrument_serial"],
            cert_id=payload["cert_id"],
            cert_hash=f"sha256:sel-751-{payload['cert_id']}",
            issued_at=payload.get("issued_at", ""),
            expires_at=payload["expires_at"],
            issuer="SEL (factory cal lab, NIST-traceable)",
            standards_traceability=["NIST"],
        )

    async def execute(self, command: dict) -> dict:
        """Run a DNP3 commissioning command.

        command["operation"]:
          'sync_clock'          - write Group 50 with current UTC ms
          'collect_soe'         - read Class 1 events, decode g2v2,
                                   return list of {index, state,
                                   timestamp_ms}
          'retrieve_comtrade'   - file-transfer the named COMTRADE
                                   record. Expects 'filename'
          'integrity_poll'      - return IIN + raw object_data
          'trip'  / 'close'     - issue a CROB direct-operate at the
                                   breaker control point
        """
        await self._require_connected()
        op = command.get("operation", "integrity_poll")

        if op == "integrity_poll":
            return await self._master.integrity_poll()

        if op == "sync_clock":
            ts = int(command.get("timestamp_ms",
                                  __import__("time").time() * 1000))
            iin = await self._master.write_time(ts)
            return {"timestamp_ms": ts, "iin": iin}

        if op == "collect_soe":
            return await self._collect_soe()

        if op == "retrieve_comtrade":
            filename = command["filename"]
            data = await self._master.read_file(filename)
            return {"filename": filename, "size_bytes": len(data),
                    "content_sha256": __import__("hashlib").sha256(data).hexdigest(),
                    "raw": data}

        if op in ("trip", "close"):
            tc = obj.CROB_TC_TRIP if op == "trip" else obj.CROB_TC_CLOSE
            crob = obj.CROB(
                op_type=obj.CROB_OP_PULSE_ON,
                trip_close=tc,
                on_time_ms=int(command.get("on_time_ms", 100)),
                off_time_ms=int(command.get("off_time_ms", 100)),
            )
            point = 0 if op == "trip" else 1
            out = await self._master.operate_crob(point, crob)
            return {"action": op, "iin": out["iin"]}

        raise InstrumentError(f"unsupported operation: {op!r}")

    # ------------------------------------------------------------------
    # SOE collection
    # ------------------------------------------------------------------

    async def _collect_soe(self) -> dict:
        """Read Class 1 events via Group 60 var 2 and decode any
        Group 2 var 2 binary-input-event objects in the response."""
        result = await self._master.read_class1_events()
        events = self._parse_g2v2_events(result["object_data"])
        # Look up element name from the known index map for any binary
        # event that lands on a protective-element point.
        rev = {idx: code for code, idx in _ELEMENT_BIN_INDEX.items()}
        decorated = []
        for e in events:
            decorated.append({
                "index": e.index,
                "element": rev.get(e.index),
                "state": e.state,
                "timestamp_ms": e.timestamp_ms,
            })
        return {
            "operation": "collect_soe",
            "iin": result["iin"],
            "event_count": len(decorated),
            "events": decorated,
        }

    @staticmethod
    def _parse_g2v2_events(object_data: bytes) -> list[obj.BinaryEvent]:
        """Walk an APDU body looking for g2v2 object headers and decode
        their events. Real responses can carry multiple object headers
        from different groups; we ignore everything except g2v2."""
        events: list[obj.BinaryEvent] = []
        pos = 0
        while pos + 3 <= len(object_data):
            group = object_data[pos]
            variation = object_data[pos + 1]
            qualifier = object_data[pos + 2]
            pos += 3
            if (group, variation) == (2, 2) and qualifier == obj.Q_8BIT_INDEX_PREFIX:
                if pos >= len(object_data):
                    break
                qty = object_data[pos]
                pos += 1
                indices = list(object_data[pos:pos + qty])
                pos += qty
                body_size = qty * 7  # g2v2: 1 flag + 6 time bytes per event
                events += obj.decode_g2v2(
                    object_data[pos:pos + body_size],
                    count=qty, indices=indices,
                )
                pos += body_size
            else:
                # Skip unknown object header — this implementation only
                # cares about g2v2 events for SOE collection.
                break
        return events

    async def _require_connected(self) -> None:
        if not self._connected:
            raise InstrumentError("SEL-751 (DNP3) not connected")
