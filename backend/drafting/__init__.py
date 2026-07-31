"""Legal document drafting grounded in retrieved law."""

from backend.drafting.service import DraftResult, generate_draft
from backend.drafting.templates import TEMPLATES, get_template, list_templates

__all__ = ["TEMPLATES", "DraftResult", "generate_draft", "get_template", "list_templates"]
