# Burkina Faso Legal AI — Evaluation Report

_Illustrative sample cases for pipeline testing only — not verified legal advice._

Generated: 2026-09-08T07:46:16.130757+00:00  
Dataset: `/app/backend/evaluation/golden_dataset.json`  
Cases: 26 — passed: 0 (0%)

_Illustrative set of 25 cases covering 10 legal domains (at least 2 cases each, including the spec §38 dismissal-rights regression case). Growing the golden set to 100+ cases with real verified texts is planned future work._

## Aggregate metrics

| Metric | Value |
| --- | --- |
| mean_groundedness | 1.000 |
| mean_faithfulness | 1.000 |
| mean_citation_accuracy | 1.000 |
| mean_answer_relevance | 0.192 |
| mean_issue_coverage | 0.500 |
| mean_recall_at_5 | 0.323 |
| mean_precision_at_5 | 0.108 |
| mean_mrr | 0.330 |
| mean_ndcg_at_5 | 0.319 |
| hallucination_rate | 0.923 |
| mean_latency_ms | 350356.900 |
| p95_latency_ms | 440858.000 |

## Per-case results

| Case | Grounded. | Cit. acc. | Relevance | Halluc. | Latency (ms) | Passed |
| --- | --- | --- | --- | --- | --- | --- |
| qa-001 | 1.00 | 1.00 | 0.50 | yes | 378650 | FAIL |
| qa-002 | 1.00 | 1.00 | 0.50 | no | 409531 | FAIL |
| qa-003 | 1.00 | 1.00 | 0.00 | yes | 265723 | FAIL |
| qa-004 | 1.00 | 1.00 | 0.00 | yes | 142565 | FAIL |
| qa-005 | 1.00 | 1.00 | 0.00 | yes | 304949 | FAIL |
| qa-006 | 1.00 | 1.00 | 0.00 | yes | 331911 | FAIL |
| qa-007 | 1.00 | 1.00 | 0.00 | yes | 319332 | FAIL |
| qa-008 | 1.00 | 1.00 | 0.00 | yes | 343231 | FAIL |
| qa-009 | 1.00 | 1.00 | 0.00 | yes | 297080 | FAIL |
| qa-010 | 1.00 | 1.00 | 1.00 | yes | 397713 | FAIL |
| qa-011 | 1.00 | 1.00 | 0.50 | yes | 390814 | FAIL |
| qa-012 | 1.00 | 1.00 | 0.00 | yes | 410106 | FAIL |
| qa-013 | 1.00 | 1.00 | 0.00 | yes | 301416 | FAIL |
| qa-014 | 1.00 | 1.00 | 0.00 | yes | 327845 | FAIL |
| qa-015 | 1.00 | 1.00 | 0.00 | yes | 308188 | FAIL |
| qa-016 | 1.00 | 1.00 | 0.00 | yes | 432390 | FAIL |
| qa-017 | 1.00 | 1.00 | 0.00 | yes | 140126 | FAIL |
| qa-018 | 1.00 | 1.00 | 0.00 | yes | 425546 | FAIL |
| qa-019 | 1.00 | 1.00 | 0.00 | yes | 312442 | FAIL |
| qa-020 | 1.00 | 1.00 | 0.50 | yes | 544781 | FAIL |
| qa-021 | 1.00 | 1.00 | 0.50 | yes | 310607 | FAIL |
| qa-022 | 1.00 | 1.00 | 0.50 | no | 440858 | FAIL |
| qa-023 | 1.00 | 1.00 | 0.00 | yes | 404527 | FAIL |
| qa-024 | 1.00 | 1.00 | 0.00 | yes | 432806 | FAIL |
| qa-025 | 1.00 | 1.00 | 0.00 | yes | 335478 | FAIL |
| qa-026 | 1.00 | 1.00 | 1.00 | yes | 400665 | FAIL |

## Failure details

- **qa-001** — Quel est le préavis de licenciement pour un employé mensualisé au Burkina Faso ?
  - precision=1.00 recall=1.00 confidence=0.25 refused=False evidence=3 chunks R@5=1.00 P@5=0.33 MRR=1.00 NDCG@5=1.00
- **qa-002** — Quel est le taux de TVA applicable au Burkina Faso ?
  - precision=0.00 recall=0.00 confidence=0.40 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-003** — À quel âge un homme et une femme peuvent-ils se marier au Burkina Faso ?
  - precision=1.00 recall=1.00 confidence=0.34 refused=False evidence=3 chunks R@5=1.00 P@5=0.33 MRR=1.00 NDCG@5=1.00
- **qa-004** — Quelle peine encourt une personne reconnue coupable de vol ?
  - precision=0.00 recall=0.00 confidence=1.00 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-005** — Comment est fixé le capital social d'une SARL en droit OHADA ?
  - precision=0.00 recall=0.00 confidence=0.77 refused=False evidence=18 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-006** — Le Burkina Faso est-il un État laïque ?
  - precision=0.67 recall=1.00 confidence=0.40 refused=False evidence=3 chunks R@5=1.00 P@5=0.33 MRR=1.00 NDCG@5=1.00
- **qa-007** — Quels droits fondamentaux la Constitution garantit-elle à tous les Burkinabè ?
  - precision=1.00 recall=1.00 confidence=0.85 refused=False evidence=3 chunks R@5=1.00 P@5=0.33 MRR=1.00 NDCG@5=1.00
- **qa-008** — Comment est calculée l'indemnité de licenciement au Burkina Faso ?
  - precision=1.00 recall=1.00 confidence=0.72 refused=False evidence=3 chunks R@5=1.00 P@5=0.33 MRR=1.00 NDCG@5=1.00
- **qa-009** — Quelle est la durée maximale d'un contrat de travail à durée déterminée au Burkina Faso ?
  - precision=1.00 recall=1.00 confidence=0.85 refused=False evidence=3 chunks R@5=1.00 P@5=0.33 MRR=1.00 NDCG@5=1.00
- **qa-010** — Quelles peines sont prévues par le Code pénal pour l'escroquerie ?
  - precision=0.00 recall=0.00 confidence=0.40 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-011** — Quels sont les motifs de divorce reconnus par le Code des personnes et de la famille ?
  - precision=1.00 recall=1.00 confidence=0.34 refused=False evidence=3 chunks R@5=1.00 P@5=0.33 MRR=1.00 NDCG@5=1.00
- **qa-012** — Quel est le taux de l'impôt sur les sociétés au Burkina Faso ?
  - precision=0.00 recall=0.00 confidence=0.75 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-013** — Quelle autorité peut être saisie d'un litige relatif au préavis et que prévoit le décret de 2023 ?
  - precision=0.00 recall=0.00 confidence=0.34 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-014** — Une SARL peut-elle être constituée par une seule personne en droit OHADA ?
  - precision=0.00 recall=0.00 confidence=0.55 refused=False evidence=18 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-015** — Quel est le barème de la redevance sur les engins spatiaux au Burkina Faso ?
  - precision=0.00 recall=1.00 confidence=0.40 refused=False evidence=3 chunks
- **qa-016** — Quels sont les droits d'un salarié licencié au Burkina Faso ?
  - precision=0.77 recall=1.00 confidence=0.72 refused=False evidence=22 chunks issues=0/7 missing_issues=dismissal_grounds,notice,compensation,accrued_rights,unfair_dismissal,legal_remedies,jurisdiction R@5=0.58 P@5=0.20 MRR=0.75 NDCG@5=0.49
- **qa-017** — Quand la responsabilité civile est-elle engagée au Burkina Faso ?
  - precision=0.00 recall=0.00 confidence=1.00 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-018** — Quel est le délai de prescription des actions civiles au Burkina Faso ?
  - precision=0.00 recall=0.00 confidence=0.75 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-019** — Un commerçant doit-il s'immatriculer au RCCM en droit OHADA ?
  - precision=0.00 recall=0.00 confidence=0.55 refused=False evidence=18 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-020** — Que comprend le fonds de commerce selon l'Acte uniforme OHADA ?
  - precision=0.00 recall=0.00 confidence=0.75 refused=False evidence=18 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-021** — Quel est le délai du recours pour excès de pouvoir contre un acte administratif au Burkina Faso ?
  - precision=0.00 recall=0.00 confidence=0.25 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-022** — Quelle juridiction connaît des litiges entre un administré et l'État au Burkina Faso ?
  - precision=0.00 recall=0.00 confidence=0.40 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-023** — Comment les terres rurales sont-elles attribuées aux exploitants au Burkina Faso ?
  - precision=0.00 recall=0.00 confidence=1.00 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-024** — Quel titre atteste la propriété d'une terre au Burkina Faso ?
  - precision=0.00 recall=0.00 confidence=0.40 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-025** — Combien d'associés une SARL peut-elle compter au maximum en droit OHADA ?
  - precision=0.00 recall=0.00 confidence=0.34 refused=False evidence=3 chunks R@5=0.00 P@5=0.00 MRR=0.00 NDCG@5=0.00
- **qa-026** — Quels sont les motifs d'une demande de divorce ? Est-il possible de divorcer si l'un des époux refuse de signer les papiers du divorce ?
  - precision=1.00 recall=1.00 confidence=0.34 refused=False evidence=3 chunks issues=4/4 R@5=0.50 P@5=0.17 MRR=0.50 NDCG@5=0.50
