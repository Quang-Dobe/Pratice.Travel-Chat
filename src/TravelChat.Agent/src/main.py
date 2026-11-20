import logging.config
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.agents.registry import build_agents
from src.conversations.router import router as conversations_router
from src.graph.builder import build_graph
from src.graph.deps import PlannerDeps
from src.providers.base import make_provider
from src.settings import get_settings
from src.utils.tools import load_tools

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
PROBLEM_JSON = "application/problem+json"

type DictRowPool = AsyncConnectionPool[AsyncConnection[DictRow]]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool: DictRowPool = AsyncConnectionPool(
        get_settings().database_url,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=False,
    )
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool:
        async with pool.connection() as connection:
            await connection.execute(schema)
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        settings = get_settings()
        provider = make_provider(settings)
        tools = await load_tools(settings)
        app.state.planner = PlannerDeps(
            provider=provider,
            settings=settings,
            tools=tools,
            agents=build_agents(provider, tools, settings),
            pool=pool,
        )
        app.state.pool = pool
        app.state.graph = build_graph(checkpointer, app.state.planner)
        yield


def problem(
    status_code: int, detail: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {
            "type": "about:blank",
            "title": HTTPStatus(status_code).phrase,
            "status": status_code,
            "detail": detail,
        },
        status_code=status_code,
        headers=headers,
        media_type=PROBLEM_JSON,
    )


logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "std"}},
        "loggers": {"src": {"level": "INFO", "handlers": ["console"]}},
    }
)

app = FastAPI(title="q-agent", lifespan=lifespan)
app.include_router(conversations_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return problem(exc.status_code, str(exc.detail), exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    detail = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )
    return problem(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "Healthy"}
