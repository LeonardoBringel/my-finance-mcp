"""Settings validation and fail-fast boot."""

import base64
import logging

import pytest
from pydantic import SecretStr, ValidationError

from my_finance_mcp.config import (
    Settings,
    format_settings_error,
    load_settings_or_exit,
)

# Unique markers so a leak of any value into logs or errors is detectable.
SECRET_VALUES = {
    "DB_PASSWORD": "ZZSECRET-db-pass",
    "FERNET_KEY": base64.urlsafe_b64encode(
        b"ZZSECRET-fernet-key-32-bytes-ok!"
    ).decode(),
    "MCP_TOKEN_SECRET": "ZZSECRET-token-secret-at-least-32-bytes",
}

VALID_ENV = {
    "DB_HOST": "db",
    "DB_PORT": "5432",
    "DB_NAME": "finance",
    "DB_USER": "my_finance_mcp",
    "MCP_PUBLIC_URL": "https://finance-mcp.x.ts.net/mcp",
    "MCP_ALLOWED_HOSTS": "finance-mcp.x.ts.net",
    **SECRET_VALUES,
}

REQUIRED = [
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "FERNET_KEY",
    "MCP_TOKEN_SECRET",
    "MCP_PUBLIC_URL",
    "MCP_ALLOWED_HOSTS",
]

RANGES = {
    "DB_PORT": (1, 65535),
    "DB_POOL_SIZE": (1, 20),
    "MCP_TOKEN_MAX_DAYS": (1, 365),
    "RATE_LIMIT_PER_MINUTE": (1, 600),
}

URL_FIELDS = ["MCP_PUBLIC_URL", "MCP_AUTH_ISSUER_URL"]

INVALID_URLS = [
    "ftp://finance-mcp.x.ts.net/mcp",
    "finance-mcp/mcp",
    "https:///mcp",
    "https://",
    "http://",
]

URL_MESSAGE = "deve ser uma URL http ou https absoluta"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    # Never read the developer's .env or inherit their shell variables.
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def valid_env(monkeypatch):
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)


def boot_errors(caplog) -> list[str]:
    caplog.set_level(logging.CRITICAL, logger="my_finance_mcp.config")
    with pytest.raises(SystemExit) as exc_info:
        load_settings_or_exit()
    assert exc_info.value.code == 1
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "my_finance_mcp.config"
        and record.levelno == logging.CRITICAL
    ]


def test_valid_environment_loads_settings(valid_env):
    settings = load_settings_or_exit()

    assert isinstance(settings, Settings)
    assert settings.DB_HOST == "db"
    assert settings.DB_PORT == 5432
    assert settings.DB_NAME == "finance"
    assert settings.DB_USER == "my_finance_mcp"
    assert settings.MCP_ALLOWED_HOSTS == ["finance-mcp.x.ts.net"]


def test_dotenv_in_cwd_is_read_and_environment_wins(tmp_path, monkeypatch):
    lines = [f"{name}={value}" for name, value in VALID_ENV.items()]
    lines += ["DB_HOST=host-from-file", "DB_NAME=name-from-file"]
    (tmp_path / ".env").write_text("\n".join(lines) + "\n")
    monkeypatch.setenv("DB_HOST", "host-from-env")

    settings = load_settings_or_exit()

    assert settings.DB_HOST == "host-from-env"
    assert settings.DB_NAME == "name-from-file"
    assert settings.DB_PASSWORD.get_secret_value() == "ZZSECRET-db-pass"


def test_variable_names_are_case_sensitive(valid_env, monkeypatch, caplog):
    monkeypatch.delenv("DB_HOST")
    monkeypatch.setenv("db_host", "db")

    assert boot_errors(caplog) == ["DB_HOST: obrigatória"]


@pytest.mark.parametrize("name", REQUIRED)
def test_missing_required_variable_exits(valid_env, monkeypatch, caplog, name):
    monkeypatch.delenv(name)

    assert boot_errors(caplog) == [f"{name}: obrigatória"]


@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.parametrize("name", REQUIRED)
def test_empty_required_variable_exits(
    valid_env, monkeypatch, caplog, name, blank
):
    monkeypatch.setenv(name, blank)

    assert boot_errors(caplog) == [f"{name}: obrigatória"]


@pytest.mark.parametrize("name,bounds", RANGES.items())
@pytest.mark.parametrize("side", ["below", "above"])
def test_out_of_range_integer_exits(
    valid_env, monkeypatch, caplog, name, bounds, side
):
    low, high = bounds
    monkeypatch.setenv(name, str(low - 1 if side == "below" else high + 1))

    assert boot_errors(caplog) == [
        f"{name}: deve estar no intervalo {low}-{high}"
    ]


@pytest.mark.parametrize("name,bounds", RANGES.items())
@pytest.mark.parametrize("edge", [0, 1])
def test_range_limits_are_accepted(valid_env, monkeypatch, name, bounds, edge):
    monkeypatch.setenv(name, str(bounds[edge]))

    assert getattr(load_settings_or_exit(), name) == bounds[edge]


@pytest.mark.parametrize("value", ["abc", "5.5"])
@pytest.mark.parametrize("name", list(RANGES))
def test_non_integer_exits(valid_env, monkeypatch, caplog, name, value):
    monkeypatch.setenv(name, value)

    assert boot_errors(caplog) == [f"{name}: deve ser um número inteiro"]


@pytest.mark.parametrize(
    "invalid_key",
    [
        "not-a-valid-fernet-key-value",
        base64.urlsafe_b64encode(b"sixteen-byte-key").decode(),
    ],
)
def test_invalid_fernet_key_exits(valid_env, monkeypatch, caplog, invalid_key):
    monkeypatch.setenv("FERNET_KEY", invalid_key)

    assert boot_errors(caplog) == ["FERNET_KEY: não é uma chave Fernet válida"]


@pytest.mark.parametrize("secret", ["a" * 31, "é" * 15 + "a"])
def test_token_secret_under_32_bytes_exits(
    valid_env, monkeypatch, caplog, secret
):
    monkeypatch.setenv("MCP_TOKEN_SECRET", secret)

    assert boot_errors(caplog) == [
        "MCP_TOKEN_SECRET: deve ter pelo menos 32 bytes"
    ]


@pytest.mark.parametrize("secret", ["a" * 32, "é" * 16])
def test_token_secret_with_32_bytes_is_accepted(valid_env, monkeypatch, secret):
    # 16 two-byte characters: the limit counts UTF-8 bytes, not characters.
    monkeypatch.setenv("MCP_TOKEN_SECRET", secret)

    settings = load_settings_or_exit()

    assert settings.MCP_TOKEN_SECRET.get_secret_value() == secret


@pytest.mark.parametrize("url", INVALID_URLS)
@pytest.mark.parametrize("name", URL_FIELDS)
def test_non_http_absolute_url_exits(valid_env, monkeypatch, caplog, name, url):
    monkeypatch.setenv(name, url)

    assert boot_errors(caplog) == [f"{name}: {URL_MESSAGE}"]


@pytest.mark.parametrize(
    "url", ["http://finance-mcp.x.ts.net/mcp", "https://finance-mcp.x.ts.net"]
)
@pytest.mark.parametrize("name", URL_FIELDS)
def test_http_and_https_urls_are_accepted(valid_env, monkeypatch, name, url):
    monkeypatch.setenv(name, url)

    assert getattr(load_settings_or_exit(), name) == url


def test_explicit_issuer_url_is_used(valid_env, monkeypatch):
    monkeypatch.setenv("MCP_AUTH_ISSUER_URL", "https://auth.x.ts.net/issuer")

    settings = load_settings_or_exit()

    assert settings.MCP_AUTH_ISSUER_URL == "https://auth.x.ts.net/issuer"


@pytest.mark.parametrize("value", ["VERBOSE", "trace", ""])
def test_invalid_log_level_exits(valid_env, monkeypatch, caplog, value):
    monkeypatch.setenv("LOG_LEVEL", value)

    assert boot_errors(caplog) == [
        "LOG_LEVEL: deve ser um de DEBUG, INFO, WARNING, ERROR, CRITICAL"
    ]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("debug", "DEBUG"),
        ("Info", "INFO"),
        ("warning", "WARNING"),
        ("ERROR", "ERROR"),
        ("critical", "CRITICAL"),
    ],
)
def test_log_level_is_case_insensitive(valid_env, monkeypatch, value, expected):
    monkeypatch.setenv("LOG_LEVEL", value)

    assert load_settings_or_exit().LOG_LEVEL == expected


@pytest.mark.parametrize("endpoint", [None, ""])
def test_tracing_without_endpoint_exits(
    valid_env, monkeypatch, caplog, endpoint
):
    monkeypatch.setenv("TRACING_ENABLED", "true")
    if endpoint is not None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)

    assert boot_errors(caplog) == [
        "OTEL_EXPORTER_OTLP_ENDPOINT: obrigatória quando TRACING_ENABLED=true"
    ]


def test_tracing_with_endpoint_is_accepted(valid_env, monkeypatch):
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel:4317")

    settings = load_settings_or_exit()

    assert settings.TRACING_ENABLED is True
    assert settings.OTEL_EXPORTER_OTLP_ENDPOINT == "http://otel:4317"


@pytest.mark.parametrize("value", ["maybe", ""])
def test_non_boolean_tracing_exits(valid_env, monkeypatch, caplog, value):
    monkeypatch.setenv("TRACING_ENABLED", value)

    assert boot_errors(caplog) == ["TRACING_ENABLED: deve ser true ou false"]


def test_every_invalid_variable_gets_exactly_one_line(monkeypatch, caplog):
    # Cross-field rules must still report when unrelated fields fail too.
    monkeypatch.setenv("DB_PORT", "abc")
    monkeypatch.setenv("DB_POOL_SIZE", "0")
    monkeypatch.setenv("FERNET_KEY", "not-a-valid-fernet-key-value")
    monkeypatch.setenv("MCP_TOKEN_SECRET", "short")
    monkeypatch.setenv("MCP_PUBLIC_URL", "ftp://finance-mcp.x.ts.net/mcp")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", " , ")
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    monkeypatch.setenv("TRACING_ENABLED", "true")

    lines = boot_errors(caplog)

    assert sorted(lines) == sorted(
        [
            "DB_HOST: obrigatória",
            "DB_PORT: deve ser um número inteiro",
            "DB_NAME: obrigatória",
            "DB_USER: obrigatória",
            "DB_PASSWORD: obrigatória",
            "DB_POOL_SIZE: deve estar no intervalo 1-20",
            "FERNET_KEY: não é uma chave Fernet válida",
            "MCP_TOKEN_SECRET: deve ter pelo menos 32 bytes",
            f"MCP_PUBLIC_URL: {URL_MESSAGE}",
            "MCP_ALLOWED_HOSTS: obrigatória",
            "LOG_LEVEL: deve ser um de DEBUG, INFO, WARNING, ERROR, CRITICAL",
            "OTEL_EXPORTER_OTLP_ENDPOINT: "
            "obrigatória quando TRACING_ENABLED=true",
        ]
    )


def test_variable_with_several_errors_gets_one_line():
    exc = ValidationError.from_exception_data(
        "Settings",
        [
            {"type": "missing", "loc": ("DB_HOST",), "input": {}},
            {"type": "string_type", "loc": ("DB_HOST",), "input": 1},
        ],
    )

    assert format_settings_error(exc) == ["DB_HOST: obrigatória"]


def test_secrets_are_stored_as_secret_str(valid_env):
    settings = load_settings_or_exit()

    for name, value in SECRET_VALUES.items():
        field = getattr(settings, name)
        assert isinstance(field, SecretStr)
        assert field.get_secret_value() == value
        assert value not in repr(settings)
        assert value not in str(settings)


def test_values_never_reach_log_or_error(monkeypatch, caplog):
    invalid = {
        "DB_HOST": "   ",
        "DB_PORT": "ZZSECRET-port",
        "DB_NAME": "",
        "DB_USER": "  ",
        "DB_PASSWORD": "",
        "FERNET_KEY": "ZZSECRET-not-a-fernet-key",
        "MCP_TOKEN_SECRET": "ZZSECRET-short",
        "MCP_PUBLIC_URL": "ftp://ZZSECRET-host/mcp",
        "MCP_ALLOWED_HOSTS": " , ",
        "MCP_AUTH_ISSUER_URL": "ZZSECRET-issuer",
        "MCP_TOKEN_MAX_DAYS": "ZZSECRET-days",
        "RATE_LIMIT_PER_MINUTE": "999999",
        "DB_POOL_SIZE": "ZZSECRET-pool",
        "LOG_LEVEL": "ZZSECRET-level",
        "TRACING_ENABLED": "ZZSECRET-bool",
    }
    for name, value in invalid.items():
        monkeypatch.setenv(name, value)

    lines = boot_errors(caplog)
    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert len(lines) == len(invalid)
    assert "ZZSECRET" not in caplog.text
    assert "999999" not in caplog.text
    assert "ZZSECRET" not in str(exc_info.value)
    assert "999999" not in str(exc_info.value)


def test_rejected_secrets_never_reach_log(valid_env, monkeypatch, caplog):
    monkeypatch.setenv("FERNET_KEY", "ZZSECRET-not-a-fernet-key")
    monkeypatch.setenv("MCP_TOKEN_SECRET", "ZZSECRET-short")

    lines = boot_errors(caplog)

    assert len(lines) == 2
    assert "ZZSECRET" not in caplog.text
    for value in SECRET_VALUES.values():
        assert value not in caplog.text


def test_optional_variables_take_defaults(valid_env):
    settings = load_settings_or_exit()

    assert settings.MCP_ALLOWED_ORIGINS == []
    assert settings.MCP_TOKEN_ISSUER == "my-finance-mcp"
    assert settings.MCP_TOKEN_MAX_DAYS == 90
    assert settings.RATE_LIMIT_PER_MINUTE == 30
    assert settings.DB_POOL_SIZE == 5
    assert settings.LOG_LEVEL == "INFO"
    assert settings.TRACING_ENABLED is False
    assert settings.OTEL_EXPORTER_OTLP_ENDPOINT is None


def test_empty_token_issuer_exits(valid_env, monkeypatch, caplog):
    monkeypatch.setenv("MCP_TOKEN_ISSUER", " ")

    assert boot_errors(caplog) == ["MCP_TOKEN_ISSUER: obrigatória"]


@pytest.mark.parametrize("name", ["MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS"])
def test_comma_separated_list_is_split_and_trimmed(
    valid_env, monkeypatch, name
):
    monkeypatch.setenv(name, " a.x.ts.net , ,b.x.ts.net,, c ,")

    settings = load_settings_or_exit()

    assert getattr(settings, name) == ["a.x.ts.net", "b.x.ts.net", "c"]


def test_blank_allowed_hosts_list_counts_as_missing(
    valid_env, monkeypatch, caplog
):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", " , ")

    assert boot_errors(caplog) == ["MCP_ALLOWED_HOSTS: obrigatória"]


def test_blank_allowed_origins_is_empty_list(valid_env, monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", " , ")

    assert load_settings_or_exit().MCP_ALLOWED_ORIGINS == []


@pytest.mark.parametrize(
    "public_url,issuer",
    [
        (
            "https://finance-mcp.x.ts.net:8443/mcp/",
            "https://finance-mcp.x.ts.net:8443",
        ),
        ("https://finance-mcp.x.ts.net/mcp", "https://finance-mcp.x.ts.net"),
        (
            "https://user@finance-mcp.x.ts.net/mcp",
            "https://finance-mcp.x.ts.net",
        ),
        ("http://localhost:8000/mcp", "http://localhost:8000"),
    ],
)
def test_issuer_defaults_to_public_url_origin(
    valid_env, monkeypatch, public_url, issuer
):
    monkeypatch.setenv("MCP_PUBLIC_URL", public_url)

    settings = load_settings_or_exit()

    assert settings.MCP_AUTH_ISSUER_URL == issuer
    assert settings.MCP_PUBLIC_URL == public_url


@pytest.mark.parametrize(
    "public_url",
    [
        "https://finance-mcp.x.ts.net:8443/mcp/",
        "https://Finance-MCP.x.ts.net:443/MCP",
    ],
)
def test_public_url_is_kept_verbatim(valid_env, monkeypatch, public_url):
    monkeypatch.setenv("MCP_PUBLIC_URL", public_url)

    assert load_settings_or_exit().MCP_PUBLIC_URL == public_url
