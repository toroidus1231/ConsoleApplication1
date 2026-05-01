"""Tests for src/auth.py — JWT encode/decode + role-based permissions."""

from __future__ import annotations

import time
import pytest

from src.auth import (
    AuthError, Role, User, decode_token, encode_token, require_permission,
)

SECRET = b"test-secret-key-32-bytes-long!!!!"


def test_encode_decode_roundtrip():
    token = encode_token(sub="jreyes", roles=[Role.OPERATOR], secret=SECRET)
    user = decode_token(token, secret=SECRET)
    assert user.sub == "jreyes"
    assert Role.OPERATOR in user.roles


def test_decode_rejects_bad_signature():
    token = encode_token(sub="jreyes", roles=[Role.OPERATOR], secret=SECRET)
    with pytest.raises(AuthError, match="bad signature"):
        decode_token(token, secret=b"different-secret-32-bytes-long!!")


def test_decode_rejects_expired_token():
    token = encode_token(sub="jreyes", roles=[Role.OPERATOR],
                         secret=SECRET, ttl_seconds=-10)
    with pytest.raises(AuthError, match="expired"):
        decode_token(token, secret=SECRET)


def test_decode_with_leeway_accepts_just_expired():
    token = encode_token(sub="jreyes", roles=[Role.OPERATOR],
                         secret=SECRET, ttl_seconds=-2)
    # Should pass with 5s leeway
    user = decode_token(token, secret=SECRET, leeway_seconds=5)
    assert user.sub == "jreyes"


def test_decode_rejects_wrong_issuer():
    token = encode_token(sub="jreyes", roles=[Role.OPERATOR],
                         secret=SECRET, issuer="other-issuer")
    with pytest.raises(AuthError, match="unexpected issuer"):
        decode_token(token, secret=SECRET)


def test_decode_rejects_malformed_token():
    with pytest.raises(AuthError, match="malformed"):
        decode_token("not.a.valid.token.at.all", secret=SECRET)


def test_decode_rejects_unknown_role():
    # Manually craft a token with a bogus role
    import base64, hashlib, hmac as hmac_, json
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": "x", "roles": ["bogus_role"], "exp": int(time.time()) + 100,
               "iss": "commissioning-platform", "iat": int(time.time())}

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    h = b64(json.dumps(header).encode())
    p = b64(json.dumps(payload).encode())
    sig = hmac_.new(SECRET, f"{h}.{p}".encode(), hashlib.sha256).digest()
    token = f"{h}.{p}.{b64(sig)}"
    with pytest.raises(AuthError, match="unknown role"):
        decode_token(token, secret=SECRET)


def test_admin_implicitly_has_every_role():
    token = encode_token(sub="alice", roles=[Role.ADMIN], secret=SECRET)
    user = decode_token(token, secret=SECRET)
    assert user.has_role(Role.OPERATOR)
    assert user.has_role(Role.AUDITOR)
    assert user.has_role(Role.COMMISSIONING_LEAD)
    assert user.is_admin


def test_operator_can_resolve_punchlist_but_not_reopen():
    user = User(sub="o", roles=frozenset([Role.OPERATOR]),
                exp=int(time.time()) + 1000, iss="commissioning-platform")
    assert user.can("punchlist.resolve")
    assert not user.can("punchlist.reopen")


def test_auditor_can_read_but_not_run_tests():
    user = User(sub="audit", roles=frozenset([Role.AUDITOR]),
                exp=int(time.time()) + 1000, iss="commissioning-platform")
    assert user.can("attestation.read")
    assert user.can("attestation.certificate_download")
    assert not user.can("test.run")


def test_admin_can_do_everything():
    user = User(sub="admin", roles=frozenset([Role.ADMIN]),
                exp=int(time.time()) + 1000, iss="commissioning-platform")
    assert user.can("config.write")
    assert user.can("user.manage")
    assert user.can("test.run")
    assert user.can("attestation.read")


def test_require_permission_passes_for_authorised_user():
    dep = require_permission("test.run")
    user = User(sub="o", roles=frozenset([Role.OPERATOR]),
                exp=int(time.time()) + 100, iss="commissioning-platform")
    assert dep(user) is user


def test_require_permission_raises_for_unauthorised():
    dep = require_permission("config.write")
    user = User(sub="o", roles=frozenset([Role.OPERATOR]),
                exp=int(time.time()) + 100, iss="commissioning-platform")
    with pytest.raises(AuthError, match="permission denied"):
        dep(user)


def test_user_with_no_roles_only_has_base_perms():
    user = User(sub="x", roles=frozenset(),
                exp=int(time.time()) + 100, iss="commissioning-platform")
    assert user.can("facility.read")
    assert not user.can("test.run")
    assert not user.can("punchlist.resolve")


def test_commissioning_lead_can_override_acceptance():
    user = User(sub="lead", roles=frozenset([Role.COMMISSIONING_LEAD]),
                exp=int(time.time()) + 100, iss="commissioning-platform")
    assert user.can("punchlist.override_acceptance")
    assert user.can("punchlist.reopen")
    assert user.can("report.finalise")
