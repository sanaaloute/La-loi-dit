#!/bin/bash
# Warm the exact answer cache with canonical FAQ questions.
#
# Run after (re)deploys on the server. The first asker of each canonical
# question pays the full pipeline; everyone afterwards gets a cached answer.
# Hits the container directly (127.0.0.1:8000) — no Cloudflare 100s cap.
#
# Usage:
#   WARMUP_USER=<login> WARMUP_PASS=<password> bash scripts/warm_cache.sh
# (any registered account works; nothing is stored but the cached answers)
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${API_BASE:-http://127.0.0.1:8000/api/v1}"
: "${WARMUP_USER:?set WARMUP_USER (account email/phone/username)}"
: "${WARMUP_PASS:?set WARMUP_PASS}"

TOKEN=$(curl -sf -X POST "$BASE/auth/token" \
  -H 'Content-Type: application/json' -H 'X-Device-Id: cache-warmup' \
  -d "{\"username\":\"$WARMUP_USER\",\"password\":\"$WARMUP_PASS\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

QUESTIONS=(
  "Quels sont les droits d'un salarié licencié au Burkina Faso ?"
  "Quel est le délai de préavis en cas de démission d'un CDI ?"
  "Quelle est la procédure de divorce au Burkina Faso ?"
  "Un bailleur peut-il expulser un locataire sans préavis ?"
  "Quels documents faut-il pour créer une SARL en droit OHADA ?"
  "Quelles sont les conditions d'adoption au Burkina Faso ?"
  "Quel est le délai de prescription d'une action civile ?"
  "Comment contester un licenciement abusif ?"
  "Quels sont les droits de succession du conjoint survivant ?"
  "Quelle est la durée légale du travail au Burkina Faso ?"
)

for q in "${QUESTIONS[@]}"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 300 \
    -X POST "$BASE/chat" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -H 'X-Device-Id: cache-warmup' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1], "language": "fr"}))' "$q")")
  echo "$code  $q"
done
echo "Answer cache warmed."
