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
BUCKET = os.environ.get("BUCKET", "archive")
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "20400"))
WORKERS = int(os.environ.get("WORKERS", "4"))
SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "1"))
DO_INVENTORY = os.environ.get("DO_INVENTORY", "1") == "1"
COPY_SLICE = int(os.environ.get("COPY_SLICE", "1800"))
INVENTORY_SLICE = int(os.environ.get("INVENTORY_SLICE", "900"))  # seconds of listing per cycle before matching/copying
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
def inventory():
    done = {r["dir"] for r in sb.table("azure_dirs_done").select("dir").limit(100000).execute().data}
    stack = [r for r in ROOTS if r not in done]
    try:
        stack += [r["dir"] for r in sb.table("azure_dirs_pending").select("dir").limit(100000).execute().data if r["dir"] not in done and not r["dir"].startswith(SKIP_DIRS)]
    except Exception as e:
        log("no pending dir queue yet", type(e).__name__)
    stack = list(dict.fromkeys(stack))
    # re-queue subdirs of done dirs that are themselves not done
    known_dirs = {r["dir"] for r in sb.table("azure_files").select("dir").limit(200000).execute().data}
    n = 0
    budget = time.time() + INVENTORY_SLICE
    while stack and time_left() > 600 and time.time() < budget:
        d = stack.pop()
        already = d in done
        try:
            items = list(share.get_directory_client(d).list_directories_and_files())
        except Exception as e:
            log("inventory error", "<dir>", type(e).__name__)
            continue
        rows = []
        for it in items:
            if it["is_directory"]:
                sub = f"{d}/{it['name']}"
                if sub not in done and not sub.startswith(SKIP_DIRS):
                    stack.append(sub)
            elif not already:
                rows.append({"path": f"{d}/{it['name']}", "dir": d, "name": it["name"], "size": it.get("size")})
        if rows:
            for i in range(0, len(rows), 500):
                sb.table("azure_files").upsert(rows[i:i + 500]).execute()
        if not already:
            sb.table("azure_dirs_done").upsert({"dir": d}).execute()
            done.add(d)
        n += len(rows)
        if n and n % 2000 < len(rows):
            log(f"inventory: {n} files this run, {len(stack)} dirs queued")
    log(f"inventory slice done: {n} files added, {len(stack)} dirs still queued")
    # persist the remaining queue so the next cycle/run continues from here
    pend = [{"dir": x} for x in stack]
    for i in range(0, len(pend), 500):
        sb.table("azure_dirs_pending").upsert(pend[i:i + 500]).execute()
    return len(stack) == 0


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
        sb.storage.from_(BUCKET).upload(base, data, {"content-type": "application/octet-stream", "upsert": "true"})
        disp = thumb = None
        w = h = None
        try:
            d, t, w, h = derivatives(data, name)
            if d:
                disp = f"display/{guid}.jpg"
                thumb = f"thumb/{guid}.jpg"
                sb.storage.from_(BUCKET).upload(disp, d, {"content-type": "image/jpeg", "upsert": "true"})
                sb.storage.from_(BUCKET).upload(thumb, t, {"content-type": "image/jpeg", "upsert": "true"})
        except Exception as e:
            log("derivative failed", aid, type(e).__name__)
        sb.table("file_copy").update({"status": "copied", "dest_original": base, "dest_display": disp,
                                      "dest_thumb": thumb, "width": w, "height": h, "error": None,
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
            q = q.eq("shard", SHARD)
        batch = q.order("asset_id").limit(WORKERS * 5).execute().data
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
            p = paths.get(str(n))
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
            sb.storage.from_(BUCKET).upload(f"{prefix}/{n}.jpg", b.getvalue(), {"content-type": "image/jpeg"})
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
            q = q.eq("shard", SHARD)
        batch = q.order("session_id").limit(WORKERS).execute().data
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
                if DO_INVENTORY and complete:
                    break
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
