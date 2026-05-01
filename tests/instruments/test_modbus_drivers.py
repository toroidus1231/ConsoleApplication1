"""Recorded-trace tests for the Modbus-TCP-based instruments:
Qualitrol 118ITM, Vaisala OPT100, SEL-751, Cat EMCP.
"""

from __future__ import annotations

import struct
from typing import Any
import pytest

from src.instruments.qualitrol_118itm import Qualitrol118ITM
from src.instruments.vaisala_opt100 import VaisalaOPT100
from src.instruments.sel_751 import SEL751, SECONDARY_INJECTION_COILS
from src.instruments.cat_emcp import CatEMCP
from src.instruments.megger_mit525 import InstrumentError


def f32_to_regs(value: float) -> list[int]:
    """Encode a float32 as 2 big-endian Modbus registers."""
    b = struct.pack(">f", value)
    return [int.from_bytes(b[:2], "big"), int.from_bytes(b[2:], "big")]


def ascii_to_regs(text: str, n_regs: int) -> list[int]:
    """Pack ASCII text into Modbus registers (2 chars per reg, MSB first)."""
    padded = text.ljust(n_regs * 2, "\x00")
    return [int.from_bytes(padded[i*2:i*2+2].encode(), "big")
            for i in range(n_regs)]


class FakeModbusClient:
    """Records reads against a static map keyed by (function, address)."""

    def __init__(self, holding: dict[int, list[int]] | None = None,
                 input_regs: dict[int, list[int]] | None = None):
        self.holding = holding or {}
        self.input_regs = input_regs or {}
        self.coils_written: list[tuple[int, bool]] = []
        self.regs_written: list[tuple[int, int]] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def read_input_registers(self, address, count, slave=1):
        block = self.input_regs.get(address)
        if block is None:
            raise AssertionError(f"no input regs scripted at {address}")
        if len(block) < count:
            raise AssertionError(f"need {count} regs at {address}, have {len(block)}")
        return block[:count]

    async def read_holding_registers(self, address, count, slave=1):
        block = self.holding.get(address)
        if block is None:
            raise AssertionError(f"no holding regs scripted at {address}")
        return block[:count]

    async def write_coil(self, address, value, slave=1):
        self.coils_written.append((address, value))

    async def write_register(self, address, value, slave=1):
        self.regs_written.append((address, value))


# ---------------------------------------------------------------------------
# Qualitrol 118ITM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qualitrol_dga_snapshot():
    holding = {
        40035: ascii_to_regs("CAL-Q118-2026-Q1", 12),
        40059: ascii_to_regs("20261231", 4),
        40067: ascii_to_regs("Q118-001", 4),
    }
    input_regs = {}  # 118ITM uses holding regs for gases per its manual
    # Gas regs are at holding 40001..40023 (the manual mixes input/holding;
    # we use holding via read_holding_registers) — the driver reads from
    # those addresses as holding regs.
    holding[40001] = f32_to_regs(45.0)   # h2
    holding[40003] = f32_to_regs(8.5)    # ch4
    holding[40005] = f32_to_regs(4.7)    # c2h6
    holding[40007] = f32_to_regs(2.2)    # c2h4
    holding[40009] = f32_to_regs(3.38)   # c2h2 (active arcing)
    holding[40011] = f32_to_regs(246.0)  # co
    holding[40013] = f32_to_regs(2200.0) # co2
    holding[40015] = f32_to_regs(15000.0)
    holding[40017] = f32_to_regs(82000.0)
    holding[40019] = f32_to_regs(8.4)    # moisture
    holding[40021] = f32_to_regs(53.4)   # oil temp
    holding[40023] = f32_to_regs(24.0)   # ambient
    inst = Qualitrol118ITM(FakeModbusClient(holding=holding, input_regs=input_regs))
    await inst.connect()
    out = await inst.execute({})
    assert out["c2h2"] == 3.38
    assert out["h2"] == 45.0
    assert out["moisture"] == 8.4
    assert out["instrument_serial"] == "Q118-001"
    assert out["cert_id"] == "CAL-Q118-2026-Q1"


# ---------------------------------------------------------------------------
# Vaisala OPT100
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vaisala_opt100_snapshot():
    inputs = {
        30001: f32_to_regs(8.4)   + f32_to_regs(35.5)
              + f32_to_regs(53.0) + f32_to_regs(82.0)
              + f32_to_regs(120.0) + f32_to_regs(48.0),
    }
    holding = {
        40005: ascii_to_regs("OPT100-77123456", 8),
        40013: ascii_to_regs("VAISALA-OPT100-CAL-2026Q1", 16),
        40029: ascii_to_regs("20260930", 4),
    }
    inst = VaisalaOPT100(FakeModbusClient(holding=holding, input_regs=inputs))
    await inst.connect()
    out = await inst.execute({})
    assert out["moisture_ppm"] == 8.4
    assert out["pd_magnitude_pc"] == 82.0
    assert out["instrument_serial"] == "OPT100-77123456"


# ---------------------------------------------------------------------------
# SEL-751
# ---------------------------------------------------------------------------


def _sel_751_holding():
    return {
        40010: ascii_to_regs("", 2),
        40022: ascii_to_regs("SEL751-A8472301", 8),
        40030: ascii_to_regs("SEL-CAL-2026-Q1", 8),
        40038: ascii_to_regs("20271231", 4),
    }


def _sel_751_inputs():
    return {
        30001: (
            f32_to_regs(13800.0) + f32_to_regs(13780.0) + f32_to_regs(13820.0)
          + f32_to_regs(380.0)   + f32_to_regs(381.0)   + f32_to_regs(379.0)
          + f32_to_regs(0.5)
          + f32_to_regs(60.001)
          + f32_to_regs(0.97)
          + f32_to_regs(8500.0)
          + f32_to_regs(2200.0)
        ),
    }


@pytest.mark.asyncio
async def test_sel_751_metering_snapshot():
    inst = SEL751(FakeModbusClient(holding=_sel_751_holding(),
                                    input_regs=_sel_751_inputs()))
    await inst.connect()
    out = await inst.execute({"test_type": "metering_snapshot"})
    assert out["va_v"] == 13800.0
    assert out["freq_hz"] == 60.001
    assert out["pf"] == 0.97
    assert out["instrument_serial"] == "SEL751-A8472301"


@pytest.mark.asyncio
async def test_sel_751_secondary_injection_writes_correct_coil():
    client = FakeModbusClient(holding=_sel_751_holding(),
                               input_regs=_sel_751_inputs())
    inst = SEL751(client)
    await inst.connect()
    out = await inst.execute({"test_type": "secondary_injection",
                              "element": "51"})
    assert (1, True) in client.coils_written
    assert out["element"] == "51"


@pytest.mark.asyncio
async def test_sel_751_unknown_element_raises():
    inst = SEL751(FakeModbusClient(holding=_sel_751_holding(),
                                    input_regs=_sel_751_inputs()))
    await inst.connect()
    with pytest.raises(InstrumentError, match="unknown injection"):
        await inst.execute({"test_type": "secondary_injection",
                            "element": "999"})


# ---------------------------------------------------------------------------
# Cat EMCP
# ---------------------------------------------------------------------------


def _cat_emcp_inputs(rpm=1800.0, freq=60.02, kw=1500.0, state_code=2):
    return {
        30001: (
            f32_to_regs(rpm)        # rpm
          + f32_to_regs(82.5)       # coolant
          + f32_to_regs(345.0)      # oil pressure kPa
          + f32_to_regs(94.0)       # fuel %
          + f32_to_regs(24.6)       # battery V
          + f32_to_regs(488.0)      # gen V L-L
          + f32_to_regs(freq)
          + f32_to_regs(kw)         # kW
          + f32_to_regs(220.0)      # kVAR
          + f32_to_regs(1810.0)     # current A
          + f32_to_regs(538.0)      # runtime h
        ),
        30023: [state_code],
    }


def _cat_emcp_holding():
    return {40009: ascii_to_regs("3516B-AB12345", 8)}


@pytest.mark.asyncio
async def test_cat_emcp_snapshot():
    inst = CatEMCP(FakeModbusClient(holding=_cat_emcp_holding(),
                                     input_regs=_cat_emcp_inputs()))
    await inst.connect()
    out = await inst.execute({"test_type": "snapshot"})
    assert out["rpm"] == 1800.0
    assert out["freq_hz"] == 60.02
    assert out["engine_state"] == "running"


@pytest.mark.asyncio
async def test_cat_emcp_loadbank_step_writes_register():
    client = FakeModbusClient(holding=_cat_emcp_holding(),
                               input_regs=_cat_emcp_inputs())
    inst = CatEMCP(client)
    await inst.connect()
    out = await inst.execute({"test_type": "loadbank_step", "target_pct": 75})
    assert (40004, 75) in client.regs_written
    assert out["step_pct"] == 75


@pytest.mark.asyncio
async def test_cat_emcp_invalid_loadbank_step():
    inst = CatEMCP(FakeModbusClient(holding=_cat_emcp_holding(),
                                     input_regs=_cat_emcp_inputs()))
    await inst.connect()
    with pytest.raises(InstrumentError, match="invalid loadbank step"):
        await inst.execute({"test_type": "loadbank_step", "target_pct": 33})


@pytest.mark.asyncio
async def test_cat_emcp_unsupported_test_type():
    inst = CatEMCP(FakeModbusClient(holding=_cat_emcp_holding(),
                                     input_regs=_cat_emcp_inputs()))
    await inst.connect()
    with pytest.raises(InstrumentError, match="unsupported test_type"):
        await inst.execute({"test_type": "bogus"})


# ---------------------------------------------------------------------------
# Coil-map sanity for SEL-751 — every advertised element has a coil
# ---------------------------------------------------------------------------


def test_sel_751_coil_map_complete():
    expected_elements = {"51", "50", "27", "81", "50N", "51N"}
    assert set(SECONDARY_INJECTION_COILS.keys()) == expected_elements
    assert set(SECONDARY_INJECTION_COILS.values()) == {1, 2, 3, 4, 5, 6}
