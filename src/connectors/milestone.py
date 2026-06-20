"""Module 12: REST API Connectors — Milestone XProtect (CCTV).

Milestone XProtect exposes camera state via its REST gateway at
``/api/rest/v1/cameras``. We normalize each camera to an ``AttestationRecord``
with measurement ``"camera_status"``.

Milestone wraps results in a ``{"array": [...]}`` envelope and authenticates
with a Bearer token (the connector default), so no header override is needed.

Camera state has no per-event time, so records use ``ns_now()``.
``device_id`` is the camera id.
"""

from __future__ import annotations

from typing import Any

from ..types import AttestationRecord
from .base import RestConnector

# Numeric encoding for ``value`` (measurement "camera_status").
#   Recording -> 1.0   (online and recording)
#   Enabled   -> 0.5   (online, not recording)
#   Offline   -> 0.0   (unreachable)
#   Disabled  -> -1.0  (administratively disabled)
# Unknown states map to ``_UNKNOWN``.
_STATE_CODES: dict[str, float] = {
    "Recording": 1.0,
    "Enabled": 0.5,
    "Offline": 0.0,
    "Disabled": -1.0,
}
_UNKNOWN = -99.0


class MilestoneConnector(RestConnector):
    """Milestone XProtect CCTV connector."""

    endpoint_path = "/api/rest/v1/cameras"

    def __init__(self, base_url: str, token: str, **kwargs):
        super().__init__("milestone", base_url, token, **kwargs)

    def _extract_events(self, payload: Any) -> list[dict]:
        if isinstance(payload, dict) and isinstance(payload.get("array"), list):
            return payload["array"]
        return super()._extract_events(payload)

    def _normalize(self, raw: dict) -> AttestationRecord:
        state = str(raw["state"])  # KeyError -> skipped by base poll()
        device_id = str(raw.get("id") or raw.get("name") or "")
        value = _STATE_CODES.get(state, _UNKNOWN)
        return self._record(
            device_id=device_id,
            measurement="camera_status",
            value=value,
            raw=raw,
        )
