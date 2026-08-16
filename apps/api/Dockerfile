FROM python:3.13.11-slim-bookworm AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /build
RUN pip install --no-cache-dir uv==0.11.33
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13.11-slim-bookworm AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder --chown=10001:10001 /build/.venv /opt/venv
COPY --chown=10001:10001 alembic.ini ./alembic.ini
COPY --chown=10001:10001 alembic ./alembic
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read()"]
CMD ["uvicorn", "release_intelligence.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
