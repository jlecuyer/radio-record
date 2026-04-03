FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies first (layer cache)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install project
COPY src/ src/
RUN uv sync --frozen --no-dev

ENV RADIO_RECORD_URL=""

VOLUME /radio

STOPSIGNAL SIGTERM

ENTRYPOINT ["uv", "run", "radio-record", "--verbose", "-o", "/radio"]
