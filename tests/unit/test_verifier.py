"""Personal token verifier and the SDK auth settings."""

import base64
import json
import logging
import threading
import time
import warnings

import anyio
import jwt
import pytest
from jwt.warnings import InsecureKeyLengthWarning
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import (
    AuthenticatedUser as SdkAuthenticatedUser,
)
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from my_finance_mcp.auth.identity import AuthenticatedUser, current_user
from my_finance_mcp.auth.tokens import issue_token
from my_finance_mcp.auth.verifier import (
    CLIENT_ID,
    SCOPE,
    PersonalTokenVerifier,
    build_auth_settings,
)
from my_finance_mcp.config import Settings
from my_finance_mcp.data.db import DatabaseUnavailableError

SECRET = "ZZSECRET-token-secret-at-least-32-bytes"
OTHER_SECRET = "ZZSECRET-previous-secret-at-least-32-bytes"
PUBLIC_URL = "https://finance-mcp.x.ts.net/mcp"
ISSUER = "my-finance-mcp"

NOW = 1791500000.7
IAT = 1791500000
EXP = 1799276000  # IAT + 90 * 86400
VALID_JTI = "0123456789abcdef0123456789abcdef"

SETTINGS_VALUES = {
    "DB_HOST": "db.internal",
    "DB_PORT": 6543,
    "DB_NAME": "finance",
    "DB_USER": "my_finance_mcp",
    "DB_PASSWORD": "ZZSECRET-db-pass",
    "FERNET_KEY": base64.urlsafe_b64encode(
        b"ZZSECRET-fernet-key-32-bytes-ok!"
    ).decode(),
    "MCP_TOKEN_SECRET": SECRET,
    "MCP_PUBLIC_URL": PUBLIC_URL,
    "MCP_ALLOWED_HOSTS": ["finance-mcp.x.ts.net"],
}

REMOVE = object()


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def debug_logs(caplog):
    caplog.set_level(logging.DEBUG)


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**SETTINGS_VALUES, **overrides})


@pytest.fixture
def settings():
    return make_settings()


def payload(**overrides) -> dict:
    claims = {
        "iss": ISSUER,
        "aud": PUBLIC_URL,
        "sub": "42",
        "tv": 3,
        "jti": VALID_JTI,
        "iat": IAT,
        "exp": EXP,
    }
    claims.update(overrides)
    return {
        name: value for name, value in claims.items() if value is not REMOVE
    }


def forge(claims: dict, secret: str = SECRET, algorithm: str = "HS256") -> str:
    # HS512 with the real secret warns the key is short for SHA-512; the
    # attack being simulated reuses that secret on purpose.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureKeyLengthWarning)
        return jwt.encode(claims, secret, algorithm=algorithm)


def b64url_json(value) -> str:
    raw = json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class FakeLookup:
    """Stands in for the users query: records each user_id and its thread."""

    def __init__(self, result=3):
        self.result = result
        self.calls = []

    def __call__(self, user_id):
        self.calls.append((user_id, threading.get_ident()))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    @property
    def user_ids(self):
        return [user_id for user_id, _ in self.calls]


def fixed_clock(value: float):
    return lambda: value


def run_verify(verifier, token):
    """Returns the verifier result and the event loop thread ident."""

    async def main():
        loop_thread = threading.get_ident()
        return await verifier.verify_token(token), loop_thread

    return anyio.run(main)


def verify(settings, token, lookup, now: float = EXP - 1):
    verifier = PersonalTokenVerifier(settings, lookup, clock=fixed_clock(now))
    result, _ = run_verify(verifier, token)
    return result


def logged_lines(caplog):
    """INFO and above, from any logger, as (level, message)."""
    return [
        (record.levelname, record.getMessage())
        for record in caplog.records
        if record.levelno >= logging.INFO
    ]


def assert_nothing_sensitive_logged(caplog, token):
    # The empty token has no marker to search for; "" is in every string.
    pieces = [SECRET, *(part for part in [token, *token.split(".")] if part)]
    for record in caplog.records:
        rendered = [
            record.getMessage(),
            repr(record.args),
            record.exc_text or "",
            logging.Formatter().format(record),
        ]
        for text in rendered:
            for piece in pieces:
                assert piece not in text
    for piece in pieces:
        assert piece not in caplog.text


def expected_access_token(token, jti=VALID_JTI) -> AccessToken:
    return AccessToken(
        token=token,
        client_id="pat",
        scopes=["finance:read"],
        subject="42",
        expires_at=1799276000,
        resource="https://finance-mcp.x.ts.net/mcp",
        claims={"jti": jti, "iss": "my-finance-mcp"},
    )


# Accepted token (AUTH-06)


def test_constants_match_the_spec():
    assert SCOPE == "finance:read"
    assert CLIENT_ID == "pat"


def test_valid_token_returns_the_spec_access_token(settings, caplog):
    token = forge(payload())
    lookup = FakeLookup(result=3)

    result = verify(settings, token, lookup)

    assert type(result) is AccessToken
    assert result == expected_access_token(token)
    assert lookup.user_ids == [42]
    assert_nothing_sensitive_logged(caplog, token)


def test_issued_token_is_accepted(settings, caplog):
    token = issue_token(settings, 42, 3, 90, now=NOW)
    jti = jwt.decode(token, options={"verify_signature": False})["jti"]

    result = verify(settings, token, FakeLookup(result=3), now=EXP - 1)

    assert result == expected_access_token(token, jti=jti)
    assert not [
        line for line in logged_lines(caplog) if "token rejected" in line[1]
    ]
    assert_nothing_sensitive_logged(caplog, token)


def test_access_token_follows_configured_issuer_and_public_url(caplog):
    settings = make_settings(
        MCP_TOKEN_ISSUER="other-issuer",
        MCP_PUBLIC_URL="https://other.x.ts.net/finance",
    )
    token = forge(
        payload(iss="other-issuer", aud="https://other.x.ts.net/finance")
    )

    result = verify(settings, token, FakeLookup(result=3))

    assert result == AccessToken(
        token=token,
        client_id="pat",
        scopes=["finance:read"],
        subject="42",
        expires_at=1799276000,
        resource="https://other.x.ts.net/finance",
        claims={"jti": VALID_JTI, "iss": "other-issuer"},
    )
    assert_nothing_sensitive_logged(caplog, token)


def test_max_subject_reaches_the_lookup_and_is_accepted(settings, caplog):
    token = forge(payload(sub="2147483647"))
    lookup = FakeLookup(result=3)

    result = verify(settings, token, lookup)

    assert lookup.user_ids == [2147483647]
    assert result.subject == "2147483647"
    assert_nothing_sensitive_logged(caplog, token)


@pytest.mark.parametrize("version", [0, 2147483647])
def test_token_version_bounds_are_accepted(settings, caplog, version):
    token = forge(payload(tv=version))

    result = verify(settings, token, FakeLookup(result=version))

    assert result == expected_access_token(token)
    assert_nothing_sensitive_logged(caplog, token)


def test_accepted_token_resolves_to_the_current_user(settings):
    token = forge(payload())
    result = verify(settings, token, FakeLookup(result=3))

    reset_token = auth_context_var.set(SdkAuthenticatedUser(result))
    try:
        user = current_user()
    finally:
        auth_context_var.reset(reset_token)

    assert user == AuthenticatedUser(user_id=42, jti=VALID_JTI)


# Injected clock (AUTH-09)


def test_one_second_before_exp_is_accepted(settings):
    token = forge(payload())

    assert verify(settings, token, FakeLookup(), now=EXP - 1) is not None


def test_clock_equal_to_exp_is_expired(settings, caplog):
    token = forge(payload())
    lookup = FakeLookup()

    result = verify(settings, token, lookup, now=EXP)

    assert result is None
    assert lookup.calls == []
    assert logged_lines(caplog) == [("INFO", "token rejected reason=expired")]
    assert_nothing_sensitive_logged(caplog, token)


def test_clock_is_read_on_each_call(settings, caplog):
    token = forge(payload())
    instants = iter([EXP - 1, EXP])
    verifier = PersonalTokenVerifier(
        settings, FakeLookup(), clock=lambda: next(instants)
    )

    first, _ = run_verify(verifier, token)
    second, _ = run_verify(verifier, token)

    assert first == expected_access_token(token)
    assert second is None
    assert logged_lines(caplog) == [("INFO", "token rejected reason=expired")]


def test_default_clock_is_wall_time(settings, caplog):
    token = issue_token(settings, 42, 3, 1, now=time.time())
    verifier = PersonalTokenVerifier(settings, FakeLookup(result=3))

    result, _ = run_verify(verifier, token)

    assert result is not None
    assert result.subject == "42"
    assert_nothing_sensitive_logged(caplog, token)


# decode_token rejections (AUTH-07 to AUTH-17, AUTH-22)

ALG_NONE_TOKEN = (
    f"{b64url_json({'alg': 'none', 'typ': 'JWT'})}.{b64url_json(payload())}."
)

DECODE_REJECTIONS = [
    ("wrong-secret", forge(payload(), secret=OTHER_SECRET), "bad_signature"),
    ("alg-none", ALG_NONE_TOKEN, "bad_algorithm"),
    ("hs512", forge(payload(), algorithm="HS512"), "bad_algorithm"),
    ("empty", "", "malformed"),
    ("text", "ZZTOKEN-not-a-jwt", "malformed"),
    ("invalid-base64", "!!!.@@@.###", "malformed"),
    (
        "aud-trailing-slash",
        forge(payload(aud=PUBLIC_URL + "/")),
        "wrong_audience",
    ),
    (
        "aud-http",
        forge(payload(aud="http://finance-mcp.x.ts.net/mcp")),
        "wrong_audience",
    ),
    ("other-issuer", forge(payload(iss="other-issuer")), "wrong_issuer"),
    ("no-iss", forge(payload(iss=REMOVE)), "missing_claim"),
    ("no-aud", forge(payload(aud=REMOVE)), "missing_claim"),
    ("no-sub", forge(payload(sub=REMOVE)), "missing_claim"),
    ("no-tv", forge(payload(tv=REMOVE)), "missing_claim"),
    ("no-jti", forge(payload(jti=REMOVE)), "missing_claim"),
    ("no-iat", forge(payload(iat=REMOVE)), "missing_claim"),
    ("no-exp", forge(payload(exp=REMOVE)), "missing_claim"),
    ("iat-future", forge(payload(iat=EXP)), "not_yet_valid"),
    ("sub-zero", forge(payload(sub="0")), "invalid_claim"),
    ("sub-leading-zero", forge(payload(sub="042")), "invalid_claim"),
    ("sub-negative", forge(payload(sub="-1")), "invalid_claim"),
    ("sub-above-int32", forge(payload(sub="2147483648")), "invalid_claim"),
    ("sub-newline", forge(payload(sub="42\n")), "invalid_claim"),
    ("sub-letters", forge(payload(sub="abc")), "invalid_claim"),
    ("tv-bool", forge(payload(tv=True)), "invalid_claim"),
    ("tv-float", forge(payload(tv=3.0)), "invalid_claim"),
    ("tv-string", forge(payload(tv="3")), "invalid_claim"),
    ("tv-negative", forge(payload(tv=-1)), "invalid_claim"),
    ("tv-above-int32", forge(payload(tv=2147483648)), "invalid_claim"),
    ("jti-65-chars", forge(payload(jti="a" * 65)), "invalid_claim"),
    ("jti-newline", forge(payload(jti="abc\ndef")), "invalid_claim"),
]


@pytest.mark.parametrize(
    "token, reason",
    [(token, reason) for _, token, reason in DECODE_REJECTIONS],
    ids=[case_id for case_id, _, _ in DECODE_REJECTIONS],
)
def test_decode_rejection_returns_none_without_lookup(
    settings, caplog, token, reason
):
    lookup = FakeLookup(result=3)

    result = verify(settings, token, lookup, now=EXP - 1)

    assert result is None
    assert lookup.calls == []
    assert logged_lines(caplog) == [("INFO", f"token rejected reason={reason}")]
    assert_nothing_sensitive_logged(caplog, token)


def test_token_signed_before_secret_rotation_is_bad_signature(caplog):
    token = forge(payload())
    rotated = make_settings(
        MCP_TOKEN_SECRET="ZZSECRET-rotated-secret-at-least-32-bytes"
    )
    lookup = FakeLookup()

    result = verify(rotated, token, lookup)

    assert result is None
    assert lookup.calls == []
    assert logged_lines(caplog) == [
        ("INFO", "token rejected reason=bad_signature")
    ]
    assert "ZZSECRET-rotated-secret-at-least-32-bytes" not in caplog.text


# users lookup (AUTH-18 to AUTH-21)


def test_unknown_user_returns_none(settings, caplog):
    token = forge(payload())
    lookup = FakeLookup(result=None)

    result = verify(settings, token, lookup)

    assert result is None
    assert lookup.user_ids == [42]
    assert logged_lines(caplog) == [
        ("INFO", "token rejected reason=unknown_user")
    ]
    assert_nothing_sensitive_logged(caplog, token)


@pytest.mark.parametrize("stored", [4, 2, 0])
def test_other_token_version_is_revoked(settings, caplog, stored):
    token = forge(payload(tv=3))
    lookup = FakeLookup(result=stored)

    result = verify(settings, token, lookup)

    assert result is None
    assert lookup.user_ids == [42]
    assert logged_lines(caplog) == [("INFO", "token rejected reason=revoked")]
    assert_nothing_sensitive_logged(caplog, token)


def test_version_bump_revokes_a_token_accepted_before(settings, caplog):
    token = forge(payload(tv=3))
    lookup = FakeLookup(result=3)
    verifier = PersonalTokenVerifier(
        settings, lookup, clock=fixed_clock(EXP - 1)
    )

    first, _ = run_verify(verifier, token)
    lookup.result = 4
    second, _ = run_verify(verifier, token)

    assert first == expected_access_token(token)
    assert second is None
    assert lookup.user_ids == [42, 42]
    assert logged_lines(caplog) == [("INFO", "token rejected reason=revoked")]


def test_lookup_runs_once_with_int_user_id_off_the_loop_thread(settings):
    token = forge(payload(sub="7"))
    lookup = FakeLookup(result=3)
    verifier = PersonalTokenVerifier(
        settings, lookup, clock=fixed_clock(EXP - 1)
    )

    result, loop_thread = run_verify(verifier, token)

    assert result is not None
    assert len(lookup.calls) == 1
    user_id, lookup_thread = lookup.calls[0]
    assert user_id == 7
    assert type(user_id) is int
    assert lookup_thread != loop_thread


def test_database_unavailable_returns_none_and_logs_error(settings, caplog):
    token = forge(payload())
    lookup = FakeLookup(result=DatabaseUnavailableError())

    result = verify(settings, token, lookup)

    assert result is None
    assert lookup.user_ids == [42]
    assert logged_lines(caplog) == [
        ("ERROR", "token rejected reason=db_unavailable")
    ]
    assert_nothing_sensitive_logged(caplog, token)


def test_unexpected_lookup_error_propagates(settings, caplog):
    token = forge(payload())
    error = RuntimeError("boom")
    lookup = FakeLookup(result=error)

    with pytest.raises(RuntimeError) as info:
        verify(settings, token, lookup)

    assert info.value is error
    assert lookup.user_ids == [42]
    assert_nothing_sensitive_logged(caplog, token)


# AuthSettings (AUTH-26, AUTH-28, AUTH-32)


def test_auth_settings_match_the_spec(settings):
    auth = build_auth_settings(settings)

    assert type(auth) is AuthSettings
    assert str(auth.issuer_url) == "https://finance-mcp.x.ts.net"
    assert str(auth.resource_server_url) == "https://finance-mcp.x.ts.net/mcp"
    assert auth.required_scopes == ["finance:read"]
    assert auth.validate_token_resource is False


def test_auth_settings_follow_configured_urls():
    settings = make_settings(
        MCP_PUBLIC_URL="https://other.x.ts.net/finance",
        MCP_AUTH_ISSUER_URL="https://auth.x.ts.net",
    )

    auth = build_auth_settings(settings)

    assert str(auth.issuer_url) == "https://auth.x.ts.net"
    assert str(auth.resource_server_url) == "https://other.x.ts.net/finance"


def test_auth_settings_emit_no_warning(settings):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("error")
        build_auth_settings(settings)

    assert caught == []
