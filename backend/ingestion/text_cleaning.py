"""Text normalization and boilerplate removal.

Unicode NFKC normalization and whitespace cleanup preserve French accents
(é, è, ê, à, ç...) — NFKC only folds compatibility forms (ligatures,
full-width chars), never strips diacritics.  PDF extraction artifacts are
repaired by :func:`repair_extraction_artifacts`: line-break hyphenation
("docum-ents", "consente - ment") and intra-word spaces ("lie u" -> "lieu").
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from backend.core.config import Settings, get_settings
from backend.ingestion.loaders import ExtractedDocument

# PDF extraction often breaks words at line ends with a hyphen.  Depending on
# when whitespace is collapsed this shows up as "docum-\nents", "docum- ents"
# or "docum - ents".  Only lowercase-to-letter joins are repaired so genuine
# hyphens (number ranges like 033-2012, uppercase starts, bullet dashes) are
# preserved.
_HYPHEN_LINEBREAK_RE = re.compile(r"(?<=[A-Za-zÀ-ÖØ-öø-ÿ])-\s*\n\s*(?=[a-zà-öø-ÿ])")
_HYPHEN_SPACED_RE = re.compile(r"(?<=[a-zà-öø-ÿ])\s?-\s+(?=[a-zà-öø-ÿ])")

# PDF extraction with letter-spacing sometimes inserts spaces INSIDE a word:
# "lie u" (for "lieu"), "d roit" (for "droit"), or even "l i e u".  We rejoin a
# standalone lowercase letter to an adjacent lowercase word.  Genuine one-letter
# French words (a, à, y) and structural keywords followed by a lowercase roman
# numeral ("titre i", "section v") are never touched.
_FR_LOWER = "a-zàâäçéèêëîïôöùûü"
_ONE_LETTER_WORDS = {"a", "à", "y"}
# Letters that start a French elision (l', d', n', s', j', c', m', t'): never
# join them to the following word — the space may be a lost apostrophe.
_ELISION_LETTERS = set("cdjlmnst")
# For tail joins ("droi t" -> "droit") only a subset is risky: a trailing "t"
# never occurs in real French ("droi t" is a split), whereas "le d" may be the
# first half of a head split ("d roit") and must stay untouched.
_ELISION_TAIL_LETTERS = set("cdjlmns")
_STRUCTURE_WORDS = {"titre", "chapitre", "section", "article", "alinéa", "alinea", "annexe", "partie", "livre"}
_ROMAN_LETTERS = set("ivxlcdm")
# Common French function words: when a lone letter precedes one of these, it
# belongs to the PRECEDING word ("lie u de" -> "lieu de"); otherwise it belongs
# to the following one ("la u nion" -> "la union").
_FUNC_WORDS = {
    "de", "la", "le", "les", "des", "et", "en", "au", "aux", "du", "un", "une",
    "est", "sur", "par", "pour", "dans", "qui", "que", "sa", "se", "ne", "pas",
    "ou", "à", "son", "ses", "leur", "leurs", "ce", "ces", "une", "sont",
}
# Standalone French words of 2-4 letters (plurals handled by stripping s/x):
# a fragment that is itself a valid word is never joined to a neighbour.
_VALID_SHORT_WORDS = {
    "an", "art", "as", "au", "à", "a", "ai", "air", "âne", "bac", "bas", "bol",
    "bon", "but", "ça", "car", "cas", "ce", "ci", "cru", "cru", "de", "dès",
    "dit", "don", "dû", "du", "eau", "en", "est", "et", "eu", "eux", "fer",
    "feu", "foi", "fût", "gag", "il", "je", "jus", "là", "la", "le", "lit",
    "loi", "lu", "ma", "mal", "me", "mer", "mes", "mil", "moi", "mon", "mur",
    "ne", "né", "ni", "nom", "nu", "on", "ont", "or", "os", "où", "ou", "par",
    "pas", "peu", "pu", "pur", "qui", "que", "ras", "riz", "roi", "ru", "rue",
    "sa", "sac", "se", "sec", "sel", "ses", "si", "sol", "son", "su", "sud",
    "sur", "ta", "tas", "te", "tel", "tes", "tir", "toi", "ton", "tu", "un",
    "une", "va", "val", "ver", "via", "vie", "vin", "vis", "vu", "y", "acte",
    "afin", "aide", "ainsi", "alors", "avec", "bien", "bois", "ceci", "cela",
    "ceux", "cher", "chez", "code", "comme", "coup", "dans", "dont", "droit",
    "elle", "entre", "être", "fait", "fois", "fort", "haut", "hors", "joli",
    "leur", "lois", "lors", "mais", "même", "mise", "mois", "nord", "notre",
    "nous", "après", "avant", "plus", "pour", "près", "pris", "puis", "quel",
    "quoi", "sans", "sont", "sort", "sous", "tort", "tous", "tout", "très",
    "vers", "votre", "vous", "vue",
    "âge", "age", "cent", "cinq", "cour", "deux", "dix", "été", "fils", "font",
    "huit", "juge", "mère", "neuf", "père", "peur", "prix", "sait", "sept",
    "six", "sœur", "soeur", "taux", "voir", "vont",
    "fée", "idée", "née",
}
_SPLIT_TAIL_RE = re.compile(rf"\b([{_FR_LOWER}]{{2,}}) +([{_FR_LOWER}])\b")
# Fixed-width negative lookbehinds: a single letter right after a structure
# word ("titre i", "section v") is a roman numeral, not a split word.
_STRUCT_LOOKBEHIND = "".join(f"(?<!{w} )" for w in _STRUCTURE_WORDS)
_SPLIT_HEAD_RE = re.compile(rf"{_STRUCT_LOOKBEHIND}\b([{_FR_LOWER}]) +([{_FR_LOWER}]{{2,}})")
_SPLIT_RUN_RE = re.compile(rf"\b(?:[{_FR_LOWER}] +){{2,}}[{_FR_LOWER}]\b")
# Multi-letter fragments: "travaill eur" -> "travailleur", "tr availleur" ->
# "travailleur".  Two morphological guards keep real text intact:
# - TAIL: the fragment must END with a derivational suffix ("eur", "tion",
#   "ment", "ée"...) and the left side must NOT end with one — a left side
#   ending in a suffix is a complete word ("licenciement jugé", "cette image",
#   "lésée peut" stay untouched).
# - HEAD: the fragment must be a known prefix ("ir", "con", "dé"...) and is
#   capped at 3 letters ("juge vérifie" must never become "jugevérifie").
_FRAG_SUFFIXES_STRONG = (
    "ements", "ement", "tions", "tion", "sions", "sion", "euses", "euse",
    "eurs", "eur", "ions", "ion", "ments", "ment", "ités", "ité", "aires",
    "aire", "istes", "iste", "ismes", "isme", "ances", "ance", "ences",
    "ence", "ailles", "aille", "ettes", "ette", "esses", "esse", "ières",
    "ière", "ères", "ère", "ures", "ure", "ages", "age", "ants", "ant",
    "ents", "ent", "tés", "té",
)
# Past-participle endings: these DO form complete words ("lésée", "année",
# "idée"), so fragments carrying them are capped at 4 letters ("gée", "er").
_FRAG_SUFFIXES_WEAK = ("ées", "ée", "ers", "er", "es")
_FRAG_PREFIXES = {
    "tr", "tra", "tri", "con", "com", "cor", "pré", "prè", "pre", "pro",
    "dé", "dés", "ré", "re", "in", "im", "ir", "per", "em", "ex", "sub",
    "di", "dis", "mi", "mé", "co", "ob", "oc", "op", "ab", "ac", "ad",
    "ap", "at", "ag", "am", "ar", "uni",
}
_FRAG_TAIL_RE = re.compile(rf"\b([{_FR_LOWER}]{{3,}}) +([{_FR_LOWER}]{{2,7}})\b")
_FRAG_HEAD_RE = re.compile(rf"\b([{_FR_LOWER}]{{2,3}}) +([{_FR_LOWER}]{{3,}})\b")


def _ends_with_suffix(word: str) -> bool:
    return any(word.endswith(sfx) for sfx in (*_FRAG_SUFFIXES_STRONG, *_FRAG_SUFFIXES_WEAK))


def _looks_like_fragment(frag: str) -> bool:
    """True when a right-side fragment plausibly completes a split word."""
    if len(frag) <= 7 and any(frag.endswith(s) for s in _FRAG_SUFFIXES_STRONG):
        return True
    # Weak (participle) endings form real words when longer ("lésée", "année").
    return len(frag) <= 4 and any(frag.endswith(s) for s in _FRAG_SUFFIXES_WEAK)


def _is_valid_short_word(word: str) -> bool:
    """True for common standalone French words (plural s/x stripped)."""
    if word in _VALID_SHORT_WORDS or word in _FUNC_WORDS or word in _ONE_LETTER_WORDS:
        return True
    if len(word) > 2 and word[-1] in "sx" and word[:-1] in _VALID_SHORT_WORDS:
        return True
    return False

# Lines that are almost always boilerplate rather than legal content.
_BOILERPLATE_PATTERNS = [
    re.compile(r"^\s*page\s+\d+(\s*(/|de|sur|of)\s*\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*[-–—]\s*\d+\s*[-–—]\s*$"),
    re.compile(r"^\s*\d+\s*/\s*\d+\s*$"),
]


def _settings() -> Settings:
    return get_settings()


def normalize_unicode(text: str) -> str:
    """NFKC-normalize and unify space characters; French accents stay intact."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(" ", " ").replace(" ", " ")
    return text


def normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs and more-than-two consecutive newlines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def repair_hyphenation(text: str) -> str:
    """Rejoin words split by PDF line-break hyphenation.

    Handles both the raw line-break form ("docum-\\nents") and the already
    flattened forms ("docum- ents", "consente - ment").  Genuine hyphens are
    left untouched: the join only happens between a letter and a lowercase
    letter.
    """
    text = _HYPHEN_LINEBREAK_RE.sub("", text)
    return _HYPHEN_SPACED_RE.sub("", text)


def repair_split_words(text: str) -> str:
    """Rejoin words broken by spurious intra-word spaces ("lie u" -> "lieu").

    Conservative repairs, all lowercase-only:
    - a word (>= 2 letters) followed by a single letter: "lie u" -> "lieu";
    - a single letter followed by a word (>= 2 letters): "u nion" -> "union";
    - a run of 3+ single letters: "l i e u" -> "lieu";
    - multi-letter fragments joined only under morphological guards: the
      fragment must end with a derivational suffix and the left side must not
      ("travaill eur" -> "travailleur", but "licenciement jugé" is untouched);
      head fragments must be known prefixes ("ir régulière" -> "irrégulière").

    Genuine one-letter French words (a, à, y) are never joined, structural
    keywords followed by a roman numeral ("titre i", "section v") are preserved,
    and real phrases ("sur mer", "des fers", "le code", "cette image") are
    never merged.
    """

    def _join_tail(match: re.Match) -> str:
        word, letter = match.group(1), match.group(2)
        if letter in _ONE_LETTER_WORDS or letter in _ELISION_TAIL_LETTERS:
            return match.group(0)
        if word in _STRUCTURE_WORDS and letter in _ROMAN_LETTERS:
            return match.group(0)
        return word + letter

    def _join_head(match: re.Match) -> str:
        letter, word = match.group(1), match.group(2)
        if letter in _ONE_LETTER_WORDS or letter in _ELISION_LETTERS:
            return match.group(0)
        if word in _FUNC_WORDS:
            # "lie u de ...": the letter belongs to the preceding word (tail).
            return match.group(0)
        return letter + word

    def _join_run(match: re.Match) -> str:
        letters = match.group(0).split()
        if any(letter in _ONE_LETTER_WORDS for letter in letters):
            return match.group(0)
        return "".join(letters)

    def _join_frag_tail(match: re.Match) -> str:
        left, frag = match.group(1), match.group(2)
        if _is_valid_short_word(left) or _is_valid_short_word(frag):
            return match.group(0)
        # A left side ending in a derivational suffix is a complete word
        # ("licenciement jugé", "lésée peut", "cette image"): never merge.
        if _ends_with_suffix(left):
            return match.group(0)
        # The fragment must look like a suffix ("travaill eur", "expre ssément").
        if not _looks_like_fragment(frag):
            return match.group(0)
        return left + frag

    def _join_frag_head(match: re.Match) -> str:
        frag, right = match.group(1), match.group(2)
        if _is_valid_short_word(frag) or _is_valid_short_word(right):
            return match.group(0)
        # The fragment must be a known prefix ("ir régulière", "tr availleur").
        if frag not in _FRAG_PREFIXES:
            return match.group(0)
        return frag + right

    text = _SPLIT_RUN_RE.sub(_join_run, text)
    text = _SPLIT_HEAD_RE.sub(_join_head, text)
    text = _SPLIT_TAIL_RE.sub(_join_tail, text)
    # Tail fragments before head fragments: in "travaill eur licencié" the
    # fragment belongs to the preceding word, not to the following one.
    text = _FRAG_TAIL_RE.sub(_join_frag_tail, text)
    return _FRAG_HEAD_RE.sub(_join_frag_head, text)


def repair_extraction_artifacts(text: str) -> str:
    """Repair all known PDF extraction artifacts (hyphenation + split words)."""
    return repair_split_words(repair_hyphenation(text))


def _is_boilerplate_line(line: str) -> bool:
    return any(pattern.match(line) for pattern in _BOILERPLATE_PATTERNS)


def strip_repeated_headers_footers(pages: list[str]) -> list[str]:
    """Remove first/last lines repeated across most pages (running heads)."""
    cfg = _settings()
    min_pages = cfg.text_cleaning_min_pages_for_header
    min_freq = cfg.text_cleaning_header_min_frequency
    if len(pages) < min_pages:
        return pages

    edge_lines: Counter[str] = Counter()
    for page in pages:
        lines = [ln.strip() for ln in page.split("\n") if ln.strip()]
        for candidate in {lines[0], lines[-1]} if lines else set():
            if len(candidate) >= 3:  # ignore single digits etc.
                edge_lines[candidate] += 1

    threshold = max(2, int(len(pages) * min_freq))
    repeated = {line for line, count in edge_lines.items() if count >= threshold}
    if not repeated:
        return pages

    cleaned = []
    for page in pages:
        lines = page.split("\n")
        while lines and lines[0].strip() in repeated:
            lines.pop(0)
        while lines and lines[-1].strip() in repeated:
            lines.pop()
        cleaned.append("\n".join(lines))
    return cleaned


def clean_text(text: str) -> str:
    """Normalize a single text block: unicode, extraction artifacts, boilerplate, whitespace."""
    text = normalize_unicode(text)
    text = repair_extraction_artifacts(text)
    lines = [ln for ln in text.split("\n") if not _is_boilerplate_line(ln.strip())]
    return normalize_whitespace("\n".join(lines))


def clean_pages(pages: list[str]) -> list[str]:
    """Clean each page, stripping repeated headers/footers first."""
    pages = [normalize_unicode(p) for p in pages]
    pages = strip_repeated_headers_footers(pages)
    return [clean_text(p) for p in pages]


def clean_document(doc: ExtractedDocument) -> ExtractedDocument:
    """Return a cleaned copy of an :class:`ExtractedDocument`."""
    pages = clean_pages(doc.pages) if doc.pages else []
    text = clean_text(doc.text) if not pages else "\n\n".join(p for p in pages if p)
    return doc.model_copy(update={"text": text, "pages": pages})
