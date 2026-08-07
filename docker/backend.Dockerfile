# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder: install all Python dependencies into /install — fully OFFLINE.
# The wheels are pre-downloaded on the host (data/download_wheels.sh) so the
# build never touches a package registry: slow/filtered build networks only
# need the base image. PIP_INDEX_URL remains as an escape hatch for builds on
# healthy networks (fallback if a wheel is missing).
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=600 \
    PIP_RETRIES=10 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /build
COPY requirements.txt ./
# NOTE: no apt packages — every dependency ships a manylinux wheel for cp312
# (grpcio, pydantic-core, SQLAlchemy, lxml, temporalio, ...); the only sdist
# (sgmllib3k, pure Python) installs without a compiler.
COPY docker/wheels ./wheels
RUN pip install --no-index --find-links ./wheels --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
# Runtime: minimal image, non-root user, application code only
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app app

COPY --from=builder /install /usr/local

WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-deps -e . \
    && mkdir -p /app/data \
    && chown -R app:app /app

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
