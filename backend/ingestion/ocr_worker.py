"""Child-process OCR worker — the ONLY place paddle is loaded.

PaddleOCR/paddlepaddle and ctranslate2 (faster-whisper STT) crash the
interpreter with SIGSEGV when their native stacks share a process, so the
API process must never import paddle. This module runs as
``python -m backend.ingestion.ocr_worker <manifest.json>`` where the manifest
is ``{"images": [path, ...], "out": result_json_path}``; it writes
``{image_path: recognized_text}`` JSON to ``out``. Exit code 0 means the
batch ran (individual images may still have empty text); 1 means the engine
could not be built.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _build_engine() -> Any:
    """Construct the PaddleOCR engine from settings.

    The model cache env vars must be set before importing paddleocr so a
    pre-populated directory (Docker volume / baked layer) serves models
    offline: PaddleX 3.x caches under ``PADDLE_PDX_CACHE_HOME/official_models``
    (``PADDLEOCR_HOME`` covers the 2.x layout). Pre-downloaded model dirs are
    passed explicitly (name + dir) because PaddleX resolves official models
    through a network hoster healthcheck that fails on offline deployments.
    """
    from backend.core.config import get_settings

    settings = get_settings()
    models_dir = settings.ocr_models_path
    models_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(models_dir))
    os.environ.setdefault("PADDLEOCR_HOME", str(models_dir))

    from paddleocr import PaddleOCR

    kwargs: dict[str, Any] = {"lang": settings.ocr_lang}
    det_dir = models_dir / "official_models" / settings.ocr_det_model_name
    rec_dir = models_dir / "official_models" / settings.ocr_rec_model_name
    if det_dir.is_dir():
        kwargs["text_detection_model_name"] = settings.ocr_det_model_name
        kwargs["text_detection_model_dir"] = str(det_dir)
    if rec_dir.is_dir():
        kwargs["text_recognition_model_name"] = settings.ocr_rec_model_name
        kwargs["text_recognition_model_dir"] = str(rec_dir)

    try:
        return PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
            **kwargs,
        )
    except TypeError:
        # Constructor signature differs in this paddleocr release.
        return PaddleOCR(lang=settings.ocr_lang)


def _extract_text(result: Any) -> str:
    """Join recognized lines from either the 3.x or the legacy result shape."""
    if not result:
        return ""
    lines: list[str] = []
    for item in result:
        lines.extend(_item_texts(item))
    return "\n".join(line for line in lines if line.strip())


def _item_texts(item: Any) -> list[str]:
    """Recognized strings from one result item (one page/image)."""
    if item is None:
        return []
    rec_texts: Any = None
    if isinstance(item, dict):
        rec_texts = item.get("rec_texts")
    else:
        try:  # PaddleOCR 3.x result objects are dict-like
            rec_texts = item["rec_texts"]
        except Exception:
            rec_texts = None
    if rec_texts is not None:
        return [str(text) for text in rec_texts]
    if isinstance(item, (list, tuple)):
        # Legacy shape: [box, (text, score)] entries.
        texts = []
        for entry in item:
            try:
                texts.append(str(entry[1][0]))
            except (TypeError, IndexError, KeyError):
                continue
        return texts
    return []


def main(manifest_path: str) -> int:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    out_path = Path(manifest["out"])
    try:
        engine = _build_engine()
    except Exception as exc:
        print(f"OCR engine unavailable: {exc}", file=sys.stderr)
        out_path.write_text("{}", encoding="utf-8")
        return 1
    results: dict[str, str] = {}
    for image in manifest["images"]:
        try:
            # File input lets PaddleOCR decode the image itself, sidestepping
            # the RGB/BGR ndarray convention differences between releases.
            if hasattr(engine, "predict"):  # PaddleOCR 3.x
                result = engine.predict(image)
            else:  # legacy 2.x API
                result = engine.ocr(image)
            results[image] = _extract_text(result)
        except Exception as exc:
            print(f"OCR failed for {image}: {exc}", file=sys.stderr)
            results[image] = ""
    out_path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
