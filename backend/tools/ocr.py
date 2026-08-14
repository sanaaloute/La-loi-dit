"""OCR tool for scanned documents/images, backed by the shared PaddleOCR
wrapper (``backend.ingestion.ocr``). The OCR stack is optional: when it is
unavailable the failure is reported gracefully in the result payload.
"""

from __future__ import annotations

TOOL_SPEC = {
    "name": "ocr",
    "description": "Extract text from an image or scanned PDF page via OCR (PaddleOCR).",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the image file."},
            "language": {"type": "string", "description": "PaddleOCR language code.", "default": "fr"},
        },
        "required": ["path"],
    },
}


async def run(path: str, language: str = "fr") -> dict:
    """TOOL entrypoint: OCR an image file."""
    from backend.ingestion import ocr as ocr_engine

    if not ocr_engine.ocr_available():
        return {
            "success": False,
            "error": "OCR unavailable: PaddleOCR is not installed or its models are missing",
            "path": path,
        }
    try:
        from PIL import Image  # lazy: optional dependency
    except Exception as exc:
        return {"success": False, "error": f"OCR dependencies unavailable (Pillow): {exc}", "path": path}
    try:
        with Image.open(path) as img:
            # The engine language comes from settings (ocr_lang); the
            # ``language`` argument is accepted for interface compatibility.
            text = ocr_engine.ocr_image(img.convert("RGB"))
        return {"success": True, "path": path, "language": language, "text": text.strip()}
    except Exception as exc:
        return {"success": False, "error": f"OCR failed: {exc}", "path": path}
