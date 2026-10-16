#!/bin/bash
# Download cp312 wheels for the Docker image build, matching the host's Linux
# architecture (Apple Silicon/arm64 or Intel/amd64).
#
# The image build installs from these local files (--no-index), so it needs NO
# package-registry network access. Run this on any machine with a decent
# connection before `docker compose build`, and re-run it whenever
# requirements.txt changes. The wheels directory is gitignored (hundreds of MB).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=python
fi

ARCH="$(uname -m)"
case "$ARCH" in
  arm64|aarch64)
    PLATFORMS=(
      manylinux_2_17_aarch64 manylinux2014_aarch64
      manylinux_2_24_aarch64 manylinux_2_28_aarch64
    )
    ;;
  x86_64|amd64)
    PLATFORMS=(
      manylinux_2_17_x86_64 manylinux2014_x86_64
      manylinux_2_24_x86_64 manylinux_2_28_x86_64
    )
    ;;
  *)
    echo "Unsupported architecture: $ARCH (expected arm64/aarch64 or x86_64/amd64)" >&2
    exit 1
    ;;
esac

PLATFORM_ARGS=(--only-binary=:all: --implementation cp --abi cp312 --abi abi3 --abi none --python-version 3.12)
for platform in "${PLATFORMS[@]}"; do
  PLATFORM_ARGS+=(--platform "$platform")
done

# Override PIP_INDEX_URL for another mirror. Aliyun is the default because
# pypi.org crawls/times out on restricted networks.
INDEX="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"

# sgmllib3k (feedparser dep) is sdist-only, which is incompatible with the
# platform-constrained download below — so feedparser's subtree is fetched
# separately. The sdist is pure Python and installs without a compiler.
grep -v '^feedparser' requirements.txt > /tmp/req-main.txt

# NOTE: the OCR additions (PyMuPDF, paddlepaddle, paddleocr, Pillow,
# opencv-contrib-python-headless) are covered by the requirements download
# below — the paddle tree is large (~1 GB of wheels), expect a slow first run.
# The faster-whisper tree (ctranslate2, onnxruntime, av, tokenizers, …) is
# covered the same way (local STT provider, see backend/core/stt.py).
# PaddleOCR *models* are NOT pip packages: pre-download them separately into
# the OCR models dir (LEGAL_AI_OCR_MODELS_DIR, default data/ocr_models), e.g.
#   PADDLE_PDX_CACHE_HOME=data/ocr_models python -c \
#     "from paddleocr import PaddleOCR; PaddleOCR(lang='fr', device='cpu')"
# then mount/bake that directory so the container initializes fully offline.

"$PY" -m pip download -r /tmp/req-main.txt -d docker/wheels -i "$INDEX" "${PLATFORM_ARGS[@]}"

# uv itself must be available for offline Docker builds. Its wheel is
# platform-specific, so it uses the same architecture selection.
"$PY" -m pip download uv -d docker/wheels --no-deps -q -i "$INDEX" "${PLATFORM_ARGS[@]}"

# feedparser + its sdist-only dep are fetched separately, pinned to the exact
# versions in requirements.txt.
FEEDPARSER_VERSION=$(sed -n 's/^feedparser==\([0-9.]*\).*/\1/p' requirements.txt)
"$PY" -m pip download "feedparser==$FEEDPARSER_VERSION" -d docker/wheels --no-deps -q -i "$INDEX"
"$PY" -m pip download sgmllib3k -d docker/wheels --no-deps -q -i "$INDEX"
# setuptools/wheel: needed offline by pip's isolated build env for the sdist.
"$PY" -m pip download setuptools wheel -d docker/wheels --no-deps -q -i "$INDEX"
# uvloop (uvicorn[standard]): its marker excludes win32, so a Windows host
# never resolves it — fetch it explicitly for the linux target.
"$PY" -m pip download "uvloop>=0.14.0,!=0.15.0,!=0.15.1" -d docker/wheels --no-deps -q -i "$INDEX" \
  "${PLATFORM_ARGS[@]}"

echo "Wheels ready in docker/wheels for $ARCH ($(ls docker/wheels | wc -l) files)"
