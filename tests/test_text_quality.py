"""Bad-OCR-layer detection (ingestion/text_quality.py) and the related
cleaning/chunking behaviors.

The garbage/clean excerpts below are real samples from the corpus: the old
Assemblée scans carry a broken embedded OCR layer, recent documents are
born-digital.
"""

from backend.ingestion.chunking import (
    _ARTICLE_HEADING_RE,
    _normalize_article_number,
    legal_parent_child_chunk,
    looks_like_legal,
    parent_child_chunk,
)
from backend.ingestion.loaders import ExtractedDocument, _bad_layer_pages
from backend.ingestion.text_cleaning import clean_pages, _is_boilerplate_line
from backend.ingestion.text_quality import (
    has_orphan_article_heading,
    page_needs_ocr,
    text_quality_score,
)

# Real excerpt from assemblee_1001 (Loi 60/95/ADP, 1995) — broken embedded layer.
GARBAGE_LAYER = '''
L'ASSEMBLEE
DES DEPUTES DU PEUPLE
VU la Conditution ;
VU la Rélolution n° 01/ADP du 17juin 1992, portant validation
du mandat deI Députél ;
VU la latu N°AN VII-00421FP/PRES du 12juillet 1990, portant
réglementation bancaire;
A délibéré en fi léance du 13décembre1995 et adopté la loi dont la teneurlUit :
Article 1er: LeI dilpolitionl de l'article 40 de la latu n°AN VII-00421FP/PRESdu
12 juillet 1990 portant réglementationbancairelont abrogéelet remplacéelpar lel
luivantel. Arfid, 40 III"""} : «lel banquelet établillementl financierldoivent
arrêter leurl comptel au 31 décembre de chaqueannée.
'''

# Real excerpt from assemblee_1793 (code général des impôts, 2017) — born-digital.
CLEAN_LAYER = """
Livre 1 - Impôts directs et taxes assimilées
Titre 1 - Impôts sur le revenu
Chapitre 1 - Impôt sur les bénéfices industriels, commerciaux et agricoles (IBICA)
Section 1 - Champ d'application
Art. 1.- Il est établi, au profit du budget de l'État, un impôt annuel sur les
bénéfices de toute nature réalisés par des personnes physiques ou des sociétés
dans le cadre d'activités industrielles, commerciales ou agricoles exercées au
Burkina Faso, sous réserve des conventions internationales et des dispositions
contraires prévues par la présente loi.
"""

# Real excerpt from assemblee_1833 (loi de finances rectificative) — budget
# tables must never be mistaken for a broken layer.
BUDGET_TABLE = """
Article 1 : Sont annulés au budget de l'État, exercice 2017 :
En recettes : Art  Par  NATURE DES RESSOURCES  ANNULATION
72  RECETTES NON FISCALES  19 172 830 000
72 722  Droits et frais administratifs
72 723  Amendes et sanctions
"""


def test_garbage_layer_scores_below_threshold():
    score = text_quality_score(GARBAGE_LAYER)
    assert score is not None and score < 0.90


def test_clean_layer_scores_high():
    score = text_quality_score(CLEAN_LAYER)
    assert score is not None and score >= 0.95


def test_budget_table_is_not_flagged():
    score = text_quality_score(BUDGET_TABLE)
    assert score is not None and score >= 0.90
    assert not page_needs_ocr(BUDGET_TABLE, min_score=0.92)


def test_short_text_has_no_score_and_is_not_flagged():
    assert text_quality_score("Court texte.") is None
    assert not page_needs_ocr("Court texte.", min_score=0.92)


def test_orphan_article_heading_flags_page():
    # assemblee_1016's broken layer splits "Article 1er" into "Article\nl'\n".
    assert has_orphan_article_heading("Article\nl'\nLa présente loi fixe les modalités.")
    assert page_needs_ocr("Article\nl'\nLa présente loi fixe les modalités.", min_score=0.92)
    assert not has_orphan_article_heading("Article 1er : Les dispositions…")
    assert not has_orphan_article_heading("ARTICLE 12 : Nonobstant…")


def test_page_needs_ocr_on_garbage_excerpt():
    assert page_needs_ocr(GARBAGE_LAYER, min_score=0.92)


class _FakePdf:
    def __init__(self, has_images: list[bool]):
        self._has_images = has_images

    def __getitem__(self, index):
        fake = type("FakePage", (), {})()
        fake.get_images = lambda: ["img"] if self._has_images[index] else []
        return fake


def test_bad_layer_pages_only_flags_image_pages_with_broken_text():
    pages = [GARBAGE_LAYER, CLEAN_LAYER, GARBAGE_LAYER]
    pdf = _FakePdf(has_images=[True, True, False])
    assert _bad_layer_pages(pdf, pages) == [1]


def test_bad_layer_pages_disabled_by_setting(monkeypatch):
    from backend.core.config import get_settings

    monkeypatch.setenv("LEGAL_AI_OCR_BAD_LAYER_ENABLED", "false")
    get_settings.cache_clear()  # settings are lru_cached between tests
    try:
        pdf = _FakePdf(has_images=[True])
        assert _bad_layer_pages(pdf, [GARBAGE_LAYER]) == []
    finally:
        get_settings.cache_clear()


def test_chunker_accepts_ocr_ler_alias_for_article_one():
    match = _ARTICLE_HEADING_RE.search("Article ler: Les dispositions de l'article 40…")
    assert match is not None
    assert _normalize_article_number(match.group(1)) == "1"


def test_printed_page_numbers_stripped_but_content_numbers_kept():
    pages = [
        "BURKINA FASO\nIV. REPUBLIQUE",
        "2\nL'ASSEMBLEE\nVU la Constitution ;",
        "suite du texte\n3",
        # page index 3: "72" is not at a page-number position — it is content.
        "72\nRECETTES NON FISCALES\n19 172 830 000",
    ]
    out = clean_pages(pages)
    assert "2\nL'ASSEMBLEE" not in out[1]
    assert out[1].startswith("L'ASSEMBLEE")
    assert out[2] == "suite du texte"
    assert out[3].startswith("72")


def test_dash_runs_are_boilerplate():
    assert _is_boilerplate_line("-- -- --")
    assert _is_boilerplate_line("---- -")
    assert not _is_boilerplate_line("- le conseil provincial")


def test_chunker_accepts_ocr_ter_alias_for_article_one():
    # Real excerpt from assemblee_1098 (Loi 15/95/ADP): PaddleOCR misreads
    # "Article 1er" as "Article ter" (1 -> t) on the 1995 scans.
    match = _ARTICLE_HEADING_RE.search("Article ter : Le Gouvernement est autorisé…")
    assert match is not None
    assert _normalize_article_number(match.group(1)) == "1"


def test_chunker_accepts_article_unique_as_article_one():
    match = _ARTICLE_HEADING_RE.search("Article unique : Les présentes dispositions…")
    assert match is not None
    assert _normalize_article_number(match.group(1)) == "1"


def test_single_article_ratification_law_looks_legal():
    # Real shape of assemblee_1385 (Loi 044-2008/AN): one single article.
    text = (
        "L'ASSEMBLEE NATIONALE\n\nvu la Constitution ;\n\n"
        "a délibéré en sa séance du 04 novembre 2008\n"
        "et adopté la loi dont la teneur suit :\n\n"
        "Article 1 :\n\nLe gouvernement du Burkina Faso est autorisé à ratifier la Convention.\n\n"
        "La présente loi sera exécutée comme loi de l'Etat."
    )
    assert looks_like_legal(text)


def test_inline_article_citations_do_not_look_legal():
    # Parliamentary reports quote articles inline, never at a line start:
    # they must stay on the generic chunking path.
    text = (
        "Rapport de la commission des finances.\n\n"
        "Au terme de l'article 5 de la loi n°012-2024/ALT, la commission a examiné le projet.\n"
        "L'article 7 prévoit par ailleurs que le Gouvernement rende compte.\n"
        "Observations : adoption à l'unanimité."
    )
    assert not looks_like_legal(text)


def test_legal_chunker_keeps_preamble_retrievable_and_stamps_seq():
    doc = ExtractedDocument(
        name="loi_test.pdf",
        text=(
            "LOI N° 044-2008/AN\n\nPORTANT AUTORISATION DE RATIFICATION\n\n"
            "L'ASSEMBLEE NATIONALE a délibéré et adopté la loi dont la teneur suit :\n\n"
            "Article 1 :\n\nLe gouvernement est autorisé à ratifier la Convention.\n\n"
            "Article 2 :\n\nLa présente loi sera exécutée comme loi de l'Etat."
        ),
    )
    chunks = legal_parent_child_chunk(
        doc, "docid", document_name="Loi test", law_number="044-2008/AN"
    )
    parents = [c for c in chunks if c.metadata["role"] == "parent"]
    # Preamble parent: retrievable (role "parent", not "heading"), no article.
    assert parents[0].article is None
    assert "PORTANT AUTORISATION" in parents[0].content
    pre_children = [c for c in chunks if c.parent_chunk_id == parents[0].chunk_id]
    assert pre_children and all(c.metadata["role"] == "child" for c in pre_children)
    assert all((c.retrieval_text or "").startswith("« Loi test") for c in pre_children)
    assert "Article None" not in (pre_children[0].retrieval_text or "")
    # Both articles detected as parents.
    assert sorted(c.article for c in parents if c.article) == ["1", "2"]
    # seq is a monotonic, gap-free reading order over all chunks.
    seqs = [c.metadata["seq"] for c in chunks]
    assert seqs == list(range(len(chunks)))


def test_internal_headings_stay_inline_in_article_body():
    # Section headings between articles do not open their own segment: the
    # heading line stays in the flow and only stamps the next articles'
    # section/hierarchy metadata.
    doc = ExtractedDocument(
        name="code.pdf",
        text=(
            "Article 1 :\n\nPremier alinéa.\n\n"
            "TITRE II : DES CONTRATS\n\n"
            "Article 2 :\n\nSecond alinéa."
        ),
    )
    chunks = legal_parent_child_chunk(doc, "docid", document_name="Code test")
    parents = [c for c in chunks if c.parent_chunk_id is None]
    assert [c.article for c in parents] == ["1", "2"]
    assert "TITRE II : DES CONTRATS" in parents[0].content
    assert parents[1].section == "Titre II"
    assert not any(c.metadata["role"] == "heading" for c in chunks)


def test_article_keeps_following_budget_tables():
    # Loi de finances shape: the table between "Article 1" and "Article 2"
    # is article 1's content (stub-article regression).
    text = (
        "Article 1 : Sont annulés au Budget de l'Etat, les crédits ci-après :\n\n"
        "Titre 0 : Recettes\n\n"
        "Section 15 : Ministère des Finances\n\n"
        "Chapitre 12101 : Cabinet\n\n1 000 000\n\n"
        "Article 2 : Sont ouverts les crédits."
    )
    doc = ExtractedDocument(name="lf.pdf", text=text)
    chunks = legal_parent_child_chunk(doc, "docid", document_name="LF test")
    parents = [c for c in chunks if c.parent_chunk_id is None]
    a1 = [c for c in parents if c.article == "1"]
    assert a1, "article 1 missing"
    body = a1[0].content
    assert "Titre 0 : Recettes" in body
    assert "Section 15 : Ministère des Finances" in body
    assert "Chapitre 12101 : Cabinet" in body and "1 000 000" in body
    assert parents[-1].article == "2"


def test_section_only_document_stays_searchable():
    # No article headings at all: the whole text becomes one preamble
    # parent/child group (retrievable), not unsearchable heading chunks.
    doc = ExtractedDocument(
        name="guide.pdf",
        text="Chapitre 1 : Dispositions.\n\nDu texte utile.\n\nChapitre 2 : Suite.\n\nAutre texte utile.",
    )
    chunks = legal_parent_child_chunk(doc, "docid", document_name="Guide")
    parents = [c for c in chunks if c.parent_chunk_id is None]
    assert len(parents) == 1 and parents[0].article is None
    assert parents[0].metadata["role"] == "parent"
    children = [c for c in chunks if c.parent_chunk_id == parents[0].chunk_id]
    assert children and all(c.retrieval_text for c in children)


def test_generic_chunker_stamps_seq():
    doc = ExtractedDocument(name="rapport.pdf", text="Rapport de séance. " * 400)
    chunks = parent_child_chunk(doc, "docid", document_name="Rapport")
    seqs = [c.metadata["seq"] for c in chunks]
    assert seqs == list(range(len(chunks)))
    assert all(c.metadata["role"] in ("parent", "child") for c in chunks)


def test_oversized_legal_parent_is_split_to_fit_milvus_varchar():
    # Session reports (PV) can yield one giant segment (whole debate before an
    # annexed "Article 1er"); the parent must be split under the 65535-char
    # chunk_json limit without losing text.
    big_preamble = "Séance du mardi.\n\n" + ("Le député intervenit sur le quorum et la suite des débats. " * 1200)
    doc = ExtractedDocument(
        name="pv.pdf",
        text=big_preamble + "\n\nArticle 1er :\n\nLe projet est adopté.",
    )
    chunks = legal_parent_child_chunk(doc, "docid", document_name="PV test")
    parents = [c for c in chunks if c.parent_chunk_id is None]
    assert len(parents) > 1
    assert all(len(c.content) <= 30_500 for c in parents)
    # Articles still detected despite the split preamble.
    assert any(c.article == "1" for c in parents)
    # Children reference only existing parents, text coverage preserved.
    joined = "".join(c.content for c in parents)
    assert "Le député intervenit" in joined and "Le projet est adopté" in joined
    seqs = [c.metadata["seq"] for c in chunks]
    assert seqs == list(range(len(chunks)))


def test_amendment_markers_attach_to_introduced_article():
    # Amending laws quote each modified article as "Au lieu de : <old> Lire :
    # <new>"; the marker must open the block it introduces, not trail the
    # previous one (loi n°007-2005/AN shape).
    text = (
        "Loi portant modification de la loi n°026-94/ADP.\n\n"
        "Article 1 :\n\nLes articles 33 et 34 sont modifiés comme suit :\n\n"
        "Au lieu de :\n\n"
        "Article 33 :\n\nAncien texte de l'article trente-trois.\n\n"
        "Lire :\n\n"
        "Article 33 :\n\nNouveau texte de l'article trente-trois.\n\n"
        "Au lieu de :\n\n"
        "Article 34 :\n\nAncien texte de l'article trente-quatre."
    )
    doc = ExtractedDocument(name="loi.pdf", text=text)
    chunks = legal_parent_child_chunk(doc, "docid", document_name="Loi modif")
    parents = [c for c in chunks if c.parent_chunk_id is None]

    a33 = [c for c in parents if c.article == "33"]
    assert len(a33) == 2
    assert a33[0].content.startswith("Au lieu de :")
    assert "Ancien texte de l'article trente-trois." in a33[0].content
    assert a33[1].content.startswith("Lire :")
    assert "Nouveau texte de l'article trente-trois." in a33[1].content

    # No marker left stranded at any segment tail; the quoting article keeps
    # its own body without the moved marker.
    assert not any(
        c.content.rstrip().endswith(("Au lieu de :", "Lire :")) for c in parents
    )
    a1 = [c for c in parents if c.article == "1"]
    assert a1 and a1[0].content.rstrip().endswith("comme suit :")

    a34 = [c for c in parents if c.article == "34"]
    assert len(a34) == 1 and a34[0].content.startswith("Au lieu de :")


def test_budget_account_codes_are_not_headings():
    # Finance-law budget tables label rows "Chapitre <4-8-digit account>"; they
    # must stay inline text, not become structural headings (loi 020-2006).
    text = (
        "Article 1 : Sont ouverts les crédits suivants.\n\n"
        "Chapitre 1218241 : Bureau du Médiateur du Faso\n\n"
        "500 000\n\n"
        "Chapitre 12101 : Cabinet\n\n"
        "1 000 000\n\n"
        "Article 2 : La présente loi sera exécutée comme loi de l'Etat."
    )
    doc = ExtractedDocument(name="lf.pdf", text=text)
    chunks = legal_parent_child_chunk(doc, "docid", document_name="LF test")
    parents = [c for c in chunks if c.parent_chunk_id is None]
    assert all(c.section is None or "Chapitre 12" not in c.section for c in parents)
    a1 = [c for c in parents if c.article == "1"]
    assert a1 and "Chapitre 1218241" in a1[0].content and "Chapitre 12101" in a1[0].content
    assert any(c.article == "2" for c in parents)


def test_repeated_budget_article_lines_leave_the_index():
    # "Article 62 : Achats de biens..." repeats per ministry in finance laws;
    # past 3 occurrences it is nomenclature: folded into the previous segment
    # (still searchable) and out of the article index.
    lines = ["Article 1 : Sont annulés les crédits suivants."]
    for i in range(6):
        lines.append(f"Article 62 : Achats de biens et services, paragraphe 621, lot {i}, 1 000 000.")
    lines.append("Article 2 : Sont ouverts les crédits.")
    doc = ExtractedDocument(name="lf.pdf", text="\n\n".join(lines))
    chunks = legal_parent_child_chunk(doc, "docid", document_name="LF test")
    parents = [c for c in chunks if c.parent_chunk_id is None]
    assert {c.article for c in parents if c.article} == {"1", "2"}
    a1 = [c for c in parents if c.article == "1"]
    assert a1 and a1[0].content.count("Achats de biens et services") == 6


def test_amendment_old_new_pair_stays_articles_after_demotion():
    # The demotion threshold must not touch amendment old/new pairs (2 quotes).
    text = (
        "Article 1 : Les articles 33 et 34 sont modifiés comme suit :\n\n"
        "Au lieu de :\n\nArticle 33 : Ancien texte.\n\n"
        "Lire :\n\nArticle 33 : Nouveau texte."
    )
    doc = ExtractedDocument(name="modif.pdf", text=text)
    chunks = legal_parent_child_chunk(doc, "docid", document_name="Modif test")
    parents = [c for c in chunks if c.parent_chunk_id is None]
    assert len([c for c in parents if c.article == "33"]) == 2


def test_budget_line_repeated_mid_text_is_demoted_even_with_one_segment():
    # OCR tables put most "Article 63 : Subventions" lines mid-line (no
    # segment opens), so per-segment counts miss them; the heading form
    # still repeats dozens of times in the full text (loi 020-2006 case).
    table_rows = " | ".join(
        f"Chapitre {10000+i} : Direction X  {i} 000 000  Article 63 : Subventions exploitation"
        for i in range(8)
    )
    text = (
        "Article 1 : Sont annulés les crédits ci-après.\n\n"
        f"{table_rows}\n\n"
        "Article 63 : Subventions exploitation\n\n- Paragraphe 639 : Subventions.\n\n"
        "Article 2 : Sont ouverts les crédits."
    )
    doc = ExtractedDocument(name="lf.pdf", text=text)
    chunks = legal_parent_child_chunk(doc, "docid", document_name="LF test")
    parents = [c for c in chunks if c.parent_chunk_id is None]
    assert {c.article for c in parents if c.article} == {"1", "2"}
    # The demoted line's text folds into article 1 (stays searchable).
    a1 = [c for c in parents if c.article == "1"][0]
    assert "Paragraphe 639 : Subventions." in a1.content
