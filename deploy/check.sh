#!/usr/bin/env bash
# Smoke test for a deployed TrueCost API.   bash check.sh https://api.yourdomain.com
BASE="${1:?usage: bash check.sh https://your-api-url}"
BASE="${BASE%/}"
fail=0
ok()   { echo "  OK    $1"; }
bad()  { echo "  FAIL  $1"; fail=1; }

echo "Checking $BASE"
h=$(curl -fsS "$BASE/health") && ok "health: $h" || bad "health"
echo "$h" | grep -q '"db":"postgres"' && ok "database is Postgres (Tiger Data)" || bad "database is not postgres (is DATABASE_URL set?)"

p=$(curl -fsS "$BASE/api/v1/plans?insurer=Univera") && ok "plans: $p" || bad "plans"

body='{"codes":["77066","76642"],"insurance":{"insurer":"Independent Health","plan":"Any","deductible_remaining":1200,"coinsurance_pct":20,"oop_max_remaining":4000},"patient_zip":"14226"}'
e=$(curl -fsS -X POST "$BASE/estimate" -H 'content-type: application/json' -d "$body") && ok "estimate returned $(echo "$e" | grep -o '"hospital"' | wc -l) hospitals" || bad "estimate"

x=$(curl -fsS -X POST "$BASE/extract" -H 'content-type: application/json' -d '{"note":"Bilateral diagnostic mammogram ordered for both breasts."}') \
  && ok "extract: $(echo "$x" | grep -o '"extractor":"[^"]*"')" || bad "extract"

d=$(curl -fsS -X POST "$BASE/api/v1/assistance/match" -H 'content-type: application/json' -d '{"case":{},"demo_mode":true}') \
  && ok "assistant demo mode works" || bad "assistant demo mode"

c=$(curl -fsS -D - -o /dev/null -X OPTIONS "$BASE/estimate" -H 'Origin: https://example.lovable.app' -H 'Access-Control-Request-Method: POST') \
  && echo "$c" | grep -qi 'access-control-allow-origin' && ok "CORS headers present" || bad "CORS headers missing (check CORS_ORIGINS)"

[ $fail -eq 0 ] && echo "ALL GOOD" || { echo "SOME CHECKS FAILED"; exit 1; }
