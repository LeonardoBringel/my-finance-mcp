"""Today's date in Sao Paulo and the single system-clock call site."""

import ast
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from my_finance_mcp.domain.clock import SystemClock

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
CLOCK_MODULE = Path("my_finance_mcp/domain/clock.py")

# (receiver, method) pairs that read the system clock.
FORBIDDEN_CALLS = {
    ("date", "today"),
    ("datetime", "today"),
    ("datetime", "now"),
}


@pytest.fixture
def utc_process_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _clock_at(instant: datetime) -> SystemClock:
    return SystemClock(now=lambda: instant)


def _clock_reads(root: Path) -> set[Path]:
    """Files under root that call date.today, datetime.today or datetime.now."""
    offenders = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            receiver = func.value
            if isinstance(receiver, ast.Attribute):
                name = receiver.attr
            elif isinstance(receiver, ast.Name):
                name = receiver.id
            else:
                continue
            if (name, func.attr) in FORBIDDEN_CALLS:
                offenders.add(path.relative_to(root))
    return offenders


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        # Still the previous evening in Sao Paulo (23:30 on the 8th).
        (datetime(2026, 10, 9, 2, 30, tzinfo=UTC), date(2026, 10, 8)),
        # Year boundary: 23:30 on the 31st in Sao Paulo.
        (datetime(2027, 1, 1, 2, 30, tzinfo=UTC), date(2026, 12, 31)),
        # Exactly midnight in Sao Paulo (UTC-3).
        (datetime(2026, 10, 8, 3, 0, tzinfo=UTC), date(2026, 10, 8)),
    ],
)
def test_today_is_the_sao_paulo_date(instant, expected):
    assert _clock_at(instant).today() == expected


def test_today_ignores_the_process_timezone(utc_process_timezone):
    instant = datetime(2026, 10, 9, 2, 30, tzinfo=UTC)

    assert _clock_at(instant).today() == date(2026, 10, 8)


def test_today_without_now_uses_the_real_clock():
    today = SystemClock().today()

    assert type(today) is date


def test_src_reads_the_system_clock_only_in_the_clock_module():
    assert _clock_reads(SRC) == {CLOCK_MODULE}


def test_clock_scan_flags_a_planted_datetime_now(tmp_path):
    planted = tmp_path / "planted.py"
    planted.write_text(
        "from datetime import datetime\nvalue = datetime.now()\n"
    )

    assert _clock_reads(tmp_path) == {Path("planted.py")}
