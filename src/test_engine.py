"""Module 10: Active Test Engine.

Executes a single active test as the state machine defined in contracts spec
§5.3::

    INIT → PRECONDITION_CHECK → MANUAL_WAIT → COMMANDING → MONITORING
         → EVALUATING → RESTORING → RESTORE_VERIFY → COMPLETE

Each test definition lives in the device's Config Context (``active_tests[]``,
spec §2.2). The engine is generic — preconditions, command, abort conditions,
monitor registers, acceptance criteria and restore are all data.

Safety properties carried over from the spec:

* Preconditions are safety interlocks — any failure short-circuits to COMPLETE
  with ``precondition_failed`` (no command is ever written).
* ``identity_register`` is verified before any write so a gateway unit-id
  misroute can't command the wrong device.
* The MONITORING loop runs a watchdog: 3 consecutive failed polls abort the test
  and trigger a *blind* restore (fire-and-forget write) because the device can no
  longer be read.
* Any ``abort_conditions[]`` match restores immediately.

Everything external is injected: the read poller, the write/command function, the
attestation engine, the InfluxDB writer, and ``sleep`` — so the whole state
machine is unit-tested deterministically with no real device and no real time.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Protocol

from .pollers.modbus import poll_device as modbus_poll
from .timeutil import iso_now
from .types import (
    AttestationRecord,
    DeviceInfo,
    Event,
    PollResult,
    TestRequest,
    TestResult,
)

PollFn = Callable[[DeviceInfo], Awaitable[PollResult]]
CommandFn = Callable[[DeviceInfo, dict], Awaitable[None]]

MANUAL_TIMEOUT_SECONDS = 1800.0  # 30 minutes (contracts spec §5.3 MANUAL_WAIT)
WATCHDOG_FAILURES = 3


class InfluxLike(Protocol):
    async def write_poll(self, result: PollResult, test_id: str | None = ...) -> None: ...


class AttestationLike(Protocol):
    async def submit(self, record: AttestationRecord) -> None: ...


# --- Pure helpers (also unit-tested directly) --------------------------------

_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "eq": lambda a, e: a == e,
    "neq": lambda a, e: a != e,
    "gt": lambda a, e: a > e,
    "lt": lambda a, e: a < e,
    "gte": lambda a, e: a >= e,
    "lte": lambda a, e: a <= e,
}


def evaluate(actual, operator: str, expected) -> bool:
    """Apply a comparison operator. Unknown operator or ``None`` actual → False."""
    if actual is None:
        return False
    op = _OPERATORS.get(operator)
    return bool(op(actual, expected)) if op else False


def compute_metric(metric: str, values: list[float], collected: list[PollResult], reg: str, threshold=None):
    """Reduce a series of readings to a single acceptance value (spec §5.3 EVALUATING)."""
    if not values:
        return None
    if metric == "max":
        return max(values)
    if metric == "min":
        return min(values)
    if metric == "avg":
        return sum(values) / len(values)
    if metric == "delta":
        return abs(values[-1] - values[0])
    if metric == "settled_value":
        # Average of the last 10% of readings (at least one).
        tail = values[-(len(values) // 10 or 1):]
        return sum(tail) / len(tail)
    if metric == "time_to_value":
        # First time (ms) the register crossed the threshold.
        for p in collected:
            v = p.measurements.get(reg)
            if v is not None and threshold is not None and v >= threshold:
                return (p.timestamp_ns - collected[0].timestamp_ns) / 1e6
        return float("inf")
    if metric == "rate_of_change":
        if len(collected) < 2:
            return 0.0
        dt = (collected[-1].timestamp_ns - collected[0].timestamp_ns) / 1e9
        return (values[-1] - values[0]) / dt if dt else 0.0
    return None


class TestEngine:
    def __init__(
        self,
        *,
        attestation: AttestationLike,
        influx: InfluxLike,
        event_queue: asyncio.Queue | None = None,
        poll_fn: PollFn = modbus_poll,
        command_fn: CommandFn | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        manual_timeout_seconds: float = MANUAL_TIMEOUT_SECONDS,
    ):
        self._attestation = attestation
        self._influx = influx
        self._event_queue = event_queue
        self._poll_fn = poll_fn
        self._command_fn = command_fn or _default_command_fn
        self._sleep = sleep_fn
        self._manual_timeout = manual_timeout_seconds
        self.manual_confirmations: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def execute(self, request: TestRequest, device: DeviceInfo) -> TestResult:
        result = TestResult(
            test_id=request.test_id,
            device_id=request.device_id,
            test_name=request.test_name,
            started_at=iso_now(),
        )
        start = time.monotonic()

        test_def = self._find_test(device, request.test_name)
        if test_def is None:
            result.status = "failed"
            result.abort_reason = f"Test '{request.test_name}' not found in Config Context"
            return self._finish(result, start)

        # PRECONDITION_CHECK
        if not await self._check_preconditions(device, test_def, result):
            return self._finish(result, start)  # precondition_failed

        # MANUAL_WAIT
        if not await self._await_manual(request, test_def, result):
            return self._finish(result, start)  # aborted (timeout)

        # COMMANDING (identity verify, then write)
        if not await self._command(device, test_def, result):
            return self._finish(result, start)  # aborted

        await self._emit("test_started", {"test_id": request.test_id, "device_id": device.device_id})

        # MONITORING
        collected = await self._monitor(request, device, test_def, result)

        # RESTORING + RESTORE_VERIFY
        await self._restore(device, test_def, result)

        # EVALUATING (skipped if aborted or restore failed)
        if not result.abort_triggered and result.status != "restore_failure":
            self._evaluate(test_def, collected, result)
        if result.abort_triggered and result.status != "restore_failure":
            result.status = "aborted"

        return self._finish(result, start)

    def confirm_manual(self, test_id: str, confirmed_by: str) -> bool:
        """Satisfy a pending manual confirmation. Returns True if one was waiting."""
        event = self.manual_confirmations.get(test_id)
        if event is None:
            return False
        event._confirmed_by = confirmed_by  # type: ignore[attr-defined]
        event.set()
        return True

    # ------------------------------------------------------------------
    # States
    # ------------------------------------------------------------------
    @staticmethod
    def _find_test(device: DeviceInfo, test_name: str) -> dict | None:
        for t in device.config_context.get("active_tests", []) or []:
            if t.get("name") == test_name:
                return t
        return None

    async def _check_preconditions(self, device, test_def, result) -> bool:
        preconditions = test_def.get("preconditions", []) or []
        if not preconditions:
            return True
        poll = await self._poll_fn(device)
        all_passed = True
        for pre in preconditions:
            reg = pre["register"]
            actual = poll.measurements.get(reg)
            passed = evaluate(actual, pre["operator"], pre["value"])
            result.precondition_results.append(
                {"register": reg, "expected": pre["value"], "actual": actual, "passed": passed}
            )
            if not passed:
                all_passed = False
        if not all_passed:
            result.status = "precondition_failed"
            return False
        return True

    async def _await_manual(self, request, test_def, result) -> bool:
        prompts = test_def.get("manual_confirmation", []) or []
        for prompt in prompts:
            result.status = "manual_pending"
            event = asyncio.Event()
            self.manual_confirmations[request.test_id] = event
            await self._emit(
                "manual_confirmation_needed",
                {"test_id": request.test_id, "prompt": prompt},
            )
            try:
                await asyncio.wait_for(event.wait(), timeout=self._manual_timeout)
            except asyncio.TimeoutError:
                result.status = "aborted"
                result.abort_reason = "Manual confirmation timed out"
                self.manual_confirmations.pop(request.test_id, None)
                return False
            result.manual_confirmations.append(
                {
                    "prompt": prompt,
                    "confirmed_by": getattr(event, "_confirmed_by", "unknown"),
                    "confirmed_at": iso_now(),
                }
            )
            self.manual_confirmations.pop(request.test_id, None)
        return True

    async def _command(self, device, test_def, result) -> bool:
        # Identity verification before any write (spec §5.1 gateway-misroute guard).
        identity = device.config_context.get("identity_register")
        if identity:
            poll = await self._poll_fn(device)
            actual = poll.measurements.get(identity.get("register"))
            expected = identity.get("expected")
            if actual != expected:
                result.status = "aborted"
                result.abort_reason = f"Identity mismatch: expected {expected}, got {actual}"
                return False

        command = test_def.get("command")
        if command:
            try:
                await self._command_fn(device, command)
            except Exception as e:  # noqa: BLE001 — a failed command must abort, not crash
                result.status = "aborted"
                result.abort_reason = f"Command write failed: {type(e).__name__}: {e}"
                return False
        return True

    async def _monitor(self, request, device, test_def, result) -> list[PollResult]:
        interval_ms = int(test_def.get("monitor_interval_ms", 1000))
        duration_s = float(test_def.get("monitor_duration_seconds", 60))
        monitor_names = test_def.get("monitor", []) or []
        abort_conditions = test_def.get("abort_conditions", []) or []

        # Deterministic sample budget: duration / interval, at least one sample.
        max_samples = max(1, round(duration_s * 1000 / interval_ms))
        collected: list[PollResult] = []
        consecutive_failures = 0

        for _ in range(max_samples):
            poll = await self._poll_fn(device)
            if not poll.success:
                consecutive_failures += 1
                if consecutive_failures >= WATCHDOG_FAILURES:
                    result.abort_triggered = True
                    result.abort_reason = "device_unreachable"
                    break
                await self._sleep(interval_ms / 1000)
                continue
            consecutive_failures = 0
            collected.append(poll)

            await self._influx.write_poll(poll, test_id=request.test_id)
            for name in monitor_names:
                if name in poll.measurements:
                    record = AttestationRecord(
                        timestamp_ns=poll.timestamp_ns,
                        device_id=device.device_id,
                        measurement=name,
                        value=float(poll.measurements[name]),
                        raw_bytes=poll.raw_bytes.get(name, ""),
                        protocol=poll.protocol,
                        source_ip=device.primary_ip,
                        worker_id="",
                        test_id=request.test_id,
                    )
                    await self._attestation.submit(record)
                    if record.nonce:
                        result.evidence_hashes.append(record.nonce)

            if self._abort_fired(poll, abort_conditions, result):
                break
            await self._sleep(interval_ms / 1000)

        return collected

    @staticmethod
    def _abort_fired(poll, abort_conditions, result) -> bool:
        for ab in abort_conditions:
            v = poll.measurements.get(ab["register"])
            if v is not None and evaluate(v, ab["operator"], ab["value"]):
                result.abort_triggered = True
                result.abort_reason = f"{ab['register']} {ab['operator']} {ab['value']}"
                return True
        return False

    async def _restore(self, device, test_def, result) -> None:
        restore = test_def.get("restore")
        if restore:
            try:
                await self._command_fn(device, restore)
            except Exception:  # noqa: BLE001 — blind restore: safer to try than not
                pass

        verify = test_def.get("restore_verify", []) or []
        if not verify:
            return
        timeout_s = int(test_def.get("restore_timeout_seconds", 30))
        for _ in range(max(1, timeout_s)):  # poll every ~1s up to the timeout
            poll = await self._poll_fn(device)
            if all(
                evaluate(poll.measurements.get(rv["register"]), rv["operator"], rv["value"])
                for rv in verify
            ):
                result.restore_success = True
                return
            await self._sleep(1.0)
        result.restore_success = False
        result.status = "restore_failure"

    def _evaluate(self, test_def, collected: list[PollResult], result) -> None:
        all_passed = True
        for acc in test_def.get("acceptance", []) or []:
            reg = acc["register"]
            metric = acc["metric"]
            values = [p.measurements[reg] for p in collected if reg in p.measurements]
            if not values:
                result.acceptance_results.append(
                    {"register": reg, "metric": metric, "expected": acc["value"], "actual": None, "passed": False}
                )
                all_passed = False
                continue
            actual = compute_metric(metric, values, collected, reg, acc.get("value"))
            passed = evaluate(actual, acc["operator"], acc["value"])
            result.acceptance_results.append(
                {"register": reg, "metric": metric, "expected": acc["value"], "actual": actual, "passed": passed}
            )
            if not passed:
                all_passed = False
        result.status = "passed" if all_passed else "failed"

    # ------------------------------------------------------------------
    # Finalization / events
    # ------------------------------------------------------------------
    def _finish(self, result: TestResult, start: float) -> TestResult:
        result.completed_at = iso_now()
        result.duration_seconds = time.monotonic() - start
        # Fire-and-forget completion event (don't block return on the queue).
        if self._event_queue is not None:
            self._event_queue.put_nowait(
                Event(
                    event_type="test_completed",
                    timestamp=iso_now(),
                    data={"test_id": result.test_id, "status": result.status},
                )
            )
        return result

    async def _emit(self, event_type: str, data: dict) -> None:
        if self._event_queue is not None:
            await self._event_queue.put(Event(event_type=event_type, timestamp=iso_now(), data=data))


async def _default_command_fn(device: DeviceInfo, command: dict) -> None:
    """Write a command to a Modbus device. Lazily builds a PyModbus client."""
    from pymodbus.client import AsyncModbusTcpClient  # noqa: PLC0415 — keep import local

    conn = device.config_context.get("connection", {}) or {}
    host = device.primary_ip or conn.get("host", "")
    client = AsyncModbusTcpClient(host=host, port=int(conn.get("port", 502)))
    try:
        await client.connect()
        address = int(command["address"])
        value = int(command["value"])
        slave = int(conn.get("unit_id", 1))
        if int(command.get("function_code", 6)) == 16:
            await client.write_registers(address=address, values=[value], slave=slave)
        else:
            await client.write_register(address=address, value=value, slave=slave)
    finally:
        client.close()
