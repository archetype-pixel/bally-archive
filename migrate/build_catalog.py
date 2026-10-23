"""Pre-build the catalogue index as a static file on the storage CDN so visitors no longer wait for
the database to assemble it (about 1.5 s per visit). Adds each lead photo's content box (bb) for
tile framing. Writes web/catalog/index.json = {"v": <catalog_version>, "built": <iso>, "rows": [...]}.
Rebuild whenever catalog_version() changes; the site falls back to the live RPC when the file is older.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, FORCE (1 = rebuild even if unchanged)
"""
import json
import os
import sys
import time

import requests

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
FORCE = os.environ.get("FORCE") == "1"
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
PATH = "catalog/index.json"


def rpc(name, body=None):
    r = requests.post(f"{SB_URL}/rest/v1/rpc/{name}", headers=H, json=body or {}, timeout=300)
    r.raise_for_status()
    return r.json()


def all_boxes():
    out, start = {}, 0
    while True:
        r = requests.get(f"{SB_URL}/rest/v1/image_bbox", timeout=120, headers=H,
                         params={"select": "asset_guid,x0,y0,x1,y1", "order": "asset_guid", "offset": start, "limit": 1000})
        r.raise_for_status()
        rows = r.json()
        for b in rows:
            out[b["asset_guid"].upper()] = [b["x0"], b["y0"], b["x1"], b["y1"]]
        if not rows:
            return out
        start += len(rows)


def main():
    version = rpc("catalog_version")
    if not FORCE:
        cur = requests.get(f"{SB_URL}/storage/v1/object/public/web/{PATH}", timeout=60)
        if cur.ok:
            try:
                if cur.json().get("v") == version:
                    print("unchanged", version)
                    return
            except ValueError:
                pass
    rows = rpc("catalog_index_json")
    boxes = all_boxes()
    hit = 0
    for r in rows:
        img = (r.get("img") or "")
        g = img.split("/")[-1].split(".")[0].upper() if img else ""
        b = boxes.get(g)
        if b and b != [0, 0, 1, 1]:
            r["bb"] = b
            hit += 1
    body = json.dumps({"v": version, "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": rows},
                      ensure_ascii=False, separators=(",", ":")).encode()
    u = requests.post(f"{SB_URL}/storage/v1/object/web/{PATH}", data=body, timeout=300,
                      headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json",
                               "x-upsert": "true", "cache-control": "max-age=300"})
    print("uploaded", u.status_code, len(body), "bytes", len(rows), "rows", hit, "with boxes", version)
    u.raise_for_status()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
