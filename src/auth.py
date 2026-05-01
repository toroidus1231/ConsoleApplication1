"""Auth module: bearer-token + role-based access control.

Replaces the dev-mode single-API-key auth with proper user identities
and roles. Token format: signed JWT (HS256) carrying {sub, roles, exp,
iat, iss}. Tokens are issued by an external SSO/OIDC provider in
production; for self-hosted deployments the platform issues them via
POST /auth/token after verifying credentials against an LDAP/AD store.

Roles (per spec §6.4 Authorisation matrix):

    OPERATOR       — read everything, sign off checklists, mark punch
                      items resolved, schedule tests, confirm manual
                      test steps.
    COMMISSIONING_LEAD — operator + reopen punch items, override
                      acceptance, generate final reports.
    ADMIN          — everything + edit Config Contexts, run discovery,
                      import BIM, generate Config Context from PDF.
    AUDITOR        — read everything (including attestation chain
                      certificate downloads) but cannot mutate.

Acceptable token TTL: 8 hours operator, 24 hours admin/auditor.

Key rotation: tokens are signed with the active key; keys rotate every
30 days. Old keys remain valid for verification for one rotation
window. See src/auth_keys.py (separate module) for key storage —
this module only handles encode/decode/role check.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Role(str, Enum):
    OPERATOR = "operator"
    COMMISSIONING_LEAD = "commissioning_lead"
    ADMIN = "admin"
    AUDITOR = "auditor"


@dataclass(frozen=True)
class User:
    sub: str            # subject — username or email
    roles: frozenset[Role]
    exp: int            # epoch seconds
    iss: str            # token issuer

    @property
    def is_admin(self) -> bool:
        return Role.ADMIN in self.roles

    def has_role(self, role: Role) -> bool:
        return role in self.roles or self.is_admin

    def can(self, permission: str) -> bool:
        return permission in self._permissions()

    def _permissions(self) -> set[str]:
        perms: set[str] = set(_ROLE_PERMISSIONS["base"])
        for r in self.roles:
            perms.update(_ROLE_PERMISSIONS.get(r.value, ()))
        return perms


class AuthError(Exception):
    """Raised for invalid token, expired token, missing claim, role miss."""


# ---------------------------------------------------------------------------
# Permissions — central registry; FastAPI deps ask `user.can("punchlist.resolve")`
# ---------------------------------------------------------------------------

_ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "base": (
        "facility.read", "device.read", "test.read", "punchlist.read",
        "checklist.read", "attestation.read", "telemetry.read", "report.read",
    ),
    Role.OPERATOR.value: (
        "test.run", "test.confirm", "punchlist.resolve",
        "checklist.signoff", "report.generate",
    ),
    Role.COMMISSIONING_LEAD.value: (
        "test.run", "test.confirm", "punchlist.resolve", "punchlist.reopen",
        "punchlist.override_acceptance", "checklist.signoff",
        "report.generate", "report.finalise",
    ),
    Role.AUDITOR.value: (
        "attestation.certificate_download", "audit.review",
    ),
    Role.ADMIN.value: (
        "test.run", "test.confirm", "punchlist.resolve", "punchlist.reopen",
        "punchlist.override_acceptance", "checklist.signoff",
        "report.generate", "report.finalise",
        "config.write", "discovery.run", "bim.import", "config.generate_from_pdf",
        "user.manage", "audit.review", "attestation.certificate_download",
    ),
}


# ---------------------------------------------------------------------------
# JWT (HS256) encode / decode
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


def encode_token(*, sub: str, roles: list[Role], secret: bytes,
                 ttl_seconds: int = 8 * 3600,
                 issuer: str = "commissioning-platform") -> str:
    """Create a signed JWT. Used at login / token-issuance time."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": sub,
        "roles": [r.value for r in roles],
        "iat": now,
        "exp": now + ttl_seconds,
        "iss": issuer,
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret, f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def decode_token(token: str, *, secret: bytes,
                 expected_issuer: str = "commissioning-platform",
                 leeway_seconds: int = 0,
                 now: Optional[int] = None) -> User:
    """Verify signature, expiry, issuer; return a User. Raises AuthError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("malformed token")
    h_b64, p_b64, sig_b64 = parts
    expected = hmac.new(secret, f"{h_b64}.{p_b64}".encode(), hashlib.sha256).digest()
    actual = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected, actual):
        raise AuthError("bad signature")
    try:
        payload = json.loads(_b64url_decode(p_b64))
    except (ValueError, json.JSONDecodeError) as e:
        raise AuthError(f"unparseable payload: {e}") from e

    for claim in ("sub", "roles", "exp", "iss"):
        if claim not in payload:
            raise AuthError(f"missing claim: {claim}")

    if payload["iss"] != expected_issuer:
        raise AuthError(f"unexpected issuer: {payload['iss']!r}")

    cur = now if now is not None else int(time.time())
    if cur > payload["exp"] + leeway_seconds:
        raise AuthError("token expired")

    try:
        roles = frozenset(Role(r) for r in payload["roles"])
    except ValueError as e:
        raise AuthError(f"unknown role: {e}") from e

    return User(sub=payload["sub"], roles=roles,
                exp=int(payload["exp"]), iss=payload["iss"])


# ---------------------------------------------------------------------------
# FastAPI dependency factory
# ---------------------------------------------------------------------------


def require_permission(permission: str):
    """Returns a FastAPI dependency that 403s if the user lacks `permission`."""
    def dep(user: User) -> User:
        if not user.can(permission):
            raise AuthError(f"permission denied: {permission}")
        return user
    return dep
