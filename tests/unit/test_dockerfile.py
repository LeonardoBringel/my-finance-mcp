"""Static checks on the Dockerfile."""

import json
import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"

EXPECTED_CMD = [
    "uvicorn",
    "my_finance_mcp.main:create_app",
    "--factory",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
    "--workers",
    "1",
    "--proxy-headers",
    "--forwarded-allow-ips",
    "*",
]


def instructions() -> list[str]:
    """Instructions, with `\\` continuations joined and comments removed."""
    text = DOCKERFILE.read_text().replace("\\\n", " ")
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def index_of(prefix: str) -> int:
    matches = [i for i, line in enumerate(instructions()) if prefix in line]
    assert len(matches) == 1, prefix
    return matches[0]


def sync_indexes() -> list[int]:
    return [
        i
        for i, line in enumerate(instructions())
        if line.startswith("RUN ") and "uv sync --frozen --no-dev" in line
    ]


def test_base_image_is_python_313_slim():
    assert instructions()[0] == "FROM python:3.13-slim"


def test_uv_is_copied_from_the_pinned_image():
    uv_copies = [
        line
        for line in instructions()
        if line.startswith("COPY --from=") and "astral-sh/uv" in line
    ]

    assert len(uv_copies) == 1
    assert uv_copies[0].startswith("COPY --from=ghcr.io/astral-sh/uv:0.13.0 ")


def test_dependencies_are_installed_in_two_uv_sync_steps():
    steps = instructions()

    assert len(sync_indexes()) == 2
    first, second = sync_indexes()
    assert "--no-install-project" in steps[first]
    assert "--no-install-project" not in steps[second]


def test_first_uv_sync_runs_before_src_is_copied_and_second_after():
    assert len(sync_indexes()) == 2
    first, second = sync_indexes()
    src_copy = index_of("COPY src/")

    assert first < src_copy < second


def test_first_uv_sync_follows_the_copy_of_project_metadata_files():
    first = sync_indexes()[0]
    metadata_copy = instructions()[first - 1]

    assert metadata_copy.startswith("COPY ")
    assert metadata_copy.split()[1:-1] == [
        "pyproject.toml",
        "uv.lock",
        "README.md",
    ]


def test_app_version_build_arg_defaults_to_latest():
    assert "ARG APP_VERSION=latest" in instructions()


def test_app_version_is_written_to_the_version_file():
    writes = [
        line
        for line in instructions()
        if line.startswith("RUN ") and "/app/.version" in line
    ]

    assert len(writes) == 1
    assert re.search(
        r'echo "?\$\{?APP_VERSION\}?"? > /app/\.version', writes[0]
    )
    assert index_of("ARG APP_VERSION") < index_of("/app/.version")


def test_healthcheck_calls_health_endpoint_with_urllib():
    healthchecks = [
        line for line in instructions() if line.startswith("HEALTHCHECK")
    ]

    assert len(healthchecks) == 1
    assert "python -c" in healthchecks[0]
    assert "urllib.request.urlopen(" in healthchecks[0]
    assert "http://localhost:8000/health" in healthchecks[0]


def test_dockerfile_does_not_mention_curl():
    assert "curl" not in DOCKERFILE.read_text().lower()


def test_appuser_is_created_with_uid_1000():
    creations = [line for line in instructions() if "useradd" in line]

    assert len(creations) == 1
    assert re.search(r"(--uid[ =]|-u )1000\b", creations[0])
    assert creations[0].endswith(" appuser")


def test_container_runs_as_appuser():
    users = [line for line in instructions() if line.startswith("USER ")]

    assert users[-1] == "USER appuser"


def test_cmd_is_the_exact_uvicorn_command():
    commands = [line for line in instructions() if line.startswith("CMD ")]

    assert len(commands) == 1
    assert json.loads(commands[0].removeprefix("CMD ")) == EXPECTED_CMD
