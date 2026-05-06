# Commissioning Platform — Coding Spec v2

Engineering specification for the commissioning platform codebase. Supersedes v1 / v5 PDF specs in `docs/`. Documents the architecture as it stands at branch `claude/retrieve-previous-code-uAXPT` HEAD `fe84ca7`.

This is an engineering reference, not a product document. Reader audience: contributors implementing or extending platform modules.

---

## 1. Module Map

```
src/
  __init__.py
  api/
    server.py                  FastAPI app, endpoint surface (§11)
  attestation.py               HMAC-chained AttestationEngine
  auth.py                      JWT (HS256), Role enum, permissions matrix
  bim_import.py                IFC ingest → equipment manifest
  compliance/
    cal_certs.py               CalCert + CertVerifier
    audit_review.py            AuditQuery + AuditPage paging
    retention.py               RetentionPolicy enforcement
    certificate_gen.py         HMAC-signed PDF certificates
  config.py                    DemoConfig + facility metadata
  config_loader.py             load_device_type_configs(),
                               merge_instance_overrides()
  connectors/                  External integration adapters
  discovery.py                 Subnet sweep + identity classifier
  equipment_loader.py          Re-exports config_loader for back-compat
  equipment_views.py           Category dispatch (§9)
  evidence_store.py            EvidenceStore Protocol + InMemoryEvidenceStore
  influx_writer.py             Optional time-series sink
  instruments/                 Driver registry + per-driver impl (§3)
    __init__.py                _DRIVERS registry, register(), get_driver()
    channels.py                Transport abstractions
    detector.py                InstrumentDetector + scanners
    dnp3/                      DNP3 stack (§5)
      __init__.py
      datalink.py              Frame + CRC-16-DNP
      transport.py             Segmenter + Reassembler
      application.py           Request / Response / IIN
      objects.py               Object groups 1, 2, 12, 30, 50, 70
      master.py                DNP3Master high-level operations
    megger_mit525.py           5 kV megohmmeter (SCPI/serial)
    megger_dlro10x.py          DLRO low-resistance ohmmeter
    megger_tm1800.py           Breaker analyzer (SCPI/serial)
    vitrek_95x.py              Hipot test set (SCPI)
    qualitrol_118itm.py        DGA monitor (Modbus TCP)
    vaisala_opt100.py          Moisture/PD monitor (Modbus TCP)
    sel_751.py                 SEL-751 over Modbus TCP
    sel_751_dnp3.py            SEL-751 over DNP3 (registered transport=dnp3)
    doble_f6150.py             Relay test set (TCP/SCPI)
    cat_emcp.py                CAT EMCP4.4 generator controller
  main.py                      Production app entrypoint
  middleware/
    rate_limit.py              Token-bucket per-key limiting
    audit_log.py               Per-request structured audit
  netbox_reader.py             Read inventory from NetBox
  observability/
    structured_logging.py      JSONFormatter, configure(), trace IDs
    metrics.py                 Counter / Gauge / Histogram (Prometheus exposition)
    health.py                  HealthChecker + HealthCheckResult
  orchestrator.py              Test scheduling
  pdf_pipeline.py              OEM PDF ingest → metadata extraction
  pollers/                     Polling workers
  reconciliation.py            Cross-reading + sensor-drift detection
  reports.py                   Multi-test PDF report generation
  storage/
    minio_evidence_store.py    EvidenceStore Protocol over MinIO
    influx_test_results.py     Test-run + test-metric writer
  test_engine.py               Live-test execution + SSE event stream
  test_executor.py             Generic dispatch on test_def["type"] (§7)
  topology/
    dynamic_sld.py             Layered-DAG SLD layout
  types.py                     Common dataclasses
  workers/                     Background workers

config/
  equipment/
    *.json                     Device-type Config Contexts (§8)

tests/
  dnp3/                        Per-layer DNP3 tests
  instruments/                 Per-driver recorded-trace tests
  unit/                        Module unit tests
  integration/                 Full pipeline tests
  hardware/                    Gated on env vars (HW_*_IP)

dev_server.py                  Demo facility builder + seed pipeline
frontend/                      React + Vite tablet UI
```

---

## 2. Coding Conventions

- **Async-first.** All I/O paths use `async def`. Sync wrappers exist only at module entrypoints (CLI, tests).
- **Protocol typing.** Cross-module dependencies declared as `typing.Protocol`. Concrete implementations injected at construction. Avoids tight coupling and makes recorded-trace testing trivial.
- **Dataclasses for value types.** No bare dicts crossing module boundaries. `@dataclass` (or `@dataclasses.dataclass(frozen=True)` where mutation is illegal).
- **No per-product Python.** Per-device-type behaviour lives in `config/equipment/<slug>.json`. Adding a product is a JSON drop, not a class file. The deleted `src/equipment_models/` is not coming back.
- **No comments explaining what.** Comments only for non-obvious *why*: hidden constraint, subtle invariant, workaround, surprising behaviour. Identifiers carry the *what*.
- **Recorded-trace tests for hardware-bound code.** Every driver test scripts `(write, expected_response)` pairs through a `RecordedSerialChannel` / `RecordedTcpChannel` / `FakeGatt` / `FakeLinkChannel` fake. Tests fail loudly when the driver issues an unexpected command, catching protocol drift between code and OEM manual.
- **Spec citations in result records.** Every `TestResult` carries `spec_reference` (e.g., `"NETA ATS-17 §7.10 / IEEE C37.233 §6.3"`). The reference travels with the evidence into the signed PDF and the attestation chain.
- **No emoji in code.** No emoji in PR descriptions, commits, comments, or strings unless the user explicitly asks.
- **Test count is non-negotiable.** Every commit must keep the suite green. Hardware tests skip on missing env vars; everything else passes locally without external dependencies.

---

## 3. Driver Registry

`src/instruments/__init__.py` is the registry.

### 3.1 Schema

```python
_DRIVERS: dict[tuple[str, str, str | None], type] = {}
```

The key is `(vendor.lower(), model.lower(), transport.lower() | None)`. The same physical device (e.g., SEL-751) may register multiple driver classes, one per protocol the operator might use to talk to it.

### 3.2 Decorator

```python
def register(driver_cls: type | None = None, *, transport: str | None = None):
    """Bare decorator (`@register`) → transport=None.
       Parametric (`@register(transport="dnp3")`) → keyed by transport.

       The driver class may also declare a class attribute TRANSPORT
       which takes precedence over the decorator argument."""
```

### 3.3 Lookup

```python
def get_driver(vendor: str, model: str,
               transport: str | None = None) -> type | None:
    """If transport is given, returns the exact match.
       If None, returns the (vendor, model, None) entry first,
       then falls back to any registered (vendor, model, *) entry."""
```

### 3.4 Driver protocol

Every driver implements:

```python
class XYZDriver:
    VENDOR: str
    MODEL: str
    TRANSPORT: str | None  # optional class attribute

    def __init__(self, channel, config=None): ...

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def execute(self, command: dict) -> dict: ...
    async def verify_calibration(self) -> CalibrationCert: ...
```

`CalibrationCert` is the dataclass in `src/instruments/__init__.py`:

```python
@dataclass
class CalibrationCert:
    instrument_serial: str
    cert_id: str
    cert_hash: str          # 'sha256:<vendor>-<model>-<cert_id>'
    issued_at: str          # ISO 8601 or ''
    expires_at: str         # ISO 8601
    issuer: str
    standards_traceability: list[str]   # e.g., ['NIST', 'UKAS']
```

### 3.5 Eager registration

`_eager_register()` imports every driver module so its `@register` decorator runs. Tests that only need registry types do not import this; tests that exercise lookups do.

Currently registered drivers (transport in parens):

| Vendor | Model | Transports |
|---|---|---|
| Megger | MIT525 | None (serial) |
| Megger | DLRO10X | None (serial) |
| Megger | TM1800 | None (serial) |
| Vitrek | 95X | None (TCP/SCPI) |
| Qualitrol | 118ITM | None (modbus_tcp) |
| Vaisala | OPT100 | None (modbus_tcp) |
| SEL | SEL-751 | None (modbus_tcp) |
| SEL | SEL-751 | dnp3 |
| Doble | F6150e | None (TCP/SCPI) |
| Caterpillar | EMCP4.4 | None (modbus_tcp) |

---

## 4. Channel Abstractions

`src/instruments/channels.py` defines transport-layer Protocols. Drivers depend on the Protocol, not on a concrete library.

### 4.1 Serial / SCPI

```python
class SerialChannel(Protocol):
    async def write_line(self, line: str) -> None: ...
    async def read_line(self, timeout_s: float = 5.0) -> str: ...
```

Production: `pyserial-asyncio` wrapping `/dev/ttyACMn`.
Tests: `RecordedSerialChannel` (scripted `(expected_write, response)` deque).

### 4.2 TCP ASCII / SCPI-over-IP

```python
class TcpAsciiChannel(Protocol):
    async def write_line(self, line: str) -> None: ...
    async def read_line(self, timeout_s: float = 5.0) -> str: ...
```

Production: `asyncio.open_connection(host, port)` + line buffering.
Tests: `RecordedTcpChannel` (same shape as serial).

### 4.3 Modbus TCP

```python
class ModbusClient(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def read_input_registers(self, address: int, count: int,
                                    slave: int = 1) -> list[int]: ...
    async def read_holding_registers(self, address: int, count: int,
                                      slave: int = 1) -> list[int]: ...
    async def write_coil(self, address: int, value: bool,
                          slave: int = 1) -> None: ...
```

Production: `pymodbus`.
Tests: `RecordedModbusClient` from `tests/instruments/test_modbus_drivers.py`.

### 4.4 BLE GATT

```python
class GattCharacteristic(Protocol):
    async def read(self) -> bytes: ...
    async def subscribe(self, callback) -> None: ...
    async def unsubscribe(self) -> None: ...

class GattClient(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def characteristic(self, uuid: str) -> GattCharacteristic: ...
```

Production: `bleak`.
Tests: `FakeGatt` with mock characteristics.

### 4.5 DNP3 link

```python
class LinkChannel(Protocol):
    async def write_bytes(self, data: bytes) -> None: ...
    async def read_bytes(self, n: int, timeout_s: float) -> bytes: ...
    async def close(self) -> None: ...
```

Production: `asyncio.open_connection(host, 20000)`.
Tests: `FakeLinkChannel` (round-trips through `dl.encode` / `dl.decode`).

---

## 5. DNP3 Stack

Implements IEEE Std 1815-2012. Layered to match the protocol; each layer is independently testable.

### 5.1 Data link layer (`dnp3/datalink.py`)

#### Frame

```
Bytes 0-1   : Start sentinel 0x05 0x64
Byte 2      : Length (count from byte 3 through last user-data byte
              exclusive of CRCs, max 255)
Byte 3      : Control octet (DIR, PRM, FCB, FCV, FUNCTION_CODE)
Bytes 4-5   : Destination address (little-endian)
Bytes 6-7   : Source address (little-endian)
Bytes 8-9   : Header CRC over bytes 0-7

Then up to 16 user-data blocks. Each block is up to 16 bytes payload
followed by a 2-byte CRC over that block.
```

#### CRC

CRC-16-DNP, polynomial `0x3D65`, init `0x0000`, reflected, XOR-out `0xFFFF`. Reference vector: header `0x05 0x64 0x05 0xC0 0x01 0x00 0x00 0x04` → CRC `0x21E9` (per IEEE 1815-2012 §9.2.4.5).

#### API

```python
crc16(data: bytes) -> int
crc16_check(data: bytes, expected: int) -> bool

encode_blocks(user_data: bytes) -> bytes    # max 250 bytes payload
decode_blocks(blocks: bytes) -> bytes       # raises on CRC mismatch

@dataclass
class LinkFrame:
    function_code: int
    dest_address: int
    source_address: int
    user_data: bytes = b""
    direction: int = 1   # 1 = master→outstation
    primary: int = 1     # 1 = request
    fcb: int = 0
    fcv: int = 0

encode(frame: LinkFrame) -> bytes
decode(buf: bytes) -> tuple[LinkFrame, int]   # bytes consumed
encoded_size(user_data_len: int) -> int

# Function code constants
PRI_RESET_LINK_STATES = 0x00
PRI_TEST_LINK_STATES = 0x02
PRI_CONFIRMED_USER_DATA = 0x03
PRI_UNCONFIRMED_USER_DATA = 0x04
PRI_REQUEST_LINK_STATUS = 0x09
SEC_ACK = 0x00
SEC_NACK = 0x01
SEC_LINK_STATUS = 0x0B
SEC_NOT_SUPPORTED = 0x0F
```

### 5.2 Transport layer (`dnp3/transport.py`)

1-byte header per segment: bit 7 = FIN, bit 6 = FIR, bits 5..0 = sequence (mod 64).

APDU max 2048 bytes. Segment payload max 249 bytes (link payload 250 - 1-byte header).

```python
class TransportSegmenter:
    def segment(self, apdu: bytes) -> list[bytes]: ...

class TransportReassembler:
    def feed(self, segment: bytes) -> bytes | None:
        """Returns the reassembled APDU when FIN=1; else None."""
```

`TransportError` raised on: empty segment, oversize segment, oversize APDU, non-FIR with no APDU in progress, out-of-order sequence.

### 5.3 Application layer (`dnp3/application.py`)

#### APDU shape

```
Request:
  Byte 0   : Application control (FIR | FIN | CON | UNS | SEQ)
  Byte 1   : Function code
  Bytes 2+ : Object headers + objects

Response:
  Byte 0   : Application control
  Byte 1   : Function code (FC_RESPONSE = 0x81 / FC_UNSOLICITED_RESPONSE = 0x82)
  Bytes 2-3: IIN (little-endian)
  Bytes 4+ : Object data
```

#### Function codes (subset)

```
FC_CONFIRM = 0x00
FC_READ = 0x01
FC_WRITE = 0x02
FC_DIRECT_OPERATE = 0x05
FC_DIRECT_OPERATE_NR = 0x06
FC_ENABLE_UNSOLICITED = 0x14
FC_DISABLE_UNSOLICITED = 0x15
FC_DELAY_MEASURE = 0x17
FC_RECORD_CURRENT_TIME = 0x18
FC_OPEN_FILE = 0x19
FC_CLOSE_FILE = 0x1A
FC_RESPONSE = 0x81
FC_UNSOLICITED_RESPONSE = 0x82
```

#### IIN flags (`class IIN`)

```
BROADCAST, CLASS_1_EVENTS, CLASS_2_EVENTS, CLASS_3_EVENTS,
NEED_TIME, LOCAL_CONTROL, DEVICE_TROUBLE, DEVICE_RESTART,
NO_FUNC_CODE_SUPPORT, OBJECT_UNKNOWN, PARAMETER_ERROR,
EVENT_BUFFER_OVERFLOW, ALREADY_EXECUTING, CONFIG_CORRUPT
```

#### Types

```python
@dataclass
class ApplicationControl:
    fir: bool = True
    fin: bool = True
    con: bool = False
    uns: bool = False
    sequence: int = 0

@dataclass
class Request:
    function_code: int
    application_control: ApplicationControl
    object_data: bytes = b""

@dataclass
class Response:
    function_code: int
    application_control: ApplicationControl
    iin: int = 0
    object_data: bytes = b""

    def has_iin(self, bit: int) -> bool: ...
    @classmethod
    def decode(cls, apdu: bytes) -> "Response": ...

iin_decode(iin: int) -> dict[str, bool]
```

### 5.4 Object groups (`dnp3/objects.py`)

#### Qualifier codes

```
Q_8BIT_START_STOP = 0x00
Q_16BIT_START_STOP = 0x01
Q_ALL_OBJECTS = 0x06
Q_8BIT_LIMITED_QTY = 0x07
Q_16BIT_LIMITED_QTY = 0x08
Q_8BIT_INDEX_PREFIX = 0x17
Q_16BIT_INDEX_PREFIX = 0x28
```

#### Group 1 — Binary Input

`g1v2`: 1 byte per point (bit 7 = state, lower bits = quality flags per IEEE 1815 Table 11-7).

```python
encode_g1v2(points: list[dict]) -> bytes
decode_g1v2(payload: bytes, count: int) -> list[dict]
```

#### Group 2 — Binary Input Event

`g2v2`: 1 byte flags + 6 bytes 48-bit UTC ms timestamp per event.

```python
@dataclass
class BinaryEvent:
    index: int
    state: bool
    online: bool = True
    timestamp_ms: int | None = None

encode_g2v2(events: list[BinaryEvent]) -> bytes
decode_g2v2(payload: bytes, count: int, indices: list[int]) -> list[BinaryEvent]
```

#### Group 12 — CROB

```python
CROB_OP_NUL, CROB_OP_PULSE_ON, CROB_OP_PULSE_OFF,
CROB_OP_LATCH_ON, CROB_OP_LATCH_OFF
CROB_TC_CLOSE = 0x40
CROB_TC_TRIP  = 0x80

@dataclass
class CROB:
    op_type: int
    trip_close: int = 0
    count: int = 1
    on_time_ms: int = 100
    off_time_ms: int = 100
    status: int = 0

encode_g12v1(c: CROB) -> bytes      # 11 bytes
decode_g12v1(payload: bytes) -> CROB
```

#### Group 30 — Analog Input (float + flag)

```python
encode_g30v5(points: list[dict]) -> bytes      # 5 bytes per point
decode_g30v5(payload: bytes, count: int) -> list[dict]
```

#### Group 50 — Time and Date

```python
encode_g50v1(timestamp_ms: int) -> bytes       # 6-byte 48-bit
decode_g50v1(payload: bytes) -> int
```

#### Group 70 — File transfer

```python
@dataclass
class FileCommand:           # g70v3
    filename: str
    file_size: int = 0
    permissions: int = 0
    auth_key: int = 0
    file_mode: int = FILE_MODE_READ
    max_block_size: int = 1024
    request_id: int = 0
    operational_mode: int = FILE_OP_OPEN
    file_type: int = 0

@dataclass
class FileCommandStatus:     # g70v4
    handle: int
    file_size: int
    max_block_size: int
    request_id: int
    status: int
    text: str = ""

@dataclass
class FileTransport:         # g70v5
    handle: int
    block_number: int        # bit 31 = EOF flag
    file_data: bytes

    @property
    def is_last(self) -> bool: ...
    @property
    def sequence(self) -> int: ...
```

### 5.5 Master (`dnp3/master.py`)

```python
@dataclass
class DNP3MasterConfig:
    master_address: int = 1
    outstation_address: int = 4
    response_timeout_s: float = 5.0
    file_block_size: int = 1024

class DNP3Master:
    def __init__(self, channel: LinkChannel,
                 config: DNP3MasterConfig | None = None): ...

    async def integrity_poll(self) -> dict
    async def read_class1_events(self) -> dict
    async def write_time(self, timestamp_ms: int) -> int
    async def operate_crob(self, point_index: int, crob: CROB) -> dict
    async def open_file(self, filename: str) -> FileCommandStatus
    async def read_file_block(self, handle: int) -> FileTransport
    async def close_file(self, handle: int) -> int
    async def read_file(self, filename: str) -> bytes
    async def disconnect(self) -> None
```

The master holds the application-layer mod-16 sequence counter; each
`_request_response` allocates the next sequence and waits for a matching
response. Out-of-order or stale-sequence responses are skipped. The
file-read convenience method does open → loop blocks until `is_last` →
close.

---

## 6. Hot-plug Detector

`src/instruments/detector.py`. Background loop that finds new candidate addresses, runs identity probes, looks up drivers, constructs and connects.

### 6.1 Scanner protocol

```python
class Scanner(Protocol):
    async def scan(self) -> list[tuple[str, str]]:
        """Returns [(transport, address), ...] for newly-seen candidates."""
```

Built-in scanners:

- `USBSerialScanner(patterns=("/dev/ttyACM*", "/dev/ttyUSB*"))`
- `ModbusTcpScanner(targets=[(host, port), ...])`
- `BLEScanner(service_uuid="...")`

Each scanner remembers candidates it has emitted; a `forget(address)` method exists for testing re-detection.

### 6.2 Identity probe

```python
IdentityProbe = Callable[[str], Awaitable[tuple[str, str, str] | None]]
# returns (vendor, model, serial) or None if the device doesn't identify
```

Identity probes are passed in as a `dict[transport, IdentityProbe]`.

### 6.3 Detector

```python
class InstrumentDetector:
    def __init__(self, scanners: list[Scanner],
                 identity_probes: dict[str, IdentityProbe]): ...

    async def scan_once(self) -> list[DetectedInstrument]
    async def start(self, scan_interval_s: float = 2.0) -> None
    async def stop(self) -> None

    @property
    def connected(self) -> list[DetectedInstrument]
    def get_by_vendor_model(self, vendor: str, model: str) -> DetectedInstrument | None
```

Each detected instrument carries `(transport, address, vendor, model, serial, driver, last_seen_ts, cal_cert_id, cal_expires_at)`.

---

## 7. Test Executor

`src/test_executor.py`. Single dispatch table on `test_def["type"]`.

### 7.1 Public entry point

```python
def execute_test(*, device_id: str, config: dict, test_def: dict,
                 run_date: datetime,
                 cross_refs: dict[str, Any] | None = None) -> dict:
    """Run one test definition against a Config Context. Returns a
    TestResult-shaped dict (see §7.3). Raises KeyError on unknown
    test_type."""
```

### 7.2 Dispatch table

```python
_DISPATCH = {
    "dc_withstand":              _dc_withstand,
    "insulation_resistance":     _insulation_resistance,
    "joint_resistance_dlro":     _joint_resistance_dlro,
    "dissolved_gas_analysis":    _dissolved_gas_analysis,
    "transformer_turns_ratio":   _transformer_turns_ratio,
    "polarization_index":        _polarization_index,
    "transformer_hipot_history": _transformer_hipot_history,
    "ups_battery_transfer":      _ups_battery_transfer,
    "generator_loadbank":        _generator_loadbank,
    "ats_transfer_sequence":     _ats_transfer_sequence,
    "sel_secondary_injection":   _sel_secondary_injection,
    "sel_primary_injection":     _sel_primary_injection,
    "relay_soe_collection":      _relay_soe_collection,
    "relay_comtrade_retrieval":  _relay_comtrade_retrieval,
    "breaker_timing":            _breaker_timing,
}

SUPPORTED_TEST_TYPES = list(_DISPATCH.keys())
```

### 7.3 Common result envelope

Every handler returns at minimum:

```python
{
    "device_id": str,
    "test_name": str,             # from test_def["name"]
    "test_type": str,
    "spec_reference": str,
    "manufacturer": str,
    "model": str,
    "instrument": dict,           # from test_def["instrument"]
    "passed": bool,
    "completed_at": str,          # ISO 8601
    # ... type-specific fields
}
```

Type-specific fields documented in §10 (Evidence record schemas).

### 7.4 Helpers

```python
_age_years(config: dict, run_date: datetime) -> float
_seed(device_id: str, run_date: datetime, salt: int = 0) -> random.Random
_insulation_megohm(config: dict, run_date: datetime) -> float
_instrument_block(test_def: dict) -> dict
_fault_progress(config: dict, run_date: datetime) -> float
_gas_levels(config: dict, run_date: datetime) -> dict
_dga_history(config: dict, run_date: datetime, weeks: int = 14) -> list
_gen_build_time(gen_config: dict, run_date: datetime) -> tuple[float, int]
```

The relay-injection handlers `_sel_secondary_injection` and `_sel_primary_injection` are produced by the factory `_relay_injection_handler(injection_kind)` — both share the same logic, differing only by salt seed and result `injection_kind` field.

### 7.5 Cross-references

`cross_refs` is a dict mapping logical roles to other devices' Config Contexts. Used by handlers that span devices:

- `_ats_transfer_sequence` consumes `cross_refs["upstream_gen"]` and `cross_refs["downstream_ups"]: list`.

Resolved upstream by `_resolve_effective_configs()` in `dev_server.py` from each device's `cross_refs` block.

### 7.6 Manual sign-off renderer

```python
def render_manual_signoffs(config: dict, run_date: datetime) -> dict:
    """Iterates config['manual_signoffs'] and produces the operator-side
    signoff record. Used by the busway aggregator and the new relay
    aggregator."""
```

---

## 8. Config Context Schema

`config/equipment/<slug>.json`. One JSON file per device-type. Loaded at startup by `config_loader.load_device_type_configs()`. Per-device-instance values flow in via `merge_instance_overrides()` which deep-merges into `ageing_model.params` and `ratings`.

### 8.1 Top-level schema

```json
{
  "device_type_slug": "string",        // matches filename without .json
  "manufacturer": "string",
  "model": "string",
  "category": "cable | transformer | generator | ups | ats | busway | relay | circuit_breaker",
  "ratings":      { ... },             // category-specific (§8.3)
  "ageing_model": { "kind": "string", "params": { ... } },
  "active_tests": [ ... ],             // §8.4
  "manual_signoffs": [ ... ],          // §8.5
  "panel": { ... }                     // §9.3 panel.layout DSL
}
```

### 8.2 Categories and aggregators

Each category has a corresponding aggregator in `equipment_views._CATEGORY_DISPATCH`. Adding a new category requires:

1. A new entry in `_CATEGORY_DISPATCH` mapping to a `*_panel(device_id, store)` aggregator
2. (Optional) URL-kind alias in `URL_KIND_TO_CATEGORY`

Currently registered: cable, transformer, generator, ups, ats, busway, relay, circuit_breaker.

### 8.3 Per-category required ratings

```
cable:           rated_kv, length_m_default, voltage_class_v
transformer:     primary_kv, secondary_v, vector_group, rated_kva
generator:       rated_kw, voltage_v, nominal_rpm, nominal_freq_hz
ups:             cells_count, cell_nominal_v, cell_weak_threshold_v,
                 nominal_v, rated_kw
ats:             rated_amps, voltage_class_v
busway:          rated_amps, voltage_class_v
relay:           voltage_class_v, ct_ratio, vt_ratio,
                 relay_elements: [{
                   code, function, pickup_a | null, pickup_v?,
                   pickup_hz_low?, pickup_hz_high?,
                   curve_kind?, td?
                 }]
circuit_breaker: rated_amps, voltage_class_v, interrupt_kA,
                 nominal_open_time_ms, nominal_close_time_ms,
                 nominal_spring_charge_s,
                 nominal_stroke_mm,
                 nominal_trip_coil_a, nominal_close_coil_a
```

### 8.4 active_tests entry

```json
{
  "name": "string",                    // unique within the device type
  "type": "string",                    // dispatch key in _DISPATCH
  "spec_reference": "string",          // travels with evidence
  "instrument": {
    "vendor": "string",
    "model": "string",
    "transport": "string?",            // optional, selects driver registry transport
    "serial": "string?",
    "cal_cert": "string?"
  },
  "parameters": { ... },               // type-specific
  "acceptance":  { ... }               // type-specific
}
```

#### Per-type parameters / acceptance

```
dc_withstand:
  parameters: target_kv | (test_factor + test_kind), ramp_seconds,
              hold_seconds, test_kind ('fixed_kv' | 'ieee_400_maintenance')
  acceptance: max_leakage_ma

insulation_resistance:
  parameters: test_voltage_v, applied_seconds, phase_pairs?
  acceptance: min_megohm

joint_resistance_dlro:
  parameters: test_current_a, joints_per_meter?, end_terminations?, phases?
  acceptance: max_uohm_per_joint

dissolved_gas_analysis:
  parameters: history_weeks
  acceptance: max_c2h2_ppm

transformer_turns_ratio:
  parameters: taps[], phases[]
  acceptance: max_deviation_pct

polarization_index:
  parameters: test_voltage_v, duration_minutes
  acceptance: min_pi

transformer_hipot_history:
  parameters: test_factor, duration_seconds
  acceptance: max_leakage_ma

ups_battery_transfer:
  parameters: waveform_seconds, sample_period_ms
  acceptance: min_voltage_v, max_switchover_ms, max_weak_cells

generator_loadbank:
  parameters: steps[]                  // load fractions, e.g. [0.25, 0.5, 0.75, 1.0]
  acceptance: max_ready_signal_seconds

ats_transfer_sequence:
  parameters: (none beyond cross_refs)
  acceptance: max_transfer_seconds

sel_secondary_injection / sel_primary_injection:
  parameters: elements: [{code, pickup_a, curve_kind, td, multiples[]}]
  acceptance: pickup_tolerance_pct, timing_tolerance_pct,
              min_timing_tolerance_ms

relay_soe_collection:
  parameters: expected_elements[]
  acceptance: max_relative_skew_ms

relay_comtrade_retrieval:
  parameters: filename?, samples_per_cycle, capture_cycles
  acceptance: min_total_samples

breaker_timing:
  parameters: mode ('open' | 'close' | 'open_close' | 'close_open'),
              include_motion?, include_spring?, include_coils?
  acceptance: timing_tolerance_pct,
              max_open_simultaneity_ms, max_close_simultaneity_ms,
              max_spring_charge_s
```

### 8.5 manual_signoffs

```json
[
  {
    "name": "string",                  // unique within device
    "label": "string",                 // operator-facing
    "spec_reference": "string"
  }
]
```

Rendered by `render_manual_signoffs()` and shown in the panel layout's manual-signoff card.

---

## 9. Equipment Views

`src/equipment_views.py`. Aggregates evidence-store records for a device into a category-specific panel response. Frontend consumes the `panel_layout` block to render generically.

### 9.1 Category dispatch

```python
_CATEGORY_DISPATCH = {
    "cable":           cable_panel,
    "transformer":     transformer_panel,
    "generator":       generator_panel,
    "ups":             ups_panel,
    "ats":             ats_panel,
    "busway":          busway_panel,
    "relay":           relay_panel,
    "circuit_breaker": circuit_breaker_panel,
}
```

### 9.2 Aggregator contract

```python
def category_panel(device_id: str, store: EvidenceStore) -> dict:
    """Read all relevant evidence runs for this device, build a panel
    response. Always returns a dict; absence of runs returns
    _no_prior_run(...) shape so the frontend renders an empty state."""
```

### 9.3 panel.layout DSL

The Config Context's `panel` block describes how the frontend renders the aggregator's output. Top level:

```json
{
  "header": {
    "title_template": "string",        // {field} substitution
    "crumb_template":  "string",
    "status_field": "passed",
    "status_labels": {"true": "PASSED", "false": "FAIL"}
  },
  "tiles": [
    { "label": "string",
      "value_field": "dot.path"  | "value_template": "{field} units",
      "format": "{:.2f}"?,
      "color_field": "dot.path"?,
      "color_labels": {"true": "var(--pass)", "false": "var(--fail)"}? }
  ],
  "cards": [
    { "kind": "table" | "pi_curve" | "tcc_chart" | ... ,
      "title": "string",
      "data_path": "dot.path.to.array",
      "columns": [ ... ]    // for kind=table
    }
  ]
}
```

`build_panel_by_id()` attaches `panel_layout` from the Config Context to the aggregator's output before returning to the frontend.

---

## 10. Evidence Record Schemas

Each handler in `_DISPATCH` produces a result dict. Persisted via `EvidenceStore.put(device_id, test_name, record)`.

### 10.1 Common envelope

See §7.3.

### 10.2 dc_withstand

```python
{
    **common_envelope,
    "rated_amps", "voltage_class_v", "rated_kv",
    "target_kv": float,
    "ramp_seconds": int, "hold_seconds": int,
    "leakage_trip_threshold_ma": float, "leakage_trip_ma": float,
    "trace": [{"t_seconds", "voltage_kv", "leakage_ma", "phase"}],
    "peak_leakage_ma": float,
    "insulation_megohm": float,
}
```

### 10.3 insulation_resistance

```python
{
    **common_envelope,
    "test_voltage_v", "applied_seconds", "acceptance_megohm",
    "readings": [{"from", "to", "megohm", "passed"}],
    "min_megohm": float,
}
```

### 10.4 joint_resistance_dlro

```python
{
    **common_envelope,
    "test_current_a", "joint_count", "joint_acceptance_uohm",
    "joints": [{"joint", "uohm_per_phase", "max_uohm", "passed"}],
    "max_joint_uohm": float,
}
```

### 10.5 dissolved_gas_analysis

```python
{
    **common_envelope,
    "fault_state": "healthy" | "active_arcing" | "partial_discharge" | "overheating",
    "current_gases": {"h2", "ch4", "c2h6", "c2h4", "c2h2", "co", "co2"},
    "dga_history": [{"days_ago", **gases}],
}
```

### 10.6 transformer_turns_ratio

```python
{
    **common_envelope,
    "ttr": [{"tap", "phase", "expected", "measured", "deviation_pct", "passed"}],
}
```

### 10.7 polarization_index

```python
{
    **common_envelope,
    "polarization_index": {
        "value": float,
        "curve": [{"minute", "resistance_mohm"}],
        "passed": bool,
    },
}
```

### 10.8 transformer_hipot_history

```python
{
    **common_envelope,
    "hipot_history": [
        {"date_offset_days", "voltage_kv", "leakage_ma", "duration_seconds", "passed"}
    ],
}
```

### 10.9 ups_battery_transfer

```python
{
    **common_envelope,
    "rated_kw", "cells_count",
    "transfer_waveform_60s": [{"t_ms", "output_voltage", "battery_pct", "load_pct"}],
    "all_cells": [{"id", "voltage", "ok"}],
    "weak_cells": list,
    "min_voltage_v", "switchover_ms", "runtime_min", "battery_pct",
}
```

### 10.10 generator_loadbank

```python
{
    **common_envelope,
    "rated_kw", "operating_hours",
    "loadbank_trace": [{"t_seconds", "kw", "voltage_v", "freq_hz", "rpm",
                         "oil_temp_c", "coolant_temp_c"}],
    "startup_sequence": [{"step", "expected", "actual", "passed"}],
    "passed_overall": bool,
}
```

### 10.11 ats_transfer_sequence

```python
{
    **common_envelope,
    "rated_amps",
    "sequence": [{"step", "expected", "actual", "passed", "t_offset_ms"}],
    "downstream_ups": [{"id", "site", "min_input_v_during",
                         "battery_pct_after", "stayed_online"}],
    "overall_passed": bool,
}
```

### 10.12 sel_secondary_injection / sel_primary_injection

```python
{
    **common_envelope,
    "injection_kind": "secondary" | "primary",
    "elements_tested": ["51", "50", "51N", ...],
    "results": [{
        "code": str,
        "function": str,
        "curve_kind": str,
        "td": float,
        "setpoint_a": float,
        "actual_pickup_a": float,
        "pickup_deviation_pct": float,
        "pickup_passed": bool,
        "tcc_points": [{
            "multiple": float,
            "current_a": float,
            "expected_s": float,
            "actual_s": float,
            "deviation_pct": float,
            "passed": bool,
        }],
        "timing_passed": bool,
        "passed": bool,
    }],
}
```

### 10.13 relay_soe_collection

```python
{
    **common_envelope,
    "expected_elements": list[str],
    "events_captured": int,
    "events": [{"element": str | None, "state": bool, "timestamp_ms": int}],
    "monotonic": bool,
    "max_relative_skew_ms": float,
    "all_elements_present": bool,
}
```

### 10.14 relay_comtrade_retrieval

```python
{
    **common_envelope,
    "filename": str,
    "config_size_bytes": int,
    "data_size_bytes": int,
    "total_size_bytes": int,
    "config_parseable": bool,
    "samples_per_cycle": int,
    "capture_cycles": int,
    "total_samples": int,
}
```

### 10.15 breaker_timing

```python
{
    **common_envelope,
    "rated_amps", "voltage_class_v",
    "nominal_open_time_ms": float, "nominal_close_time_ms": float,
    "timing_tolerance_pct": float,
    "per_pole": {
        "A": {"open_ms", "close_ms", "open_passed", "close_passed"},
        "B": ...,
        "C": ...,
    },
    "max_simultaneity_ms": {"open": float, "close": float},
    "open_simultaneity_passed": bool,
    "close_simultaneity_passed": bool,
    "stroke": {"peak_mm", "overtravel_mm", "rebound_mm"},
    "spring_charge_s": float,
    "spring_charge_passed": bool,
    "coil_peak": {"trip_a": float, "close_a": float},
    "timing_passed": bool,
    "simultaneity_passed": bool,
}
```

---

## 11. Evidence Store

`src/evidence_store.py`. Protocol with two implementations.

### 11.1 Protocol

```python
class EvidenceStore(Protocol):
    def put(self, device_id: str, test_name: str, record: dict) -> None: ...
    def list_runs(self, device_id: str, test_name: str) -> list[dict]: ...
    def get_by_hash(self, evidence_hash: str) -> dict | None: ...
    def list_devices(self) -> list[str]: ...
```

### 11.2 Object layout (MinIO)

```
evidence/<device_id>/<test_name>/<iso8601>-<seq>.json
```

ISO timestamp uses `Z` suffix; `<seq>` zero-pads to 4 digits to disambiguate sub-second writes. Object content is the canonical-JSON-serialized result record.

### 11.3 In-memory implementation

`InMemoryEvidenceStore` keeps `dict[(device_id, test_name), list[dict]]` ordered by insertion. Used for tests + the demo server.

### 11.4 MinIO implementation

`storage.minio_evidence_store.MinioEvidenceStore` mirrors the Protocol against a real (or `DemoMinio`) client. Performs canonical-JSON encoding and SHA-256 hashing.

---

## 12. Attestation Chain

`src/attestation.py`. Append-only, HMAC-linked.

### 12.1 AttestationRecord

```python
@dataclass
class AttestationRecord:
    timestamp_ns: int
    device_id: str
    measurement: str
    value: float | dict        # measurement-specific
    raw_bytes: str             # hex
    protocol: str              # 'modbus_tcp' | 'dnp3' | 'serial' | ...
    source_ip: str
    worker_id: str
```

### 12.2 Chain mechanics

Each record's hash is `sha256(canonical_json(record) || prev_hash)`, where `prev_hash` is the previous record's hash (or all-zeros for the first).

`AttestationEngine`:

```python
class AttestationEngine:
    async def submit(self, record: AttestationRecord) -> str:
        """Returns this record's hash. Persists to WAL + MinIO."""
    async def drain(self) -> None:
        """Flushes the in-memory queue to durable storage."""
    async def verify(self) -> dict:
        """Walks the entire chain, returns {chain_valid: bool, length: int,
        last_hash: str}."""
```

### 12.3 Storage

WAL: append-only log file in `wal_dir`, one JSON record per line.
MinIO: each record persisted at `attestation/<chain-position-zero-padded>.json`.

### 12.4 Operator-action attestation

The five operator endpoints (PATCH `/punchlist`, POST `/checklist/signoff`, POST `/reports/generate`, POST `/bim/import`, POST `/config/generate`) push a record into the chain on success. `measurement` field is the action verb (e.g., `"resolve_punchlist"`, `"sign_checklist_item"`).

---

## 13. Auth + Middleware

### 13.1 Auth (`src/auth.py`)

```python
class Role(Enum):
    OPERATOR
    COMMISSIONING_LEAD
    ADMIN
    AUDITOR

@dataclass
class User:
    username: str
    role: Role
    badge_id: str

encode_token(user: User, secret: str, ttl_s: int = 28_800) -> str
decode_token(token: str, secret: str) -> User
require_permission(role: Role, action: str) -> None    # raises if unauthorized
```

JWT HS256. Permissions matrix in `auth.PERMISSIONS_BY_ACTION` keyed on action name → set of roles.

### 13.2 Rate limiting (`src/middleware/rate_limit.py`)

Token-bucket per API key + per IP. Default: 60 req/min/key, burst 10.

### 13.3 Audit log (`src/middleware/audit_log.py`)

Each request emits a structured `AuditEntry`. Sinks: `StdoutJSONSink` (default), `InMemorySink` (tests).

---

## 14. Observability

`src/observability/`.

### 14.1 Structured logging

```python
configure(level: str = "INFO", json_output: bool = True) -> None
get_logger(name: str) -> logging.Logger
set_trace_id(trace_id: str) -> None      # contextvar; auto-included in JSON
```

### 14.2 Metrics

Zero-deps Prometheus exposition.

```python
class Counter:    inc(value=1) / labels(**kwargs)
class Gauge:      set(v) / inc(v) / dec(v) / labels(**kwargs)
class Histogram:  observe(v) / labels(**kwargs)

REGISTRY: global registry singleton
render_text_format() -> str        # served at /metrics
```

### 14.3 Health

```python
class HealthCheckResult: name, ok, message, latency_ms
class HealthChecker:
    def add(self, name: str, fn: Callable[[], Awaitable[HealthCheckResult]]): ...
    async def check_all(self) -> list[HealthCheckResult]: ...
```

Served at `/healthz` (liveness) and `/readyz` (readiness).

---

## 15. API Surface

`src/api/server.py`. FastAPI. All paths under `/api/v1`.

### 15.1 Endpoints

```
GET    /healthz
GET    /readyz
GET    /metrics

GET    /api/v1/devices
GET    /api/v1/devices/{device_id}
GET    /api/v1/devices/{device_id}/active_tests

GET    /api/v1/equipment/{device_id}              # generic panel response
GET    /api/v1/equipment/{kind}/{device_id}       # legacy URL-kind dispatch

GET    /api/v1/sld/layout
GET    /api/v1/sld/layout/{site}

GET    /api/v1/tests
GET    /api/v1/tests/{test_id}
POST   /api/v1/tests/{test_id}/start              # SSE stream

GET    /api/v1/timeline
GET    /api/v1/punchlist
PATCH  /api/v1/punchlist/{id}                     # operator: resolve

GET    /api/v1/checklist
POST   /api/v1/checklist/{id}/signoff             # operator: sign

GET    /api/v1/attestation/cert
POST   /api/v1/attestation/verify
POST   /api/v1/reports/generate                   # operator: generate PDF

POST   /api/v1/bim/import                         # operator: BIM/IFC upload
POST   /api/v1/config/generate                    # operator: PDF→ConfigContext

GET    /api/v1/discovery
GET    /api/v1/discovery/{scan_id}
GET    /api/v1/instruments/connected              # populates topbar banner

GET    /api/v1/telemetry
GET    /api/v1/sse                                # facility-wide event stream
```

### 15.2 Auth

All endpoints except `/healthz`, `/readyz`, `/metrics` require an API key in `X-API-Key` header. Operator-action endpoints additionally require a JWT operator token in `Authorization: Bearer <token>`.

### 15.3 Response envelope

JSON. Errors:

```json
{ "error": { "code": "string", "message": "string", "details": {...} } }
```

---

## 16. dev_server.py

Demo facility builder. Not production code — exists so contributors can boot the platform without real hardware or external services.

### 16.1 Entry points

```python
async def main():                       # builds + serves the demo facility
def build_facility() -> tuple[list[FacilityDevice], list[FacilityTestRun]]
def power_graph(devices) -> dict
```

### 16.2 Key helpers

```python
_resolve_effective_configs(today)       # device_id → merged Config Context
_seed_evidence_store(store, configs, today)
_make_device_loader(effective_configs)
_flatten_execution(test_def)
_build_demo_instrument_detector()
_device_lineup(today)                   # the demo facility's assets
_seed_attestation_chain(attest, devices)
```

### 16.3 Adding a device-instance to the demo

Edit `_device_lineup()`. Each entry is:

```python
{
    "device_id": "string",
    "device_type_slug": "string",       # must have a config/equipment/<slug>.json
    "overrides": { ... },               # merged into ageing_model.params + ratings
    "cross_refs": {                     # optional
        "upstream_gen": "device_id",
        "downstream_ups": ["device_id", ...]
    }
}
```

---

## 17. Test Conventions

### 17.1 Layout

```
tests/
  __init__.py
  conftest.py
  dnp3/                      # DNP3 stack tests, per layer
    test_datalink.py
    test_transport.py
    test_application_and_objects.py
    test_master.py
  instruments/               # per-driver recorded-trace tests
    test_megger_mit525.py
    test_megger_dlro10x.py
    test_megger_tm1800.py
    test_vitrek_95x.py
    test_modbus_drivers.py
    test_doble_f6150.py
    test_sel_751_dnp3.py
    test_detector.py
    test_registry.py
  unit/                      # module unit tests
    test_evidence_store.py
    test_equipment_loader.py
    test_test_executor.py
    test_equipment_views.py
    test_api_operator_endpoints.py
    test_auth.py
    test_middleware.py
    test_observability.py
    test_storage.py
    test_compliance.py
    test_topology.py
    ...
  integration/               # full pipeline (TestClient + asyncio.run)
  hardware/                  # gated on env vars
    test_cm2000.py           # skipped without HW_CM2000_IP
    test_mtz_breaker.py      # skipped without HW_MTZ_IP
    test_nvml_real_gpu.py    # skipped without HW_NVML
```

### 17.2 Recorded-trace pattern (drivers)

```python
class RecordedSerialChannel:
    def __init__(self, script: list[tuple[str, str]]):
        self._script = deque(script)

    async def write_line(self, line: str) -> None:
        expected, _ = self._script[0]
        assert line.strip() == expected.strip()

    async def read_line(self, timeout_s: float = 5.0) -> str:
        _, response = self._script.popleft()
        if response is None:
            raise asyncio.TimeoutError
        return response
```

`RecordedTcpChannel`, `RecordedModbusClient`, `FakeGatt`, `FakeLinkChannel` follow the same pattern. Each test scripts the exact byte/line sequence the driver should emit; mismatched writes raise `AssertionError`.

### 17.3 DNP3 master tests

Use `FakeLinkChannel` from `tests/dnp3/test_master.py`. Helper `_make_response(seq, iin, object_data)` builds a wire response. Tests for the SEL-751 DNP3 driver import this fake from the dnp3 test directory.

### 17.4 Hardware tests

Live-instrument tests live in `tests/hardware/`. Each gated on a per-instrument env var:

```python
@pytest.mark.skipif(not os.getenv("HW_CM2000_IP"),
                     reason="HW_CM2000_IP not set")
def test_real_cm2000_metering(): ...
```

Never run in CI by default. Operator runs them manually against a bench instrument before each release.

### 17.5 Suite invariants

```
suite count >= 579         # at HEAD fe84ca7
0 failures                 # excluding the known pre-existing skips
hardware tests skip        # without env vars set
```

---

## 18. PDF Generation

`src/compliance/certificate_gen.py`. Zero-dep PDF writer. Output is HMAC-signed: signature embedded in a content stream and verifiable via `verify_certificate(pdf_bytes, secret) -> bool`.

### 18.1 Inputs

```python
@dataclass
class CommissioningCertificate:
    facility_name: str
    project_id: str
    device_id: str
    test_runs: list[dict]              # evidence records
    operator: str
    issued_at: str
    cert_id: str
    chain_first_hash: str
    chain_last_hash: str
    chain_length: int
```

### 18.2 Multi-test report

`src/reports.py`. Aggregates results across multiple devices into a single PDF via `AttestationSummary` (chain_valid, first_hash, last_hash, length).

---

## 19. Evolutions Since v1 / v5

For reviewers familiar with the v1 / v5 PDFs, these are the substantive code-level changes.

### 19.1 Driver registry

- **Was:** `_DRIVERS: dict[tuple[str, str], type]`
- **Now:** `_DRIVERS: dict[tuple[str, str, str | None], type]` with optional transport key
- **Migration:** `register()` is backwards-compatible as a bare decorator. Existing callers unchanged. `get_driver()` accepts optional transport with fallback semantics.

### 19.2 New protocol stack

`src/instruments/dnp3/` is a new subpackage implementing IEEE 1815-2012. 5 source files, ~1500 lines, 72 dedicated tests at the protocol layer.

### 19.3 New drivers

- `src/instruments/doble_f6150.py` — relay test set, includes `ieee_c37_112_curve_seconds()` for inverse-time curve math
- `src/instruments/megger_tm1800.py` — breaker analyzer
- `src/instruments/sel_751_dnp3.py` — SEL-751 over DNP3, includes `_ELEMENT_BIN_INDEX` mapping for SOE event decoration

### 19.4 New test_executor types

5 new entries in `_DISPATCH`:
- `sel_secondary_injection`, `sel_primary_injection` (factory `_relay_injection_handler`)
- `relay_soe_collection`
- `relay_comtrade_retrieval`
- `breaker_timing`

### 19.5 New equipment categories

- `relay` → `relay_panel` aggregator
- `circuit_breaker` → `circuit_breaker_panel` aggregator

### 19.6 Schema additions

- `ratings.relay_elements[]` for relays
- `ratings.nominal_*_time_ms`, `nominal_spring_charge_s`, `nominal_stroke_mm`, `nominal_*_coil_a` for breakers
- `active_tests[].instrument.transport` field
- `parameters.elements[]` for injection tests
- `parameters.expected_elements[]` for SOE
- `parameters.samples_per_cycle` / `capture_cycles` for COMTRADE

### 19.7 New Config Context files

- `config/equipment/sel-751.json` (4 active_tests)
- `config/equipment/schneider-mtz-1200a.json`

### 19.8 dev_server

`_device_lineup()` extended with 7 SEL-751 instances + 4 Schneider MTZ-1200a instances.

### 19.9 Suite

433 → 579 tests. Hardware-gated tests still skip; rest pass.

---

## 20. Spec References

Standards cited by handlers and Config Contexts:

- **NETA ATS-17** §7.5 (switchgear), §7.10 (protective relays), §7.22 (ATS)
- **IEC 61439-6** (LV switchgear assemblies)
- **IEC 62271-100** §6.101 (HV switchgear, breaker timing)
- **IEC 62040-3** (UPS classification, transfer)
- **IEEE 43-2013** (insulation resistance, polarization index)
- **IEEE 400.1-2018** (cable hipot, DC withstand)
- **IEEE C37.09** (breaker mechanical acceptance)
- **IEEE C37.111-2013** (COMTRADE)
- **IEEE C37.112-2018** §5 (inverse-time curves)
- **IEEE C37.13.1** §8.2 (CT polarity)
- **IEEE C37.232** §6 (SEE accuracy)
- **IEEE C37.233** §6.3 (functional element test)
- **IEEE C37.230** §6 (relay settings management)
- **IEEE C57.104** (DGA fault codes)
- **IEEE C57.12.90** (transformer turns ratio)
- **IEEE C57.149** (SFRA, future)
- **IEEE 1184** (UPS DC source for stationary applications)
- **IEEE 1815-2012** (DNP3 over IP)
- **IEEE 142** (Green Book, grounding)
- **NFPA 110** (emergency standby power)
- **NEC Article 250** (grounding + bonding)

Spec citations propagate from `active_tests[].spec_reference` into the result record's `spec_reference` field, then into the signed PDF certificate, then into the attestation chain entry. Single source of truth.

---

## 21. Out of Scope

Items deliberately not in this codebase, to prevent scope drift:

- MES (production scheduling, OEE, recipe management, batch genealogy)
- PLC programming (ladder logic, structured text, function blocks)
- Enterprise data integration (ERP, supply chain, finance)
- Predictive maintenance ML (would consume our evidence; not generate it)
- IT operations (rack PDU monitoring, server health, network ops)
- DCIM (capacity planning, IT asset management)
- Building Management System control loops (we ingest BMS evidence but don't run BMS)

Adjacencies that may be added later, with explicit work tracking, are not in scope of this spec until that point.

---

End of v2.
