"""Tests for PDF hyphenation repair in text cleaning."""

from backend.ingestion.text_cleaning import (
    clean_text,
    repair_extraction_artifacts,
    repair_hyphenation,
    repair_split_words,
)


def test_repairs_linebreak_hyphenation():
    assert repair_hyphenation("les docum-\nents officiels") == "les documents officiels"


def test_repairs_flattened_hyphenation():
    assert repair_hyphenation("les docum- ents officiels") == "les documents officiels"


def test_repairs_spaced_hyphenation():
    assert repair_hyphenation("le libre consente - ment des époux") == "le libre consentement des époux"


def test_preserves_genuine_hyphens():
    # Number ranges, uppercase continuations and already-joined words stay intact.
    assert repair_hyphenation("Loi N°033-2012/AN") == "Loi N°033-2012/AN"
    assert repair_hyphenation("rendez-vous") == "rendez-vous"
    assert repair_hyphenation("- Item de liste") == "- Item de liste"


def test_clean_text_applies_hyphenation_repair():
    cleaned = clean_text("Le mariage est fondé sur le libre consente -\nment de l'homme.")
    assert "consentement" in cleaned
    assert "consente -" not in cleaned


def test_repairs_split_word_tail():
    assert repair_split_words("au lie u de la résidence") == "au lieu de la résidence"


def test_repairs_split_word_head():
    assert repair_split_words("la u nion fait la force") == "la union fait la force"


def test_preserves_elision_letters():
    # "d roit" is NOT joined: "d" may be a lost elision ("d'accord").
    assert repair_split_words("le d roit de visite") == "le d roit de visite"


def test_repairs_split_letter_run():
    assert repair_split_words("l i e u de célébration") == "lieu de célébration"


def test_preserves_one_letter_words_and_elisions():
    # Valid one-letter French words and elisions with a lost apostrophe stay as-is.
    assert repair_split_words("il y a un droit") == "il y a un droit"
    assert repair_split_words("d accord avec vous") == "d accord avec vous"
    assert repair_split_words("l homme et la femme") == "l homme et la femme"


def test_preserves_roman_numerals_after_structure_words():
    assert repair_split_words("titre i et section v") == "titre i et section v"


def test_repair_extraction_artifacts_combines_both():
    assert repair_extraction_artifacts("au lie u des docum- ents") == "au lieu des documents"


def test_repairs_multiletter_fragment_tail():
    assert repair_split_words("le travaill eur licencié") == "le travailleur licencié"


def test_repairs_multiletter_fragment_head():
    assert repair_split_words("le tr availleur licencié") == "le travailleur licencié"


def test_preserves_real_short_word_phrases():
    # Both sides are valid standalone words -> never merged.
    assert repair_split_words("sur mer et sur terre") == "sur mer et sur terre"
    assert repair_split_words("des fers et des lois") == "des fers et des lois"
    assert repair_split_words("une foi sincère") == "une foi sincère"
    assert repair_split_words("le juge vérifie les faits") == "le juge vérifie les faits"


def test_never_merges_complete_words():
    # Regression cases from real Code du travail chunks.
    assert repair_split_words("licenciement jugé abusif") == "licenciement jugé abusif"
    assert repair_split_words("la partie lésée peut saisir") == "la partie lésée peut saisir"
    assert repair_split_words("réparation du préjudice subi") == "réparation du préjudice subi"
    assert repair_split_words("à cet effet doit mentionner") == "à cet effet doit mentionner"
    assert repair_split_words("cette image et cette page") == "cette image et cette page"
    assert repair_split_words("bonne idée reçue") == "bonne idée reçue"
    assert repair_split_words("une année bissextile") == "une année bissextile"


def test_repairs_prefix_fragment():
    assert repair_split_words("rupture ir régulière du contrat") == "rupture irrégulière du contrat"
    assert repair_split_words("expre ssément le motif") == "expressément le motif"
