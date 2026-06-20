"""Module 12: REST API Connectors.

External systems (access control, CCTV, change management, CMMS) expose REST
APIs. Each connector fetches recent events, normalizes them to
``AttestationRecord``, and submits them to the attestation engine.

Re-exports the shared base and the four vendor connectors for convenience.
"""

from __future__ import annotations

from .base import RestConnector
from .genetec import GenetecConnector
from .maximo import MaximoConnector
from .milestone import MilestoneConnector
from .servicenow import ServiceNowConnector

__all__ = [
    "RestConnector",
    "GenetecConnector",
    "ServiceNowConnector",
    "MaximoConnector",
    "MilestoneConnector",
]
