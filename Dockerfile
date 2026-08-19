FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.14
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system commerce && useradd --system --gid commerce --home /app commerce
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --chown=commerce:commerce src ./src
COPY --chown=commerce:commerce streamlit_app.py alembic.ini ./
COPY --chown=commerce:commerce infrastructure/docker/entrypoint.sh ./infrastructure/docker/entrypoint.sh
RUN chmod 0555 ./infrastructure/docker/entrypoint.sh
USER commerce
ENTRYPOINT ["./infrastructure/docker/entrypoint.sh"]

FROM runtime AS api
EXPOSE 8000
CMD ["uvicorn", "commerce_operations.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

FROM runtime AS worker
CMD ["python", "-m", "commerce_operations.worker"]

FROM runtime AS frontend
EXPOSE 8501
CMD ["streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]

FROM runtime AS migrate
CMD ["alembic", "upgrade", "head"]
