"""Static checks on the .env.sample shipped with the repository."""

from pathlib import Path

from my_finance_mcp.config import Settings

SAMPLE = Path(__file__).resolve().parents[2] / ".env.sample"

SECRETS = ("DB_PASSWORD", "FERNET_KEY", "MCP_TOKEN_SECRET")


def sample_entries() -> list[tuple[str, str]]:
    entries = []
    for line in SAMPLE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        assert separator == "=", line
        entries.append((key.strip(), value.strip()))
    return entries


def test_sample_has_exactly_one_key_per_settings_field():
    keys = [key for key, _ in sample_entries()]

    assert sorted(keys) == sorted(Settings.model_fields)


def test_sample_secrets_are_placeholders():
    values = dict(sample_entries())

    assert {name: values[name] for name in SECRETS} == {
        name: "change-me" for name in SECRETS
    }
