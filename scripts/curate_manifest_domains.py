#!/usr/bin/env python3
"""Curate ``legal_domains`` for every document in ``data/legal_sources.json``.

The retrieval-time domain filter only works when chunk domains are clean and
specific. Folder slugs (bf/ohada/cima) and 2000-char text inference previously
tagged nearly every document with 5+ pseudo-domains; this script writes an
explicit, reviewed mapping for all files under ``data/legal_docs/`` into the
``document_metadata`` manifest (existing entries keep their other fields;
missing entries are created with the domains only).

Rule order below: the first matching predicate wins. Files matched by no rule
fall back to name-only keyword inference, then to ``administrative_law`` (the
neutral choice for organic/ratification laws). Idempotent.

Usage:
    python scripts/curate_manifest_domains.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.ingestion.classification import infer_legal_domains, load_domain_keywords

SOURCES_PATH = Path("data/legal_sources.json")
DOCS_ROOT = Path("data/legal_docs")

# (predicate substrings — ALL must appear in the normalized filename, domains)
_RULES: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    # --- core codes ---------------------------------------------------------
    (("code_travail",), ["labor_code"]),
    (("code_famille",), ["family_code"]),
    (("code_civil",), ["civil_law"]),
    (("code_impots",), ["tax_law"]),
    (("code_mines",), ["land_law"]),
    (("procedure-civile",), ["civil_law", "government_procedures"]),
    (("procedure-penale",), ["criminal_law", "government_procedures"]),
    (("code-penal",), ["criminal_law"]),
    (("code_penal",), ["criminal_law"]),
    (("constitution",), ["constitution"]),
    (("aviation",), ["administrative_law"]),  # NOT civil_law: polluted prescription answers
    # --- assemblies / organic laws ------------------------------------------
    (("assemblee_loi_n_042",), ["tax_law", "administrative_law"]),  # loi de finances
    (("assemblee_loi_n_043",), ["criminal_law"]),  # amnistie
    (("assemblee_loi_n_045",), ["environmental_law"]),  # emballages plastiques
    (("assemblee_",), ["administrative_law"]),  # ratifications / habilitations
    (("loi-organique", "conseil-d-etat"), ["administrative_law", "government_procedures"]),
    # --- themed bf laws ------------------------------------------------------
    (("anticorruption",), ["administrative_law"]),
    (("concurrence",), ["commercial_law"]),
    (("commande-publique",), ["procurement_law"]),
    (("commande_publique",), ["procurement_law"]),
    (("investissement",), ["commercial_law"]),
    (("electoral",), ["elections"]),
    (("collectivites-territoriales",), ["administrative_law"]),
    (("expropriation",), ["land_law", "administrative_law"]),
    (("foncier",), ["land_law"]),
    (("blanchiment",), ["criminal_law", "commercial_law"]),
    (("justice-militaire",), ["criminal_law"]),
    (("organisation-judiciaire",), ["government_procedures"]),
    (("magistrature",), ["public_service"]),
    (("police-nationale",), ["public_service"]),
    (("personnels-des-forces",), ["public_service"]),
    (("violences-femmes",), ["criminal_law"]),
    (("securite-nationale",), ["administrative_law"]),
    (("renseignement",), ["administrative_law"]),
    (("etat-de-siege",), ["administrative_law"]),
    (("systemes-dinformation",), ["administrative_law"]),
    (("systemes-financiers",), ["commercial_law"]),
    (("bureaux-dinformation",), ["commercial_law"]),
    (("recepisse",), ["commercial_law"]),
    (("ordre-",), ["commercial_law"]),  # ordre des experts-comptables
    (("conventions-fiscales",), ["tax_law"]),
    (("incitations-fiscales",), ["tax_law"]),
    (("securite-sociale",), ["labor_code"]),
    (("travail-dinterim",), ["labor_code"]),
    (("permis-de-conduire",), ["traffic_law"]),
    (("contraventions-circulation",), ["traffic_law"]),
    (("ohada_acte-uniforme_transport-route",), ["ohada_law", "commercial_law"]),
    (("contraventions",), ["criminal_law"]),
    (("images-tabac",), ["health_regulations"]),
    (("phylogenetique",), ["environmental_law"]),
    (("neutralite-p",), ["environmental_law"]),  # neutralité carbone
    (("contenu-local",), ["land_law"]),
    (("mines-energie",), ["land_law"]),
    (("environnement",), ["environmental_law"]),
    (("transactions-electroniques",), ["commercial_law"]),
    (("identification-unique",), ["administrative_law"]),
    (("reunions-manifestations",), ["administrative_law"]),
    (("police-de-proximite",), ["administrative_law"]),
    (("traité-sur-le-commerce",), ["commercial_law"]),
    (("traite-sur-le-commerce",), ["commercial_law"]),
    (("culture",), ["administrative_law"]),
    (("protection",), ["administrative_law"]),
    (("defense",), ["administrative_law"]),
    (("economie",), ["administrative_law"]),
    (("justice",), ["administrative_law"]),
    (("transport",), ["administrative_law"]),
    (("finances",), ["administrative_law"]),
    (("numerique",), ["administrative_law"]),
    (("divers",), ["administrative_law"]),
    # --- regional instruments -------------------------------------------------
    (("cima",), ["commercial_law"]),  # code des assurances CIMA
    (("uemoa",), ["commercial_law"]),  # réglementation bancaire UEMOA
    (("traite-ohada",), ["ohada_law"]),
    (("reglement_ccja-procedure",), ["ohada_law", "government_procedures"]),
    (("reglement_ccja",), ["ohada_law", "commercial_law"]),
    (("ohada_acte-uniforme_procedures-collectives",), ["ohada_law", "commercial_law", "government_procedures"]),
    (("ohada_acte-uniforme_voies-dexecution",), ["ohada_law", "commercial_law", "government_procedures"]),
    (("ohada_acte-uniforme",), ["ohada_law", "commercial_law"]),
)


def _norm(name: str) -> str:
    import unicodedata

    lowered = name.lower().replace(" ", "_")
    return "".join(
        ch for ch in unicodedata.normalize("NFD", lowered) if unicodedata.category(ch) != "Mn"
    )


def propose_domains(filename: str, valid: set[str]) -> tuple[list[str], str]:
    norm = _norm(filename)
    for predicates, domains in _RULES:
        if all(p in norm for p in predicates):
            return [d for d in domains if d in valid], "rule"
    inferred = [d for d in infer_legal_domains(filename) if d in valid]
    if inferred:
        return inferred, "inferred"
    return ["administrative_law"], "fallback"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    metadata = sources.setdefault("document_metadata", {})
    valid = set(load_domain_keywords())

    files = sorted(p for p in DOCS_ROOT.rglob("*") if p.suffix.lower() in {".pdf", ".docx", ".txt", ".html"})
    changed = 0
    for path in files:
        key = path.name.lower()
        domains, origin = propose_domains(path.name, valid)
        if not domains:
            print(f"!! no valid domains for {path.name}")
            continue
        entry = metadata.get(key)
        if entry is None:
            # Preserve the manifest's original-case lookup behavior: keys are
            # lowercase filenames in this repo.
            entry = {}
            metadata[key] = entry
        if entry.get("legal_domains") != domains:
            entry["legal_domains"] = domains
            changed += 1
            print(f"[{origin:8s}] {path.parent.name}/{path.name[:70]} -> {domains}")

    print(f"\n{len(files)} files scanned, {changed} manifest entries updated")
    if not args.dry_run and changed:
        SOURCES_PATH.write_text(
            json.dumps(sources, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"manifest written: {SOURCES_PATH}")
    else:
        print("dry-run — manifest untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
