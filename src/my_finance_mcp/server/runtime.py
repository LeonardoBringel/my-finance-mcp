"""The single pipeline every tool call goes through."""

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Generic, TypeVar

from mcp_types import CallToolResult, TextContent

from my_finance_mcp.auth.identity import AuthenticatedUser, current_user
from my_finance_mcp.domain.clock import Clock
from my_finance_mcp.server.schemas.envelope import (
    D,
    Meta,
    Period,
    ToolResult,
    WarningCode,
)

logger = logging.getLogger(__name__)

A = TypeVar("A")


class ToolInputError(Exception):
    """A business error the model can correct, returned as coded text."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        # Only the code goes to Exception.args: message and hint may carry
        # user data and must not surface in any log or traceback.
        super().__init__(code)
        self.code = code
        self.message = message
        self.hint = hint

    @property
    def text(self) -> str:
        text = f"{self.code}: {self.message}"
        return f"{text} {self.hint}" if self.hint else text


class CallCache(dict):
    """Per-call cache; a dict subclass so it accepts weak references."""


@dataclass(frozen=True)
class CallContext:
    user_id: int
    today: date
    cache: CallCache


@dataclass(frozen=True)
class ToolOutput(Generic[D]):
    data: D
    warnings: Sequence[WarningCode] = ()
    period: Period | None = None
    truncated: bool = False


Handler = Callable[[CallContext, A], ToolOutput[D]]


class ToolRuntime:
    def __init__(
        self,
        clock: Clock,
        identity: Callable[[], AuthenticatedUser] = current_user,
    ) -> None:
        self._clock = clock
        self._identity = identity

    def run(
        self,
        name: str,
        args: A,
        handler: Handler[A, D],
        result_type: type[ToolResult[D]],
    ) -> CallToolResult:
        started = time.perf_counter()
        user = None
        status, code = "error", "INTERNAL"
        try:
            user = self._identity()
            today = self._clock.today()
            ctx = CallContext(user.user_id, today, CallCache())
            output = handler(ctx, args)
            meta = Meta(
                period=output.period,
                reference_date=today,
                truncated=output.truncated,
                warnings=list(dict.fromkeys(output.warnings)),
            )
            exclude = {"meta": {"period"}} if output.period is None else None
            structured = result_type(meta=meta, data=output.data).model_dump(
                mode="json", exclude=exclude
            )
            result = CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(structured, ensure_ascii=False),
                    )
                ],
                structured_content=structured,
            )
            status, code = "ok", "-"
            return result
        except ToolInputError as exc:
            code = exc.code
            return CallToolResult(
                content=[TextContent(type="text", text=exc.text)],
                is_error=True,
            )
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "tool=%s status=%s code=%s duration_ms=%d user_id=%s jti=%s",
                name,
                status,
                code,
                duration_ms,
                user.user_id if user else "-",
                user.jti if user else "-",
            )
