"""Export router: generate styled PDF, Word and CSV exports of an answer.

The caller posts the same JSON shape returned by ``POST /api/v1/chat``
(the ``ChatResponse``), so exports work for both the REST and streaming
endpoints without re-running the graph.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.api.deps import get_ctx
from backend.core.models import Citation, EvidenceChunk, FinalAnswer

router = APIRouter(tags=["export"])


class ExportRequest(BaseModel):
    """Payload accepted by the export endpoints."""

    query: str = Field(default="", description="Original user question")
    answer: FinalAnswer
    session_id: str = Field(default="", description="Conversation session id")
    latency_ms: float = Field(default=0.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_markdown(text: str) -> str:
    """Best-effort markdown to plain text for Word/PDF exports."""
    text = re.sub(r"```.*?```", lambda m: m.group(0).strip("`"), text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = text.replace("\u2022", "-")
    return text.strip()


def _format_confidence(value: float) -> str:
    return f"{value * 100:.0f}%"


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------


def _build_pdf(data: ExportRequest) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleCustom",
        parent=styles["Title"],
        fontSize=18,
        textColor=colors.HexColor("#1e3a8a"),
        spaceAfter=14,
        alignment=1,
    )
    heading_style = ParagraphStyle(
        "HeadingCustom",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#1e40af"),
        spaceBefore=14,
        spaceAfter=8,
    )
    normal_style = ParagraphStyle(
        "NormalCustom",
        parent=styles["BodyText"],
        fontSize=10,
        leading=14,
        spaceAfter=6,
    )
    small_style = ParagraphStyle(
        "SmallCustom",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#374151"),
    )

    story: list[Any] = []
    story.append(Paragraph("Réponse juridique", title_style))
    story.append(Paragraph(f"<b>Question :</b> {_strip_markdown(data.query)}", normal_style))
    story.append(Spacer(1, 6))

    # metadata badges
    badges = [
        ["Confiance", _format_confidence(data.answer.confidence)],
        ["Langue", data.answer.language or "fr"],
        ["Session", data.session_id or "-"],
        ["Latence", f"{data.latency_ms:.0f} ms"],
    ]
    meta_table = Table(badges, colWidths=[4 * cm, 10 * cm])
    meta_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eff6ff")),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#1e40af")),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bfdbfe")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(meta_table)

    if data.answer.requires_human_review:
        story.append(Spacer(1, 8))
        story.append(
            Paragraph(
                "<b>Révision humaine requise</b> — cette réponse doit être validée par un juriste.",
                ParagraphStyle(
                    "Warning",
                    parent=normal_style,
                    textColor=colors.HexColor("#991b1b"),
                    backColor=colors.HexColor("#fee2e2"),
                    borderPadding=6,
                ),
            )
        )

    story.append(Spacer(1, 12))
    story.append(Paragraph("Réponse", heading_style))
    for paragraph in _strip_markdown(data.answer.answer).split("\n\n"):
        if paragraph.strip():
            story.append(Paragraph(paragraph.replace("\n", "<br/>"), normal_style))

    if data.answer.citations:
        story.append(Paragraph("Citations", heading_style))
        for i, citation in enumerate(data.answer.citations, 1):
            line = f"[{i}] <b>{_strip_markdown(citation.label)}</b> — {citation.document_name}"
            if citation.article:
                line += f", art. {citation.article}"
            status = "✓ vérifiée" if citation.verified else "non vérifiée"
            line += f" <i>({status})</i>"
            if citation.url:
                line += f"<br/><a href='{citation.url}' color='blue'>{citation.url}</a>"
            story.append(Paragraph(line, small_style))
            story.append(Spacer(1, 4))

    if data.answer.evidence:
        story.append(Paragraph("Preuves", heading_style))
        for i, ev in enumerate(data.answer.evidence, 1):
            header = f"[{i}] <b>{_strip_markdown(ev.document_name)}</b>"
            if ev.article:
                header += f" — art. {ev.article}"
            story.append(Paragraph(header, small_style))
            body = _strip_markdown(ev.content)[:600]
            story.append(Paragraph(body, small_style))
            story.append(Spacer(1, 4))

    if data.answer.warnings:
        story.append(Paragraph("Avertissements", heading_style))
        for warning in data.answer.warnings:
            story.append(Paragraph(f"• {_strip_markdown(warning)}", small_style))

    story.append(Spacer(1, 20))
    story.append(
        Paragraph(
            "Avertissement : cette réponse est une aide à la recherche juridique. "
            "Elle ne constitue pas un conseil juridique. Consultez un professionnel du droit.",
            ParagraphStyle(
                "Disclaimer",
                parent=small_style,
                textColor=colors.HexColor("#92400e"),
                alignment=1,
            ),
        )
    )
    story.append(
        Paragraph(
            f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} — Assistant Juridique Burkina Faso",
            ParagraphStyle("Footer", parent=small_style, alignment=1, fontSize=8),
        )
    )

    doc.build(story)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Word export
# ---------------------------------------------------------------------------


def _build_docx(data: ExportRequest) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.8)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(0.9)
    section.right_margin = Inches(0.9)

    title = doc.add_heading("Réponse juridique", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.runs[0]
    run.font.color.rgb = RGBColor(30, 58, 138)
    run.font.size = Pt(20)

    doc.add_paragraph(f"Question : {_strip_markdown(data.query)}")

    meta = doc.add_paragraph()
    meta.add_run("Confiance : ").bold = True
    meta.add_run(_format_confidence(data.answer.confidence))
    meta.add_run("  |  Langue : ").bold = True
    meta.add_run(data.answer.language or "fr")
    meta.add_run("  |  Latence : ").bold = True
    meta.add_run(f"{data.latency_ms:.0f} ms")

    if data.answer.requires_human_review:
        p = doc.add_paragraph()
        p.add_run("Révision humaine requise").bold = True
        p.add_run(" — cette réponse doit être validée par un juriste.")
        p.runs[0].font.color.rgb = RGBColor(153, 27, 27)

    doc.add_heading("Réponse", level=1)
    for paragraph in _strip_markdown(data.answer.answer).split("\n\n"):
        if paragraph.strip():
            doc.add_paragraph(paragraph)

    if data.answer.citations:
        doc.add_heading("Citations", level=1)
        for i, citation in enumerate(data.answer.citations, 1):
            p = doc.add_paragraph(style="List Number")
            p.add_run(f" {_strip_markdown(citation.label)}").bold = True
            p.add_run(f" — {citation.document_name}")
            if citation.article:
                p.add_run(f", art. {citation.article}")
            status = "vérifiée" if citation.verified else "non vérifiée"
            p.add_run(f" ({status})")
            if citation.url:
                p.add_run(f" — {citation.url}")

    if data.answer.evidence:
        doc.add_heading("Preuves", level=1)
        for i, ev in enumerate(data.answer.evidence, 1):
            p = doc.add_paragraph(style="List Number")
            p.add_run(f" {_strip_markdown(ev.document_name)}").bold = True
            if ev.article:
                p.add_run(f" — art. {ev.article}")
            doc.add_paragraph(_strip_markdown(ev.content)[:600])

    if data.answer.warnings:
        doc.add_heading("Avertissements", level=1)
        for warning in data.answer.warnings:
            doc.add_paragraph(warning, style="List Bullet")

    disclaimer = doc.add_paragraph()
    disclaimer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = disclaimer.add_run(
        "Avertissement : cette réponse est une aide à la recherche juridique. "
        "Elle ne constitue pas un conseil juridique. Consultez un professionnel du droit."
    )
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(146, 64, 14)

    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer.add_run(
        f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} — Assistant Juridique Burkina Faso"
    )
    footer_run.font.size = Pt(8)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def _build_csv(data: ExportRequest) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Assistant Juridique Burkina Faso — Export CSV"])
    writer.writerow([])
    writer.writerow(["Question", _strip_markdown(data.query)])
    writer.writerow(["Session", data.session_id])
    writer.writerow(["Confiance", _format_confidence(data.answer.confidence)])
    writer.writerow(["Langue", data.answer.language or "fr"])
    writer.writerow(["Révision humaine", "Oui" if data.answer.requires_human_review else "Non"])
    writer.writerow(["Latence (ms)", f"{data.latency_ms:.1f}"])
    writer.writerow([])
    writer.writerow(["Réponse"])
    for paragraph in _strip_markdown(data.answer.answer).split("\n\n"):
        writer.writerow([paragraph])
    writer.writerow([])
    writer.writerow(["Citations"])
    writer.writerow(["#", "Label", "Document", "Article", "Vérifiée", "URL"])
    for i, citation in enumerate(data.answer.citations, 1):
        writer.writerow(
            [
                i,
                citation.label,
                citation.document_name,
                citation.article or "",
                "Oui" if citation.verified else "Non",
                citation.url or "",
            ]
        )
    writer.writerow([])
    writer.writerow(["Preuves"])
    writer.writerow(["#", "Document", "Article", "Section", "Source", "Contenu"])
    for i, ev in enumerate(data.answer.evidence, 1):
        writer.writerow(
            [
                i,
                ev.document_name,
                ev.article or "",
                ev.section or "",
                ev.source_kind,
                _strip_markdown(ev.content)[:1000],
            ]
        )
    writer.writerow([])
    writer.writerow(["Avertissements"])
    for warning in data.answer.warnings:
        writer.writerow([warning])
    writer.writerow([])
    writer.writerow(
        [
            "Avertissement : cette réponse est une aide à la recherche juridique. "
            "Elle ne constitue pas un conseil juridique. Consultez un professionnel du droit."
        ]
    )
    return buffer.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


_FORMATS: dict[str, tuple[str, str, Callable[[ExportRequest], bytes]]] = {
    "pdf": ("application/pdf", ".pdf", _build_pdf),
    "word": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
        _build_docx,
    ),
    "csv": ("text/csv; charset=utf-8", ".csv", _build_csv),
}


@router.post("/export/{format}")
async def export_answer(
    request: Request,
    format: str,
    payload: ExportRequest,
) -> Response:
    """Export a FinalAnswer to PDF, Word or CSV."""
    format = format.lower()
    if format not in _FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format: {format}. Supported: {', '.join(_FORMATS)}.",
        )
    ctx = get_ctx(request)
    mime, ext, builder = _FORMATS[format]
    filename = f"reponse-juridique-{datetime.now().strftime('%Y%m%d-%H%M%S')}{ext}"
    try:
        data = builder(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )
