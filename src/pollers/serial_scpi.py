"""Module 23: Serial SCPI Bench-Instrument Poller.

Reads from any test instrument that speaks SCPI over a serial link (RS-232,
RS-485, USB-CDC, USB-to-serial dongle). Covers: Vitrek 95X / V7X hipot
testers, Megger insulation testers with SCPI option, Fluke 8508A reference
DMM (over LAN-SCPI it's identical), Yokogawa power analyzers, etc.

Same shape as the Modbus / BACnet / SNMP pollers — config_context says which
SCPI commands map to which register names, the poller writes each command
and reads one line of response, decodes to a number, returns a PollResult
that flows through the same Influx + attestation pipeline.

The instrument's calibration certificate hash is recorded with every reading
so the attestation chain proves which calibrated instrument took the
measurement (matters for legal-trace audits — "this hipot result was taken
with cert XYZ valid through 2026-12-31").

pyserial is loaded lazily so unit tests don't need it. Tests inject a
``client`` matching the small SerialLike protocol below.

Config Context shape::

    {
      "protocol": "serial_scpi",
      "connection": {
        "port": "/dev/ttyUSB0",
        "baud": 9600,
        "timeout_seconds": 5,
        "newline": "\\r\\n"
      },
      "instrument": {
        "vendor": "Vitrek",
        "model": "95X",
        "serial_number": "12345",
        "calibration_cert_hash": "sha256:abcdef..."
      },
      "registers": [
        {"name": "leakage_current_ma", "scpi": ":READ:LEAK?",
         "data_type": "float", "scale": 1.0, "unit": "mA"},
        {"name": "test_voltage_kv",   "scpi": ":READ:VOLT?",
         "data_type": "float", "scale": 0.001, "unit": "kV"}
      ]
    }
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Protocol

from ..types import DeviceInfo, PollResult


class SerialLike(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def write(self, data: bytes) -> None: ...
    def readline(self) -> bytes: ...


def _default_client(host: str, port: int, baud: int, timeout_seconds: float) -> SerialLike:
    """Lazy pyserial bridge. Imported inside so tests don't need it."""
    import serial  # noqa: PLC0415
    return _PySerialAdapter(serial.Serial(port=host, baudrate=baud, timeout=timeout_seconds))


class _PySerialAdapter:
    def __init__(self, ser):
        self._ser = ser

    def open(self) -> None:
        if not self._ser.is_open:
            self._ser.open()

    def close(self) -> None:
        if self._ser.is_open:
            self._ser.close()

    def write(self, data: bytes) -> None:
        self._ser.write(data)

    def readline(self) -> bytes:
        return self._ser.readline()


_DECODERS: dict[str, Callable[[str], Any]] = {
    "float": float,
    "int": int,
    "string": str,
    "bool": lambda s: s.strip() not in {"0", "OFF", "FALSE", ""},
}


async def poll_device(
    device: DeviceInfo,
    *,
    client: SerialLike | None = None,
) -> PollResult:
    """Issue every register's SCPI command, read one line back per command,
    decode it, and return a PollResult.

    Tests inject ``client`` (a fake SerialLike). Production builds a pyserial
    adapter from the connection params.
    """
    conn = device.config_context.get("connection", {}) or {}
    port = conn.get("port", "/dev/ttyUSB0")
    baud = int(conn.get("baud", 9600))
    timeout = float(conn.get("timeout_seconds", 5))
    newline = conn.get("newline", "\r\n").encode("utf-8")

    instrument = device.config_context.get("instrument", {}) or {}
    cal_hash = instrument.get("calibration_cert_hash", "")
    instrument_id = (
        f"{instrument.get('vendor', '')}/{instrument.get('model', '')}/"
        f"{instrument.get('serial_number', '')}"
    ).strip("/")

    owns_client = client is None
    if client is None:
        client = await asyncio.to_thread(_default_client, port, port, baud, timeout)

    measurements: dict = {}
    raw_bytes: dict = {}
    errors: dict = {}

    try:
        await asyncio.to_thread(client.open)

        for reg in device.config_context.get("registers", []) or []:
            name = reg["name"]
            try:
                cmd = reg["scpi"]
                line = await asyncio.to_thread(_round_trip, client, cmd, newline)
                response = line.decode("utf-8", errors="replace").strip()
                # Pack raw bytes: SCPI command + raw response + cal cert hash
                # so the attestation chain can prove what was asked, what came
                # back, and which calibrated instrument did the measuring.
                raw_bytes[name] = (
                    f"cmd={cmd!r} resp={response!r} instrument={instrument_id} cert={cal_hash}"
                )

                decoder_name = reg.get("data_type", "float")
                decoder = _DECODERS.get(decoder_name)
                if decoder is None:
                    errors[name] = f"unknown data_type {decoder_name!r}"
                    continue
                try:
                    value = decoder(response)
                except (ValueError, TypeError) as e:
                    errors[name] = (
                        f"non-{decoder_name} response: {response!r} ({e})"
                    )
                    continue

                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    measurements[name] = value * float(reg.get("scale", 1.0))
                else:
                    measurements[name] = value
            except Exception as e:  # noqa: BLE001 — per-register isolation
                errors[name] = f"{type(e).__name__}: {e}"
    finally:
        if owns_client:
            try:
                await asyncio.to_thread(client.close)
            except Exception:  # noqa: BLE001
                pass

    return PollResult(
        device_id=device.device_id,
        timestamp_ns=time.time_ns(),
        measurements=measurements,
        raw_bytes=raw_bytes,
        protocol="serial_scpi",
        source_ip=instrument_id or device.primary_ip or "",
        success=len(errors) == 0,
        errors=errors,
    )


def _round_trip(client: SerialLike, cmd: str, newline: bytes) -> bytes:
    """Write a SCPI command and read one line back. Sync helper run via to_thread."""
    payload = cmd.encode("utf-8")
    if not payload.endswith(newline):
        payload += newline
    client.write(payload)
    return client.readline()
