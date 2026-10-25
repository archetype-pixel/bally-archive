"""Mirror every remaining file on the Azure share into Supabase Storage (bucket "archive", prefix azure-mirror/<original path>).
Works through table mirror_copy (status pending -> copied/error) for one shard. Files already copied as linked originals or
360 frames are not in the table. Mode PACK=1 instead bundles the tiny priority-3 hot-folder XML logs into tar.gz files.
Env: AZ_ACCOUNT, AZ_SHARE, AZ_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, SHARD, WORKERS, RUN_SECONDS, MAX_PRIORITY, PACK."""
import io, mimetypes, os, re, sys, tarfile, time, unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
import requests
from azure.storage.fileshare import ShareServiceClient

SB = os.environ["SUPABASE_URL"].rstrip("/"); KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
SHARD = int(os.environ.get("SHARD", "0")); WORKERS = int(os.environ.get("WORKERS", "8"))
RUN = int(os.environ.get("RUN_SECONDS", "20400")); T0 = time.time()
MAXP = int(os.environ.get("MAX_PRIORITY", "2"))
share = ShareServiceClient(account_url=f"https://{os.environ['AZ_ACCOUNT']}.file.core.windows.net",
                           credential={"account_name": os.environ["AZ_ACCOUNT"], "account_key": os.environ["AZ_KEY"]}
                           ).get_share_client(os.environ["AZ_SHARE"])
sess = requests.Session()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def mark(path, status, error=None):
    for t in range(4):
        try:
            r = sess.patch(f"{SB}/rest/v1/mirror_copy", headers={**H, "Prefer": "return=minimal"}, timeout=60,
                           params={"path": f"eq.{path}"}, json={"status": status, "error": error, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            if r.ok:
                return
        except Exception:
            pass
        time.sleep(2 + t * 3)


def safe_key(path):
    """Storage keys must be plain ASCII: strip accents (Müller -> Muller) and replace other odd characters."""
    s = unicodedata.normalize("NFKD", path).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9/._ ()\-,'&+=@]", "_", s)


def upload(dest, data, ctype):
    url = f"{SB}/storage/v1/object/archive/{quote(dest, safe='/')}"
    last = ""
    for t in range(4):
        try:
            r = sess.post(url, data=data, timeout=1800, headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": ctype, "x-upsert": "true"})
            if r.ok:
                return None
            last = f"{r.status_code} {r.text[:160]}"
            if r.status_code in (400, 413):
                return last
        except Exception as e:
            last = f"{type(e).__name__} {str(e)[:160]}"
        time.sleep(3 + t * 5)
    return last


def one(row):
    if time.time() - T0 > RUN:
        return "timeout"
    path = row["path"]
    try:
        data = share.get_file_client(path).download_file(max_concurrency=4).readall()
    except Exception as e:
        mark(path, "error", f"download {type(e).__name__} {str(e)[:160]}")
        return "error"
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    key = safe_key(path)
    err = upload(f"azure-mirror/{key}", data, ctype)
    mark(path, "error" if err else "copied", err or (None if key == path else f"stored as azure-mirror/{key}"))
    return "error" if err else "copied"


def batch(n=2000):
    r = sess.get(f"{SB}/rest/v1/mirror_copy", headers=H, timeout=120,
                 params={"select": "path,size", "shard": f"eq.{SHARD}", "status": "eq.pending", "priority": f"lte.{MAXP}",
                         "order": "priority,path", "limit": n})
    r.raise_for_status()
    return r.json()


def mirror():
    from collections import Counter
    c = Counter()
    while time.time() - T0 < RUN:
        rows = batch()
        if not rows:
            break
        with ThreadPoolExecutor(WORKERS) as ex:
            for f in as_completed([ex.submit(one, r) for r in rows]):
                c[f.result()] += 1
        log("shard", SHARD, dict(c))
        if c["timeout"]:
            break
    log("done", dict(c))


def pack():
    """Bundle priority-3 hot-folder XML logs (hundreds of thousands of tiny files) into tar.gz archives, one per top folder."""
    from collections import defaultdict
    rows, off = [], 0
    while True:
        r = sess.get(f"{SB}/rest/v1/mirror_copy", headers=H, timeout=120,
                     params={"select": "path", "priority": "eq.3", "status": "eq.pending", "shard": f"eq.{SHARD}", "order": "path", "limit": 5000, "offset": off})
        page = r.json(); rows += page; off += 5000
        if len(page) < 5000:
            break
    groups = defaultdict(list)
    for x in rows:
        groups["/".join(x["path"].split("/")[:2])].append(x["path"])
    for g, paths in groups.items():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            def get(p):
                try:
                    return p, share.get_file_client(p).download_file().readall()
                except Exception:
                    return p, None
            with ThreadPoolExecutor(WORKERS) as ex:
                for p, data in ex.map(get, paths):
                    if data is not None:
                        ti = tarfile.TarInfo(p); ti.size = len(data); tar.addfile(ti, io.BytesIO(data))
        name = f"azure-mirror/_packed/{g.replace('/', '__')}__shard{SHARD:02d}.tar.gz"
        err = upload(name, buf.getvalue(), "application/gzip")
        log(g, len(paths), "files ->", name, err or "ok")
        if not err:
            for i in range(0, len(paths), 500):
                chunk = ",".join('"' + p.replace('"', '\\"') + '"' for p in paths[i:i + 500])
                sess.patch(f"{SB}/rest/v1/mirror_copy", headers={**H, "Prefer": "return=minimal"}, timeout=120,
                           params={"path": f"in.({chunk})"}, json={"status": "packed", "error": name})


if __name__ == "__main__":
    try:
        pack() if os.environ.get("PACK") == "1" else mirror()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300]); sys.exit(1)
