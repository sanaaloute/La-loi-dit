"""OCR runner for scanned documents (French, CPU).

Supports two providers:

* ``tesseract`` (default): system ``tesseract`` binary via ``pytesseract``.
  Stable on ARM64/x86_64 and does not require the heavy PaddleOCR/PaddlePaddle
  native stack.
* ``paddleocr``: PaddleX/PaddleOCR engine. Kept for backwards compatibility, but
  it runs in a child process (``python -m backend.ingestion.ocr_worker``) because
  paddlepaddle and ctranslate2 (faster-whisper STT) segfault the interpreter
  when their native stacks share a process — observed killing API workers
  mid-request.

A missing package, crashed or timed-out engine simply means "OCR unavailable":
text PDFs extract normally, scanned pages stay unrecovered.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


def _provider() -> str:
    from backend.core.config import get_settings

    provider = get_settings().ocr_provider.lower().strip()
    return provider if provider in {"tesseract", "paddleocr"} else "tesseract"


def _tesseract_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    return importlib.util.find_spec("pytesseract") is not None


def _paddleocr_available() -> bool:
    return (
        importlib.util.find_spec("paddleocr") is not None
        and importlib.util.find_spec("paddle") is not None
    )


def ocr_available() -> bool:
    """Cheap availability check for the configured provider.

    Importing the OCR engine here would defeat process isolation for PaddleOCR,
    so we only verify the config switch and that the required packages/binary
    are present. Model/engine failures surface as empty batch results.
    """
    from backend.core.config import get_settings

    if not get_settings().ocr_enabled:
        return False
    if _provider() == "tesseract":
        return _tesseract_available()
    return _paddleocr_available()


def _ocr_images_tesseract(
    image_paths: Sequence[Path], timeout: Optional[int] = None
) -> dict[str, str]:
    """OCR a batch of page images with Tesseract.

    Each image is processed sequentially via ``pytesseract.image_to_string``,
    which shells out to the ``tesseract`` binary. This is naturally isolated
    and stable on both x86_64 and ARM64.
    """
    from backend.core.config import get_settings

    settings = get_settings()
    lang = settings.ocr_lang
    # Tesseract packs use three-letter codes; "fr" is a common shorthand.
    if lang == "fr":
        lang = "fra"

    try:
        import pytesseract
        from PIL import Image as PilImage
    except Exception as exc:
        logger.warning("tesseract imports unavailable: %s", exc)
        return {}

    results: dict[str, str] = {}
    for image_path in image_paths:
        try:
            if timeout is not None:
                # pytesseract itself does not accept a timeout; wrap the call.
                import threading

                text = ""
                exc_holder: list[Exception] = []

                def _run() -> None:
                    nonlocal text
                    try:
                        text = pytesseract.image_to_string(
                            str(image_path), lang=lang
                        )
                    except Exception as e:
                        exc_holder.append(e)

                t = threading.Thread(target=_run)
                t.start()
                t.join(timeout)
                if t.is_alive():
                    logger.warning("tesseract timeout on %s", image_path)
                    # The thread continues in the background; the result is lost.
                    results[str(image_path)] = ""
                    continue
                if exc_holder:
                    raise exc_holder[0]
            else:
                text = pytesseract.image_to_string(str(image_path), lang=lang)
            results[str(image_path)] = text.strip()
        except Exception as exc:
            logger.warning("tesseract failed for %s: %s", image_path, exc)
            results[str(image_path)] = ""
    return results


def _ocr_images_paddleocr(
    image_paths: Sequence[Path], timeout: Optional[int] = None
) -> dict[str, str]:
    """OCR a batch of page images in one PaddleOCR child process."""
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


def ocr_images(image_paths: Sequence[Path], timeout: Optional[int] = None) -> dict[str, str]:
    """OCR a batch of page images with the configured provider.

    Returns ``{image_path: recognized_text}`` (missing/empty entries for
    failed images). Never raises.
    """
    if not image_paths:
        return {}
    provider = _provider()
    if provider == "tesseract":
        return _ocr_images_tesseract(image_paths, timeout=timeout)
    return _ocr_images_paddleocr(image_paths, timeout=timeout)


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
