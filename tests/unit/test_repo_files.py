"""Static checks on the versioned files of the repository."""

import subprocess
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]

# Built by concatenation so this file does not match its own patterns.
FORBIDDEN_REFERENCES = ("docs" + "/", "." + "specs")


def tracked_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [ROOT / name for name in output.split("\0") if name]


def test_pyproject_does_not_declare_alembic():
    content = (ROOT / "pyproject.toml").read_text().lower()

    assert "alembic" not in content


def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def requirement_names(specifiers: list[str]) -> set[str]:
    return {Requirement(specifier).name for specifier in specifiers}


def test_pyproject_pins_mcp_sdk_to_major_version_2():
    assert "mcp>=2.3,<3" in pyproject()["project"]["dependencies"]


def test_pyproject_declares_tzdata():
    dependencies = pyproject()["project"]["dependencies"]

    assert "tzdata" in requirement_names(dependencies)


def test_dev_group_declares_jsonschema():
    dev = pyproject()["dependency-groups"]["dev"]

    assert "jsonschema" in requirement_names(dev)


def test_lockfile_does_not_contain_alembic():
    content = (ROOT / "uv.lock").read_text().lower()

    assert "alembic" not in content


def test_repo_has_no_migration_files():
    assert not (ROOT / "alembic.ini").exists()
    assert not (ROOT / "alembic").exists()
    assert not (ROOT / "migrations").exists()


def test_tracked_files_do_not_reference_internal_documents():
    files = [path for path in tracked_files() if path.name != ".gitignore"]

    assert files
    offenders = []
    for path in files:
        if not path.is_file():
            continue
        content = path.read_text(errors="ignore")
        for reference in FORBIDDEN_REFERENCES:
            if reference in content:
                offenders.append(f"{path.relative_to(ROOT)}: {reference}")

    assert offenders == []
