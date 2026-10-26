"""The SDK token verifier for personal tokens, and the matching AuthSettings."""

import logging
import time
from collections.abc import Callable

import anyio.to_thread
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from my_finance_mcp.auth.tokens import TokenRejected, decode_token
from my_finance_mcp.config import Settings
from my_finance_mcp.data.db import DatabaseUnavailableError

logger = logging.getLogger(__name__)

SCOPE = "finance:read"
CLIENT_ID = "pat"


class PersonalTokenVerifier:
    """Decodes the token, then checks its version against `users`.

    Logs only the rejection reason, never the token. Keeps no state between
    calls, so a revocation takes effect on the next request.
    """

    def __init__(
        self,
        settings: Settings,
        lookup: Callable[[int], int | None],
        clock: Callable[[], float] = time.time,
    ):
        self._settings = settings
        self._lookup = lookup
        self._clock = clock

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = decode_token(self._settings, token, self._clock())
        except TokenRejected as exc:
            logger.info("token rejected reason=%s", exc.reason)
            return None

        # The lookup is synchronous I/O: keep it off the event loop thread.
        # Any other exception is a bug and propagates.
        try:
            version = await anyio.to_thread.run_sync(
                self._lookup, claims.user_id
            )
        except DatabaseUnavailableError:
            logger.error("token rejected reason=db_unavailable")
            return None

        if version is None:
            logger.info("token rejected reason=unknown_user")
            return None
        if version != claims.token_version:
            logger.info("token rejected reason=revoked")
            return None

        # subject and claims["jti"] are what current_user() reads.
        return AccessToken(
            token=token,
            client_id=CLIENT_ID,
            scopes=[SCOPE],
            subject=str(claims.user_id),
            expires_at=claims.expires_at,
            resource=self._settings.MCP_PUBLIC_URL,
            claims={"jti": claims.jti, "iss": claims.issuer},
        )


def build_auth_settings(settings: Settings) -> AuthSettings:
    # validate_token_resource=False: decode_token already checks the audience,
    # and leaving it unset makes the SDK warn.
    return AuthSettings(
        issuer_url=settings.MCP_AUTH_ISSUER_URL,
        resource_server_url=settings.MCP_PUBLIC_URL,
        required_scopes=[SCOPE],
        validate_token_resource=False,
    )
