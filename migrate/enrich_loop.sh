#!/usr/bin/env bash
# Always-on watcher: checks for new work every 2 minutes for ~5h20m, processes it, then hands over to a fresh run.
set -uo pipefail
H=(-H "apikey: $SUPABASE_SERVICE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" -H "Content-Type: application/json")
SB="$SUPABASE_URL"
END=$(( $(date +%s) + ${LOOP_SECONDS:-19200} ))
export RUN_SECONDS=2400 SHARD=0 SHARDS=1 LIMIT=0
while [ "$(date +%s)" -lt "$END" ]; do
  N=$(curl -sS -X POST "$SB/rest/v1/rpc/intake_scan" "${H[@]}" -d '{}')
  Q=$(curl -sS -X POST "$SB/rest/v1/rpc/enrich_enqueue" "${H[@]}" -d '{}')
  WORK=$(python3 -c "import json,sys
try: q=json.loads(sys.argv[1])
except Exception: q={}
print(1 if isinstance(q,dict) and any(q.get(k,0) for k in ('pending_describe','pending_embed','pending_tags','pending_zoom','pending_intake')) else 0)" "$Q")
  if [ "$WORK" = 1 ]; then
    echo "$(date -u +%H:%M) new intake files: $N  queue: $Q"
    WORKERS=4 python migrate/intake.py
    curl -sS -X POST "$SB/rest/v1/rpc/enrich_enqueue" "${H[@]}" -d '{}' >/dev/null
    WORKERS=4 python migrate/describe.py
    python migrate/embed.py
    python migrate/tag.py
    WORKERS=4 python migrate/make_zoom.py
    curl -sS -X POST "$SB/rest/v1/rpc/refresh_ai_profile" "${H[@]}" -d '{}' >/dev/null
    gh workflow run catalog-bundle.yml -R "$GITHUB_REPOSITORY" -f force=1 || true
  fi
  sleep 120
done
echo "handing over to the next watcher run"
gh workflow run enrich.yml -R "$GITHUB_REPOSITORY" || true
