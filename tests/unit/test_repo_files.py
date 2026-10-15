"""Static checks on the versioned files of the repository."""

import subprocess
from pathlib import Path

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
