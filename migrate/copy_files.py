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
ROOTS = [r for r in os.environ.get("AZ_ROOTS", "DAM").split(",") if r]
PREFER = ["DAM/PRJ/HERITAGE", "DAM/PRJ", "DAM/Shots", "DAM/Shot_Export", "DAM/Products", "DAM/MOVIES"]
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
    # re-queue subdirs of done dirs that are themselves not done
    known_dirs = {r["dir"] for r in sb.table("azure_files").select("dir").limit(200000).execute().data}
    n = 0
    while stack and time_left() > 600:
        d = stack.pop()
        if d in done:
            continue
        try:
            items = list(share.get_directory_client(d).list_directories_and_files())
        except Exception as e:
            log("inventory error", "<dir>", type(e).__name__)
            continue
        rows = []
        for it in items:
            if it["is_directory"]:
                sub = f"{d}/{it['name']}"
                if sub not in done:
                    stack.append(sub)
            else:
                rows.append({"path": f"{d}/{it['name']}", "dir": d, "name": it["name"], "size": it.get("size")})
        if rows:
            for i in range(0, len(rows), 500):
                sb.table("azure_files").upsert(rows[i:i + 500]).execute()
        sb.table("azure_dirs_done").upsert({"dir": d}).execute()
        done.add(d)
        n += len(rows)
        if n and n % 2000 < len(rows):
            log(f"inventory: {n} files this run, {len(stack)} dirs queued")
    log(f"inventory pass done: {n} files added, {len(stack)} dirs still queued")
    return len(stack) == 0


# ---------- 2. match ----------
def match():
    pend = sb.table("file_copy").select("asset_id,name").eq("status", "pending").limit(100000).execute().data
    if not pend:
        return
    log(f"matching {len(pend)} pending records")
    names = sorted({p["name"] for p in pend if p["name"]})
    found = {}
    for i in range(0, len(names), 200):
        chunk = names[i:i + 200]
        res = sb.table("azure_files").select("path,name,size").in_("name", chunk).execute().data
        for r in res:
            found.setdefault(r["name"], []).append(r)
    upd = 0
    for p in pend:
        cands = found.get(p["name"], [])
        if not cands:
            continue
        # prefer heritage project folders, then any; note ambiguity
        def rank(c):
            for i, pref in enumerate(PREFER):
                if c["path"].startswith(pref + "/"):
                    return i
            return len(PREFER)
        cands.sort(key=rank)
        best = cands[0]
        note = "" if len(cands) == 1 else f"{len(cands)} candidates; chose by folder preference"
        sb.table("file_copy").update({"azure_path": best["path"], "azure_size": best["size"],
                                      "match_note": note, "status": "matched",
                                      "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}).eq("asset_id", p["asset_id"]).execute()
        upd += 1
    log(f"matched {upd} of {len(pend)}")


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


def copy_all():
    total = 0
    n = 0
    while time_left() > 300:
        batch = sb.table("file_copy").select("asset_id,guid,name,azure_path").eq("status", "matched") \
            .order("asset_id").limit(WORKERS * 5).execute().data
        if not batch:
            log("nothing left to copy")
            break
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for fut in as_completed([ex.submit(copy_one, r) for r in batch]):
                total += fut.result()
                n += 1
        log(f"copied {n} files, {total / 1e9:.2f} GB this run")
    return n


if __name__ == "__main__":
    try:
        complete = inventory()
        match()
        if not complete:
            log("inventory not complete yet; copying what is matched so far")
        copy_all()
        log("run finished")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
