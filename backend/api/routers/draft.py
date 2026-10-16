"""Authenticated legal document drafting with shared model access and quotas."""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.deps import get_ctx, require_user
from backend.core.model_router import check_budget, resolve_llm
from backend.core.llm import LLMClient
from backend.drafting import service as drafting_service
from backend.drafting.templates import get_template, list_templates
from backend.security.jwt import TokenPayload

router = APIRouter(prefix="/draft", tags=["draft"])


class DraftRequest(BaseModel):
    template_id: str
    fields: dict[str, str] = Field(default_factory=dict)
    instructions: str = ""
    model: Optional[str] = None


class DraftResponse(BaseModel):
    title: str
    template_id: str
    draft_markdown: str
    citations: list[dict[str, Any]]
    warnings: list[str]
    requires_human_review: bool
    latency_ms: float


@router.get("/templates")
async def list_draft_templates(
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> dict[str, Any]:
    """List template metadata (no skeletons) for the drafting UI."""
    return {"templates": list_templates()}


@router.post("", response_model=DraftResponse)
async def create_draft(
    payload: DraftRequest,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> DraftResponse:
    """Generate a grounded draft from a template and caller-supplied fields."""
    template = get_template(payload.template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Modèle de document introuvable.")

    missing = [
        field["label"]
        for field in template["fields"]
        if field.get("required") and not (payload.fields.get(field["name"]) or "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="Champs requis manquants : " + ", ".join(missing),
        )

    ctx = get_ctx(request)
    await check_budget(ctx.user_store, user, ctx.settings)
    llm = resolve_llm(ctx, user, payload.model)
    usage_before = dict(llm.usage_totals)
    started = time.perf_counter()
    result = await drafting_service.generate_draft(
        ctx, template, payload.fields, payload.instructions, llm
    )
    if user.user_id and ctx.user_store is not None:
        tokens_in = llm.usage_totals["tokens_in"] - usage_before["tokens_in"]
        tokens_out = llm.usage_totals["tokens_out"] - usage_before["tokens_out"]
        if tokens_in <= 0 and tokens_out <= 0:
            # Offline/mock: the enrichment pass is skipped; estimate instead.
            tokens_in = LLMClient._estimate_tokens(payload.instructions + str(payload.fields))
            tokens_out = LLMClient._estimate_tokens(result.draft_markdown)
        try:
            await ctx.user_store.record_usage(user.user_id, tokens_in, tokens_out)
        except Exception:
            pass  # metering must never break drafting
    return DraftResponse(
        title=result.title,
        template_id=result.template_id,
        draft_markdown=result.draft_markdown,
        citations=[c.model_dump(mode="json") for c in result.citations],
        warnings=result.warnings,
        requires_human_review=result.requires_human_review,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )
