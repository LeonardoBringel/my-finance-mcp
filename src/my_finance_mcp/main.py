"""App factory, started by uvicorn with --factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI

from my_finance_mcp.api.errors import register_error_handlers
from my_finance_mcp.api.routes import health
from my_finance_mcp.config import Settings, load_settings_or_exit
from my_finance_mcp.data.db import (
    CachedRevisionReader,
    create_db_engine,
    read_schema_revisions,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    # Installed before loading settings so configuration errors are printed
    # with their level. It is a no-op when the root logger already has
    # handlers, which is why the level is set on its own below.
    logging.basicConfig()
    if settings is None:
        # Exits with code 1 on invalid configuration, before uvicorn binds.
        settings = load_settings_or_exit()
    logging.getLogger().setLevel(settings.LOG_LEVEL)

    # Creating the engine does not connect; the database is first touched by
    # /ready.
    engine = create_db_engine(settings)

    app = FastAPI(
        lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.revision_reader = CachedRevisionReader(
        partial(read_schema_revisions, engine)
    )
    register_error_handlers(app)
    app.include_router(health.router)
    return app
