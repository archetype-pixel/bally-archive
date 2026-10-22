"""Bally Heritage Archive: copy originals from the Azure Files share into Supabase Storage.

Resumable. Each run:
  1. inventories the Azure share into table azure_files (skips dirs already listed)
  2. matches every file record in file_copy to a path on the share
  3. for each matched file: downloads from Azure, uploads original to Storage,
     makes a display-size JPEG (1600px) and a thumbnail (400px), records status.

Env: AZ_ACCOUNT, AZ_SHARE, AZ_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY,
     RUN_SECONDS (default 20400 = 5h40m), WORKERS (default 4), BUCKET (default 'archive')
"""
import io
import os
import sys
import time
import traceback
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

from azure.storage.fileshare import ShareServiceClient
from PIL import Image, ImageOps
import requests
from sbrest import Client as create_client

Image.MAX_IMAGE_PIXELS = None  # large TIFF scans

AZ_ACCOUNT = os.environ["AZ_ACCOUNT"]
AZ_SHARE = os.environ["AZ_SHARE"]
AZ_KEY = os.environ["AZ_KEY"]
SB_URL = os.environ["SUPABASE_URL"]
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
BUCKET = os.environ.get("BUCKET", "archive")        # originals (private)
WEB = os.environ.get("WEB_BUCKET", "web")             # display/thumb/spin frames (public)
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "20400"))
WORKERS = int(os.environ.get("WORKERS", "4"))
SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "1"))
DB_SHARDS = 8  # file_copy.shard / spin_copy.shard are computed as key % 8; jobs beyond 8 pair up and work from the other end
DB_SHARD = SHARD % DB_SHARDS
ORDER_SUFFIX = "" if SHARD < DB_SHARDS else ".desc"
DO_INVENTORY = os.environ.get("DO_INVENTORY", "1") == "1"
COPY_SLICE = int(os.environ.get("COPY_SLICE", "1800"))
INVENTORY_SLICE = int(os.environ.get("INVENTORY_SLICE", "1500"))  # seconds of listing per cycle before matching/copying
ROOTS = [r for r in os.environ.get("AZ_ROOTS", "DAM").split(",") if r]
PREFER = ["DAM/PRJ/HERITAGE", "DAM/PRJ", "DAM/Shots", "DAM/Shot_Export", "DAM/Products", "DAM/MOVIES"]
SKIP_DIRS = tuple(os.environ.get("AZ_SKIP", "DAM/xHOTFOLDERS,DAM/xHOTFOLDERS.old,DAM/xHOTFOLDERS.old2,DAM/ImpExp_Temp,DAM/EXPORT_TEMP,DAM/Trash,DAM/xPROJECTS,DAM/xCOVERS,DAM/APS").split(","))
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}

T0 = time.time()
sb = create_client(SB_URL, SB_KEY)
svc = ShareServiceClient(account_url=f"https://{AZ_ACCOUNT}.file.core.windows.net",
                         credential={"account_name": AZ_ACCOUNT, "account_key": AZ_KEY})
share = svc.get_share_client(AZ_SHARE)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def time_left():
    return RUN_SECONDS - (time.time() - T0)


# ---------- 1. inventory ----------
def pending_dirs():
    """Folders queued but not yet listed (the database does the anti-join; the queue keeps finished rows too)."""
    r = requests.post(f"{SB_URL}/rest/v1/rpc/pending_dirs_json", headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json", "Prefer": "count=none"}, json={}, timeout=300)
    r.raise_for_status()
    return list(r.json())


def inventory():
    done = {r["dir"] for r in sb.table("azure_dirs_done").select("dir").limit(100000).execute().data}
    stack = [r for r in ROOTS if r not in done]
    try:
        stack += [d for d in pending_dirs() if d not in done and not d.startswith(SKIP_DIRS)]
    except Exception as e:
        log("no pending dir queue yet", type(e).__name__)
    # every shard lists its own slice of the queue (by hash of the path); dirs found for other shards go to the shared queue
    mine = lambda d: SHARDS == 1 or (zlib.crc32(d.encode()) % SHARDS) == SHARD
    foreign = []
    stack = [d for d in dict.fromkeys(stack) if mine(d) or d in ROOTS]
    n = 0
    budget = time.time() + INVENTORY_SLICE

    def list_dir(d):
        try:
            return d, list(share.get_directory_client(d).list_directories_and_files())
        except Exception as e:
            log("inventory error", "<dir>", type(e).__name__)
            return d, None

    def flush_foreign():
        pend = [{"dir": x} for x in dict.fromkeys(foreign)]
        for i in range(0, len(pend), 500):
            sb.table("azure_dirs_pending").upsert(pend[i:i + 500]).execute()
        foreign.clear()

    def refill():
        # hand over what we found for other shards, pick up what they found for us
        flush_foreign()
        fresh = pending_dirs()
        return [d for d in dict.fromkeys(fresh) if mine(d) and d not in done and not d.startswith(SKIP_DIRS)]

    waits = 0
    with ThreadPoolExecutor(max_workers=WORKERS * 2) as ex:
        while time_left() > 600 and time.time() < budget:
            if not stack:
                stack = refill()
                if not stack:
                    waits += 1
                    if waits > 6:
                        break
                    time.sleep(20)
                    continue
                waits = 0
            if len(foreign) > 200:
                flush_foreign()
            batch = [stack.pop() for _ in range(min(WORKERS * 2, len(stack)))]
            rows, finished = [], []
            for d, items in ex.map(list_dir, batch):
                if items is None:
                    continue
                already = d in done
                for it in items:
                    if it["is_directory"]:
                        sub = f"{d}/{it['name']}"
                        if sub not in done and not sub.startswith(SKIP_DIRS):
                            (stack if mine(sub) else foreign).append(sub)
                    elif not already:
                        rows.append({"path": f"{d}/{it['name']}", "dir": d, "name": it["name"], "size": it.get("size")})
                if not already:
                    finished.append(d)
            for i in range(0, len(rows), 500):
                sb.table("azure_files").upsert(rows[i:i + 500]).execute()
            if finished:
                sb.table("azure_dirs_done").upsert([{"dir": d} for d in finished]).execute()
                done.update(finished)
            n += len(rows)
            if n and n % 2000 < len(rows):
                log(f"inventory: {n} files this run, {len(stack)} dirs queued")
    log(f"inventory slice done: {n} files added, {len(stack)} dirs still queued")
    # persist the remaining queue so the next cycle/run continues from here
    foreign.extend(stack)
    flush_foreign()
    return len(stack) == 0 and waits > 6


# ---------- 2. match (done in the database: one statement, folder preference, retouched over raw) ----------
def match():
    try:
        r = requests.post(f"{SB_URL}/rest/v1/rpc/match_pending_files", headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}, json={}, timeout=300)
        log("matched", r.text.strip()[:40] if r.ok else f"rpc {r.status_code}")
    except Exception as e:
        log("match error", type(e).__name__)


# ---------- 3. copy ----------
def derivatives(data: bytes, name: str):
    ext = os.path.splitext(name)[1].lower()
    if ext not in IMAGE_EXT:
        return None, None, None, None
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    w, h = im.size
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    out = []
    for px in (1600, 400):
        c = im.copy()
        c.thumbnail((px, px))
        b = io.BytesIO()
        c.save(b, "JPEG", quality=85, optimize=True)
        out.append(b.getvalue())
    return out[0], out[1], w, h


def copy_one(rec):
    aid, guid, name, path = rec["asset_id"], rec["guid"], rec["name"], rec["azure_path"]
    try:
        f = share.get_file_client(path)
        data = f.download_file().readall()
        base = f"originals/{guid}/{name}"
        disp = thumb = None
        w = h = None
        try:
            d, t, w, h = derivatives(data, name)
            if d:
                disp = f"display/{guid}.jpg"
                thumb = f"thumb/{guid}.jpg"
                sb.storage.from_(WEB).upload(disp, d, {"content-type": "image/jpeg", "upsert": "true"})
                sb.storage.from_(WEB).upload(thumb, t, {"content-type": "image/jpeg", "upsert": "true"})
        except Exception as e:
            log("derivative failed", aid, type(e).__name__)
        status, err = "copied", None
        try:
            sb.storage.from_(BUCKET).upload(base, data, {"content-type": "application/octet-stream", "upsert": "true"})
        except ValueError as e:
            if "413" in str(e) or "too large" in str(e).lower():
                status, err, base = "too_large", f"original {len(data) / 1e6:.0f} MB exceeds storage upload limit", None
            else:
                raise
        sb.table("file_copy").update({"status": status, "dest_original": base, "dest_display": disp,
                                      "dest_thumb": thumb, "width": w, "height": h, "error": err,
                                      "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}).eq("asset_id", aid).execute()
        return len(data)
    except Exception as e:
        sb.table("file_copy").update({"status": "error", "error": str(e)[:500],
                                      "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}).eq("asset_id", aid).execute()
        log("copy error", aid, type(e).__name__)
        return 0


def copy_all(max_seconds=None):
    total = 0
    n = 0
    stop_at = time.time() + (max_seconds or 10**9)
    while time_left() > 300 and time.time() < stop_at:
        q = sb.table("file_copy").select("asset_id,guid,name,azure_path").eq("status", "matched")
        if SHARDS > 1:
            q = q.eq("shard", DB_SHARD)
        batch = q.order("asset_id" + ORDER_SUFFIX).limit(WORKERS * 5).execute().data
        if not batch:
            log("nothing left to copy")
            break
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for fut in as_completed([ex.submit(copy_one, r) for r in batch]):
                total += fut.result()
                n += 1
        log(f"copied {n} files, {total / 1e9:.2f} GB this run")
    return n


# ---------- 4. 360 spins (24 frames each, from the share) ----------
def match_spins():
    try:
        r = requests.post(f"{SB_URL}/rest/v1/rpc/match_pending_spins", headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}, json={}, timeout=300)
        log("spins matched", r.text.strip()[:40] if r.ok else f"rpc {r.status_code}")
    except Exception as e:
        log("spin match error", type(e).__name__)


def copy_spin(rec):
    sid, paths = rec["session_id"], rec["frame_paths"] or {}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        prefix = f"display/spins/{sid}"
        for n in range(24):
            p = paths.get(str(n + 1)) or paths.get(str(n))  # matcher keys frames 1..24 (file suffix _001.._024)
            if not p:
                raise RuntimeError(f"frame {n} missing")
            data = share.get_file_client(p).download_file().readall()
            ext = os.path.splitext(p)[1].lower() or ".jpg"
            sb.storage.from_(BUCKET).upload(f"originals/spins/{sid}/{n:02d}{ext}", data, {"content-type": "image/jpeg"})
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((1000, 1000))
            b = io.BytesIO()
            im.save(b, "JPEG", quality=85, optimize=True)
            sb.storage.from_(WEB).upload(f"{prefix}/{n}.jpg", b.getvalue(), {"content-type": "image/jpeg"})
        sb.table("spin_copy").update({"status": "copied", "dest_prefix": prefix, "error": None, "updated_at": now}).eq("session_id", sid).execute()
        sb.table("spins").update({"storage_prefix": prefix}).eq("session_id", sid).execute()
        return 1
    except Exception as e:
        sb.table("spin_copy").update({"status": "error", "error": str(e)[:300], "updated_at": now}).eq("session_id", sid).execute()
        log("spin error", sid, type(e).__name__)
        return 0


def copy_spins(max_seconds):
    n = 0
    stop_at = time.time() + max_seconds
    while time_left() > 300 and time.time() < stop_at:
        q = sb.table("spin_copy").select("session_id,frame_paths").eq("status", "matched")
        if SHARDS > 1:
            q = q.eq("shard", DB_SHARD)
        batch = q.order("session_id" + ORDER_SUFFIX).limit(WORKERS).execute().data
        if not batch:
            break
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for fut in as_completed([ex.submit(copy_spin, r) for r in batch]):
                n += fut.result()
        log(f"spins copied this run: {n}")
    return n


if __name__ == "__main__":
    try:
        complete = not DO_INVENTORY
        idle = 0
        while time_left() > 600:
            if DO_INVENTORY and not complete:
                complete = inventory()
            match()
            match_spins()
            n = copy_all(max_seconds=COPY_SLICE)
            n += copy_spins(max_seconds=COPY_SLICE // 2)
            if n == 0:
                idle += 1
                if idle > 40:
                    log("nothing to do for a long while; ending run")
                    break
                time.sleep(90)
            else:
                idle = 0
        log("run finished")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
