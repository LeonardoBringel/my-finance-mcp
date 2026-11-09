"""Single pipeline every tool call goes through."""

import gc
import json
import logging
import re
import time
import weakref
from datetime import date
from functools import partial

import pytest
from mcp_types import TextContent
from pydantic import BaseModel, ValidationError

from my_finance_mcp.auth.identity import AuthenticatedUser, MissingIdentityError
from my_finance_mcp.data.db import (
    DatabaseUnavailableError,
    SchemaUnreadableError,
)
from my_finance_mcp.server.runtime import (
    CallCache,
    CallContext,
    ToolInputError,
    ToolOutput,
    ToolRuntime,
    require_supported_schema,
)
from my_finance_mcp.server.schemas.envelope import Period, ToolResult

TODAY = date(2026, 10, 8)
USER = AuthenticatedUser(user_id=42, jti="j-1")
RUNTIME_LOGGER = "my_finance_mcp.server.runtime"

UNKNOWN_CATEGORY = ToolInputError(
    "UNKNOWN_CATEGORY",
    "Categoria 'x' não encontrada.",
    "Categorias parecidas: Mercado.",
)


class SampleData(BaseModel):
    amount: float


SampleResult = ToolResult[SampleData]


class CountingClock:
    def __init__(self, today: date = TODAY) -> None:
        self.calls = 0
        self._today = today

    def today(self) -> date:
        self.calls += 1
        return self._today


def fixed_identity() -> AuthenticatedUser:
    return USER


def failing_identity() -> AuthenticatedUser:
    raise MissingIdentityError("no access token in request context")


def supported_schema() -> None:
    return None


class StubUserData:
    """Stands in for the user data facade; the runtime only passes it on."""

    def __init__(self, user_id: int, cache: CallCache) -> None:
        self.user_id = user_id
        self.cache = cache


def make_runtime(
    clock=None,
    identity=fixed_identity,
    schema_check=supported_schema,
    user_data=StubUserData,
) -> ToolRuntime:
    return ToolRuntime(
        clock or CountingClock(),
        schema_check=schema_check,
        user_data=user_data,
        identity=identity,
    )


def ok_handler(ctx, args):
    return ToolOutput(data=SampleData(amount=10.5))


def run_ok(runtime, handler=ok_handler, args=None):
    return runtime.run("sample_tool", args, handler, SampleResult)


def summary_lines(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == RUNTIME_LOGGER and record.levelno == logging.INFO
    ]


def test_identity_resolves_before_handler_and_handler_gets_context():
    events = []
    received = []
    args = object()

    def identity():
        events.append("identity")
        return USER

    def handler(ctx, handler_args):
        events.append("handler")
        received.append((ctx, handler_args))
        return ToolOutput(data=SampleData(amount=1))

    run_ok(make_runtime(identity=identity), handler, args)

    assert events == ["identity", "handler"]
    [(ctx, handler_args)] = received
    assert isinstance(ctx, CallContext)
    assert ctx.user_id == 42
    assert ctx.today == TODAY
    assert isinstance(ctx.cache, CallCache)
    assert handler_args is args


def test_clock_is_read_once_and_feeds_context_and_reference_date():
    clock = CountingClock(date(2026, 12, 31))
    seen = []

    def handler(ctx, args):
        seen.append(ctx.today)
        return ToolOutput(data=SampleData(amount=1))

    result = run_ok(make_runtime(clock=clock), handler)

    assert clock.calls == 1
    assert seen == [date(2026, 12, 31)]
    assert result.structured_content["meta"]["reference_date"] == "2026-12-31"


def test_each_call_gets_a_new_empty_cache():
    caches = []

    def handler(ctx, args):
        caches.append((ctx.cache, dict(ctx.cache)))
        ctx.cache["key"] = "value"
        return ToolOutput(data=SampleData(amount=1))

    runtime = make_runtime()
    run_ok(runtime, handler)
    run_ok(runtime, handler)

    [(first, first_at_start), (second, second_at_start)] = caches
    assert first is not second
    assert first_at_start == {}
    assert second_at_start == {}


@pytest.mark.parametrize("raises", [False, True])
def test_cache_is_not_kept_after_the_call(raises):
    refs = []

    def handler(ctx, args):
        ctx.cache["key"] = "value"
        refs.append(weakref.ref(ctx.cache))
        if raises:
            raise ToolInputError("UNKNOWN_CATEGORY", "Categoria 'x'.")
        return ToolOutput(data=SampleData(amount=1))

    runtime = make_runtime()
    result = run_ok(runtime, handler)
    gc.collect()

    assert result.is_error is raises
    assert refs[0]() is None


def test_failing_identity_skips_handler_and_reraises():
    calls = []

    def handler(ctx, args):
        calls.append(ctx)
        return ToolOutput(data=SampleData(amount=1))

    with pytest.raises(MissingIdentityError):
        run_ok(make_runtime(identity=failing_identity), handler)

    assert calls == []


def test_success_result_has_only_meta_and_data_and_matching_text():
    result = run_ok(make_runtime())

    assert result.is_error is False
    assert set(result.structured_content) == {"meta", "data"}
    assert result.structured_content["data"] == {"amount": 10.5}
    assert len(result.content) == 1
    [block] = result.content
    assert isinstance(block, TextContent)
    assert json.loads(block.text) == result.structured_content


def test_success_meta_defaults():
    result = run_ok(make_runtime())

    assert result.structured_content["meta"] == {
        "currency": "BRL",
        "reference_date": "2026-10-08",
        "truncated": False,
        "warnings": [],
    }


def test_success_meta_carries_handler_truncated_and_warnings():
    def handler(ctx, args):
        return ToolOutput(
            data=SampleData(amount=1),
            truncated=True,
            warnings=["INCLUDES_FUTURE", "NO_DATA"],
        )

    meta = run_ok(make_runtime(), handler).structured_content["meta"]

    assert meta["currency"] == "BRL"
    assert meta["reference_date"] == "2026-10-08"
    assert meta["truncated"] is True
    assert meta["warnings"] == ["INCLUDES_FUTURE", "NO_DATA"]


def test_period_is_present_when_handler_sets_it():
    def handler(ctx, args):
        period = Period(start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))
        return ToolOutput(data=SampleData(amount=1), period=period)

    result = run_ok(make_runtime(), handler)

    expected = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    assert result.structured_content["meta"]["period"] == expected
    assert json.loads(result.content[0].text)["meta"]["period"] == expected


def test_period_key_is_absent_when_handler_omits_it():
    result = run_ok(make_runtime())

    assert "period" not in result.structured_content["meta"]
    assert "period" not in json.loads(result.content[0].text)["meta"]


def test_repeated_warnings_keep_first_occurrence_order():
    def handler(ctx, args):
        return ToolOutput(
            data=SampleData(amount=1),
            warnings=["NO_DATA", "PARTIAL_PERIOD", "NO_DATA"],
        )

    result = run_ok(make_runtime(), handler)

    assert result.structured_content["meta"]["warnings"] == [
        "NO_DATA",
        "PARTIAL_PERIOD",
    ]


def test_unknown_warning_is_an_unexpected_error(caplog):
    caplog.set_level(logging.INFO)

    def handler(ctx, args):
        return ToolOutput(data=SampleData(amount=1), warnings=["FOO"])

    with pytest.raises(ValidationError):
        run_ok(make_runtime(), handler)

    [line] = summary_lines(caplog)
    assert "status=error code=INTERNAL " in line


def test_tool_input_error_becomes_coded_error_text():
    def handler(ctx, args):
        raise UNKNOWN_CATEGORY

    result = run_ok(make_runtime(), handler)

    assert result.is_error is True
    assert result.structured_content is None
    assert len(result.content) == 1
    [block] = result.content
    assert isinstance(block, TextContent)
    assert block.text == (
        "UNKNOWN_CATEGORY: Categoria 'x' não encontrada. "
        "Categorias parecidas: Mercado."
    )


@pytest.mark.parametrize(
    "error",
    [
        ToolInputError("UNKNOWN_CATEGORY", "Categoria 'x' não encontrada.", ""),
        ToolInputError("UNKNOWN_CATEGORY", "Categoria 'x' não encontrada."),
    ],
)
def test_tool_input_error_without_hint_ends_at_message(error):
    def handler(ctx, args):
        raise error

    result = run_ok(make_runtime(), handler)

    text = result.content[0].text
    assert text == "UNKNOWN_CATEGORY: Categoria 'x' não encontrada."
    assert text.endswith("encontrada.")


def test_unexpected_exception_is_reraised_unchanged():
    error = RuntimeError("ZZSECRET789")

    def handler(ctx, args):
        raise error

    with pytest.raises(RuntimeError) as exc_info:
        run_ok(make_runtime(), handler)

    assert exc_info.value is error


SUMMARY = re.compile(
    r"tool=sample_tool status=(?P<status>\S+) code=(?P<code>\S+) "
    r"duration_ms=(?P<ms>\d+) user_id=(?P<user_id>\S+) jti=(?P<jti>\S+)"
)


def raise_input_error(ctx, args):
    raise UNKNOWN_CATEGORY


def raise_unexpected(ctx, args):
    raise RuntimeError("ZZSECRET789")


@pytest.mark.parametrize(
    ("handler", "identity", "expected"),
    [
        (ok_handler, fixed_identity, ("ok", "-", "42", "j-1")),
        (
            raise_input_error,
            fixed_identity,
            ("error", "UNKNOWN_CATEGORY", "42", "j-1"),
        ),
        (raise_unexpected, fixed_identity, ("error", "INTERNAL", "42", "j-1")),
        (ok_handler, failing_identity, ("error", "INTERNAL", "-", "-")),
    ],
)
def test_one_info_summary_line_per_call(caplog, handler, identity, expected):
    caplog.set_level(logging.DEBUG)

    try:
        run_ok(make_runtime(identity=identity), handler)
    except (RuntimeError, MissingIdentityError):
        pass

    [line] = summary_lines(caplog)
    match = SUMMARY.fullmatch(line)
    assert match is not None, line
    assert match.group("status", "code", "user_id", "jti") == expected


def test_summary_duration_is_in_whole_milliseconds(caplog):
    caplog.set_level(logging.INFO)

    def handler(ctx, args):
        time.sleep(0.03)
        return ToolOutput(data=SampleData(amount=1))

    run_ok(make_runtime(), handler)

    [line] = summary_lines(caplog)
    ms = int(SUMMARY.fullmatch(line).group("ms"))
    assert 30 <= ms < 10_000


MARKERS = ["ZZDESC123", "987654.32", "ZZHINT456", "ZZMSG321"]


def leaked_markers(caplog) -> list[str]:
    texts = [caplog.text]
    for record in caplog.records:
        texts.append(record.getMessage())
        texts.append(repr(record.args))
    return [marker for marker in MARKERS if any(marker in t for t in texts)]


def test_success_logs_no_arguments_or_data(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(ctx, args):
        return ToolOutput(data=SampleData(amount=987654.32))

    result = run_ok(make_runtime(), handler, {"description": "ZZDESC123"})

    assert result.structured_content["data"] == {"amount": 987654.32}
    assert caplog.records
    assert leaked_markers(caplog) == []


def test_tool_input_error_logs_no_arguments_message_or_hint(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(ctx, args):
        raise ToolInputError(
            "UNKNOWN_CATEGORY", "Categoria ZZMSG321 sumiu.", "ZZHINT456"
        )

    result = run_ok(make_runtime(), handler, {"description": "ZZDESC123"})

    assert result.content[0].text == (
        "UNKNOWN_CATEGORY: Categoria ZZMSG321 sumiu. ZZHINT456"
    )
    assert caplog.records
    assert leaked_markers(caplog) == []


UNSUPPORTED_SCHEMA_TEXT = (
    "UNSUPPORTED_SCHEMA: Versão do schema do banco não suportada."
)


def returning(revisions):
    return lambda: list(revisions)


def raising(error: Exception):
    def reader():
        raise error

    return reader


def schema_gate(reader):
    return partial(require_supported_schema, reader)


def test_schema_check_runs_after_identity_and_before_user_data_and_handler():
    events = []

    def identity():
        events.append("identity")
        return USER

    def schema_check():
        events.append("schema_check")

    def user_data(user_id, cache):
        events.append("user_data")
        return StubUserData(user_id, cache)

    def handler(ctx, args):
        events.append("handler")
        return ToolOutput(data=SampleData(amount=1))

    runtime = make_runtime(
        identity=identity, schema_check=schema_check, user_data=user_data
    )
    run_ok(runtime, handler)

    assert events == ["identity", "schema_check", "user_data", "handler"]


def test_failing_identity_skips_schema_check_and_user_data():
    events = []

    def schema_check():
        events.append("schema_check")

    def user_data(user_id, cache):
        events.append("user_data")
        return StubUserData(user_id, cache)

    runtime = make_runtime(
        identity=failing_identity,
        schema_check=schema_check,
        user_data=user_data,
    )
    with pytest.raises(MissingIdentityError):
        run_ok(runtime)

    assert events == []


@pytest.mark.parametrize(
    "reader",
    [
        returning(["0010"]),
        returning([]),
        returning(["0009", "0010"]),
        raising(SchemaUnreadableError("permission denied")),
    ],
    ids=["unknown", "empty", "mixed", "unreadable"],
)
def test_unsupported_schema_is_refused_without_running_handler(reader):
    calls = []

    def user_data(user_id, cache):
        calls.append("user_data")
        return StubUserData(user_id, cache)

    def handler(ctx, args):
        calls.append("handler")
        return ToolOutput(data=SampleData(amount=1))

    runtime = make_runtime(
        schema_check=schema_gate(reader), user_data=user_data
    )
    result = run_ok(runtime, handler)

    assert result.is_error is True
    assert result.structured_content is None
    [block] = result.content
    assert block.text == UNSUPPORTED_SCHEMA_TEXT
    assert calls == []


def test_supported_schema_lets_the_handler_run():
    result = run_ok(make_runtime(schema_check=schema_gate(returning(["0009"]))))

    assert result.is_error is False
    assert result.structured_content["data"] == {"amount": 10.5}


def test_unavailable_database_during_schema_check_propagates():
    error = DatabaseUnavailableError()
    calls = []

    def handler(ctx, args):
        calls.append(ctx)
        return ToolOutput(data=SampleData(amount=1))

    runtime = make_runtime(schema_check=schema_gate(raising(error)))
    with pytest.raises(DatabaseUnavailableError) as exc_info:
        run_ok(runtime, handler)

    assert exc_info.value is error
    assert calls == []


def test_refused_schema_is_logged_with_its_code(caplog):
    caplog.set_level(logging.INFO)

    run_ok(make_runtime(schema_check=schema_gate(returning(["0010"]))))

    [line] = summary_lines(caplog)
    match = SUMMARY.fullmatch(line)
    assert match is not None, line
    assert match.group("status", "code") == ("error", "UNSUPPORTED_SCHEMA")


def test_context_data_is_built_for_the_caller_and_its_cache():
    built = []
    received = []

    def user_data(user_id, cache):
        data = StubUserData(user_id, cache)
        built.append((user_id, cache, data))
        return data

    def handler(ctx, args):
        received.append(ctx)
        return ToolOutput(data=SampleData(amount=1))

    run_ok(make_runtime(user_data=user_data), handler)

    [(user_id, cache, data)] = built
    [ctx] = received
    assert user_id == 42
    assert ctx.data is data
    assert cache is ctx.cache


@pytest.mark.parametrize("raises", [False, True])
def test_context_data_is_not_kept_after_the_call(raises):
    refs = []

    def handler(ctx, args):
        refs.append(weakref.ref(ctx.data))
        if raises:
            raise ToolInputError("UNKNOWN_CATEGORY", "Categoria 'x'.")
        return ToolOutput(data=SampleData(amount=1))

    result = run_ok(make_runtime(), handler)
    gc.collect()

    assert result.is_error is raises
    assert refs[0]() is None


@pytest.mark.parametrize("missing", ["schema_check", "user_data"])
def test_runtime_without_schema_check_or_user_data_cannot_be_built(missing):
    wiring = {"schema_check": supported_schema, "user_data": StubUserData}
    del wiring[missing]

    with pytest.raises(TypeError, match=missing):
        ToolRuntime(CountingClock(), identity=fixed_identity, **wiring)
