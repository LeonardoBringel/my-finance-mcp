FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# Dependencies first so this layer is cached until the lock file changes
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

ARG APP_VERSION=latest
RUN echo "${APP_VERSION}" > /app/.version

ENV PATH="/app/.venv/bin:${PATH}"

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "my_finance_mcp.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
