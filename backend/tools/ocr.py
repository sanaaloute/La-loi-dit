"""OCR tool for scanned documents/images. ``pytesseract`` (and the
Tesseract binary) are optional: both are imported lazily and any absence
or failure is reported gracefully in the result payload.
"""

from __future__ import annotations

TOOL_SPEC = {
    "name": "ocr",
    "description": "Extract text from an image or scanned PDF page via OCR (requires pytesseract).",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the image file."},
            "language": {"type": "string", "description": "Tesseract language code.", "default": "fra"},
        },
        "required": ["path"],
    },
}


async def run(path: str, language: str = "fra") -> dict:
    """TOOL entrypoint: OCR an image file."""
    try:
        import pytesseract  # lazy: optional dependency
        from PIL import Image  # lazy: optional dependency
    except Exception as exc:
        return {"success": False, "error": f"OCR dependencies unavailable (pytesseract/Pillow): {exc}"}
    try:
        with Image.open(path) as img:
            text = pytesseract.image_to_string(img, lang=language)
        return {"success": True, "path": path, "language": language, "text": text.strip()}
    except Exception as exc:
        return {"success": False, "error": f"OCR failed: {exc}", "path": path}
