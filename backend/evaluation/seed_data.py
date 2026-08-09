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
    """Return ~28 realistic (but synthetic) French legal excerpt chunks."""
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
                "Article 95 — En cas de licenciement ou de rupture du contrat de "
                "travail à durée indéterminée, un préavis doit être observé par la "
                "partie qui prend l'initiative de la rupture. La durée du préavis "
                "est de huit jours pour les ouvriers journaliers et payés à "
                "l'heure, d'un mois pour les employés et ouvriers mensualisés, et "
                "de trois mois pour les cadres et agents de maîtrise. Le salarié "
                "licencié conserve le bénéfice de son préavis."
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
                "Article 96 — Le salarié licencié a droit à une indemnité de "
                "licenciement calculée sur la base du salaire mensuel moyen des "
                "douze derniers mois. Elle est fixée à vingt-cinq pour cent du "
                "salaire mensuel moyen par année de présence pour les cinq "
                "premières années, trente pour cent de la sixième à la dixième "
                "année et quarante pour cent au-delà de dix ans."
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
        # --- Dismissal issues (golden regression case, spec §38) --------------
        _chunk(
            document_id="code-travail-2008",
            document_name="Code du travail du Burkina Faso",
            article="90",
            content=(
                "Article 90 — Le licenciement d'un salarié ne peut intervenir que "
                "pour un motif réel et sérieux lié à l'aptitude ou à la conduite du "
                "salarié, ou aux nécessités du fonctionnement de l'entreprise. La "
                "faute grave est celle qui rend impossible le maintien du salarié "
                "dans l'entreprise ; le salarié licencié pour faute grave perd le "
                "bénéfice du préavis."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2008, 5, 20),
            government_body="Ministère du Travail et de la Protection sociale",
            url=f"{_JO_URL}/code-du-travail#art-90",
            confidence=0.95,
        ),
        _chunk(
            document_id="code-travail-2008",
            document_name="Code du travail du Burkina Faso",
            article="97",
            content=(
                "Article 97 — Le salarié licencié conserve ses droits acquis : le "
                "paiement des salaires échus, des congés payés acquis jusqu'à la "
                "date de rupture et de tous autres avantages échus. Ces droits "
                "acquis sont dus indépendamment du motif du licenciement."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2008, 5, 20),
            government_body="Ministère du Travail et de la Protection sociale",
            url=f"{_JO_URL}/code-du-travail#art-97",
            confidence=0.95,
        ),
        _chunk(
            document_id="code-travail-2008",
            document_name="Code du travail du Burkina Faso",
            article="98",
            content=(
                "Article 98 — Le licenciement abusif, dépourvu de motif réel et "
                "sérieux ou entaché d'une irrégularité de procédure, ouvre droit "
                "pour le salarié licencié à des dommages et intérêts dont le "
                "montant est fixé par la juridiction compétente au regard du "
                "préjudice subi."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2008, 5, 20),
            government_body="Ministère du Travail et de la Protection sociale",
            url=f"{_JO_URL}/code-du-travail#art-98",
            confidence=0.95,
        ),
        _chunk(
            document_id="code-travail-2008",
            document_name="Code du travail du Burkina Faso",
            article="100",
            content=(
                "Article 100 — Le tribunal du travail est compétent pour connaître "
                "des litiges nés du licenciement. Le salarié licencié dispose d'un "
                "recours devant cette juridiction après la tentative préalable de "
                "conciliation devant l'inspection du travail territorialement "
                "compétente."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2008, 5, 20),
            government_body="Ministère du Travail et de la Protection sociale",
            url=f"{_JO_URL}/code-du-travail#art-100",
            confidence=0.95,
        ),
        # --- Civil law ----------------------------------------------------------
        _chunk(
            document_id="code-civil-bf",
            document_name="Code civil du Burkina Faso",
            article="1382",
            content=(
                "Article 1382 — Tout fait quelconque de l'homme, qui cause à "
                "autrui un dommage, oblige celui par la faute duquel il est arrivé "
                "à le réparer. La responsabilité civile est engagée par la faute, "
                "la négligence ou l'imprudence."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(1989, 8, 4),
            government_body="Ministère de la Justice",
            url=f"{_JO_URL}/code-civil#art-1382",
        ),
        _chunk(
            document_id="code-civil-bf",
            document_name="Code civil du Burkina Faso",
            article="2262",
            content=(
                "Article 2262 — Les actions en justice, tant réelles que "
                "personnelles, se prescrivent par trente ans, sans que celui qui "
                "allègue cette prescription soit obligé d'en rapporter un titre. "
                "La prescription civile s'acquiert par le non-exercice du droit "
                "pendant le délai légal."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(1989, 8, 4),
            government_body="Ministère de la Justice",
            url=f"{_JO_URL}/code-civil#art-2262",
        ),
        # --- Commercial law (OHADA) ---------------------------------------------
        _chunk(
            document_id="audcg-2010",
            document_name="Acte uniforme OHADA portant droit commercial général (AUDCG)",
            article="27",
            content=(
                "Article 27 — Toute personne physique ou morale exerçant une "
                "activité commerciale doit s'immatriculer au registre du commerce "
                "et du crédit mobilier (RCCM) dans le mois du début de son "
                "activité. L'immatriculation au RCCM confère la qualité de "
                "commerçant."
            ),
            authority=AuthorityLevel.TREATY_OHADA,
            publication_date=date(2010, 12, 15),
            government_body="Organisation pour l'Harmonisation en Afrique du Droit des Affaires (OHADA)",
            url="https://www.ohada.org/acte-uniforme/audcg#art-27",
            confidence=0.95,
        ),
        _chunk(
            document_id="audcg-2010",
            document_name="Acte uniforme OHADA portant droit commercial général (AUDCG)",
            article="92",
            content=(
                "Article 92 — Le fonds de commerce est un bien meuble incorporel "
                "qui comprend notamment la clientèle, l'enseigne, le nom "
                "commercial et le droit au bail. La vente du fonds de commerce "
                "est constatée par un acte écrit et publiée au RCCM."
            ),
            authority=AuthorityLevel.TREATY_OHADA,
            publication_date=date(2010, 12, 15),
            government_body="Organisation pour l'Harmonisation en Afrique du Droit des Affaires (OHADA)",
            url="https://www.ohada.org/acte-uniforme/audcg#art-92",
            confidence=0.95,
        ),
        # --- Administrative law ---------------------------------------------------
        _chunk(
            document_id="code-contentieux-admin",
            document_name="Code du contentieux administratif du Burkina Faso",
            article="12",
            content=(
                "Article 12 — Le recours pour excès de pouvoir contre les actes "
                "administratifs doit être formé dans un délai de deux mois à "
                "compter de la notification ou de la publication de la décision "
                "attaquée. L'excès de pouvoir entraîne l'annulation de l'acte "
                "administratif."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2001, 12, 18),
            government_body="Ministère de la Justice",
            url=f"{_JO_URL}/contentieux-administratif#art-12",
        ),
        _chunk(
            document_id="code-contentieux-admin",
            document_name="Code du contentieux administratif du Burkina Faso",
            article="3",
            content=(
                "Article 3 — Les chambres administratives des juridictions du "
                "ressort connaissent des litiges entre les administrés et l'État "
                "ou les collectivités territoriales, notamment en matière de "
                "fonction publique et de contrats administratifs."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2001, 12, 18),
            government_body="Ministère de la Justice",
            url=f"{_JO_URL}/contentieux-administratif#art-3",
        ),
        # --- Land law -------------------------------------------------------------
        _chunk(
            document_id="loi-034-2009-foncier",
            document_name="Loi n° 034-2009 portant régime foncier rural du Burkina Faso",
            article="14",
            content=(
                "Article 14 — Les droits d'usage coutumiers sur les terres rurales "
                "sont reconnus ; leur gestion relève des commissions villageoises "
                "de gestion foncière rurale qui attribuent les terres aux "
                "exploitants agricoles."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2009, 6, 16),
            government_body="Ministère de l'Agriculture",
            url=f"{_JO_URL}/foncier-rural#art-14",
        ),
        _chunk(
            document_id="loi-034-2009-foncier",
            document_name="Loi n° 034-2009 portant régime foncier rural du Burkina Faso",
            article="31",
            content=(
                "Article 31 — Le titre foncier est le seul titre de propriété "
                "reconnu sur les terres ; il est délivré par le service du domaine "
                "après enquête foncière et purge des droits coutumiers "
                "préexistants."
            ),
            authority=AuthorityLevel.LAW,
            publication_date=date(2009, 6, 16),
            government_body="Ministère de l'Agriculture",
            url=f"{_JO_URL}/foncier-rural#art-31",
        ),
    ]
