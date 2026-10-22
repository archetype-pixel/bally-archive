"""Image fingerprints (CLIP ViT-B/32, 512-d) for every copied photo, for 'find similar' search.
Reads display JPEGs from the public web bucket, writes to image_embeddings via RPC. Resumable, sharded.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, SHARD, SHARDS, RUN_SECONDS
"""
import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests
import torch
import open_clip
from PIL import Image

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "1"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "20400"))
BATCH = 32
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
T0 = time.time()
torch.set_num_threads(max(1, os.cpu_count() or 2))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rpc(name, body):
    r = requests.post(f"{SB_URL}/rest/v1/rpc/{name}", headers=H, json=body, timeout=300)
    r.raise_for_status()
    return r.json()


def fetch(rec):
    try:
        r = requests.get(f"{SB_URL}/storage/v1/object/public/web/{rec['thumb_path']}", timeout=60)
        if r.status_code != 200:
            return rec, None
        return rec, Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        return rec, None


def main():
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
    model.eval()
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        while RUN_SECONDS - (time.time() - T0) > 300:
            batch = rpc("embed_batch", {"p_shard": SHARD, "p_shards": SHARDS, "p_limit": BATCH * 4})
            if not batch:
                log("nothing left")
                break
            loaded = list(ex.map(fetch, batch))
            rows, failed = [], []
            for i in range(0, len(loaded), BATCH):
                part = [(r, im) for r, im in loaded[i:i + BATCH] if im is not None]
                failed += [r["asset_guid"] for r, im in loaded[i:i + BATCH] if im is None]
                if not part:
                    continue
                with torch.no_grad():
                    x = torch.stack([preprocess(im) for _, im in part])
                    f = model.encode_image(x)
                    f = f / f.norm(dim=-1, keepdim=True)
                for (r, _), v in zip(part, f.tolist()):
                    rows.append({"asset_guid": r["asset_guid"], "object_guid": r["object_guid"], "embedding": v})
            rpc("embed_store", {"p_rows": rows, "p_failed": failed})
            done += len(rows)
            log(f"embedded {done} this run ({len(failed)} failed in batch)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
