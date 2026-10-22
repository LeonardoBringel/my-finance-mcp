"""Fail-closed identity resolved from the SDK access token."""

from pathlib import Path

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import (
    AuthenticatedUser as SdkAuthenticatedUser,
)
from mcp.server.auth.provider import AccessToken

from my_finance_mcp.auth.identity import (
    AuthenticatedUser,
    MissingIdentityError,
    current_user,
)

SRC = Path(__file__).resolve().parents[2] / "src"

INVALID_SUBJECTS = [None, "0", "042", "-1", "2147483648", "abc", "42\n"]

INVALID_CLAIMS = [None, {}, {"jti": ""}, {"jti": 123}]


def resolve(subject, claims):
    token = AccessToken(
        token="opaque",
        client_id="client",
        scopes=[],
        subject=subject,
        claims=claims,
    )
    reset_token = auth_context_var.set(SdkAuthenticatedUser(token))
    try:
        return current_user()
    finally:
        auth_context_var.reset(reset_token)


def test_valid_token_returns_user():
    user = resolve("42", {"jti": "j-1"})

    assert user == AuthenticatedUser(user_id=42, jti="j-1")
    assert type(user.user_id) is int


def test_max_int32_subject_is_accepted():
    user = resolve("2147483647", {"jti": "j-1"})

    assert user == AuthenticatedUser(user_id=2147483647, jti="j-1")


def test_missing_token_raises():
    assert auth_context_var.get() is None

    with pytest.raises(MissingIdentityError):
        current_user()


@pytest.mark.parametrize("subject", INVALID_SUBJECTS)
def test_invalid_subject_raises(subject):
    with pytest.raises(MissingIdentityError):
        resolve(subject, {"jti": "j-1"})


@pytest.mark.parametrize("claims", INVALID_CLAIMS)
def test_invalid_jti_raises(claims):
    with pytest.raises(MissingIdentityError):
        resolve("42", claims)


@pytest.mark.parametrize(
    ("subject", "claims", "received"),
    [
        ("2147483648", {"jti": "ZZJTI-subject-case"}, ["2147483648", "ZZJTI"]),
        ("987654", {"jti": 123456789}, ["987654", "123456789"]),
    ],
)
def test_error_message_omits_received_values(subject, claims, received):
    with pytest.raises(MissingIdentityError) as exc_info:
        resolve(subject, claims)

    message = str(exc_info.value)
    assert message
    for value in received:
        assert value not in message


def test_only_identity_module_reads_access_token():
    readers = sorted(
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "get_access_token" in path.read_text(encoding="utf-8")
    )

    assert readers == ["my_finance_mcp/auth/identity.py"]
