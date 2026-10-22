"""Make 3200px 'zoom' JPEGs for every copied photo, from the original in the private bucket,
into the public web bucket at zoom/<guid>.jpg. Resumable, sharded.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, SHARD, SHARDS, WORKERS, RUN_SECONDS
"""
import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None
SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "1"))
WORKERS = int(os.environ.get("WORKERS", "6"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "20400"))
ZOOM_PX = int(os.environ.get("ZOOM_PX", "3200"))
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
T0 = time.time()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rpc(name, body):
    r = requests.post(f"{SB_URL}/rest/v1/rpc/{name}", headers=H, json=body, timeout=300)
    r.raise_for_status()
    return r.json()


def one(rec):
    guid, orig = rec["guid"], rec["dest_original"]
    try:
        r = requests.get(f"{SB_URL}/storage/v1/object/archive/{orig}", headers=H, timeout=600)
        if r.status_code != 200:
            return guid, "missing"
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(r.content)))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if max(im.size) <= 1600:
            return guid, "small"  # display copy already shows it at full size
        im.thumbnail((ZOOM_PX, ZOOM_PX))
        b = io.BytesIO()
        im.save(b, "JPEG", quality=85, optimize=True, progressive=True)
        u = requests.post(f"{SB_URL}/storage/v1/object/web/zoom/{guid}.jpg", data=b.getvalue(), timeout=600,
                          headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "image/jpeg", "x-upsert": "true"})
        return guid, "copied" if u.status_code in (200, 201) else f"upload {u.status_code}"
    except Exception as e:
        return guid, f"error {type(e).__name__}"


def main():
    n = 0
    while RUN_SECONDS - (time.time() - T0) > 300:
        batch = rpc("zoom_batch", {"p_shard": SHARD, "p_shards": SHARDS, "p_limit": WORKERS * 4})
        if not batch:
            log("nothing left")
            break
        results = []
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for fut in as_completed([ex.submit(one, r) for r in batch]):
                results.append(fut.result())
        rpc("zoom_mark", {"p_rows": [{"guid": g, "status": s} for g, s in results]})
        n += sum(1 for _, s in results if s == "copied")
        log(f"zoom made this run: {n}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:200])
        sys.exit(1)
