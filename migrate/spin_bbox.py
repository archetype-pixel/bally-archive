"""One framing box per 360 spin: the union of the object's outline across 12 frames spread
evenly round the turn, so the viewer can centre and size the object once for the whole spin
(no drifting or jumping between frames). Same background method as bbox.py. Frames are read
from the public web bucket (display/spins/<id>/<n>.jpg). Resumable, sharded.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, SHARD, SHARDS, RUN_SECONDS, SAMPLES
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
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "6600"))
SAMPLES = int(os.environ.get("SAMPLES", "12"))
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
T0 = time.time()
S = requests.Session()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def post(path, body, extra=None):
    for i in range(5):
        try:
            r = S.post(f"{SB_URL}/rest/v1/{path}", headers={**H, **(extra or {})}, json=body, timeout=120)
            if r.ok:
                return r.json() if r.text.strip() else None
            log("http", r.status_code, r.text[:150])
        except requests.RequestException as e:
            log("net", type(e).__name__)
        time.sleep(3 * (i + 1))
    raise RuntimeError(path)


def frame_box(url):
    """(x0, y0, x1, y1) fractions of the object in one frame, None if unreadable, 'busy' if no plain background."""
    try:
        r = S.get(url, timeout=60)
        if r.status_code != 200:
            return None
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        im.thumbnail((400, 400))
        a = np.asarray(im, dtype=np.int16)
        h, w, _ = a.shape
        border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
        bg = np.median(border, axis=0)
        if np.mean(np.abs(border - bg).sum(axis=1) > 30) > 0.25:
            return "busy"
        diff = np.abs(a - bg).sum(axis=2) > 36
        rc, cc = diff.sum(axis=1), diff.sum(axis=0)
        rows = np.where(rc >= max(2, w * 0.004))[0]
        cols = np.where(cc >= max(2, h * 0.004))[0]
        if not len(rows) or not len(cols):
            return None
        return (cols[0] / w, rows[0] / h, (cols[-1] + 1) / w, (rows[-1] + 1) / h, "#%02x%02x%02x" % tuple(int(v) for v in bg))
    except Exception:
        return None


def spin_box(rec):
    prefix, frames = rec["storage_prefix"], max(1, int(rec.get("frames") or 24))
    idx = sorted({round(i * frames / SAMPLES) % frames for i in range(SAMPLES)})
    boxes = [frame_box(f"{SB_URL}/storage/v1/object/public/web/{prefix}/{i}.jpg") for i in idx]
    good = [b for b in boxes if isinstance(b, tuple)]
    if not good:
        bg = "busy" if any(b == "busy" for b in boxes) else "unreadable"
        return {"storage_prefix": prefix, "x0": 0, "y0": 0, "x1": 1, "y1": 1, "frames_sampled": 0, "bg": bg}
    pad = 0.02
    x0 = max(0.0, min(b[0] for b in good) - pad); y0 = max(0.0, min(b[1] for b in good) - pad)
    x1 = min(1.0, max(b[2] for b in good) + pad); y1 = min(1.0, max(b[3] for b in good) + pad)
    return {"storage_prefix": prefix, "x0": round(x0, 4), "y0": round(y0, 4), "x1": round(x1, 4), "y1": round(y1, 4),
            "frames_sampled": len(good), "bg": good[0][4]}


def main():
    done = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        while RUN_SECONDS - (time.time() - T0) > 120:
            batch = post("rpc/spin_bbox_batch", {"p_shard": SHARD, "p_shards": SHARDS, "p_limit": 96})
            if not batch:
                log("nothing left")
                break
            rows = list(ex.map(spin_box, batch))
            post("spin_bbox", rows, {"Prefer": "resolution=merge-duplicates,return=minimal"})
            done += len(rows)
            log(f"spins {done}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
