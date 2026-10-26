"""Personal token issue and decode."""

import base64
import json
import re
import traceback
import warnings

import jwt
import pytest
from jwt.warnings import InsecureKeyLengthWarning

from my_finance_mcp.auth.tokens import (
    REQUIRED_CLAIMS,
    TokenClaims,
    TokenRejected,
    decode_token,
    issue_token,
)
from my_finance_mcp.config import Settings

SECRET = "ZZSECRET-token-secret-at-least-32-bytes"
OTHER_SECRET = "ZZSECRET-previous-secret-at-least-32-bytes"
PUBLIC_URL = "https://finance-mcp.x.ts.net/mcp"
ISSUER = "my-finance-mcp"

NOW = 1791500000.7
IAT = 1791500000
EXP = 1799276000  # IAT + 90 * 86400
VALID_JTI = "0123456789abcdef0123456789abcdef"

SPEC_REQUIRED_CLAIMS = ("iss", "aud", "sub", "tv", "jti", "iat", "exp")

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


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_json(value) -> str:
    return b64url(json.dumps(value).encode())


def read_claims(token: str) -> dict:
    return jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience=PUBLIC_URL,
        options={"verify_exp": False, "verify_iat": False},
    )


def rejection(settings, token: str, now: float = EXP - 1) -> str:
    with pytest.raises(TokenRejected) as info:
        decode_token(settings, token, now)
    return info.value.reason


# issue_token (AUTH-01 to AUTH-04)


def test_issued_header_is_hs256_jwt(settings):
    token = issue_token(settings, 42, 3, 90, now=NOW)

    assert jwt.get_unverified_header(token) == {"alg": "HS256", "typ": "JWT"}


def test_issued_token_is_signed_with_token_secret(settings):
    token = issue_token(settings, 42, 3, 90, now=NOW)

    read_claims(token)
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            token,
            OTHER_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False, "verify_exp": False},
        )


def test_issued_payload_has_exactly_the_spec_claims(settings):
    token = issue_token(settings, 42, 3, 90, now=NOW)

    claims = read_claims(token)
    jti = claims.pop("jti")
    assert claims == {
        "iss": "my-finance-mcp",
        "aud": "https://finance-mcp.x.ts.net/mcp",
        "sub": "42",
        "tv": 3,
        "iat": 1791500000,
        "exp": 1799276000,
    }
    assert isinstance(jti, str)


def test_issued_claims_follow_configured_issuer_and_audience():
    settings = make_settings(
        MCP_TOKEN_ISSUER="other-issuer",
        MCP_PUBLIC_URL="https://other.x.ts.net/finance",
    )

    token = issue_token(settings, 7, 0, 1, now=NOW)

    claims = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience="https://other.x.ts.net/finance",
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["iss"] == "other-issuer"
    assert claims["aud"] == "https://other.x.ts.net/finance"
    assert claims["sub"] == "7"
    assert claims["tv"] == 0
    assert claims["exp"] == 1791500000 + 86400


def test_issued_jti_is_32_lowercase_hex(settings):
    token = issue_token(settings, 42, 3, 90, now=NOW)

    assert re.fullmatch(r"[0-9a-f]{32}", read_claims(token)["jti"])


def test_each_issue_has_a_new_jti(settings):
    first = issue_token(settings, 42, 3, 90, now=NOW)
    second = issue_token(settings, 42, 3, 90, now=NOW)

    assert read_claims(first)["jti"] != read_claims(second)["jti"]


@pytest.mark.parametrize("days", [0, 91])
def test_issue_rejects_days_out_of_range(settings, days):
    assert settings.MCP_TOKEN_MAX_DAYS == 90

    with pytest.raises(ValueError):
        issue_token(settings, 42, 3, days, now=NOW)


def test_issue_rejects_days_above_a_lower_ceiling():
    settings = make_settings(MCP_TOKEN_MAX_DAYS=30)

    with pytest.raises(ValueError):
        issue_token(settings, 42, 3, 31, now=NOW)
    assert read_claims(issue_token(settings, 42, 3, 30, now=NOW))["exp"] == (
        1791500000 + 30 * 86400
    )


def test_issue_accepts_max_days(settings):
    token = issue_token(settings, 42, 3, settings.MCP_TOKEN_MAX_DAYS, now=NOW)

    assert read_claims(token)["exp"] == 1791500000 + 90 * 86400


# decode_token: accepted tokens


def test_decode_returns_claims_of_issued_token(settings):
    token = issue_token(settings, 42, 3, 90, now=NOW)
    jti = read_claims(token)["jti"]

    claims = decode_token(settings, token, 1799276000 - 1)

    assert claims == TokenClaims(42, 3, jti, "my-finance-mcp", 1799276000)


def test_decode_accepts_max_subject(settings):
    claims = decode_token(settings, forge(payload(sub="2147483647")), EXP - 1)

    assert claims.user_id == 2147483647


@pytest.mark.parametrize("version", [0, 2147483647])
def test_decode_accepts_token_version_bounds(settings, version):
    claims = decode_token(settings, forge(payload(tv=version)), EXP - 1)

    assert claims.token_version == version


@pytest.mark.parametrize("jti", ["a" * 64, "A-z_9", "x"])
def test_decode_accepts_jti_matching_the_pattern(settings, jti):
    claims = decode_token(settings, forge(payload(jti=jti)), EXP - 1)

    assert claims.jti == jti


def test_decode_accepts_iat_equal_to_now(settings):
    claims = decode_token(settings, forge(payload()), IAT)

    assert claims == TokenClaims(42, 3, VALID_JTI, "my-finance-mcp", EXP)


# decode_token: rejections (AUTH-07 to AUTH-17)


def test_wrong_secret_is_bad_signature(settings):
    token = forge(payload(), secret=OTHER_SECRET)

    assert rejection(settings, token) == "bad_signature"


def test_tampered_payload_is_bad_signature(settings):
    header, _, signature = forge(payload()).split(".")
    tampered = b64url_json(payload(sub="7"))

    assert (
        rejection(settings, f"{header}.{tampered}.{signature}")
        == "bad_signature"
    )


def test_alg_none_is_bad_algorithm(settings):
    header = b64url_json({"alg": "none", "typ": "JWT"})
    token = f"{header}.{b64url_json(payload())}."

    assert rejection(settings, token) == "bad_algorithm"


def test_hs512_is_bad_algorithm(settings):
    token = forge(payload(), algorithm="HS512")

    assert rejection(settings, token) == "bad_algorithm"


@pytest.mark.parametrize(
    "token",
    ["not a token", "", "!!!.@@@.###", "only.two"],
    ids=["text", "empty", "invalid-base64", "two-segments"],
)
def test_undecodable_token_is_malformed(settings, token):
    assert rejection(settings, token) == "malformed"


def test_payload_that_is_not_an_object_is_malformed(settings):
    token = jwt.api_jws.encode(b"[1]", SECRET, algorithm="HS256")

    assert rejection(settings, token) == "malformed"


def test_required_claims_are_the_spec_claims():
    assert set(REQUIRED_CLAIMS) == set(SPEC_REQUIRED_CLAIMS)


@pytest.mark.parametrize("claim", SPEC_REQUIRED_CLAIMS)
def test_missing_claim_is_missing_claim(settings, claim):
    token = forge(payload(**{claim: REMOVE}))

    assert rejection(settings, token) == "missing_claim"


@pytest.mark.parametrize(
    "audience",
    [PUBLIC_URL + "/", "http://finance-mcp.x.ts.net/mcp", [PUBLIC_URL]],
    ids=["trailing-slash", "http", "list"],
)
def test_other_audience_is_wrong_audience(settings, audience):
    token = forge(payload(aud=audience))

    assert rejection(settings, token) == "wrong_audience"


def test_other_issuer_is_wrong_issuer(settings):
    token = forge(payload(iss="other-issuer"))

    assert rejection(settings, token) == "wrong_issuer"


def test_now_equal_to_exp_is_expired(settings):
    assert rejection(settings, forge(payload()), now=EXP) == "expired"


def test_issued_token_is_expired_at_exp(settings):
    token = issue_token(settings, 42, 3, 90, now=NOW)

    assert rejection(settings, token, now=1799276000) == "expired"


def test_iat_after_now_is_not_yet_valid(settings):
    token = forge(payload(iat=IAT + 1))

    assert rejection(settings, token, now=IAT) == "not_yet_valid"


@pytest.mark.parametrize(
    "subject",
    ["0", "042", "-1", "2147483648", "42\n", "abc", 42, "9" * 5000],
    ids=[
        "zero",
        "leading-zero",
        "negative",
        "above-int32",
        "trailing-newline",
        "letters",
        "int",
        "huge",
    ],
)
def test_invalid_subject_is_invalid_claim(settings, subject):
    token = forge(payload(sub=subject))

    assert rejection(settings, token) == "invalid_claim"


@pytest.mark.parametrize(
    "version",
    [True, 3.0, "3", -1, 2147483648],
    ids=["bool", "float", "string", "negative", "above-int32"],
)
def test_invalid_token_version_is_invalid_claim(settings, version):
    token = forge(payload(tv=version))

    assert rejection(settings, token) == "invalid_claim"


@pytest.mark.parametrize(
    "jti",
    ["a" * 65, "abc\ndef", 7, ""],
    ids=["65-chars", "newline", "int", "empty"],
)
def test_invalid_jti_is_invalid_claim(settings, jti):
    token = forge(payload(jti=jti))

    assert rejection(settings, token) == "invalid_claim"


@pytest.mark.parametrize(
    "claim, value",
    [
        ("exp", EXP + 0.5),
        ("exp", str(EXP)),
        ("exp", True),
        ("iat", IAT + 0.5),
        ("iat", str(IAT)),
        ("iat", True),
    ],
    ids=[
        "exp-float",
        "exp-string",
        "exp-bool",
        "iat-float",
        "iat-string",
        "iat-bool",
    ],
)
def test_non_integer_time_claim_is_invalid_claim(settings, claim, value):
    token = forge(payload(**{claim: value}))

    assert rejection(settings, token) == "invalid_claim"


# TokenRejected never carries the token


@pytest.mark.parametrize(
    "token",
    [
        forge(payload(), secret=OTHER_SECRET),
        forge(payload(sub="abc")),
        forge(payload(), algorithm="HS512"),
        "ZZTOKEN-not-a-jwt",
    ],
    ids=["bad-signature", "invalid-claim", "bad-algorithm", "malformed"],
)
def test_rejection_text_never_contains_the_token(settings, token):
    with pytest.raises(TokenRejected) as info:
        decode_token(settings, token, EXP - 1)

    rendered = [
        str(info.value),
        repr(info.value),
        "".join(traceback.format_exception(info.value)),
    ]
    pieces = [token, *(part for part in token.split(".") if part)]
    for text in rendered:
        for piece in pieces:
            assert piece not in text
