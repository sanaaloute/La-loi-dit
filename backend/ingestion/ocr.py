"""PaddleOCR runner for scanned documents (French, CPU).

Process isolation: paddle runs ONLY in a child process
(``python -m backend.ingestion.ocr_worker``), never in the API process —
paddlepaddle and ctranslate2 (faster-whisper STT) segfault the interpreter
when their native stacks share a process, which killed API workers
mid-request. A missing package, crashed or timed-out child simply means
"OCR unavailable": text PDFs extract normally, scanned pages stay
unrecovered.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


def ocr_available() -> bool:
    """Cheap availability check — importing paddle here would defeat the
    process isolation, so this only verifies the config switch and that the
    packages exist. Model/engine failures surface as empty batch results."""
    from backend.core.config import get_settings

    if not get_settings().ocr_enabled:
        return False
    return (
        importlib.util.find_spec("paddleocr") is not None
        and importlib.util.find_spec("paddle") is not None
    )


def ocr_images(image_paths: Sequence[Path], timeout: Optional[int] = None) -> dict[str, str]:
    """OCR a batch of page images in one child process.

    Returns ``{image_path: recognized_text}`` (missing/empty entries for
    failed images). Never raises: any worker failure (missing deps, crash,
    timeout) returns an empty mapping.
    """
    if not image_paths:
        return {}
    from backend.core.config import get_settings

    settings = get_settings()
    if timeout is None:
        # Base budget for engine start + a per-page allowance (CPU OCR).
        timeout = settings.ocr_subprocess_timeout_seconds + 30 * len(image_paths)
    manifest_path: Optional[Path] = None
    out_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as manifest:
            manifest_path = Path(manifest.name)
        out_fd, out_name = tempfile.mkstemp(suffix=".json")
        os.close(out_fd)
        out_path = Path(out_name)
        manifest_path.write_text(
            json.dumps(
                {"images": [str(p) for p in image_paths], "out": str(out_path)}
            ),
            encoding="utf-8",
        )
        # Cap the child's BLAS/OMP thread pools: unbounded pools on a loaded
        # host spike RAM hard enough to trigger the kernel OOM killer.
        child_env = os.environ.copy()
        threads = str(max(1, settings.ocr_cpu_threads))
        child_env.setdefault("OMP_NUM_THREADS", threads)
        child_env.setdefault("MKL_NUM_THREADS", threads)
        proc = subprocess.run(
            [sys.executable, "-m", "backend.ingestion.ocr_worker", str(manifest_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
        )
        if proc.returncode != 0:
            logger.warning(
                "OCR worker failed (exit %s): %s",
                proc.returncode,
                (proc.stderr or proc.stdout or "").strip()[-500:],
            )
            return {}
        results = json.loads(out_path.read_text(encoding="utf-8"))
        return {str(key): str(value) for key, value in results.items()}
    except subprocess.TimeoutExpired:
        logger.warning(
            "OCR worker timed out after %ds (%d images)", timeout, len(image_paths)
        )
        return {}
    except Exception as exc:
        logger.warning("OCR worker error: %s", exc)
        return {}
    finally:
        if manifest_path is not None:
            manifest_path.unlink(missing_ok=True)
        if out_path is not None:
            out_path.unlink(missing_ok=True)


def ocr_image(image: "Image.Image") -> str:
    """Recognize text in one PIL image (agent OCR tool); "" on any failure."""
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        image.convert("RGB").save(tmp_path, format="PNG")
        return ocr_images([tmp_path]).get(str(tmp_path), "")
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
