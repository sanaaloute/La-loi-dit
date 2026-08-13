#!/bin/bash
# Re-ingest the legal corpus into the Milvus server (localhost:19530).
set -x
PY=.venv/Scripts/python.exe
$PY -m backend.ingestion.pipeline data/legal_docs/constitution-burkina-faso.pdf --name "Constitution du Burkina Faso" --url "https://www.education.gov.bf" 2>&1 | tail -2
$PY -m backend.ingestion.pipeline data/legal_docs/constitution-burkina-faso-transition-2015.pdf --name "Constitution du Burkina Faso (révisée) et Charte de la Transition 2015" --url "https://cabri-sbo.org" 2>&1 | tail -2
$PY -m backend.ingestion.pipeline data/legal_docs/code-du-travail-burkina-faso.pdf --name "Code du travail du Burkina Faso (Loi 028-2008/AN)" --url "https://natlex.ilo.org" 2>&1 | tail -2
$PY -m backend.ingestion.pipeline data/legal_docs/traite-ohada.pdf --name "Traité OHADA (révisé, 2008)" --url "https://www.sgg-mali.ml" 2>&1 | tail -2
$PY -m backend.ingestion.pipeline data/legal_docs/audcg-droit-commercial-general-2010.pdf --name "Acte uniforme OHADA sur le droit commercial général (AUDCG, 2010)" --url "https://www.mincommerce.gov.cm" 2>&1 | tail -2
$PY -m backend.ingestion.pipeline data/legal_docs/auscgie-societes-commerciales-gie-2014.pdf --name "Acte uniforme OHADA relatif au droit des sociétés commerciales et du GIE (AUSCGIE révisé, 2014)" --url "https://www.fao.org/faolex" 2>&1 | tail -2
$PY -m backend.ingestion.pipeline data/legal_docs/aus-suretes-2010.pdf --name "Acte uniforme OHADA sur les sûretés (révisé, 2010)" --url "https://www.leganet.cd" 2>&1 | tail -2
echo INGEST_ALL_DONE
