"""Personal token issue and decode, pure and without I/O."""

import re
import uuid
from dataclasses import dataclass

import jwt

from my_finance_mcp.config import Settings

ALGORITHM = "HS256"
SECONDS_PER_DAY = 86400
MAX_INT32 = 2_147_483_647

REQUIRED_CLAIMS = ("iss", "aud", "sub", "tv", "jti", "iat", "exp")

SUBJECT_PATTERN = r"[1-9][0-9]*"
JTI_PATTERN = r"[A-Za-z0-9_-]{1,64}"

# Subclasses before their bases: InvalidSignatureError is a DecodeError.
REJECTIONS = (
    (jwt.InvalidSignatureError, "bad_signature"),
    (jwt.InvalidAlgorithmError, "bad_algorithm"),
    (jwt.DecodeError, "malformed"),
    (jwt.MissingRequiredClaimError, "missing_claim"),
    (jwt.InvalidAudienceError, "wrong_audience"),
    (jwt.InvalidIssuerError, "wrong_issuer"),
    (jwt.ImmatureSignatureError, "not_yet_valid"),
)


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    token_version: int
    jti: str
    issuer: str
    expires_at: int


class TokenRejected(Exception):
    """The token is not acceptable. Carries only the reason, never the token."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def issue_token(
    settings: Settings, user_id: int, token_version: int, days: int, now: float
) -> str:
    if not 1 <= days <= settings.MCP_TOKEN_MAX_DAYS:
        raise ValueError(
            f"days must be between 1 and {settings.MCP_TOKEN_MAX_DAYS}"
        )
    issued_at = int(now)
    payload = {
        "iss": settings.MCP_TOKEN_ISSUER,
        "aud": settings.MCP_PUBLIC_URL,
        "sub": str(user_id),
        "tv": token_version,
        "jti": uuid.uuid4().hex,
        "iat": issued_at,
        "exp": issued_at + days * SECONDS_PER_DAY,
    }
    return jwt.encode(
        payload,
        settings.MCP_TOKEN_SECRET.get_secret_value(),
        algorithm=ALGORITHM,
        headers={"typ": "JWT"},
    )


def _is_int(value) -> bool:
    # bool is a subclass of int, and JSON true must not pass as 1.
    return type(value) is int


def decode_token(settings: Settings, token: str, now: float) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.MCP_TOKEN_SECRET.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=settings.MCP_PUBLIC_URL,
            issuer=settings.MCP_TOKEN_ISSUER,
            # exp and iat are checked below against the injected clock.
            options={
                "require": list(REQUIRED_CLAIMS),
                "verify_exp": False,
                "verify_iat": False,
                "strict_aud": True,
            },
        )
    except jwt.InvalidTokenError as exc:
        reason = next(
            (code for kind, code in REJECTIONS if isinstance(exc, kind)),
            "invalid_claim",
        )
        # from None: the PyJWT message stays out of the traceback.
        raise TokenRejected(reason) from None

    expires_at = payload["exp"]
    issued_at = payload["iat"]
    if not _is_int(expires_at) or not _is_int(issued_at):
        raise TokenRejected("invalid_claim")
    # Same criterion as the SDK bearer backend, with no leeway.
    if now >= expires_at:
        raise TokenRejected("expired")
    if issued_at > now:
        raise TokenRejected("not_yet_valid")

    subject = payload["sub"]
    # fullmatch, not match with "$": "$" also accepts a trailing newline.
    if not isinstance(subject, str) or not re.fullmatch(
        SUBJECT_PATTERN, subject
    ):
        raise TokenRejected("invalid_claim")
    # Length first: int() refuses strings past 4300 digits with ValueError.
    if len(subject) > len(str(MAX_INT32)) or int(subject) > MAX_INT32:
        raise TokenRejected("invalid_claim")
    user_id = int(subject)

    token_version = payload["tv"]
    if not _is_int(token_version) or not 0 <= token_version <= MAX_INT32:
        raise TokenRejected("invalid_claim")

    jti = payload["jti"]
    if not isinstance(jti, str) or not re.fullmatch(JTI_PATTERN, jti):
        raise TokenRejected("invalid_claim")

    return TokenClaims(
        user_id=user_id,
        token_version=token_version,
        jti=jti,
        issuer=payload["iss"],
        expires_at=expires_at,
    )
