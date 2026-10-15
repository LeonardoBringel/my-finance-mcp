"""Liveness (/health) and readiness (/ready), without Authorization."""

import logging
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request

from my_finance_mcp.api.errors import ApiError
from my_finance_mcp.data.db import (
    SUPPORTED_SCHEMA_REVISIONS,
    DatabaseUnavailableError,
    SchemaUnreadableError,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _unsupported_schema() -> ApiError:
    return ApiError(
        503, "UNSUPPORTED_SCHEMA", "Versão do schema do banco não suportada."
    )


def get_revision_reader(request: Request) -> Callable[[], list[str]]:
    return request.app.state.revision_reader


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Plain def: FastAPI runs it in the threadpool, so a slow database read does
# not block the event loop.
@router.get("/ready")
def ready(
    reader: Callable[[], list[str]] = Depends(get_revision_reader),
) -> dict:
    try:
        revisions = reader()
    except DatabaseUnavailableError:
        raise ApiError(503, "DB_UNAVAILABLE", "Banco de dados indisponível.")
    except SchemaUnreadableError:
        # The database reason was already logged where it was read.
        raise _unsupported_schema()

    if not revisions or any(
        revision not in SUPPORTED_SCHEMA_REVISIONS for revision in revisions
    ):
        logger.warning("Revisões de schema não suportadas: %r", revisions)
        raise _unsupported_schema()

    return {"status": "ready", "schema_revisions": sorted(set(revisions))}
