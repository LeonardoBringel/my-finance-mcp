"""The single place that turns the SDK access token into a user."""

import re
from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token

MAX_USER_ID = 2_147_483_647


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: int
    jti: str


class MissingIdentityError(Exception):
    """The request carries no usable identity. Never echoes token values."""


def current_user() -> AuthenticatedUser:
    token = get_access_token()
    if token is None:
        raise MissingIdentityError("no access token in request context")

    subject = token.subject
    # fullmatch, not match with "$": "$" also accepts a trailing newline.
    if subject is None or not re.fullmatch(r"[1-9][0-9]*", subject):
        raise MissingIdentityError("access token subject is not a user id")
    user_id = int(subject)
    if user_id > MAX_USER_ID:
        raise MissingIdentityError("access token subject is out of range")

    jti = (token.claims or {}).get("jti")
    if not isinstance(jti, str) or not jti:
        raise MissingIdentityError("access token has no valid jti claim")

    return AuthenticatedUser(user_id=user_id, jti=jti)
