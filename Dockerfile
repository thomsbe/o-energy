FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# dependencies first (cached layer)
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --no-dev --no-install-project

COPY src ./src
RUN uv sync --no-dev

# the SQLite database lives in a volume
ENV DB_URL=sqlite:////data/o-energy.db
VOLUME ["/data"]
EXPOSE 8000

# run as a non root user (uid/gid 1000 matches the default host user, so that
# the ./data bind mount stays writable)
RUN mkdir -p /data && chown -R 1000:1000 /data /app
USER 1000:1000

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()"

CMD ["oenergy", "serve", "--host", "0.0.0.0", "--port", "8000"]
