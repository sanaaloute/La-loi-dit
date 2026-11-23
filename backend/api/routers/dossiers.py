"""Dossiers router: personal case folders and documents ("Mes dossiers").

Unlike ``routers/documents.py`` (expert corpus ingestion), these endpoints are
per-user: any authenticated account manages its own case folders, uploads
documents into them, asks questions about them, and deletes them. Files and
extracted text live on disk under ``data_dir/user_dossiers/<user_id>/``; the
``user_dossiers`` and ``dossier_folders`` tables are the index.

Two analysis modes exist:

- ``documents``: answers grounded exclusively on the folder's documents
  (single LLM call over the combined excerpts).
- ``case``: full case analysis — a condensed case brief is built from the
  folder's documents (map-step extraction for long documents, cached on
  disk), then fed with the question into the legal corpus pipeline so the
  answer applies the indexed law to the case facts, with [n] citations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from backend.api.deps import get_ctx, get_graph, require_user
from backend.core.llm import LLMClient
from backend.core.model_router import check_budget, resolve_llm
from backend.security.jwt import TokenPayload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dossiers", tags=["dossiers"])

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

#: Extensions the ingestion loaders can extract text from.
_ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx"}

#: Multipart framing headroom for the early Content-Length check (same
#: approach as routers/documents.py).
_MULTIPART_OVERHEAD_BYTES = 64 * 1024

#: Extracted text kept on disk is capped to bound storage per dossier.
_MAX_STORED_CHARS = 500_000

#: Context window budget for analysis: the first ~8k tokens of the document.
#: Longer documents are flagged ``truncated`` so the UI can say so.
_ANALYZE_CONTEXT_CHARS = 32_000

#: Case-brief budget: documents shorter than this are quoted raw; longer ones
#: go through an LLM fact-extraction pass. The assembled brief is capped so
#: the case query stays well inside the model context window.
_BRIEF_RAW_CHARS = 6_000
_BRIEF_TOTAL_CHARS = 8_000
_BRIEF_EXTRACT_CHARS = 24_000

_ANALYSIS_SYSTEM_PROMPT = (
    "Tu es Yawoto, un avocat senior du barreau du Burkina Faso. L'utilisateur "
    "t'a confié un ou plusieurs documents personnels dans le cadre de son "
    "affaire. Réponds à sa question en te fondant EXCLUSIVEMENT sur le contenu "
    "de ces documents ; n'invente aucun passage. Analyse les documents comme "
    "tu le ferais pour un client : identifie les faits pertinents, les points "
    "de droit qu'ils engagent et ce que les documents établissent ou laissent "
    "incertain. Si les documents ne contiennent pas l'information demandée, "
    "dis-le clairement. Réponds en français, de façon complète et structurée, "
    "en citant entre guillemets les passages pertinents quand c'est utile. "
    "Termine en rappelant brièvement que cette analyse est fournie à titre "
    "informatif et ne constitue pas un conseil juridique."
)

_CASE_BRIEF_SYSTEM = (
    "Tu es Yawoto, un avocat senior du Burkina Faso. On te donne le texte "
    "extrait d'un document personnel d'un client. Produis une fiche de faits "
    "structurée et concise (10-20 lignes) : parties concernées, dates, "
    "montants, objets juridiques, clauses ou éléments pertinents pour une "
    "analyse de droit burkinabè. Ne commente pas, n'invente rien : rapporte "
    "uniquement ce que le document établit. Écris en français."
)


class DossierOut(BaseModel):
    id: str
    name: str
    original_name: str
    extension: str
    size_bytes: int
    char_count: int
    page_count: int
    status: str
    error: str
    folder_id: str = ""
    created_at: str


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2000)


class FolderUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=2000)


class FolderOut(BaseModel):
    id: str
    name: str
    description: str = ""
    document_count: int = 0
    created_at: str


class FolderDetailOut(BaseModel):
    folder: FolderOut
    documents: list[DossierOut]


class DossierMoveIn(BaseModel):
    folder_id: str = Field(default="", max_length=64)  # "" = unfiled


class AnalyzeIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AnalyzeOut(BaseModel):
    answer: str
    truncated: bool
    chars_used: int


class FolderAnalyzeIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["case", "documents"] = "case"


class FolderAnalyzeOut(BaseModel):
    answer: str
    mode: str
    confidence: float = 0.0
    citations: list[dict[str, Any]] = []
    documents_used: list[str] = []
    truncated: bool = False  # the case brief was capped to fit the context window


def _user_store(request: Request):
    store = getattr(get_ctx(request), "user_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    return store


def _user_root(request: Request, user_id: str) -> Path:
    return Path(get_ctx(request).settings.data_dir) / "user_dossiers" / user_id


def _dossier_dir(request: Request, user_id: str, dossier_id: str) -> Path:
    return _user_root(request, user_id) / dossier_id


def _folder_dir(request: Request, user_id: str, folder_id: str) -> Path:
    return _user_root(request, user_id) / "folders" / folder_id


def _extract_text(path: Path, extension: str) -> tuple[str, int]:
    """Run the ingestion loader for one file; returns (text, page_count)."""
    from backend.ingestion import loaders

    if extension == ".pdf":
        doc = loaders.load_pdf(path)
    elif extension == ".docx":
        doc = loaders.load_docx(path)
    elif extension == ".md":
        doc = loaders.load_markdown(path)
    else:
        doc = loaders.load_txt(path)
    page_count = len(doc.pages) if doc.pages else int(doc.metadata.get("page_count", 0) or 0)
    return doc.text, page_count


async def _owned_dossier(request: Request, user: TokenPayload, dossier_id: str) -> dict[str, Any]:
    dossier = await _user_store(request).get_dossier(user.user_id, dossier_id)
    if dossier is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable.")
    return dossier


async def _owned_folder(request: Request, user: TokenPayload, folder_id: str) -> dict[str, Any]:
    folder = await _user_store(request).get_folder(user.user_id, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable.")
    return folder


def _dossier_text(request: Request, user_id: str, dossier_id: str) -> str:
    text_path = _dossier_dir(request, user_id, dossier_id) / "text.txt"
    return text_path.read_text(encoding="utf-8") if text_path.is_file() else ""


async def _record_metering(
    request: Request, user: TokenPayload, before: dict[str, int], *, query: str, answer: str, source: str
) -> None:
    """Record this request's token usage; never breaks the answer path."""
    ctx = get_ctx(request)
    if ctx.user_store is None:
        return
    tokens_in = ctx.llm.usage_totals["tokens_in"] - before.get("tokens_in", 0)
    tokens_out = ctx.llm.usage_totals["tokens_out"] - before.get("tokens_out", 0)
    if tokens_in <= 0 and tokens_out <= 0:
        tokens_in = LLMClient._estimate_tokens(query)
        tokens_out = LLMClient._estimate_tokens(answer)
    try:
        await ctx.user_store.record_usage(user.user_id, tokens_in, tokens_out)
        await ctx.user_store.record_prompt(user.user_id, query, source=source, session_id="")
    except Exception:
        pass  # metering must never break the answer path


# ---------------------------------------------------------------------------
# Folders (cases)
# ---------------------------------------------------------------------------


@router.post("/folders", response_model=FolderOut, status_code=201)
async def create_folder(
    payload: FolderIn,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> FolderOut:
    """Create one case folder."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    row = await _user_store(request).add_folder(
        user.user_id, {"id": uuid.uuid4().hex, "name": payload.name.strip(), "description": payload.description}
    )
    if row is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    logger.info("dossier folder created", extra={"folder_id": row["id"], "user": user.sub})
    return FolderOut(**row)


@router.get("/folders", response_model=list[FolderOut])
async def list_folders(
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> list[FolderOut]:
    """Newest-first folders of the current user, with document counts."""
    if not user.user_id:
        return []
    rows = await _user_store(request).list_folders(user.user_id)
    return [FolderOut(**r) for r in rows]


@router.get("/folders/{folder_id}", response_model=FolderDetailOut)
async def get_folder(
    folder_id: str,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> FolderDetailOut:
    """One folder plus its documents (owner-scoped)."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    folder = await _owned_folder(request, user, folder_id)
    docs = await _user_store(request).list_dossiers(user.user_id, folder_id)
    return FolderDetailOut(
        folder=FolderOut(**folder, document_count=len(docs)),
        documents=[DossierOut(**d) for d in docs],
    )


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: str,
    payload: FolderUpdateIn,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> FolderOut:
    """Rename or redescribe one folder."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    folder = await _owned_folder(request, user, folder_id)
    store = _user_store(request)
    updated = await store.update_folder(
        user.user_id,
        folder_id,
        name=payload.name.strip() if payload.name is not None else None,
        description=payload.description,
    )
    if not updated:
        raise HTTPException(status_code=503, detail="user store unavailable")
    fresh = await store.get_folder(user.user_id, folder_id)
    docs = await store.list_dossiers(user.user_id, folder_id)
    return FolderOut(**(fresh or folder), document_count=len(docs))


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: str,
    request: Request,
    delete_documents: bool = False,
    user: TokenPayload = Depends(require_user),
) -> Response:
    """Delete one folder; its documents become unfiled unless ``delete_documents``."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    store = _user_store(request)
    folder = await store.get_folder(user.user_id, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable.")
    doc_ids = await store.delete_folder(user.user_id, folder_id, delete_documents=delete_documents)
    if doc_ids is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    for dossier_id in doc_ids:
        shutil.rmtree(_dossier_dir(request, user.user_id, dossier_id), ignore_errors=True)
    shutil.rmtree(_folder_dir(request, user.user_id, folder_id), ignore_errors=True)
    return Response(status_code=204)


@router.post("/folders/{folder_id}/analyze", response_model=FolderAnalyzeOut)
async def analyze_folder(
    folder_id: str,
    payload: FolderAnalyzeIn,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> FolderAnalyzeOut:
    """Answer a question about one case folder.

    ``documents`` mode grounds the answer exclusively on the folder's
    documents; ``case`` mode additionally runs the legal corpus pipeline so
    the answer applies the indexed law to the case facts.
    """
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    ctx = get_ctx(request)
    await check_budget(ctx.user_store, user, ctx.settings)
    folder = await _owned_folder(request, user, folder_id)
    store = _user_store(request)
    docs = [
        d
        for d in await store.list_dossiers(user.user_id, folder_id)
        if d["status"] == "ready"
    ]
    texts = {d["id"]: _dossier_text(request, user.user_id, d["id"]) for d in docs}
    docs = [d for d in docs if texts.get(d["id"], "").strip()]
    if not docs:
        raise HTTPException(
            status_code=422,
            detail="Aucun document exploitable dans ce dossier (texte non extrait).",
        )

    usage_before = dict(ctx.llm.usage_totals)
    if payload.mode == "documents":
        out = await _answer_from_documents(request, user, folder, docs, texts, payload.question)
    else:
        out = await _answer_case(request, user, folder, docs, texts, payload.question)
    await _record_metering(
        request, user, usage_before, query=payload.question, answer=out.answer, source="dossier_folder_analyze"
    )
    return out


async def _answer_from_documents(
    request: Request,
    user: TokenPayload,
    folder: dict[str, Any],
    docs: list[dict[str, Any]],
    texts: dict[str, str],
    question: str,
) -> FolderAnalyzeOut:
    """Documents-only answer: one LLM call over the combined excerpts."""
    ctx = get_ctx(request)
    per_doc = max(2000, _ANALYZE_CONTEXT_CHARS // max(1, len(docs)))
    truncated = False
    sections = []
    for d in docs:
        excerpt = texts[d["id"]].strip()[:per_doc]
        truncated = truncated or len(texts[d["id"]].strip()) > per_doc
        sections.append(
            f"--- DÉBUT DU DOCUMENT « {d['name']} » ---\n{excerpt}\n--- FIN DU DOCUMENT ---"
        )
    user_prompt = "\n\n".join(sections) + f"\n\nQuestion : {question.strip()}"
    llm = resolve_llm(ctx, user, None, query=question)
    answer = (
        await llm.complete(
            _ANALYSIS_SYSTEM_PROMPT, user_prompt, max_tokens=ctx.settings.answer_max_tokens
        )
    ).strip()
    if not answer:
        raise HTTPException(status_code=502, detail="Le modèle n'a renvoyé aucune réponse.")
    return FolderAnalyzeOut(
        answer=answer,
        mode="documents",
        documents_used=[d["name"] for d in docs],
        truncated=truncated,
    )


async def _answer_case(
    request: Request,
    user: TokenPayload,
    folder: dict[str, Any],
    docs: list[dict[str, Any]],
    texts: dict[str, str],
    question: str,
) -> FolderAnalyzeOut:
    """Case analysis: case brief + question through the legal corpus pipeline."""
    from backend.workflows.graph import GRAPH_RECURSION_LIMIT, initial_state

    ctx = get_ctx(request)
    brief, truncated = await _build_case_brief(request, user, folder, docs, texts)
    query = (
        f"{question.strip()}\n\n"
        f"--- FICHE DE CAS (documents du dossier « {folder['name']} » fournis par l'utilisateur) ---\n"
        f"{brief}\n"
        f"--- FIN DE LA FICHE DE CAS ---"
    )
    state = initial_state(query, user_id=user.user_id, language="fr")
    graph = get_graph(request)
    final_state = await graph.ainvoke(state, config={"recursion_limit": GRAPH_RECURSION_LIMIT})
    answer = final_state["final_answer"]
    return FolderAnalyzeOut(
        answer=answer.answer,
        mode="case",
        confidence=answer.confidence,
        citations=[c.model_dump() for c in answer.citations],
        documents_used=[d["name"] for d in docs],
        truncated=truncated,
    )


async def _build_case_brief(
    request: Request,
    user: TokenPayload,
    folder: dict[str, Any],
    docs: list[dict[str, Any]],
    texts: dict[str, str],
) -> tuple[str, bool]:
    """Assemble the folder's case brief, cached on disk per content hash.

    Short documents are quoted raw; long ones get an LLM fact-extraction pass
    (falling back to a raw head excerpt when the LLM call fails). The cache is
    keyed by the folder's document fingerprint so edits invalidate it.
    """
    ctx = get_ctx(request)
    fingerprint = hashlib.sha256(
        json.dumps(
            sorted((d["id"], d["char_count"], d["size_bytes"]) for d in docs),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    folder_dir = _folder_dir(request, user.user_id, folder["id"])
    cache_path = folder_dir / "brief.json"
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fingerprint and cached.get("brief"):
                return cached["brief"], bool(cached.get("truncated"))
        except Exception:
            pass  # corrupt cache: rebuild

    llm = resolve_llm(ctx, user, None, query=folder["name"])
    parts: list[str] = []
    truncated = False
    for d in docs:
        text = texts[d["id"]].strip()
        if len(text) <= _BRIEF_RAW_CHARS:
            excerpt = text
        else:
            truncated = True
            try:
                excerpt = (
                    await llm.complete(
                        _CASE_BRIEF_SYSTEM,
                        f"Document « {d['name']} » :\n\n{text[:_BRIEF_EXTRACT_CHARS]}",
                        max_tokens=1500,
                    )
                ).strip()
            except Exception as exc:
                logger.warning(
                    "case brief extraction failed, using raw excerpt",
                    extra={"dossier_id": d["id"], "error": str(exc)},
                )
                excerpt = ""
            if not excerpt:
                excerpt = text[:_BRIEF_RAW_CHARS]
        parts.append(f"### Document : {d['name']}\n{excerpt}")

    brief = "\n\n".join(parts)
    if len(brief) > _BRIEF_TOTAL_CHARS:
        brief = brief[:_BRIEF_TOTAL_CHARS]
        truncated = True
    try:
        folder_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"fingerprint": fingerprint, "brief": brief, "truncated": truncated}),
            encoding="utf-8",
        )
    except Exception:
        pass  # caching is an optimization only
    return brief, truncated


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@router.post("", response_model=DossierOut, status_code=201)
async def upload_dossier(
    request: Request,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    folder_id: Optional[str] = Form(None),
    user: TokenPayload = Depends(require_user),
) -> DossierOut:
    """Upload one personal document and extract its text for analysis."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    ctx = get_ctx(request)
    max_bytes = ctx.settings.max_upload_bytes_user
    too_large = f"Fichier trop volumineux (max {max_bytes // (1024 * 1024)} Mo)"
    content_length = request.headers.get("content-length")
    if (
        content_length
        and content_length.isdigit()
        and int(content_length) > max_bytes + _MULTIPART_OVERHEAD_BYTES
    ):
        raise HTTPException(status_code=413, detail=too_large)

    original_name = file.filename or "document"
    extension = Path(original_name).suffix.lower()
    if extension not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Format non pris en charge. Formats acceptés : PDF, TXT, MD, DOCX.",
        )

    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=too_large)
    if not content:
        raise HTTPException(status_code=400, detail="Fichier vide.")

    folder = ""
    if folder_id:
        folder_row = await _user_store(request).get_folder(user.user_id, folder_id)
        if folder_row is None:
            raise HTTPException(status_code=404, detail="Dossier introuvable.")
        folder = folder_row["id"]

    dossier_id = uuid.uuid4().hex
    safe_name = _SAFE_NAME.sub("_", original_name)
    dest_dir = _dossier_dir(request, user.user_id, dossier_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    original_path = dest_dir / f"original{extension}"
    original_path.write_bytes(content)

    record: dict[str, Any] = {
        "name": (title or Path(original_name).stem).strip()[:500] or "Document",
        "original_name": safe_name,
        "extension": extension,
        "size_bytes": len(content),
        "status": "ready",
        "folder_id": folder,
    }
    try:
        text, page_count = _extract_text(original_path, extension)
        text = text.strip()
        if not text:
            raise ValueError("Aucun texte extractible (document scanné ou vide).")
        (dest_dir / "text.txt").write_text(text[:_MAX_STORED_CHARS], encoding="utf-8")
        record["char_count"] = len(text)
        record["page_count"] = page_count
    except Exception as exc:
        logger.warning("dossier text extraction failed", extra={"error": str(exc)})
        record["status"] = "error"
        record["error"] = "Le texte du document n'a pas pu être extrait."

    row = await _user_store(request).add_dossier(user.user_id, {"id": dossier_id, **record})
    if row is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    logger.info(
        "dossier uploaded",
        extra={"dossier_id": row["id"], "user": user.sub, "status": row["status"], "folder_id": folder},
    )
    return DossierOut(**row)


@router.get("", response_model=list[DossierOut])
async def list_dossiers(
    request: Request,
    folder_id: Optional[str] = None,
    user: TokenPayload = Depends(require_user),
) -> list[DossierOut]:
    """Newest-first dossiers of the current user.

    Without ``folder_id`` every document is returned; ``folder_id=`` (empty)
    lists unfiled documents and ``folder_id=<id>`` lists one folder's.
    """
    if not user.user_id:
        return []
    rows = await _user_store(request).list_dossiers(user.user_id, folder_id)
    return [DossierOut(**r) for r in rows]


@router.get("/{dossier_id}/text")
async def dossier_text(
    dossier_id: str,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> dict[str, Any]:
    """Return the extracted text of one dossier (owner-scoped)."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    dossier = await _owned_dossier(request, user, dossier_id)
    text = _dossier_text(request, user.user_id, dossier_id)
    return {"dossier": dossier, "text": text}


@router.patch("/{dossier_id}", response_model=DossierOut)
async def move_dossier(
    dossier_id: str,
    payload: DossierMoveIn,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> DossierOut:
    """Move one dossier into a folder (``folder_id=""`` unfiles it)."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    dossier = await _owned_dossier(request, user, dossier_id)
    store = _user_store(request)
    if payload.folder_id:
        folder = await store.get_folder(user.user_id, payload.folder_id)
        if folder is None:
            raise HTTPException(status_code=404, detail="Dossier introuvable.")
    updated = await store.update_dossier_folder(user.user_id, dossier_id, payload.folder_id)
    if not updated:
        raise HTTPException(status_code=503, detail="user store unavailable")
    return DossierOut(**{**dossier, "folder_id": payload.folder_id})


@router.delete("/{dossier_id}", status_code=204)
async def delete_dossier(
    dossier_id: str,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> Response:
    """Delete one dossier: index row plus files on disk."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    deleted = await _user_store(request).delete_dossier(user.user_id, dossier_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Dossier introuvable.")
    shutil.rmtree(_dossier_dir(request, user.user_id, dossier_id), ignore_errors=True)
    return Response(status_code=204)


@router.post("/{dossier_id}/analyze", response_model=AnalyzeOut)
async def analyze_dossier(
    dossier_id: str,
    payload: AnalyzeIn,
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> AnalyzeOut:
    """Answer one question grounded exclusively on the dossier's text."""
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    ctx = get_ctx(request)
    await check_budget(ctx.user_store, user, ctx.settings)
    dossier = await _owned_dossier(request, user, dossier_id)
    if dossier["status"] != "ready":
        raise HTTPException(
            status_code=422,
            detail=dossier["error"] or "Le texte de ce document n'est pas disponible.",
        )
    text = _dossier_text(request, user.user_id, dossier_id)
    if not text:
        raise HTTPException(status_code=422, detail="Le texte de ce document n'est pas disponible.")

    truncated = len(text) > _ANALYZE_CONTEXT_CHARS
    excerpt = text[:_ANALYZE_CONTEXT_CHARS]
    user_prompt = (
        f"--- DÉBUT DU DOCUMENT « {dossier['name']} » ---\n"
        f"{excerpt}\n"
        f"--- FIN DU DOCUMENT ---\n\n"
        f"Question : {payload.question.strip()}"
    )

    usage_before = dict(ctx.llm.usage_totals)
    llm = resolve_llm(ctx, user, None, query=payload.question)
    answer = (
        await llm.complete(
            _ANALYSIS_SYSTEM_PROMPT, user_prompt, max_tokens=ctx.settings.answer_max_tokens
        )
    ).strip()
    if not answer:
        raise HTTPException(status_code=502, detail="Le modèle n'a renvoyé aucune réponse.")

    await _record_metering(
        request,
        user,
        usage_before,
        query=payload.question,
        answer=answer,
        source="dossier_analyze",
    )
    return AnalyzeOut(answer=answer, truncated=truncated, chars_used=len(excerpt))
