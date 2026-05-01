"""Hot-plug instrument detector — the "plug it in, it just works" layer.

Watches for new instruments appearing on three transports:

  - USB serial (/dev/ttyUSB*, /dev/ttyACM*)
  - Bluetooth peripherals (BLE/SPP, advertised name match)
  - Network ranges (Modbus TCP at the configured ports)

When a new device appears, the detector:

  1. Probes it for an identity (*IDN? for SCPI; Modbus identity-block
     read for Modbus instruments).
  2. Parses (vendor, model, serial) from the response.
  3. Looks the driver up in the (vendor, model) registry.
  4. Connects via the appropriate channel (Serial / Modbus / Bluetooth).
  5. Verifies the calibration cert.
  6. Adds it to `connected` so the operator UI banner can show it
     and the test engine can dispatch tests to it.

Operator never installs anything. They plug in the Megger, the
platform shows it as "Megger MIT525 (SN-MIT-78421) — connected, cal
valid until 2026-12-31."
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol

from src.instruments import _eager_register, get_driver
from src.instruments.megger_mit525 import InstrumentError


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class DetectedInstrument:
    transport: str           # "serial" | "modbus_tcp" | "bluetooth"
    address: str             # /dev/ttyACM0 | 10.4.0.50:502 | XX:XX:XX:XX:XX:XX
    vendor: str
    model: str
    serial: str
    driver: object           # the connected driver instance
    cal_cert_id: str = ""
    cal_expires_at: str = ""
    last_seen_ts: float = field(default_factory=time.time)
    state: str = "connected"  # connected | disconnected | cal_expired | fault


# ---------------------------------------------------------------------------
# Transport scanners
# ---------------------------------------------------------------------------


class TransportScanner(Protocol):
    """Each scanner returns a list of (transport_name, address) tuples
    of new candidates since the last poll."""
    async def scan(self) -> list[tuple[str, str]]: ...


class USBSerialScanner:
    """Watch /dev for new tty devices.

    Production (Linux): pyudev for true hot-plug events. Polling the
    glob is portable and good enough for our latency budget (~1 s)."""

    DEFAULT_PATTERNS = ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/rfcomm*")

    def __init__(self, patterns: tuple[str, ...] = DEFAULT_PATTERNS):
        self._patterns = patterns
        self._seen: set[str] = set()

    async def scan(self) -> list[tuple[str, str]]:
        import glob
        current = set()
        for pat in self._patterns:
            for p in glob.glob(pat):
                current.add(p)
        new = current - self._seen
        self._seen = current
        return [("serial", path) for path in sorted(new)]

    def forget(self, address: str) -> None:
        self._seen.discard(address)


class ModbusTcpScanner:
    """Probe the configured network ranges for Modbus-TCP instruments.

    Real deployment: nmap-style port scan of the commissioning subnet.
    For a workshop scan we just check a static allowlist of (host, port)
    candidates pulled from platform.yml."""

    def __init__(self, candidates: list[tuple[str, int]]):
        self._candidates = candidates
        self._seen: set[str] = set()

    async def scan(self) -> list[tuple[str, str]]:
        new = []
        for host, port in self._candidates:
            address = f"{host}:{port}"
            if address in self._seen:
                continue
            if await self._probe(host, port):
                self._seen.add(address)
                new.append(("modbus_tcp", address))
        return new

    async def _probe(self, host: str, port: int, timeout: float = 1.0) -> bool:
        try:
            fut = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(fut, timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def forget(self, address: str) -> None:
        self._seen.discard(address)


class BLEScanner:
    """Watch for advertised BLE peripherals matching known instrument
    name prefixes."""

    def __init__(self, name_prefixes: tuple[str, ...] = ("DLRO", "MEGGER", "FLUKE")):
        self._name_prefixes = name_prefixes
        self._seen: set[str] = set()

    async def scan(self) -> list[tuple[str, str]]:
        try:
            from bleak import BleakScanner
        except ImportError:
            return []
        try:
            devices = await BleakScanner.discover(timeout=2.0)
        except Exception:
            return []
        new = []
        for d in devices:
            name = (d.name or "")
            if not any(name.upper().startswith(p) for p in self._name_prefixes):
                continue
            if d.address in self._seen:
                continue
            self._seen.add(d.address)
            new.append(("bluetooth", d.address))
        return new

    def forget(self, address: str) -> None:
        self._seen.discard(address)


# ---------------------------------------------------------------------------
# Identity probing — figure out vendor/model from a candidate
# ---------------------------------------------------------------------------


async def probe_serial_identity(address: str,
                                 channel_factory: Callable | None = None
                                 ) -> Optional[tuple[str, str, str]]:
    """Send `*IDN?` over a freshly-opened serial channel; parse the
    response into (vendor, model, serial)."""
    from src.instruments.channels import (
        PySerialAsyncSerialChannel, SerialPortConfig,
    )
    factory = channel_factory or (
        lambda addr: PySerialAsyncSerialChannel(SerialPortConfig(port=addr))
    )
    ch = factory(address)
    try:
        await ch.open()
        await ch.write_line("*IDN?")
        try:
            resp = await ch.read_line(timeout_s=2.0)
        except asyncio.TimeoutError:
            return None
        return _parse_idn(resp)
    except InstrumentError:
        return None
    finally:
        try:
            await ch.close()
        except Exception:
            pass


async def probe_modbus_identity(address: str,
                                 client_factory: Callable | None = None
                                 ) -> Optional[tuple[str, str, str]]:
    """Modbus instruments don't have a universal identity command; we
    try a list of vendor-specific identity blocks. First match wins.

    For each known vendor, we know which holding-register block carries
    the model + serial. If the block contains an ASCII string matching
    a known vendor pattern, we accept it."""
    host, _, port_s = address.partition(":")
    try:
        port = int(port_s) if port_s else 502
    except ValueError:
        return None

    from src.instruments.channels import (
        ModbusEndpoint, PymodbusTcpClient,
    )
    factory = client_factory or (
        lambda h, p: PymodbusTcpClient(ModbusEndpoint(host=h, port=p))
    )

    # (vendor, model, slave_id, identity_address, n_regs, parser)
    probes = [
        ("Qualitrol", "118ITM", 1, 40067, 4,
         lambda r: ("Qualitrol", "118ITM", _regs_to_ascii(r))),
        ("Vaisala", "OPT100", 240, 40005, 8,
         lambda r: ("Vaisala", "OPT100", _regs_to_ascii(r))),
        ("SEL", "SEL-751", 1, 40022, 8,
         lambda r: ("SEL", "SEL-751", _regs_to_ascii(r))),
        ("Caterpillar", "EMCP4.4", 1, 40009, 8,
         lambda r: ("Caterpillar", "EMCP4.4", _regs_to_ascii(r))),
    ]
    client = factory(host, port)
    try:
        await client.connect()
    except Exception:
        return None
    try:
        for vendor, model, slave, addr, n, parse in probes:
            try:
                regs = await client.read_holding_registers(addr, n, slave=slave)
            except Exception:
                continue
            try:
                v, m, serial = parse(regs)
            except Exception:
                continue
            if serial:
                return (v, m, serial)
        return None
    finally:
        try:
            await client.close()
        except Exception:
            pass


_IDN_RE = re.compile(r"^([^,]+),([^,]+),([^,]+)")


def _parse_idn(resp: str) -> Optional[tuple[str, str, str]]:
    """SCPI *IDN? format: '<vendor>,<model>,<serial>,<firmware>'."""
    m = _IDN_RE.match(resp.strip())
    if not m:
        return None
    return (m.group(1).strip(), m.group(2).strip(), m.group(3).strip())


def _regs_to_ascii(regs: list[int]) -> str:
    import struct
    b = b""
    for r in regs:
        b += struct.pack(">H", r & 0xFFFF)
    return b.rstrip(b"\x00 ").decode("ascii", errors="replace")


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------


class InstrumentDetector:
    """Top-level coordinator. Holds scanners, probes new candidates,
    looks up drivers from the registry, connects them, exposes the
    list of currently connected instruments.

    Operator-facing endpoint /api/v1/instruments/connected reads from
    `connected`. The test engine looks up instruments here when a test
    needs to know which physical box implements (vendor, model)."""

    def __init__(self, scanners: list[TransportScanner],
                 *, identity_probes: dict[str, Callable] | None = None):
        self._scanners = scanners
        self._connected: dict[str, DetectedInstrument] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # identity_probes maps transport → async fn(address) → (vendor, model, serial)
        self._identity_probes = identity_probes or {
            "serial": probe_serial_identity,
            "modbus_tcp": probe_modbus_identity,
        }
        # Eagerly load all driver classes so the registry is populated
        _eager_register()

    @property
    def connected(self) -> list[DetectedInstrument]:
        return list(self._connected.values())

    def get_by_vendor_model(self, vendor: str, model: str) -> Optional[DetectedInstrument]:
        for inst in self._connected.values():
            if inst.vendor.lower() == vendor.lower() and inst.model.lower() == model.lower():
                return inst
        return None

    async def start(self, scan_interval_s: float = 2.0) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(scan_interval_s))

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
        # Disconnect everything
        async with self._lock:
            for inst in self._connected.values():
                try:
                    await inst.driver.disconnect()
                except Exception:
                    pass
            self._connected.clear()

    async def scan_once(self) -> list[DetectedInstrument]:
        """Run one detection pass. Returns newly-added instruments."""
        added: list[DetectedInstrument] = []
        for scanner in self._scanners:
            try:
                candidates = await scanner.scan()
            except Exception:
                continue
            for transport, address in candidates:
                inst = await self._handle_candidate(transport, address)
                if inst:
                    added.append(inst)
        return added

    async def _loop(self, interval_s: float) -> None:
        while not self._stop.is_set():
            try:
                await self.scan_once()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass

    async def _handle_candidate(self, transport: str,
                                 address: str) -> Optional[DetectedInstrument]:
        if address in self._connected:
            self._connected[address].last_seen_ts = time.time()
            return None
        probe = self._identity_probes.get(transport)
        if probe is None:
            return None
        identity = await probe(address)
        if identity is None:
            return None
        vendor, model, serial = identity
        driver_cls = get_driver(vendor, model)
        if driver_cls is None:
            return None
        try:
            driver = await self._construct_and_connect(
                driver_cls, transport, address,
            )
        except Exception:
            return None
        cal_id = cal_exp = ""
        try:
            cert = await driver.verify_calibration()
            cal_id = cert.cert_id
            cal_exp = cert.expires_at
        except Exception:
            pass
        inst = DetectedInstrument(
            transport=transport, address=address,
            vendor=vendor, model=model, serial=serial,
            driver=driver, cal_cert_id=cal_id, cal_expires_at=cal_exp,
        )
        async with self._lock:
            self._connected[address] = inst
        return inst

    async def _construct_and_connect(self, driver_cls, transport: str,
                                      address: str):
        """Build the channel for this transport, instantiate the driver
        with it, and call connect()."""
        from src.instruments.channels import (
            BleakBluetoothChannel, BluetoothPeripheral,
            ModbusEndpoint, PymodbusTcpClient,
            PySerialAsyncSerialChannel, SerialPortConfig,
        )
        if transport == "serial":
            ch = PySerialAsyncSerialChannel(SerialPortConfig(port=address))
            await ch.open()
            driver = driver_cls(ch)
        elif transport == "modbus_tcp":
            host, _, port_s = address.partition(":")
            port = int(port_s) if port_s else 502
            client = PymodbusTcpClient(ModbusEndpoint(host=host, port=port))
            driver = driver_cls(client)
        elif transport == "bluetooth":
            # Driver tells us its expected GATT characteristic UUIDs
            uuids = getattr(driver_cls, "BLE_UUIDS", {})
            peripheral = BluetoothPeripheral(
                address=address,
                char_write_uuid=uuids.get("write", ""),
                char_notify_uuid=uuids.get("notify", ""),
            )
            ch = BleakBluetoothChannel(peripheral)
            await ch.open()
            driver = driver_cls(ch)
        else:
            raise InstrumentError(f"unknown transport: {transport}")

        await driver.connect()
        return driver
