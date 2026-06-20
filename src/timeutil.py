"""Shared time helpers.

The contracts spec §12 is emphatic: every timestamp in the platform is UTC.
Attestation records use ``time.time_ns()`` (UTC nanoseconds); API/event payloads
use ISO-8601 with a ``Z`` suffix. Centralised here so no module rolls its own.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


def ns_now() -> int:
    """UTC wall-clock time in nanoseconds. Source of truth for attestation."""
    return time.time_ns()


def iso_now() -> str:
    """Current UTC time as ISO-8601 with a ``Z`` suffix, millisecond precision."""
    return iso_from_ns(ns_now())


def iso_from_ns(timestamp_ns: int) -> str:
    """Render a nanosecond UTC timestamp as ISO-8601 ``...Z`` (millisecond precision)."""
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
