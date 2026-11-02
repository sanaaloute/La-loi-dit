"""Mobile release router: tells installed apps whether a newer store build exists.

The mobile apps poll ``GET /app/version`` at launch (and from the account
screen) and compare ``latest_version`` against their own bundle version. The
payload lives in ``data/mobile_release.json`` so publishing a store release
only requires editing that file — no backend code change or redeploy.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.api.deps import get_ctx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/app", tags=["mobile"])

RELEASE_FILENAME = "mobile_release.json"


class MobileReleaseOut(BaseModel):
    """What the stores currently serve; the apps compare against their build."""

    latest_version: str = ""
    release_notes: str = ""
    store_url_ios: str = "https://apps.apple.com/app/id6826883491"
    store_url_android: str = (
        "https://play.google.com/store/apps/details?id=net.neobytech.yawoto"
    )


def read_mobile_release(data_dir: Path) -> dict[str, Any]:
    """Best-effort read; a missing/corrupt file means "no update info"."""
    try:
        raw = json.loads((data_dir / RELEASE_FILENAME).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("mobile release file unreadable", exc_info=True)
        return {}
    return raw if isinstance(raw, dict) else {}


@router.get("/version", response_model=MobileReleaseOut)
async def app_version(request: Request) -> MobileReleaseOut:
    """Latest store release. Public: the apps call it before any login."""
    ctx = get_ctx(request)
    try:
        return MobileReleaseOut(**read_mobile_release(ctx.settings.data_dir))
    except Exception:
        logger.warning("mobile release file invalid", exc_info=True)
        return MobileReleaseOut()
