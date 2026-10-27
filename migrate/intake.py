"""Intake: process files dropped under archive/intake/<batch>/... (bulk drive load, studio drop folder).

For each new file:
  1. find an object code: file name, then folder names, then embedded photo data (XMP/IPTC/EXIF title,
     description, keywords). Only codes that exist in the Collection count.
  2. photos: make a 1600px display and a 400px thumb in the public web bucket (intake/<id>-display.jpg),
     read width/height, compute the CLIP fingerprint (same model as image_embeddings.embedding2) and
     look up the 5 most similar existing objects as suggestions.
  3. matched -> attach to the object as a media upload (original stays where it is), status 'attached'.
     films/PDFs with a code are copied to the web bucket and attached the same way.
     no code -> status 'needs_review' with suggestions, for the archivist's review page.
Fingerprints, tags, zoom and the AI description of attached photos are then handled by the enrich job.
Originals are never modified or moved. Resumable, sharded.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, SHARD, SHARDS, WORKERS, RUN_SECONDS
"""
import hashlib
import io
import os
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None
try:
    import pillow_heif  # noqa
    pillow_heif.register_heif_opener()
except Exception:
    pass

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "1"))
WORKERS = int(os.environ.get("WORKERS", "4"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "20400"))
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
T0 = time.time()
CODE = re.compile(r"(?:^|[^A-Za-z])([A-Za-z]{4}\d{3,})(?!\d)")
RAW = {"cr2", "cr3", "nef", "arw", "dng", "3fr", "fff", "iiq", "raf", "orf"}
_clip = None
import threading  # noqa: E402
_lock = threading.Lock()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rpc(name, body):
    for i in range(5):
        try:
            r = requests.post(f"{SB_URL}/rest/v1/rpc/{name}", headers=H, json=body, timeout=300)
            if r.ok:
                return r.json() if r.text.strip() else None
            log("rpc", name, r.status_code, r.text[:200])
        except requests.RequestException as e:
            log("rpc net", type(e).__name__)
        time.sleep(3 * (i + 1))
    raise RuntimeError(name)


def patch(path, row):
    for i in range(5):
        r = requests.patch(f"{SB_URL}/rest/v1/intake_files", params={"path": f"eq.{path}"}, timeout=60,
                           headers={**H, "Prefer": "return=minimal"}, json={**row, "updated_at": "now()"})
        if r.ok:
            return
        log("patch", r.status_code, r.text[:200])
        time.sleep(3 * (i + 1))


def put(bucket, key, data, ctype):
    r = requests.post(f"{SB_URL}/storage/v1/object/{bucket}/{key}", data=data, timeout=900,
                      headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": ctype, "x-upsert": "true"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"upload {bucket}/{key}: {r.status_code} {r.text[:120]}")


def candidates(path, meta):
    """Codes in priority order: file name, folders (nearest first), embedded photo data."""
    parts = [p for p in path.split("/")[2:] if p]  # drop 'intake/<batch>'
    out = []
    if parts:
        stem = re.sub(r"\.[^.]+$", "", parts[-1])
        out += [("filename", c.upper()) for c in CODE.findall(stem)]
        for folder in reversed(parts[:-1]):
            out += [("folder", c.upper()) for c in CODE.findall(folder)]
    for k in ("title", "description", "keywords", "caption", "object_name"):
        v = meta.get(k)
        if v:
            out += [("xmp", c.upper()) for c in CODE.findall(" " + (" ".join(v) if isinstance(v, list) else str(v)))]
    seen, uniq = set(), []
    for src, c in out:
        if c not in seen:
            seen.add(c)
            uniq.append((src, c))
    return uniq


def embedded_meta(im):
    meta = {}
    try:
        exif = im.getexif()
        for tag, key in ((270, "description"), (315, "artist"), (306, "date"), (272, "camera"), (33432, "copyright")):
            if exif.get(tag):
                meta[key] = str(exif.get(tag))[:500]
    except Exception:
        pass
    try:
        x = im.getxmp() if hasattr(im, "getxmp") else {}
        desc = (x.get("xmpmeta", {}).get("RDF", {}).get("Description", {})) if isinstance(x, dict) else {}
        if isinstance(desc, list):
            desc = desc[0] if desc else {}

        def text(v):
            if isinstance(v, dict):
                for k in ("Alt", "Bag", "Seq"):
                    if k in v:
                        li = v[k].get("li") if isinstance(v[k], dict) else None
                        if isinstance(li, list):
                            return [text(i) for i in li]
                        return text(li)
                return v.get("text") or ""
            return v if isinstance(v, str) else ""
        for src, key in (("title", "title"), ("description", "description"), ("subject", "keywords"), ("Headline", "caption")):
            if desc.get(src):
                meta[key] = text(desc[src])
    except Exception:
        pass
    return meta


def clip_embed(im):
    global _clip
    import open_clip
    import torch
    with _lock:
        if _clip is None:
            model, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
            model.eval()
            _clip = (model, pre)
    model, pre = _clip
    with _lock, torch.no_grad():
        f = model.encode_image(pre(im.convert("RGB")).unsqueeze(0))
        f = f / f.norm(dim=-1, keepdim=True)
    return f[0].tolist()


def open_image(data, ext):
    if ext in RAW:
        import rawpy
        with rawpy.imread(io.BytesIO(data)) as raw:
            return Image.fromarray(raw.postprocess(use_camera_wb=True, half_size=True))
    im = Image.open(io.BytesIO(data))
    try:
        im.seek(0)
    except Exception:
        pass
    return im


def to_rgb(im):
    im = ImageOps.exif_transpose(im)
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        im = im.point(lambda v: v / 256).convert("L")
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def jpeg(im, px):
    c = im.copy()
    c.thumbnail((px, px))
    b = io.BytesIO()
    c.save(b, "JPEG", quality=88, optimize=True, progressive=True)
    return b.getvalue(), c.size


def attach(rec, object_guid, display, thumb, w, h, kind):
    g = str(uuid.uuid4()).upper()
    row = {"guid": g, "object_guid": object_guid, "filename": rec["path"].split("/")[-1][:255], "kind": kind,
           "display_path": display, "thumb_path": thumb, "original_path": rec["path"], "width": w, "height": h,
           "caption": None, "media_role": None}
    r = requests.post(f"{SB_URL}/rest/v1/media_uploads", headers={**H, "Prefer": "return=minimal"}, json=row, timeout=60)
    if not r.ok:
        raise RuntimeError(f"attach {r.status_code} {r.text[:150]}")
    return g


def one(rec):
    path, kind, ext = rec["path"], rec["kind"], (rec.get("ext") or "")
    fid = hashlib.sha1(path.encode()).hexdigest()[:20]
    try:
        r = requests.get(f"{SB_URL}/storage/v1/object/archive/{path}", headers=H, timeout=1800)
        if r.status_code != 200:
            patch(path, {"status": "error", "error": f"original not readable ({r.status_code})"})
            return "error"
        data = r.content
        meta, upd = {}, {}
        display = rec.get("display_path")
        thumb = rec.get("thumb_path")
        w, h = rec.get("width"), rec.get("height")
        suggestions = None
        if kind == "photo":
            im = open_image(data, ext)
            meta = embedded_meta(im)
            im = to_rgb(im)
            w, h = im.size
            if not display:
                d, _ = jpeg(im, 1600)
                t, _ = jpeg(im, 400)
                display, thumb = f"intake/{fid}-display.jpg", f"intake/{fid}-thumb.jpg"
                put("web", display, d, "image/jpeg")
                put("web", thumb, t, "image/jpeg")
            try:
                small = im.copy()
                small.thumbnail((400, 400))
                suggestions = rpc("intake_similar", {"p_embedding": clip_embed(small), "p_k": 5})
            except Exception as e:
                log("similar failed", type(e).__name__, str(e)[:100])
        upd.update({"display_path": display, "thumb_path": thumb, "width": w, "height": h})
        if meta:
            upd["meta"] = meta
        if suggestions is not None:
            upd["suggestions"] = suggestions

        obj, code, src = rec.get("object_guid"), rec.get("code_found"), rec.get("code_source")
        if not obj:
            cands = candidates(path, meta)
            if cands:
                found = rpc("intake_codes", {"p_codes": [c for _, c in cands]}) or {}
                for s, c in cands:
                    if c in found:
                        obj, code, src = found[c], c, s
                        break
        upd.update({"code_found": code, "code_source": src, "object_guid": obj})
        if not obj:
            patch(path, {**upd, "status": "needs_review"})
            return "review"
        if kind in ("video", "document"):
            ctype = {"pdf": "application/pdf", "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}.get(ext)
            if not ctype:
                patch(path, {**upd, "status": "needs_review", "error": f"{ext} film needs converting to MP4 before it can play on the site"})
                return "review"
            web_key = f"intake/{fid}.{ext}"
            put("web", web_key, data, ctype)
            display = thumb = web_key
            upd.update({"display_path": display, "thumb_path": thumb})
        g = attach(rec, obj, display, thumb, w if kind == "photo" else None, h if kind == "photo" else None, kind)
        patch(path, {**upd, "status": "attached", "upload_guid": g, "error": None})
        return "attached"
    except Exception as e:
        patch(path, {"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"})
        return "error"


def main():
    if SHARD == 0:
        log("new files registered:", rpc("intake_scan", {}))
    tally = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        while RUN_SECONDS - (time.time() - T0) > 300:
            batch = rpc("intake_batch", {"p_shard": SHARD, "p_shards": SHARDS, "p_limit": WORKERS * 4})
            if not batch:
                log("nothing left")
                break
            for res in ex.map(one, batch):
                tally[res] = tally.get(res, 0) + 1
            log("intake", tally)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
