"""Application settings, validated once at boot."""

import binascii
import logging
import sys
from typing import Annotated
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = (
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "FERNET_KEY",
    "MCP_TOKEN_SECRET",
    "MCP_PUBLIC_URL",
    "MCP_ALLOWED_HOSTS",
)

RANGES = {
    "DB_PORT": (1, 65535),
    "DB_POOL_SIZE": (1, 20),
    "MCP_TOKEN_MAX_DAYS": (1, 365),
    "RATE_LIMIT_PER_MINUTE": (1, 600),
}

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

REQUIRED_MESSAGE = "obrigatória"
URL_SCHEMES = ("http", "https")
URL_MESSAGE = "deve ser uma URL http ou https absoluta"


def _split_url(value: str):
    try:
        parts = urlsplit(value)
        if parts.scheme in URL_SCHEMES and parts.hostname:
            return parts
    except ValueError:
        pass
    raise ValueError(URL_MESSAGE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
        case_sensitive=True,
    )

    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    DB_USER: str
    DB_PASSWORD: SecretStr
    FERNET_KEY: SecretStr
    MCP_TOKEN_SECRET: SecretStr
    MCP_PUBLIC_URL: str
    MCP_ALLOWED_HOSTS: Annotated[list[str], NoDecode]

    MCP_ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = []
    # SPEC_DEVIATION: typed str | None instead of str.
    # Reason: when MCP_PUBLIC_URL is invalid there is no origin to derive, and
    # returning None keeps this field from adding a second, misleading line.
    MCP_AUTH_ISSUER_URL: str | None = Field(None, validate_default=True)
    MCP_TOKEN_ISSUER: str = "my-finance-mcp"
    MCP_TOKEN_MAX_DAYS: int = 90
    RATE_LIMIT_PER_MINUTE: int = 30
    DB_POOL_SIZE: int = 5
    LOG_LEVEL: str = "INFO"
    TRACING_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = Field(None, validate_default=True)

    @field_validator(*REQUIRED_FIELDS, "MCP_TOKEN_ISSUER", mode="before")
    @classmethod
    def _reject_blank(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError(REQUIRED_MESSAGE)
        return value

    @field_validator("MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_comma_list(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("MCP_ALLOWED_HOSTS")
    @classmethod
    def _require_hosts(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError(REQUIRED_MESSAGE)
        return value

    @field_validator(*RANGES)
    @classmethod
    def _check_range(cls, value: int, info: ValidationInfo) -> int:
        low, high = RANGES[info.field_name]
        if not low <= value <= high:
            raise ValueError(f"deve estar no intervalo {low}-{high}")
        return value

    @field_validator("FERNET_KEY")
    @classmethod
    def _check_fernet_key(cls, value: SecretStr) -> SecretStr:
        try:
            Fernet(value.get_secret_value())
        except (ValueError, TypeError, binascii.Error):
            raise ValueError("não é uma chave Fernet válida") from None
        return value

    @field_validator("MCP_TOKEN_SECRET")
    @classmethod
    def _check_token_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("deve ter pelo menos 32 bytes")
        return value

    @field_validator("MCP_PUBLIC_URL")
    @classmethod
    def _check_public_url(cls, value: str) -> str:
        _split_url(value)
        return value

    @field_validator("MCP_AUTH_ISSUER_URL")
    @classmethod
    def _issuer_or_public_origin(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        if value is not None:
            _split_url(value)
            return value
        public_url = info.data.get("MCP_PUBLIC_URL")
        if public_url is None:
            return None
        # Origin only: keep scheme, host and port; drop path and userinfo.
        parts = urlsplit(public_url)
        netloc = parts.netloc.rpartition("@")[2]
        return f"{parts.scheme}://{netloc}"

    @field_validator("LOG_LEVEL")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"deve ser um de {', '.join(LOG_LEVELS)}")
        return level

    @field_validator("OTEL_EXPORTER_OTLP_ENDPOINT")
    @classmethod
    def _require_endpoint_when_tracing(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        if value is not None and not value.strip():
            value = None
        if value is None and info.data.get("TRACING_ENABLED") is True:
            raise ValueError("obrigatória quando TRACING_ENABLED=true")
        return value


def _reason(error) -> str:
    kind = error["type"]
    if kind == "missing":
        return REQUIRED_MESSAGE
    if kind == "int_parsing":
        return "deve ser um número inteiro"
    if kind == "bool_parsing":
        return "deve ser true ou false"
    if kind == "value_error":
        return str(error["ctx"]["error"])
    return "valor inválido"


def format_settings_error(exc: ValidationError) -> list[str]:
    """One line per invalid variable, never including the received value."""
    lines = {}
    for error in exc.errors(include_input=False, include_url=False):
        name = str(error["loc"][0])
        if name not in lines:
            lines[name] = f"{name}: {_reason(error)}"
    return list(lines.values())


def load_settings_or_exit() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        for line in format_settings_error(exc):
            logger.critical(line)
        sys.exit(1)
