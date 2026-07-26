"""Synthetic seed evidence for offline evaluation and tests.

IMPORTANT: every chunk below is *illustrative sample content* written for
pipeline testing only. It mimics the style of Burkina Faso / OHADA legal
texts but has NOT been verified against the official Journal Officiel and
must never be treated as legal advice or as authentic statutory text.
"""

from __future__ import annotations

from datetime import date

from backend.core.models import AuthorityLevel, EvidenceChunk, SearchKind

_SAMPLE_META = {
    "synthetic": True,
    "note": "Sample content for pipeline testing — not verified legal text.",
}

_JO_URL = "https://www.jo.gouv.bf"


def _chunk(
    *,
    document_id: str,
    document_name: str,
    article: str | None,
    content: str,
    authority: AuthorityLevel,
    publication_date: date,
    government_body: str,
    url: str,
    confidence: float = 0.9,
) -> EvidenceChunk:
    return EvidenceChunk(
        document_id=document_id,
        document_name=document_name,
        article=article,
        content=content,
        publication_date=publication_date,
        effective_date=publication_date,
        government_body=government_body,
        authority=authority,
        url=url,
        source_kind=SearchKind.GOVERNMENT,
        language="fr",
        confidence=confidence,
        metadata=dict(_SAMPLE_META),
    )


def seed_evidence() -> list[EvidenceChunk]:
    """Return ~15 realistic (but synthetic) French legal excerpt chunks."""
    return [
        _chunk(
            document_id="constitution-1991",
            document_name="Constitution du Burkina Faso",
            article="1",
            content=(
                "Article 1 — Le Burkina Faso est une République démocratique, une et "
                "indivisible. La République est laïque. La souveraineté nationale "
                "appartient au peuple qui l'exerce par l'intermédiaire de ses "
                "représentants et par voie de référendum."
            ),
            authority=AuthorityLevel.CONSTITUTION,
            publication_date=date(1991, 6, 2),
            government_body="Assemblée nationale du Burkina Faso",
            url=f"{_JO_URL}/constitution#art-1",
            confidence=1.0,
        ),
        _chunk(
            document_id="constitution-1991",
            document_name="Constitution du Burkina Faso",
            article="8",
            content=(
                "Article 8 — Tous les Burkinabè naissent libres et égaux en droits. "
                "Toute discrimination fondée sur la race, l'ethnie, la région, la "
                "couleur, le sexe, la langue, la religion, la caste, les opinions "
                "politiques, la richesse et la naissance est prohibée. La protection "
                "de la vie, de la liberté et de la dignité de la personne humaine "
                "est garantie à tous."
            ),
            authority=AuthorityLevel.CONSTITUTION,
            publication_date=date(1991, 6, 2),
            government_body="Assemblée nationale du Burkina Faso",
            url=f"{_JO_URL}/constitution#art-8",
            confidence=1.0,
        ),
        _chunk(
            document_id="constitution-1991",
            document_name="Constitution du Burkina Faso",
            article="17",
            content=(
                "Article 17 — Le droit au travail est reconnu à tous les citoyens. "
                "L'État œuvre à la création des conditions permettant à chacun de "
                "jouir de ce droit. Nul ne peut être lésé dans son travail en raison "
                "de ses origines, de ses opinions ou de ses croyances."
            ),
            authority=AuthorityLevel.CONSTITUTION,
            publication_date=date(1991, 6, 2),
            government_body="Assemblée nationale du Burkina Faso",
            url=f"{_JO_URL}/constitution#art-17",
            confidence=1.0,
        ),
        _chunk(
            document_id="code-travail-2008",
            document_name="Code du travail du Burkina Faso",
            article="95",
            content=(
                "Article 95 — En cas de rupture du contrat de travail à durée "
                "indéterminée, un préavis doit être observé par la partie qui prend "
                "l'initiative de la rupture. La durée du préavis est de huit jours "
                "pour les ouvriers journaliers et payés à l'heure, d'un mois pour "
                "les employés et ouvriers mensualisés, et de trois mois pour les "
                "cadres et agents de maîtrise."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2008, 5, 20),
            government_body="Ministère du Travail et de la Protection sociale",
            url=f"{_JO_URL}/code-du-travail#art-95",
        ),
        _chunk(
            document_id="code-travail-2008",
            document_name="Code du travail du Burkina Faso",
            article="96",
            content=(
                "Article 96 — L'indemnité de licenciement est calculée sur la base "
                "du salaire mensuel moyen des douze derniers mois. Elle est fixée à "
                "vingt-cinq pour cent du salaire mensuel moyen par année de présence "
                "pour les cinq premières années, trente pour cent de la sixième à la "
                "dixième année et quarante pour cent au-delà de dix ans."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2008, 5, 20),
            government_body="Ministère du Travail et de la Protection sociale",
            url=f"{_JO_URL}/code-du-travail#art-96",
        ),
        _chunk(
            document_id="code-travail-2008",
            document_name="Code du travail du Burkina Faso",
            article="33",
            content=(
                "Article 33 — Le contrat de travail à durée déterminée est celui "
                "dont le terme est fixé à l'avance par les parties. Sa durée "
                "maximale, renouvellement compris, ne peut excéder trois ans pour "
                "les travailleurs burkinabè. Le contrat à durée déterminée est "
                "établi par écrit."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2008, 5, 20),
            government_body="Ministère du Travail et de la Protection sociale",
            url=f"{_JO_URL}/code-du-travail#art-33",
        ),
        _chunk(
            document_id="decret-2023-0456",
            document_name="Décret n° 2023-0456 portant modalités d'application du Code du travail",
            article="2",
            content=(
                "Article 2 — L'inspection du travail contrôle l'application des "
                "dispositions légales relatives aux contrats de travail, aux "
                "salaires et aux conditions d'emploi. Tout litige relatif au préavis "
                "peut être soumis à l'inspecteur du travail territorialement "
                "compétent, qui tente une conciliation préalable."
            ),
            authority=AuthorityLevel.DECREE,
            publication_date=date(2023, 9, 12),
            government_body="Ministère du Travail et de la Protection sociale",
            url=f"{_JO_URL}/decret-2023-0456#art-2",
            confidence=0.85,
        ),
        _chunk(
            document_id="code-penal-1996",
            document_name="Code pénal du Burkina Faso",
            article="392",
            content=(
                "Article 392 — Constitue un vol le fait de soustraire frauduleusement "
                "la chose d'autrui. Le vol est puni d'un emprisonnement d'un an à "
                "cinq ans et d'une amende de trois cent mille (300 000) à un million "
                "cinq cent mille (1 500 000) francs CFA. La tentative est punie "
                "comme le délit consommé."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(1996, 11, 13),
            government_body="Ministère de la Justice",
            url=f"{_JO_URL}/code-penal#art-392",
        ),
        _chunk(
            document_id="code-penal-1996",
            document_name="Code pénal du Burkina Faso",
            article="380",
            content=(
                "Article 380 — Constitue une escroquerie le fait de tromper autrui "
                "en usant de fausses qualités ou de manœuvres frauduleuses afin de "
                "se faire remettre des fonds ou des valeurs. L'escroquerie est punie "
                "d'un emprisonnement de six mois à cinq ans et d'une amende de "
                "cent mille (100 000) à un million (1 000 000) francs CFA."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(1996, 11, 13),
            government_body="Ministère de la Justice",
            url=f"{_JO_URL}/code-penal#art-380",
        ),
        _chunk(
            document_id="cpf-1989",
            document_name="Code des personnes et de la famille du Burkina Faso",
            article="147",
            content=(
                "Article 147 — L'homme avant vingt ans révolus et la femme avant "
                "dix-sept ans révolus ne peuvent contracter mariage. Toutefois, le "
                "procureur de la République peut accorder une dispense d'âge pour "
                "des motifs graves."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(1989, 8, 4),
            government_body="Ministère de la Justice",
            url=f"{_JO_URL}/cpf#art-147",
        ),
        _chunk(
            document_id="cpf-1989",
            document_name="Code des personnes et de la famille du Burkina Faso",
            article="542",
            content=(
                "Article 542 — Le divorce peut être prononcé pour rupture de la vie "
                "commune, pour atteinte grave aux devoirs du mariage ou d'un commun "
                "accord des époux. La rupture de la vie commune est constituée par "
                "la cessation de la cohabitation pendant une durée d'au moins deux "
                "ans."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(1989, 8, 4),
            government_body="Ministère de la Justice",
            url=f"{_JO_URL}/cpf#art-542",
        ),
        _chunk(
            document_id="auscgie-2014",
            document_name="Acte uniforme OHADA relatif au droit des sociétés commerciales et du GIE (AUSCGIE)",
            article="311",
            content=(
                "Article 311 — La société à responsabilité limitée (SARL) est celle "
                "dans laquelle les associés ne supportent les pertes qu'à concurrence "
                "de leurs apports. Le capital social est fixé librement par les "
                "associés dans les statuts et est divisé en parts sociales égales. "
                "Les apports en numéraire doivent être libérés d'au moins un quart "
                "lors de la constitution."
            ),
            authority=AuthorityLevel.TREATY_OHADA,
            publication_date=date(2014, 1, 30),
            government_body="Organisation pour l'Harmonisation en Afrique du Droit des Affaires (OHADA)",
            url="https://www.ohada.org/acte-uniforme/auscgie#art-311",
            confidence=0.95,
        ),
        _chunk(
            document_id="auscgie-2014",
            document_name="Acte uniforme OHADA relatif au droit des sociétés commerciales et du GIE (AUSCGIE)",
            article="13",
            content=(
                "Article 13 — La SARL peut être constituée par une seule personne "
                "physique ou morale, dénommée associé unique ; elle est alors dite "
                "SARL unipersonnelle. Elle compte au maximum cinquante associés."
            ),
            authority=AuthorityLevel.TREATY_OHADA,
            publication_date=date(2014, 1, 30),
            government_body="Organisation pour l'Harmonisation en Afrique du Droit des Affaires (OHADA)",
            url="https://www.ohada.org/acte-uniforme/auscgie#art-13",
            confidence=0.95,
        ),
        _chunk(
            document_id="cgi-2023",
            document_name="Code général des impôts du Burkina Faso",
            article="271",
            content=(
                "Article 271 — Le taux de la taxe sur la valeur ajoutée (TVA) est "
                "fixé à dix-huit pour cent (18%). Sont exonérées notamment les "
                "opérations portant sur les produits de première nécessité figurant "
                "sur la liste arrêtée par le ministre chargé des finances."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2023, 12, 29),
            government_body="Direction générale des Impôts",
            url=f"{_JO_URL}/cgi#art-271",
        ),
        _chunk(
            document_id="cgi-2023",
            document_name="Code général des impôts du Burkina Faso",
            article="112",
            content=(
                "Article 112 — L'impôt sur les sociétés (IS) est liquidé au taux "
                "normal de vingt-sept virgule cinq pour cent (27,5%) du bénéfice "
                "imposable arrondi au millier de francs CFA inférieur. Des taux "
                "réduits s'appliquent aux entreprises du secteur agricole sous "
                "conditions."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2023, 12, 29),
            government_body="Direction générale des Impôts",
            url=f"{_JO_URL}/cgi#art-112",
        ),
        _chunk(
            document_id="cour-supreme-2019-042",
            document_name="Cour suprême du Burkina Faso, chambre sociale, arrêt n° 2019-042",
            article=None,
            content=(
                "Arrêt n° 2019-042 — La chambre sociale rappelle que le préavis "
                "n'est dû que par la partie qui prend l'initiative de la rupture du "
                "contrat de travail à durée indéterminée, et que l'inexécution du "
                "préavis ouvre droit à une indemnité compensatrice équivalente à la "
                "rémunération qui aurait été perçue pendant sa durée."
            ),
            authority=AuthorityLevel.CASE_LAW,
            publication_date=date(2019, 4, 18),
            government_body="Cour suprême du Burkina Faso",
            url="https://www.coursupreme.bf/arrets/2019-042",
            confidence=0.8,
        ),
    ]
