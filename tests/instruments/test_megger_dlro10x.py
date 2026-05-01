"""Recorded-trace tests for the Megger DLRO 10X driver."""

from __future__ import annotations

from collections import deque
import pytest

from src.instruments.megger_dlro10x import MeggerDLRO10X
from src.instruments.megger_mit525 import InstrumentError


class RecordedSerialChannel:
    def __init__(self, script):
        self._script = deque(script)

    async def write_line(self, line: str) -> None:
        if not self._script:
            raise AssertionError(f"unexpected write: {line!r}")
        expected, _resp = self._script[0]
        if line.strip() != expected.strip():
            raise AssertionError(f"expected {expected!r}, got {line!r}")

    async def read_line(self, timeout_s: float = 5.0) -> str:
        _expected, resp = self._script.popleft()
        return resp


@pytest.mark.asyncio
async def test_connect_rejects_bad_idn():
    ch = RecordedSerialChannel([("*IDN?", "WRONG,MODEL,SN,FW")])
    inst = MeggerDLRO10X(ch)
    with pytest.raises(InstrumentError, match="unexpected IDN"):
        await inst.connect()


@pytest.mark.asyncio
async def test_execute_three_joints_three_phases():
    n_joints = 3
    n_measurements = n_joints * 3  # 3 phases per joint
    script = [
        ("*IDN?", "MEGGER,DLRO10X,SN-DLRO-44219,FW-4.2.1"),
        ("DLRO:CURR 10.0", "OK"),
        ("DLRO:RANG AUTO", "OK"),
    ]
    for _ in range(n_measurements):
        script += [
            ("DLRO:MEAS", "OK"),
            ("DLRO:STAT?", "COMPLETE"),
            ("READ:RES?", "22.4"),
        ]
    script += [("SYST:CAL?", "DLRO-CAL-2026-Q1,2026-09-30")]
    inst = MeggerDLRO10X(RecordedSerialChannel(script))
    await inst.connect()
    out = await inst.execute({
        "test_current_a": 10.0,
        "joint_count": n_joints,
        "max_uohm_per_joint": 30.0,
    })
    assert len(out["joints"]) == 3
    assert out["max_joint_uohm"] == 22.4
    assert all(j["passed"] for j in out["joints"])
    for j in out["joints"]:
        assert set(j["uohm_per_phase"].keys()) == {"A", "B", "C"}


@pytest.mark.asyncio
async def test_execute_rejects_invalid_current():
    inst = MeggerDLRO10X(RecordedSerialChannel(
        [("*IDN?", "MEGGER,DLRO10X,SN,FW")]))
    await inst.connect()
    with pytest.raises(InstrumentError, match="unsupported DLRO current"):
        await inst.execute({"test_current_a": 99.0, "joint_count": 1,
                            "max_uohm_per_joint": 30.0})


@pytest.mark.asyncio
async def test_one_joint_above_acceptance_marks_failed():
    script = [
        ("*IDN?", "MEGGER,DLRO10X,SN-DLRO-44219,FW-4.2.1"),
        ("DLRO:CURR 10.0", "OK"),
        ("DLRO:RANG AUTO", "OK"),
        ("DLRO:MEAS", "OK"), ("DLRO:STAT?", "COMPLETE"), ("READ:RES?", "18.0"),
        ("DLRO:MEAS", "OK"), ("DLRO:STAT?", "COMPLETE"), ("READ:RES?", "19.0"),
        ("DLRO:MEAS", "OK"), ("DLRO:STAT?", "COMPLETE"), ("READ:RES?", "45.0"),  # bad
        ("SYST:CAL?", "DLRO-CAL-2026-Q1,2026-09-30"),
    ]
    inst = MeggerDLRO10X(RecordedSerialChannel(script))
    await inst.connect()
    out = await inst.execute({
        "test_current_a": 10.0, "joint_count": 1, "max_uohm_per_joint": 30.0,
    })
    assert out["joints"][0]["passed"] is False
    assert out["max_joint_uohm"] == 45.0
