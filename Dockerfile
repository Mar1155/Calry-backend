# syntax=docker/dockerfile:1

# ── Stage 1: build dependencies into an isolated virtualenv ──────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Isolated venv keeps the final image clean (only the venv is copied over).
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Only the runtime requirements — dev tools (pytest/ruff/black) stay out of the image.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# ── Stage 2: slim runtime ────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

WORKDIR /app

# Run as a non-root user (security best practice).
RUN useradd --create-home --uid 1000 appuser

# Bring in the prebuilt virtualenv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy the application source (.dockerignore keeps secrets/venv/junk out).
COPY . .

RUN chmod +x ./start.sh && chown -R appuser:appuser /app
USER appuser

# Informational only — Railway routes traffic to $PORT, which start.sh honors.
EXPOSE 8000

CMD ["./start.sh"]
