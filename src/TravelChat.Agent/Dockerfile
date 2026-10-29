FROM python:3.14-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=src/TravelChat.Agent/uv.lock,target=uv.lock \
    --mount=type=bind,source=src/TravelChat.Agent/pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY src/TravelChat.Agent/pyproject.toml src/TravelChat.Agent/uv.lock /app/
COPY src/TravelChat.Agent/src /app/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev --reinstall-package q-agent
RUN /app/.venv/bin/python -I -c "import importlib.util as u; assert u.find_spec('src.graph.plan-graph.builder'), 'plan-graph is not shipped'; assert u.find_spec('src.conversations.router'), 'stale src package'; assert not u.find_spec('q_agent'), 'q_agent is still present'"

FROM python:3.14-slim-trixie
RUN useradd --create-home app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
WORKDIR /home/app
EXPOSE 8000
CMD ["fastapi", "run", "--entrypoint", "src.main:app", "--port", "8000"]
