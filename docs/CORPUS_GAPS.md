# Lacunes du corpus — documents à acquérir

Analyse du 2026-09 (éval live golden dataset + échecs observés en production),
mise à jour du 2026-09-12 après campagne de téléchargement.

## Acquis le 2026-09-12 ✅

- **Code général des impôts (CGI)** — édition DGI consolidée loi de finances
  2021, 380 p. texte natif (dgi.bf). Cas qa-002 / qa-012.
- **Loi organique n°032-2018/AN** (Conseil d'État + procédure administrative,
  scan officiel océrisé, conseil-etat.gov.bf). Cas qa-021 / qa-022.

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
- **Nouveau Code de procédure pénale** (loi n°009-2025/ALT du 12 juin 2025) :
  remplace l'ordonnance 68-7-1968 actuellement indexée — **lacune réelle**,
  télécharger dès publication du texte promulgué.
- **Nouveau Code du travail** : adopté par l'ALT le 6 mai 2026, à remplacer
  dès promulgation (le corpus a la version 2008).
- **Version 2024 du CGI** : existe en HTML seulement (dgi.bf/verification/CGI) ;
  la version PDF consolidée 2021 indexée reste la meilleure source RAG.

## Procédure après ajout de fichiers dans `data/legal_docs/`

```bash
make reindex        # diff par hash : seuls les nouveaux fichiers sont indexés
```

Puis rejouer les cas concernés du golden dataset :

```bash
docker compose exec -T api python -m backend.evaluation.runner --live --out /app/data/eval_report_live
```

Ne PAS utiliser `--full-reindex` (inutile pour un ajout incrémental).

