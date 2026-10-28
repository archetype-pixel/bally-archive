"""Re-save public web images with a browser cache header. They were uploaded with 'no-cache', so every
thumbnail and photo was re-checked with the server on every view, which makes the site feel slow.
Same bytes, same path; only the Cache-Control header changes. Resumable (skips files already done).
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, PREFIX (e.g. thumb/), SHARD, SHARDS, MAX_AGE, RUN_SECONDS
"""
import os, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import requests

SB = os.environ["SUPABASE_URL"].rstrip("/"); KEY = os.environ["SUPABASE_SERVICE_KEY"]
PREFIX = os.environ["PREFIX"]; SHARD = int(os.environ.get("SHARD", "0")); SHARDS = int(os.environ.get("SHARDS", "1"))
MAX_AGE = os.environ.get("MAX_AGE", "604800"); RUN = int(os.environ.get("RUN_SECONDS", "20000"))
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
T0 = time.time(); S = requests.Session()


def one(row):
    name = row["name"]; q = urllib.parse.quote(name)
    for i in range(4):
        try:
            g = S.get(f"{SB}/storage/v1/object/public/web/{q}", timeout=120)
            if g.status_code != 200:
                return "missing"
            u = S.post(f"{SB}/storage/v1/object/web/{q}", data=g.content, timeout=180, headers={**H,
                "Content-Type": row.get("mimetype") or g.headers.get("content-type", "image/jpeg"),
                "cache-control": f"max-age={MAX_AGE}", "x-upsert": "true"})
            if u.status_code in (200, 201):
                return "ok"
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return "fail"


def main():
    after, tally = None, {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        while time.time() - T0 < RUN:
            r = S.post(f"{SB}/rest/v1/rpc/web_nocache_batch", headers={**H, "Content-Type": "application/json"},
                       json={"p_prefix": PREFIX, "p_after": after, "p_limit": 400, "p_shard": SHARD, "p_shards": SHARDS}, timeout=120)
            r.raise_for_status(); rows = r.json()
            if not rows:
                break
            for res in ex.map(one, rows):
                tally[res] = tally.get(res, 0) + 1
            after = rows[-1]["name"]
            print(time.strftime("%H:%M:%S"), tally, after, flush=True)
    print("done", tally, flush=True)


if __name__ == "__main__":
    main()
