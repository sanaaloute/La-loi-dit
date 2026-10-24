# Lacunes du corpus — documents à acquérir

Analyse du 2026-09 (éval live golden dataset + échecs observés en production),
mise à jour du 2026-09-12 après campagne de téléchargement, puis du 2026-10-25
après refonte fiabilité du pipeline.

## Acquis le 2026-09-12 ✅

- **Code général des impôts (CGI)** — édition DGI consolidée loi de finances
  2021, 380 p. texte natif (dgi.bf). Cas qa-002 / qa-012.
- **Loi organique n°032-2018/AN** (Conseil d'État + procédure administrative,
  scan officiel océrisé, conseil-etat.gov.bf). Cas qa-021 / qa-022.

## Acquis depuis ✅ (vérifié indexé le 2026-10-25)

- **Nouveau Code de procédure pénale** (loi n°009-2025/ALT du 12 juin 2025) —
  indexé, remplace l'ordonnance 68-7-1968.
- **Nouveau Code du travail** (loi n°013-2026-ALP) — indexé, remplace la
  version 2008.
- **Code des personnes et de la famille** (loi n°012-2025/ALT) — indexé ;
  les questions divorce/filiation retrouvent bien le code (sonde retrieval
  `scripts/probe_retrieval_live.py`).
- **Code civil de 1804** (édition annotée applicable au Burkina Faso) — indexé ;
  les articles sur la prescription (2242-2262 ss.) remontent correctement.
- **Code de procédure civile 1999** — indexé.

## Références erronées du golden dataset (à corriger dans le dataset)

- **« Code du contentieux administratif »** : n'existe pas — le contentieux
  administratif relève de la loi organique n°032-2018/AN + lois n°010/011-2016/AN
  (cours et tribunaux administratifs, PDF introuvables en ligne pour l'instant).
- **« Décret n° 2023-0456 »** : référence hallucinée, n'existe pas. Le préavis
  est dans le Code du travail (art. 34 ss., déjà indexé).
- **Port du casque** : déjà couvert par le décret 2003-418 (art. 15 : obligation
  casque motocyclettes ; art. 44 : sanction). Les textes d'origine (décret
  78-107/PRES/TPTU de 1978) n'ont pas de PDF officiel en ligne.
- **Code de procédure civile 1999** : toujours en vigueur (Cour de cassation).

## Reste à faire

- **Lois n°010-2016/AN et n°011-2016/AN** (cour administrative d'appel /
  tribunaux administratifs) : aucun PDF fiable trouvé (droit-afrique en 403,
  revuejuris 404, jobf.gov.bf injoignable). À obtenir autrement (JO papier
  scanné, Conseil d'État).
- **Version 2024 du CGI** : existe en HTML seulement (dgi.bf/verification/CGI) ;
  la version PDF consolidée 2021 indexée reste la meilleure source RAG.
- **Tags de domaine juridique — CORRIGÉ le 2026-10-25** ✅ : les chunks étaient
  tagués avec des pseudo-domaines (slugs de dossier `bf`/`ohada`/`cima` +
  inférence sur 2000 caractères → 5+ domaines par document). Refonte :
  inférence sur le nom seul à l'ingestion (`pipeline.py`), manifeste curé pour
  les 105 fichiers (`scripts/curate_manifest_domains.py`), migration en place
  des 30 612 chunks (`scripts/corpus_fix.py domains --apply`, sans
  ré-embedding). Le filtrage par domaine au retrieval est désormais effectif.

## Procédure après ajout de fichiers dans `data/legal_docs/`

```bash
make reindex        # diff par hash : seuls les nouveaux fichiers sont indexés
```

Puis rejouer les cas concernés du golden dataset :

```bash
docker compose exec -T api python -m backend.evaluation.runner --live --out /app/data/eval_report_live
```

Ne PAS utiliser `--full-reindex` (inutile pour un ajout incrémental).

