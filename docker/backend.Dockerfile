# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder: install all Python dependencies into /install — fully OFFLINE.
# The wheels are pre-downloaded on the host (scripts/download_wheels.sh) so the
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
# Offline-first install when wheels are present; otherwise fall back to the
# configured index (PIP_INDEX_URL) so the image can build on fresh servers.
# Offline-first install when wheels are present. If the local set is
# incomplete (requirements.txt changed without re-running the download
# script), fall back to an index-assisted install that still prefers the
# local wheels — so a stale wheels dir never hard-fails the build.
RUN if [ -n "$(find ./wheels -type f -name '*.whl' 2>/dev/null | head -1)" ]; then \
        echo "Installing Python deps from local wheels" && \
        (pip install --no-index --find-links ./wheels --prefix=/install -r requirements.txt \
            || { echo "Local wheels incomplete; fetching missing from ${PIP_INDEX_URL}" && \
                 pip install --find-links ./wheels --prefix=/install -r requirements.txt; }) && \
        pip install --no-index --find-links ./wheels --prefix=/install --no-deps setuptools wheel; \
    else \
        echo "No local wheels; installing from ${PIP_INDEX_URL}" && \
        pip install --prefix=/install -r requirements.txt && \
        pip install --prefix=/install --no-deps setuptools wheel; \
    fi
# paddlex (a paddleocr dep) hard-pins opencv-contrib-python, whose cv2 build
# needs the libgl system library. Swap it for the headless build pinned in
# requirements.txt (same cv2 API, no libgl) so the runtime stage stays slim.
# Only the cv2 module directory is replaced: the opencv-contrib-python
# dist-info MUST stay — paddlex checks that distribution's metadata at
# pipeline creation and fails with a DependencyError if it is missing.
RUN SITE=/install/lib/python3.12/site-packages && \
    if [ -d "$SITE/cv2" ]; then rm -rf "$SITE/cv2"; fi && \
    if [ -n "$(find ./wheels -type f -name '*.whl' 2>/dev/null | head -1)" ]; then \
        pip install --no-index --find-links ./wheels --prefix=/install --no-deps \
            opencv-contrib-python-headless==4.10.0.84 \
            || pip install --find-links ./wheels --prefix=/install --no-deps \
            opencv-contrib-python-headless==4.10.0.84; \
    else \
        pip install --prefix=/install --no-deps \
            opencv-contrib-python-headless==4.10.0.84; \
    fi

# ---------------------------------------------------------------------------
# Runtime: minimal image, non-root user, application code only
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/bin:${PATH}"

# curl: healthcheck. libgomp1: OpenMP runtime required by paddlepaddle (OCR).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app app

COPY --from=builder /install /usr/local

WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
# Build tools were copied from the builder, so install the package in editable
# mode without touching any package index.
# OCR models are NOT in the image: pre-download them into the OCR models dir
# (LEGAL_AI_OCR_MODELS_DIR, default <data>/ocr_models — /app/data is a volume)
# so PaddleOCR initializes offline; see scripts/download_wheels.sh.
RUN pip install --no-deps --no-build-isolation -e . \
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
