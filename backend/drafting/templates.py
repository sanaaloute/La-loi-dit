"""Drafting template registry.

Each template is a markdown skeleton with ``{{field}}`` placeholders authored
conservatively so it produces a valid document WITHOUT any LLM: parties,
recitals, numbered clauses, signature block. Skeletons never cite specific
article numbers — real legal references come only from retrieved evidence
(see ``backend.drafting.service``), never from the template itself.

Categories: "contract" (actes sous seing privé) and "case" (actes de
procédure). Field types: text | textarea | date | number | select.
"""

from __future__ import annotations

from typing import Any, Optional


def _field(
    name: str,
    label: str,
    type: str = "text",
    *,
    required: bool = False,
    placeholder: str = "",
    options: Optional[list[str]] = None,
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name,
        "label": label,
        "type": type,
        "required": required,
        "placeholder": placeholder,
    }
    if options:
        field["options"] = options
    return field


TEMPLATES: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # Contrats
    # ------------------------------------------------------------------
    {
        "id": "contrat_travail_cdi",
        "category": "contract",
        "label": "Contrat de travail à durée indéterminée (CDI)",
        "description": (
            "Contrat de travail CDI conforme au droit du travail applicable "
            "au Burkina Faso (Code du travail, actes uniformes OHADA/AUSCGIE)."
        ),
        "legal_query": (
            "contrat de travail à durée indéterminée CDI obligations employeur "
            "salarié rémunération résiliation préavis code du travail Burkina Faso"
        ),
        "fields": [
            _field("employeur", "Employeur (raison sociale)", required=True, placeholder="Ex. Société Faso Agro SARL"),
            _field("employeur_adresse", "Adresse de l'employeur", "textarea", placeholder="Secteur, rue, ville"),
            _field("employeur_representant", "Représentant de l'employeur", placeholder="Nom et qualité"),
            _field("salarie", "Salarié (nom et prénom)", required=True, placeholder="Ex. Awa Compaoré"),
            _field("salarie_adresse", "Adresse du salarié", "textarea"),
            _field("poste", "Poste / fonction", required=True, placeholder="Ex. Comptable"),
            _field("date_debut", "Date de prise de fonction", "date", required=True),
            _field("salaire", "Salaire mensuel brut (FCFA)", "number", required=True),
            _field("lieu_travail", "Lieu de travail", placeholder="Ex. Ouagadougou"),
            _field("periode_essai", "Période d'essai", placeholder="Ex. 3 mois renouvelable une fois"),
        ],
        "skeleton": """# CONTRAT DE TRAVAIL À DURÉE INDÉTERMINÉE

**Entre les soussignés :**

**{{employeur}}**, sise à {{employeur_adresse}}, représentée par {{employeur_representant}}, ci-après dénommée « l'Employeur »,

**d'une part,**

**Et :**

**{{salarie}}**, demeurant à {{salarie_adresse}}, ci-après dénommé(e) « le Salarié »,

**d'autre part,**

## Exposé des motifs

Les parties souhaitent formaliser leurs relations de travail dans le respect de la législation du travail en vigueur au Burkina Faso, notamment le Code du travail et les textes pris pour son application, ainsi que, le cas échéant, l'Acte uniforme OHADA relatif au droit des sociétés commerciales pour ce qui concerne la personnalité de l'employeur.

En conséquence, il a été convenu et arrêté ce qui suit :

## Article 1 — Objet du contrat

L'Employeur engage le Salarié, qui accepte, en qualité de **{{poste}}**, à compter du **{{date_debut}}**, pour une durée indéterminée. Le Salarié exercera ses fonctions à {{lieu_travail}}.

## Article 2 — Période d'essai

Le contrat est assorti d'une période d'essai de {{periode_essai}}, pendant laquelle chacune des parties peut y mettre fin dans les conditions prévues par la législation en vigueur.

## Article 3 — Rémunération

En contrepartie de ses services, le Salarié percevra un salaire mensuel brut de **{{salaire}} FCFA**, payable à terme échu, ainsi que les avantages et indemnités prévus par la législation, la réglementation et les usages applicables.

## Article 4 — Durée et organisation du travail

La durée du travail est celle prévue par la législation en vigueur et l'organisation interne de l'Employeur, dans le respect des repos hebdomadaires, jours fériés et congés payés légaux.

## Article 5 — Obligations du Salarié

Le Salarié s'engage à exécuter ses fonctions avec diligence et loyauté, à respecter le règlement intérieur de l'Employeur et à conserver la confidentialité des informations professionnelles dont il aura connaissance.

## Article 6 — Obligations de l'Employeur

L'Employeur s'engage à fournir au Salarié les moyens nécessaires à l'exécution de ses fonctions, à verser la rémunération convenue et à respecter les prescriptions légales en matière d'hygiène, de sécurité et de protection sociale.

## Article 7 — Résiliation

Le contrat peut être résilié par l'une ou l'autre des parties dans les conditions et formes prévues par la législation du travail en vigueur, notamment en ce qui concerne le préavis, les indemnités éventuelles et les causes de licenciement.

## Article 8 — Loi applicable et règlement des litiges

Le présent contrat est soumis au droit du travail en vigueur au Burkina Faso. Les litiges relatifs à son interprétation ou à son exécution seront soumis, à défaut de règlement amiable, aux juridictions compétentes.

Fait à {{lieu_travail}}, le {{date_debut}}, en deux exemplaires originaux.

| L'Employeur | Le Salarié |
|---|---|
| {{employeur}} | {{salarie}} |
| (signature) | (signature) |
""",
    },
    {
        "id": "bail_commercial",
        "category": "contract",
        "label": "Bail commercial",
        "description": "Contrat de bail de locaux à usage commercial, artisanal ou professionnel.",
        "legal_query": (
            "bail commercial loyer charges obligations bailleur locataire "
            "résiliation congé droit OHADA Burkina Faso"
        ),
        "fields": [
            _field("bailleur", "Bailleur (nom ou raison sociale)", required=True),
            _field("bailleur_adresse", "Adresse du bailleur", "textarea"),
            _field("locataire", "Locataire (nom ou raison sociale)", required=True),
            _field("locataire_adresse", "Adresse du locataire", "textarea"),
            _field("local", "Désignation du local loué", "textarea", required=True, placeholder="Adresse et description des lieux"),
            _field("usage", "Destination / usage des lieux", required=True, placeholder="Ex. commerce de généralités"),
            _field("duree", "Durée du bail (années)", "number", required=True),
            _field("date_debut", "Date d'effet", "date", required=True),
            _field("loyer", "Loyer mensuel (FCFA)", "number", required=True),
            _field("charges", "Charges et modalités", placeholder="Ex. charges locatives à la charge du locataire"),
            _field("depot_garantie", "Dépôt de garantie (FCFA)", "number"),
        ],
        "skeleton": """# CONTRAT DE BAIL COMMERCIAL

**Entre les soussignés :**

**{{bailleur}}**, demeurant à {{bailleur_adresse}}, ci-après dénommé(e) « le Bailleur »,

**d'une part,**

**Et :**

**{{locataire}}**, demeurant à {{locataire_adresse}}, ci-après dénommé(e) « le Locataire »,

**d'autre part,**

## Exposé des motifs

Le Bailleur est propriétaire (ou a la jouissance) du local décrit ci-après et accepte de le donner à bail au Locataire pour l'exercice de son activité, dans le respect de la législation en vigueur au Burkina Faso.

Il a été convenu ce qui suit :

## Article 1 — Objet du bail

Le Bailleur donne en location au Locataire, qui accepte, le local suivant : **{{local}}**, à usage exclusif de **{{usage}}**. Toute autre affectation requiert l'accord écrit préalable du Bailleur.

## Article 2 — Durée

Le bail est consenti pour une durée de **{{duree}} an(s)** à compter du **{{date_debut}}**. Il se poursuivra ou sera renouvelé selon les modalités prévues par la législation applicable.

## Article 3 — Loyer et charges

Le loyer est fixé à **{{loyer}} FCFA** par mois, payable d'avance/à terme échu selon l'usage. Charges : {{charges}}. Un dépôt de garantie de {{depot_garantie}} FCFA est versé à la signature, restituable à la fin du bail déduction faite des éventuelles dégradations.

## Article 4 — Obligations du Bailleur

Le Bailleur s'engage à délivrer le local en bon état d'usage, à en garantir la jouissance paisible et à effectuer les réparations qui lui incombent en vertu de la loi.

## Article 5 — Obligations du Locataire

Le Locataire s'engage à payer le loyer aux échéances convenues, à user des lieux en bon père de famille conformément à leur destination, et à ne procéder à aucune sous-location ni cession sans l'accord écrit du Bailleur.

## Article 6 — Résiliation et congé

Le bail peut être résilié dans les formes et délais prévus par la législation en vigueur. Tout congé doit être signifié par écrit dans le respect des délais légaux.

## Article 7 — Loi applicable et litiges

Le présent bail est soumis au droit en vigueur au Burkina Faso. Les litiges seront portés, à défaut de règlement amiable, devant les juridictions compétentes.

Fait à {{local}}, le {{date_debut}}, en deux exemplaires originaux.

| Le Bailleur | Le Locataire |
|---|---|
| {{bailleur}} | {{locataire}} |
| (signature) | (signature) |
""",
    },
    {
        "id": "accord_confidentialite",
        "category": "contract",
        "label": "Accord de confidentialité (NDA)",
        "description": "Accord de non-divulgation pour protéger des informations sensibles échangées entre parties.",
        "legal_query": (
            "confidentialité non-divulgation secret des affaires obligations "
            "contractuelles responsabilité dommages droit OHADA"
        ),
        "fields": [
            _field("partie_divulgatrice", "Partie divulgatrice", required=True),
            _field("partie_receveuse", "Partie receveuse", required=True),
            _field("objet", "Objet des échanges / informations concernées", "textarea", required=True),
            _field("duree", "Durée de l'obligation de confidentialité", required=True, placeholder="Ex. 5 ans à compter de la signature"),
            _field("juridiction", "Juridiction compétente", placeholder="Ex. tribunaux de Ouagadougou"),
        ],
        "skeleton": """# ACCORD DE CONFIDENTIALITÉ

**Entre les soussignés :**

**{{partie_divulgatrice}}**, ci-après « la Partie divulgatrice »,

**d'une part,**

**Et :**

**{{partie_receveuse}}**, ci-après « la Partie receveuse »,

**d'autre part,**

## Exposé des motifs

Dans le cadre de leurs relations, les parties seront amenées à échanger des informations confidentielles relatives à : **{{objet}}**. Elles souhaitent encadrer l'utilisation et la protection de ces informations.

Il a été convenu ce qui suit :

## Article 1 — Définition des informations confidentielles

Sont considérées comme confidentielles toutes les informations, de quelque nature et sur quelque support que ce soit, communiquées par la Partie divulgatrice dans le cadre de l'objet défini ci-dessus, à l'exception des informations déjà publiques ou licitement obtenues par ailleurs.

## Article 2 — Obligations de la Partie receveuse

La Partie receveuse s'engage à conserver la stricte confidentialité des informations, à ne les utiliser qu'aux fins de l'objet convenu, à ne les divulguer à aucun tiers sans accord écrit préalable, et à limiter leur diffusion aux seules personnes ayant à en connaître.

## Article 3 — Durée

Les obligations du présent accord demeurent en vigueur pendant **{{duree}}**.

## Article 4 — Restitution et destruction

À première demande de la Partie divulgatrice, la Partie receveuse restituera ou détruira l'ensemble des documents et supports contenant des informations confidentielles.

## Article 5 — Responsabilité

Tout manquement aux obligations du présent accord engage la responsabilité de son auteur et peut donner lieu à réparation du préjudice subi, conformément au droit en vigueur.

## Article 6 — Loi applicable et litiges

Le présent accord est soumis au droit en vigueur au Burkina Faso. Les litiges seront soumis, à défaut de règlement amiable, à {{juridiction}}.

Fait en deux exemplaires originaux.

| La Partie divulgatrice | La Partie receveuse |
|---|---|
| {{partie_divulgatrice}} | {{partie_receveuse}} |
| (signature) | (signature) |
""",
    },
    {
        "id": "contrat_prestation",
        "category": "contract",
        "label": "Contrat de prestation de services",
        "description": "Contrat par lequel un prestataire s'engage à fournir une prestation à un client.",
        "legal_query": (
            "contrat de prestation de services obligations du prestataire "
            "paiement prix responsabilité inexécution droit OHADA Burkina Faso"
        ),
        "fields": [
            _field("client", "Client (nom ou raison sociale)", required=True),
            _field("client_adresse", "Adresse du client", "textarea"),
            _field("prestataire", "Prestataire (nom ou raison sociale)", required=True),
            _field("prestataire_adresse", "Adresse du prestataire", "textarea"),
            _field("objet", "Objet de la prestation", "textarea", required=True, placeholder="Description détaillée des services"),
            _field("date_debut", "Date de début", "date", required=True),
            _field("duree", "Durée de la prestation", placeholder="Ex. 6 mois"),
            _field("montant", "Montant total (FCFA)", "number", required=True),
            _field("modalites_paiement", "Modalités de paiement", "textarea", placeholder="Ex. 30% à la signature, solde à la réception"),
        ],
        "skeleton": """# CONTRAT DE PRESTATION DE SERVICES

**Entre les soussignés :**

**{{client}}**, sise à {{client_adresse}}, ci-après dénommé(e) « le Client »,

**d'une part,**

**Et :**

**{{prestataire}}**, sise à {{prestataire_adresse}}, ci-après dénommé(e) « le Prestataire »,

**d'autre part,**

## Exposé des motifs

Le Client souhaite confier au Prestataire la réalisation de la prestation décrite ci-après, que le Prestataire déclare avoir la capacité et les compétences d'exécuter, dans le respect de la législation en vigueur au Burkina Faso.

Il a été convenu ce qui suit :

## Article 1 — Objet du contrat

Le Prestataire s'engage à réaliser pour le Client la prestation suivante : **{{objet}}**, à compter du **{{date_debut}}** et pour une durée de {{duree}}.

## Article 2 — Obligations du Prestataire

Le Prestataire s'engage à exécuter la prestation avec diligence, conformément aux règles de l'art et aux instructions légitimes du Client, et à rendre compte de l'avancement des travaux.

## Article 3 — Obligations du Client

Le Client s'engage à fournir au Prestataire les informations et moyens nécessaires à la bonne exécution de la prestation et à en payer le prix dans les conditions convenues.

## Article 4 — Prix et paiement

En contrepartie de la prestation, le Client versera au Prestataire la somme de **{{montant}} FCFA**, selon les modalités suivantes : {{modalites_paiement}}.

## Article 5 — Responsabilité

Chaque partie est responsable des dommages causés à l'autre par le manquement à ses obligations, dans les conditions prévues par le droit en vigueur.

## Article 6 — Résiliation

Le contrat peut être résilié en cas de manquement grave d'une partie à ses obligations, après mise en demeure restée sans effet, sans préjudice des dommages et intérêts éventuels.

## Article 7 — Loi applicable et litiges

Le présent contrat est soumis au droit en vigueur au Burkina Faso. Les litiges seront portés, à défaut de règlement amiable, devant les juridictions compétentes.

Fait à {{client_adresse}}, le {{date_debut}}, en deux exemplaires originaux.

| Le Client | Le Prestataire |
|---|---|
| {{client}} | {{prestataire}} |
| (signature) | (signature) |
""",
    },
    # ------------------------------------------------------------------
    # Actes de procédure
    # ------------------------------------------------------------------
    {
        "id": "requete_instance",
        "category": "case",
        "label": "Requête introductive d'instance",
        "description": "Requête générique pour saisir une juridiction (civil, commercial ou social).",
        "legal_query": (
            "requête introductive d'instance saisine tribunal procédure civile "
            "demande en justice Burkina Faso OHADA procédure simplifiée de recouvrement"
        ),
        "fields": [
            _field("juridiction", "Juridiction saisie", required=True, placeholder="Ex. Tribunal de Commerce de Ouagadougou"),
            _field("demandeur", "Demandeur (nom, prénom ou raison sociale)", required=True),
            _field("demandeur_adresse", "Adresse du demandeur", "textarea"),
            _field("defendeur", "Défendeur (nom, prénom ou raison sociale)", required=True),
            _field("defendeur_adresse", "Adresse du défendeur", "textarea"),
            _field("objet", "Objet de la demande", "textarea", required=True, placeholder="Ex. paiement de la somme de ..."),
            _field("faits", "Exposé des faits", "textarea", required=True),
            _field("moyens", "Moyens de droit", "textarea", placeholder="Fondements juridiques invoqués"),
            _field("montant", "Montant réclamé (FCFA)", "number"),
        ],
        "skeleton": """# REQUÊTE INTRODUCTIVE D'INSTANCE

**À l'attention de {{juridiction}}**

## A. Identification des parties

**Demandeur :** {{demandeur}}, demeurant à {{demandeur_adresse}}.

**Défendeur :** {{defendeur}}, demeurant à {{defendeur_adresse}}.

## B. Objet de la demande

Par la présente requête, le Demandeur a l'honneur de saisir votre juridiction aux fins de : **{{objet}}**, pour un montant de {{montant}} FCFA.

## C. Exposé des faits

{{faits}}

## D. Moyens de droit

{{moyens}}

## E. Conclusions

Par ces motifs, le Demandeur requiert qu'il plaise à la juridiction :

- dire et juger fondée la présente demande ;
- condamner le Défendeur aux fins exposées en l'objet de la demande ;
- condamner le Défendeur aux dépens.

Le Demandeur se réserve la faculté de compléter la présente requête par tous moyens et pièces utiles dans la suite de la procédure, conformément aux règles de procédure applicables.

Fait à {{juridiction}}, ce jour.

**Le Demandeur (ou son mandataire)**

{{demandeur}}

(signature)

**Pièces jointes :** à compléter selon la nature du litige.
""",
    },
    {
        "id": "plainte",
        "category": "case",
        "label": "Plainte",
        "description": "Plainte simple auprès du procureur, avec possibilité de constitution de partie civile.",
        "legal_query": (
            "plainte procureur de la République constitution de partie civile "
            "procédure pénale infraction dommages Burkina Faso code pénal"
        ),
        "fields": [
            _field("plaignant", "Plaignant (nom et prénom)", required=True),
            _field("plaignant_adresse", "Adresse du plaignant", "textarea"),
            _field("personne_mise_en_cause", "Personne mise en cause", placeholder="Nom si connu, sinon laisser vide"),
            _field("faits", "Exposé des faits", "textarea", required=True),
            _field("date_faits", "Date des faits", "date", required=True),
            _field("lieu_faits", "Lieu des faits", required=True),
            _field(
                "constitution_partie_civile",
                "Constitution de partie civile",
                "select",
                options=["Non", "Oui"],
            ),
            _field("juridiction", "Procureur auprès de", placeholder="Ex. Tribunal de Grande Instance de Ouagadougou"),
        ],
        "skeleton": """# PLAINTE

**À l'attention de Monsieur le Procureur auprès de {{juridiction}}**

## A. Identification

**Plaignant :** {{plaignant}}, demeurant à {{plaignant_adresse}}.

**Personne mise en cause :** {{personne_mise_en_cause}}.

## B. Exposé des faits

Le/la soussigné(e) a l'honneur de porter à votre connaissance les faits suivants, survenus le **{{date_faits}}** à **{{lieu_faits}}** :

{{faits}}

## C. Objet de la plainte

Ces faits sont de nature à recevoir une qualification pénale et ont causé au Plaignant un préjudice. Le Plaignant porte plainte afin que soit ouverte toute enquête utile et que des poursuites soient engagées contre les auteurs, coauteurs ou complices des faits dénoncés.

**Constitution de partie civile :** {{constitution_partie_civile}}.

## D. Pièces et témoins

Le Plaignant se tient à la disposition des services d'enquête et se réserve la faculté de communiquer toutes pièces, témoignages et justifications utiles.

Le Plaignant déclare sur l'honneur la sincérité des faits exposés.

Fait à {{lieu_faits}}, ce jour.

**Le Plaignant**

{{plaignant}}

(signature)
""",
    },
]

_BY_ID = {template["id"]: template for template in TEMPLATES}

_PUBLIC_KEYS = ("id", "category", "label", "description", "fields")


def get_template(template_id: str) -> Optional[dict[str, Any]]:
    """Return the full template (skeleton included), or None when unknown."""
    return _BY_ID.get(template_id)


def list_templates() -> list[dict[str, Any]]:
    """Public metadata for every template (no skeletons, no legal queries)."""
    return [{key: template[key] for key in _PUBLIC_KEYS} for template in TEMPLATES]
