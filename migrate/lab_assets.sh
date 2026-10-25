#!/usr/bin/env bash
# Build Product Lab assets for one design: draft 3D model from its spin and colourway studies from its photos, then upload to web/lab/<slug>/.
set -euo pipefail
SLUG=$1; SPIN=$2; FIT=$3; shift 3
BASE="$SUPABASE_URL/storage/v1/object/public/web"
mkdir -p work && cd work
FIT="$FIT" python ../migrate/visual_hull.py "$BASE/$SPIN" model.glb 24
for p in "$@"; do tag=$(basename "$p" .jpg | cut -c1-8); curl -sf -o "$tag.jpg" "$BASE/$p"; python ../migrate/colourways.py "$tag.jpg" "$tag"; done
for f in model.glb cw_*.jpg; do
  ct=image/jpeg; [[ $f == *.glb ]] && ct=model/gltf-binary
  curl -sf -X POST "$SUPABASE_URL/storage/v1/object/web/lab/$SLUG/$f" -H "apikey: $SUPABASE_SERVICE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" -H "Content-Type: $ct" -H "x-upsert: true" --data-binary "@$f" > /dev/null && echo "uploaded lab/$SLUG/$f"
done
