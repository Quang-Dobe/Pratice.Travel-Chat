FROM python:3.14-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=src/TravelChat.Agent/uv.lock,target=uv.lock \
    --mount=type=bind,source=src/TravelChat.Agent/pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY src/TravelChat.Agent/pyproject.toml src/TravelChat.Agent/uv.lock /app/
COPY src/TravelChat.Agent/q_agent /app/q_agent
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev --reinstall-package q-agent
RUN /app/.venv/bin/python -I -c "import importlib.util as u; assert u.find_spec('q_agent.conversations.router'), 'stale q_agent package'"

FROM python:3.14-slim-trixie
RUN useradd --create-home app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
WORKDIR /home/app
EXPOSE 8000
CMD ["fastapi", "run", "--entrypoint", "q_agent.main:app", "--port", "8000"]
