"""Module 10: Active Test Engine.

Implements the command-measure-verify-restore state machine from contracts
spec §5.3:

    INIT → PRECONDITION_CHECK → MANUAL_WAIT → COMMANDING → MONITORING
        → EVALUATING → RESTORING → RESTORE_VERIFY → COMPLETE

Reads of preconditions / monitor / restore_verify go through Module 3
(``poll_device``). Writes of command / restore go through ``write_register``.
Both are dependency-injected so tests run without a Modbus simulator.

Edge cases handled (spec §5.1, §5.2):
    - Gateway unit_id misroute: identity_register read BEFORE the command write
    - Timeout during active test: 3 consecutive read failures during MONITORING
      trigger a blind restore with abort_reason="device_unreachable"
    - Mechanical failure (stuck breaker): if monitor_duration_seconds elapses
      without any acceptance criterion crossing its threshold, abort_reason
      is "mechanical_failure_or_no_response" — never auto-retry
    - Coolant spill / GPU thermal: any abort_conditions[] match → immediate
      restore with abort_triggered=True
    - Restore failure: restore_verify timeout → status=restore_failure

Each measurement read during MONITORING is submitted to the attestation
queue with the current ``test_id`` (so post-test analysis can pull only
test-tagged records).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .pollers.modbus import poll_device as default_poll
from .pollers.modbus import write_register as default_write
from .types import (
    AttestationRecord,
    DeviceInfo,
    Event,
    PollResult,
    TestRequest,
    TestResult,
)


MANUAL_CONFIRM_TIMEOUT_SECONDS = 1800
DEVICE_UNREACHABLE_FAIL_STREAK = 3
DEFAULT_MONITOR_INTERVAL_MS = 1000
DEFAULT_MONITOR_DURATION_S = 60
DEFAULT_RESTORE_TIMEOUT_S = 30


Poller = Callable[[DeviceInfo], Awaitable[PollResult]]
Writer = Callable[..., Awaitable[tuple[bool, str | None]]]


class TestEngine:
    """Executes one Active Test against one device.

    A single instance can run many tests sequentially; concurrent execution
    of multiple tests is the orchestrator's job (Module 13).
    """

    def __init__(
        self,
        config,
        influx,
        attestation,
        event_queue: asyncio.Queue[Event],
        *,
        poller: Poller | None = None,
        writer: Writer | None = None,
        manual_confirm_timeout: float = MANUAL_CONFIRM_TIMEOUT_SECONDS,
    ):
        self.config = config
        self.influx = influx
        self.attestation = attestation
        self.event_queue = event_queue
        self._poller = poller or default_poll
        self._writer = writer or default_write
        self._manual_confirm_timeout = manual_confirm_timeout
        self._manual_confirms: dict[str, asyncio.Event] = {}
        self._manual_confirmed_by: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def confirm_manual(self, test_id: str, confirmed_by: str = "unknown") -> bool:
        """Satisfy a pending manual confirmation. Returns True if a test was
        waiting on this id, False otherwise."""
        evt = self._manual_confirms.get(test_id)
        if evt is None:
            return False
        self._manual_confirmed_by[test_id] = confirmed_by
        evt.set()
        return True

    async def execute(self, request: TestRequest, device: DeviceInfo) -> TestResult:
        """Run one test from end to end and return its TestResult."""
        result = TestResult(
            test_id=request.test_id,
            device_id=request.device_id,
            test_name=request.test_name,
            started_at=_iso_now(),
        )

        test_def = self._find_test_def(device, request.test_name)
        if test_def is None:
            result.status = "failed"
            result.abort_reason = (
                f"Test '{request.test_name}' not found in Config Context"
            )
            result.completed_at = _iso_now()
            return result

        wall_start = time.time()
        try:
            await self._run_state_machine(request, device, test_def, result)
        finally:
            result.completed_at = _iso_now()
            result.duration_seconds = time.time() - wall_start
            await self._emit(
                "test_completed",
                {"test_id": request.test_id, "status": result.status},
            )
        return result

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------
    async def _run_state_machine(
        self,
        request: TestRequest,
        device: DeviceInfo,
        test_def: dict,
        result: TestResult,
    ) -> None:
        # 1. PRECONDITIONS -----------------------------------------------------
        precondition_poll = await self._poller(device)
        if not self._evaluate_preconditions(test_def, precondition_poll, result):
            result.status = "precondition_failed"
            return

        # 2. MANUAL CONFIRMATION ----------------------------------------------
        if not await self._await_manual_confirmations(request, test_def, result):
            return  # status already set to aborted

        # 3. IDENTITY VERIFY (spec §5.1 gateway unit_id misroute) -------------
        if not await self._verify_identity(device, test_def, precondition_poll, result):
            return  # status set to aborted

        # 4. COMMAND -----------------------------------------------------------
        if not await self._issue_command(device, test_def, result):
            return  # status set to aborted

        await self._emit(
            "test_started",
            {"test_id": request.test_id, "device_id": device.device_id},
        )

        # 5. MONITORING --------------------------------------------------------
        collected = await self._monitor(request, device, test_def, result)

        # 6. RESTORE -----------------------------------------------------------
        await self._restore(device, test_def, result)

        # 7. RESTORE VERIFY ----------------------------------------------------
        await self._restore_verify(device, test_def, result)

        # 8. FINALIZE STATUS (priority: abort > restore_failure > acceptance) -
        self._finalize_status(test_def, collected, result)

    # ------------------------------------------------------------------
    # Phases
    # ------------------------------------------------------------------
    def _find_test_def(self, device: DeviceInfo, name: str) -> dict | None:
        for t in device.config_context.get("active_tests", []) or []:
            if t.get("name") == name:
                return t
        return None

    def _evaluate_preconditions(
        self, test_def: dict, poll: PollResult, result: TestResult
    ) -> bool:
        all_ok = True
        for pre in test_def.get("preconditions", []) or []:
            reg = pre["register"]
            op = pre["operator"]
            expected = pre["value"]
            actual = poll.measurements.get(reg)
            ok = _evaluate(actual, op, expected)
            result.precondition_results.append(
                {"register": reg, "operator": op, "expected": expected,
                 "actual": actual, "passed": ok}
            )
            if not ok:
                all_ok = False
                # Per spec, fail-fast: log all checked so far, stop checking.
                break
        return all_ok

    async def _await_manual_confirmations(
        self, request: TestRequest, test_def: dict, result: TestResult
    ) -> bool:
        prompts = test_def.get("manual_confirmation", []) or []
        for prompt in prompts:
            result.status = "manual_pending"
            await self._emit(
                "manual_confirmation_needed",
                {"test_id": request.test_id, "prompt": prompt},
            )
            evt = asyncio.Event()
            self._manual_confirms[request.test_id] = evt
            try:
                await asyncio.wait_for(evt.wait(), timeout=self._manual_confirm_timeout)
            except asyncio.TimeoutError:
                result.status = "aborted"
                result.abort_reason = "manual_confirmation_timeout"
                result.manual_confirmations.append(
                    {"prompt": prompt, "confirmed_by": None, "confirmed_at": None}
                )
                return False
            finally:
                self._manual_confirms.pop(request.test_id, None)
            confirmed_by = self._manual_confirmed_by.pop(request.test_id, "unknown")
            result.manual_confirmations.append(
                {"prompt": prompt, "confirmed_by": confirmed_by, "confirmed_at": _iso_now()}
            )
        return True

    async def _verify_identity(
        self,
        device: DeviceInfo,
        test_def: dict,
        precondition_poll: PollResult,
        result: TestResult,
    ) -> bool:
        identity = device.config_context.get("identity_register")
        if not identity:
            return True
        reg = identity.get("register")
        expected = identity.get("expected")
        actual = precondition_poll.measurements.get(reg)
        if actual is None:
            id_poll = await self._poller(device)
            actual = id_poll.measurements.get(reg)
        if actual != expected:
            result.status = "aborted"
            result.abort_reason = (
                f"identity_mismatch: expected {expected}, got {actual}"
            )
            return False
        return True

    async def _issue_command(
        self, device: DeviceInfo, test_def: dict, result: TestResult
    ) -> bool:
        cmd = test_def.get("command") or {}
        if not cmd:
            result.status = "aborted"
            result.abort_reason = "no_command_defined"
            return False
        ok, err = await self._writer(
            device,
            address=int(cmd["address"]),
            value=cmd["value"],
            function_code=int(cmd.get("function_code", 6)),
        )
        if not ok:
            result.status = "aborted"
            result.abort_reason = f"command_write_failed: {err}"
            return False
        return True

    async def _monitor(
        self,
        request: TestRequest,
        device: DeviceInfo,
        test_def: dict,
        result: TestResult,
    ) -> list[PollResult]:
        interval_s = test_def.get("monitor_interval_ms", DEFAULT_MONITOR_INTERVAL_MS) / 1000.0
        duration_s = test_def.get("monitor_duration_seconds", DEFAULT_MONITOR_DURATION_S)
        monitor_names = set(test_def.get("monitor", []) or [])
        abort_conditions = test_def.get("abort_conditions", []) or []

        collected: list[PollResult] = []
        consecutive_fails = 0
        start = time.time()

        while (time.time() - start) < duration_s:
            poll = await self._poller(device)

            if not poll.success:
                consecutive_fails += 1
                if consecutive_fails >= DEVICE_UNREACHABLE_FAIL_STREAK:
                    result.abort_triggered = True
                    result.abort_reason = "device_unreachable"
                    return collected
                await asyncio.sleep(interval_s)
                continue

            consecutive_fails = 0
            collected.append(poll)

            for name in monitor_names:
                if name in poll.measurements:
                    await self.attestation.submit(
                        AttestationRecord(
                            timestamp_ns=poll.timestamp_ns,
                            device_id=device.device_id,
                            measurement=name,
                            value=float(poll.measurements[name]),
                            raw_bytes=poll.raw_bytes.get(name, ""),
                            protocol=poll.protocol,
                            source_ip=poll.source_ip,
                            worker_id="",
                            test_id=request.test_id,
                        )
                    )

            await self.influx.write_poll(poll, test_id=request.test_id)

            # Emit a poll_result event so /api/v1/tests/live/{id} SSE
            # subscribers see per-register samples in real time.
            await self._emit("poll_result", {
                "test_id": request.test_id,
                "device_id": device.device_id,
                "measurements": {
                    n: float(poll.measurements[n])
                    for n in monitor_names if n in poll.measurements
                },
                "timestamp_ns": poll.timestamp_ns,
            })

            triggered = self._check_abort_conditions(abort_conditions, poll)
            if triggered is not None:
                result.abort_triggered = True
                result.abort_reason = triggered
                return collected

            await asyncio.sleep(interval_s)

        return collected

    def _check_abort_conditions(self, conditions: list[dict], poll: PollResult) -> str | None:
        for ab in conditions:
            reg = ab["register"]
            op = ab["operator"]
            threshold = ab["value"]
            actual = poll.measurements.get(reg)
            if actual is not None and _evaluate(actual, op, threshold):
                return f"{reg} {op} {threshold} (actual {actual})"
        return None

    async def _restore(self, device: DeviceInfo, test_def: dict, result: TestResult) -> None:
        restore = test_def.get("restore") or {}
        if not restore:
            return
        # Blind restore on monitor failure — per spec: "fire-and-forget the
        # write, don't wait for read confirmation". Failures here are logged
        # to the result but don't change status (RESTORE_VERIFY is the gate).
        ok, err = await self._writer(
            device,
            address=int(restore["address"]),
            value=restore["value"],
            function_code=int(restore.get("function_code", 6)),
        )
        if not ok:
            # Don't override an existing abort_reason from monitoring.
            if not result.abort_reason:
                result.abort_reason = f"restore_write_failed: {err}"

    async def _restore_verify(
        self, device: DeviceInfo, test_def: dict, result: TestResult
    ) -> None:
        """Poll restore_verify[] until all match or restore_timeout_seconds.

        Sets ``result.restore_success`` only — the final ``status`` is decided
        by ``_finalize_status`` so a prior abort_triggered isn't overridden.
        """
        verify = test_def.get("restore_verify", []) or []
        if not verify:
            result.restore_success = True
            return

        timeout = test_def.get("restore_timeout_seconds", DEFAULT_RESTORE_TIMEOUT_S)
        start = time.time()
        while (time.time() - start) < timeout:
            poll = await self._poller(device)
            if poll.success:
                all_ok = True
                for v in verify:
                    actual = poll.measurements.get(v["register"])
                    if not _evaluate(actual, v["operator"], v["value"]):
                        all_ok = False
                        break
                if all_ok:
                    result.restore_success = True
                    return
            await asyncio.sleep(1)

        result.restore_success = False

    def _finalize_status(
        self, test_def: dict, collected: list[PollResult], result: TestResult
    ) -> None:
        """Decide the test's final status. Priority order:

        1. ``abort_triggered`` → ``aborted`` (spec §5.2 watchdog: ABORTED wins)
        2. ``restore_success == False`` → ``restore_failure``
        3. acceptance criteria all pass → ``passed``
        4. any acceptance fails or has no readings → ``failed``
        """
        if result.abort_triggered:
            result.status = "aborted"
            self._record_acceptance_results(test_def, collected, result)
            return

        if not result.restore_success:
            result.status = "restore_failure"
            self._record_acceptance_results(test_def, collected, result)
            return

        all_passed = self._record_acceptance_results(test_def, collected, result)
        result.status = "passed" if all_passed else "failed"

    def _record_acceptance_results(
        self, test_def: dict, collected: list[PollResult], result: TestResult
    ) -> bool:
        all_passed = True
        for acc in test_def.get("acceptance", []) or []:
            reg = acc["register"]
            metric = acc["metric"]
            op = acc["operator"]
            expected = acc["value"]
            values = [p.measurements[reg] for p in collected if reg in p.measurements]

            if not values:
                all_passed = False
                result.acceptance_results.append(
                    {"register": reg, "metric": metric, "operator": op,
                     "expected": expected, "actual": None, "passed": False}
                )
                continue

            actual = _compute_metric(metric, values, collected, reg, expected)
            passed = _evaluate(actual, op, expected)
            result.acceptance_results.append(
                {"register": reg, "metric": metric, "operator": op,
                 "expected": expected, "actual": actual, "passed": passed}
            )
            if not passed:
                all_passed = False
        return all_passed

    async def _emit(self, event_type: str, data: dict) -> None:
        await self.event_queue.put(
            Event(event_type=event_type, timestamp=_iso_now(), data=data)
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


_OPERATORS: dict[str, Callable[[object, object], bool]] = {
    "eq": lambda a, e: a == e,
    "neq": lambda a, e: a != e,
    "gt": lambda a, e: a > e,
    "lt": lambda a, e: a < e,
    "gte": lambda a, e: a >= e,
    "lte": lambda a, e: a <= e,
}


def _evaluate(actual, operator: str, expected) -> bool:
    if actual is None:
        return False
    fn = _OPERATORS.get(operator)
    if fn is None:
        return False
    try:
        return fn(actual, expected)
    except TypeError:
        return False


def _compute_metric(
    metric: str,
    values: list[float],
    collected: list[PollResult],
    register: str,
    threshold,
):
    if metric == "max":
        return max(values)
    if metric == "min":
        return min(values)
    if metric == "avg":
        return sum(values) / len(values)
    if metric == "delta":
        return abs(values[-1] - values[0])
    if metric == "settled_value":
        tail = values[-(max(1, len(values) // 10)):]
        return sum(tail) / len(tail)
    if metric == "time_to_value":
        # Milliseconds from the first reading to the first sample crossing the
        # threshold. Inf if it never crossed.
        first_ts = collected[0].timestamp_ns
        for poll in collected:
            v = poll.measurements.get(register)
            if v is None:
                continue
            if isinstance(threshold, (int, float)) and v >= threshold:
                return (poll.timestamp_ns - first_ts) / 1e6
        return float("inf")
    if metric == "rate_of_change":
        if len(values) < 2:
            return 0.0
        dt_seconds = (collected[-1].timestamp_ns - collected[0].timestamp_ns) / 1e9
        if dt_seconds == 0:
            return 0.0
        return (values[-1] - values[0]) / dt_seconds
    return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
