"""Content bounding box for every photo, so the site can centre and fill tiles around the actual
object instead of the photographer's empty margins. Reads the 400px thumbs from the public web
bucket; background colour is estimated from the border; result stored as fractions (0..1) in
image_bbox. Resumable, sharded like embed_queue (shard 0..15).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, SHARD, SHARDS, RUN_SECONDS
"""
import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "16"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "20400"))
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
T0 = time.time()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def post(path, body, extra=None):
    for i in range(5):
        try:
            r = requests.post(f"{SB_URL}/rest/v1/{path}", headers={**H, **(extra or {})}, json=body, timeout=120)
            if r.ok:
                return r.json() if r.text.strip() else None
            log("http", r.status_code, r.text[:150])
        except requests.RequestException as e:
            log("net", type(e).__name__)
        time.sleep(3 * (i + 1))
    raise RuntimeError(path)


def bbox(rec):
    try:
        r = requests.get(f"{SB_URL}/storage/v1/object/public/web/{rec['thumb_path']}", timeout=60)
        if r.status_code != 200:
            return None
        a = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"), dtype=np.int16)
        h, w, _ = a.shape
        border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
        bg = np.median(border, axis=0)
        # only treat as a plain studio background if the border is uniform
        if np.mean(np.abs(border - bg).sum(axis=1) > 30) > 0.25:
            return {"asset_guid": rec["asset_guid"], "x0": 0, "y0": 0, "x1": 1, "y1": 1, "bg": "busy"}
        diff = np.abs(a - bg).sum(axis=2) > 36
        rows, cols = np.where(diff.any(axis=1))[0], np.where(diff.any(axis=0))[0]
        if not len(rows) or not len(cols):
            return {"asset_guid": rec["asset_guid"], "x0": 0, "y0": 0, "x1": 1, "y1": 1, "bg": "empty"}
        # trim tiny specks: require a few pixels per row/column
        rcount, ccount = diff.sum(axis=1), diff.sum(axis=0)
        rows = np.where(rcount >= max(2, w * 0.004))[0] if (rcount >= max(2, w * 0.004)).any() else rows
        cols = np.where(ccount >= max(2, h * 0.004))[0] if (ccount >= max(2, h * 0.004)).any() else cols
        pad = 0.02
        x0, x1 = max(0, cols[0] / w - pad), min(1, (cols[-1] + 1) / w + pad)
        y0, y1 = max(0, rows[0] / h - pad), min(1, (rows[-1] + 1) / h + pad)
        hexbg = "#%02x%02x%02x" % tuple(int(v) for v in bg)
        return {"asset_guid": rec["asset_guid"], "x0": round(float(x0), 4), "y0": round(float(y0), 4), "x1": round(float(x1), 4), "y1": round(float(y1), 4), "bg": hexbg}
    except Exception:
        return None


def main():
    done = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        while RUN_SECONDS - (time.time() - T0) > 120:
            batch = post("rpc/bbox_batch", {"p_shard": SHARD, "p_shards": SHARDS, "p_limit": 400})
            if not batch:
                log("nothing left")
                break
            rows = [r for r in ex.map(bbox, batch) if r]
            failed = len(batch) - len(rows)
            # unreadable thumbs get a full-frame box so they are not retried forever
            got = {r["asset_guid"] for r in rows}
            rows += [{"asset_guid": b["asset_guid"], "x0": 0, "y0": 0, "x1": 1, "y1": 1, "bg": "unreadable"} for b in batch if b["asset_guid"] not in got]
            post("image_bbox", rows, {"Prefer": "resolution=merge-duplicates,return=minimal"})
            done += len(rows)
            log(f"boxes {done} ({failed} unreadable in batch)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
