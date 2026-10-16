# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder: install all Python dependencies into /install with uv. Local wheels
# are used offline-first; an incomplete or wrong-architecture wheelhouse falls
# back to the configured package mirror instead of breaking the build.
# ---------------------------------------------------------------------------
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS builder

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=600 \
    PIP_RETRIES=10 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    UV_DEFAULT_INDEX=${PIP_INDEX_URL}

WORKDIR /build
COPY requirements.txt ./
# NOTE: no apt packages — every dependency ships a manylinux wheel for cp312
# (grpcio, pydantic-core, SQLAlchemy, lxml, temporalio, ...); the only sdist
# (sgmllib3k, pure Python) installs without a compiler.
COPY docker/wheels ./wheels
# Install uv from the wheelhouse when possible, otherwise from the mirror.
RUN if [ -n "$(find ./wheels -type f -name '*.whl' 2>/dev/null | head -1)" ]; then \
        pip install --no-index --find-links ./wheels uv || pip install uv; \
    else \
        pip install uv; \
    fi
# uv selects the correct platform wheels and is substantially faster than pip
# on index-assisted builds. Offline wheels remain the first choice.
RUN if [ -n "$(find ./wheels -type f -name '*.whl' 2>/dev/null | head -1)" ]; then \
        echo "Installing Python deps from local wheels with uv" && \
        (uv pip install --no-index --find-links ./wheels --prefix=/install -r requirements.txt \
            || { echo "Local wheels incomplete; fetching missing from ${UV_DEFAULT_INDEX}" && \
                 uv pip install --find-links ./wheels --prefix=/install -r requirements.txt; }) && \
        uv pip install --no-index --find-links ./wheels --prefix=/install --no-deps setuptools wheel; \
    else \
        echo "No local wheels; installing from ${UV_DEFAULT_INDEX} with uv" && \
        uv pip install --prefix=/install -r requirements.txt && \
        uv pip install --prefix=/install --no-deps setuptools wheel; \
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
        uv pip install --no-index --find-links ./wheels --prefix=/install --no-deps \
            opencv-contrib-python-headless==4.10.0.84 \
            || uv pip install --find-links ./wheels --prefix=/install --no-deps \
            opencv-contrib-python-headless==4.10.0.84; \
    else \
        uv pip install --prefix=/install --no-deps \
            opencv-contrib-python-headless==4.10.0.84; \
    fi

# ---------------------------------------------------------------------------
# Runtime: minimal image, non-root user, application code only
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/bin:${PATH}"

# curl: healthcheck. libgomp1: OpenMP runtime required by paddlepaddle (OCR).
# tesseract + poppler-utils: default OCR provider (Tesseract) and PDF utilities.
# tesseract-ocr-fra provides French language models.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
       tesseract-ocr tesseract-ocr-fra poppler-utils \
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
