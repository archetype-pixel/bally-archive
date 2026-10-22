"""Copy web derivatives (display/, thumb/, display/spins/) from the private 'archive' bucket
into the public 'web' bucket, server-side (no download). Resumable; sharded like copy_files.py.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, SHARD, SHARDS, WORKERS, RUN_SECONDS
"""
import os
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "1"))
WORKERS = int(os.environ.get("WORKERS", "8"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "20400"))
SRC, DST = "archive", "web"
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
T0 = time.time()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rpc(name, body=None):
    r = requests.post(f"{SB_URL}/rest/v1/rpc/{name}", headers=H, json=body or {}, timeout=300)
    r.raise_for_status()
    return r.json()


def copy_one(path):
    for attempt in range(4):
        r = requests.post(f"{SB_URL}/storage/v1/object/copy", headers=H, timeout=120,
                          json={"bucketId": SRC, "sourceKey": path, "destinationBucket": DST, "destinationKey": path, "copyMetadata": True})
        if r.status_code in (200, 201):
            return path, True
        if r.status_code == 400 and "already exists" in r.text.lower():
            return path, True
        if r.status_code == 404:
            return path, False
        time.sleep(2 * (attempt + 1))
    return path, False


def main():
    done = 0
    while RUN_SECONDS - (time.time() - T0) > 120:
        # the database hands out paths not yet marked copied-to-web, for this shard
        batch = rpc("web_copy_batch", {"p_shard": SHARD, "p_shards": SHARDS, "p_limit": WORKERS * 25})
        if not batch:
            log("nothing left")
            break
        ok, missing = [], []
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for fut in as_completed([ex.submit(copy_one, p) for p in batch]):
                p, good = fut.result()
                (ok if good else missing).append(p)
        rpc("web_copy_mark", {"p_paths": ok, "p_status": "copied"})
        if missing:
            rpc("web_copy_mark", {"p_paths": missing, "p_status": "missing"})
        done += len(ok)
        log(f"copied {done} to web this run ({len(missing)} missing in batch)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:200])
        sys.exit(1)
