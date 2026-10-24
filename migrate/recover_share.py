"""Copy photos that sit on the Azure share but were never linked to their Collection object (table img_recovery_plan).
Stills (close-ups) and frame 001 of a 360 shoot become new asset records (source 'recovered_share'); full 24-frame
shoots become 360 spins (session_id = 900000000 + SN). The first still, or frame 001 when there is none, is set as
the object's main image through object_overrides (catalogue rows are never touched). Env: AZ_*, SUPABASE_*."""
import io, os, sys, time, uuid
from collections import defaultdict
from azure.storage.fileshare import ShareServiceClient
from PIL import Image, ImageOps
import requests

Image.MAX_IMAGE_PIXELS = None
SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
M_ID = "b9b5e9b2-2174-4f58-b2ef-0d6a93e766d3"
share = ShareServiceClient(account_url=f"https://{os.environ['AZ_ACCOUNT']}.file.core.windows.net",
                           credential={"account_name": os.environ["AZ_ACCOUNT"], "account_key": os.environ["AZ_KEY"]}
                           ).get_share_client(os.environ["AZ_SHARE"])


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rest(method, table, **kw):
    r = requests.request(method, f"{SB}/rest/v1/{table}", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=120, **kw)
    if not r.ok:
        raise RuntimeError(f"{table} {r.status_code} {r.text[:200]}")
    return r


def put(bucket, path, data, ctype="image/jpeg"):
    r = requests.post(f"{SB}/storage/v1/object/{bucket}/{path}", data=data, timeout=300,
                      headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": ctype, "x-upsert": "true", "cache-control": "max-age=31536000"})
    r.raise_for_status()


def jpg(im, box, q=86):
    im = im.copy(); im.thumbnail(box)
    b = io.BytesIO(); im.save(b, "JPEG", quality=q, optimize=True, progressive=True)
    return b.getvalue()


def fetch(path):
    raw = share.get_file_client(path).download_file().readall()
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    return raw, im


def make_asset(obj, path, name, order):
    raw, im = fetch(path)
    g = str(uuid.uuid4()).upper()
    put("archive", f"originals/{g}/{name}", raw)
    put("web", f"display/{g}.jpg", jpg(im, (1600, 1600)))
    put("web", f"thumb/{g}.jpg", jpg(im, (400, 400), 82))
    rest("POST", "assets", json={"guid": g, "filename": name, "asset_type": "ASSET", "description": "Recovered from the archive file share (never linked in the old system)",
                                 "raw": {"recovered_from": path}})
    rest("POST", "file_copy", json={"asset_id": -(uuid.UUID(g).int % 10**12), "guid": g, "name": name, "azure_path": path, "azure_size": len(raw),
                                    "match_note": "recovered: on share, not linked", "status": "copied", "dest_original": f"originals/{g}/{name}",
                                    "dest_display": f"display/{g}.jpg", "dest_thumb": f"thumb/{g}.jpg", "width": im.width, "height": im.height})
    rest("POST", "object_assets", json={"object_guid": obj, "asset_guid": g, "sort_order": order, "source": "recovered_share"})
    return g, im


def main():
    plan = requests.get(f"{SB}/rest/v1/img_recovery_plan", headers=H, params={"select": "*", "order": "object_guid,name"}, timeout=60).json()
    by = defaultdict(list)
    for p in plan:
        by[p["object_guid"].upper()].append(p)
    for obj, files in by.items():
        stills = [f for f in files if f["cu"]]
        shoots = defaultdict(dict)
        for f in files:
            if f["frame"] is not None:
                shoots[f["sn"]][f["frame"]] = f
        order, main = 100, None
        done = {a["raw"]["recovered_from"]: a["guid"] for a in requests.get(f"{SB}/rest/v1/assets", headers=H, timeout=60,
                params={"select": "guid,raw", "raw->>recovered_from": "not.is.null"}).json()}
        for f in stills:
            if f["path"] in done:
                g = done[f["path"]]
            else:
                g, _ = make_asset(obj, f["path"], f["name"], order)
            order += 1
            main = main or g
        for sn, fr in shoots.items():
            if len(fr) < 20:
                continue
            sid = 900000000 + int(sn)
            idx = sorted(fr)
            for k, n in enumerate(idx):
                _, im = fetch(fr[n]["path"])
                put("web", f"display/spins/{sid}/{k}.jpg", jpg(im, (1000, 1000), 84))
            rest("POST", "spins", json={"session_id": sid, "object_guid": obj, "shot_type": "ROTATION", "folder_name": fr[idx[0]]["path"].split("/")[-3],
                                        "frames": len(idx), "storage_prefix": f"display/spins/{sid}"})
            log(obj, "spin", sid, len(idx), "frames")
            rest("POST", "object_overrides", json={"object_guid": obj, "field": "has_spin", "value": True, "updated_by": M_ID})
            if not main:
                g, _ = make_asset(obj, fr[idx[0]]["path"], fr[idx[0]]["name"], order); order += 1
                main = g
        if main:
            rest("POST", "object_overrides", json={"object_guid": obj, "field": "main_asset_guid", "value": main, "updated_by": M_ID})
            rest("POST", "object_edits", json={"object_guid": obj, "field": "main_asset_guid", "old_value": None, "new_value": main, "user_id": M_ID})
        log(obj, "stills", len(stills), "shoots", len(shoots), "main", main)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:400])
        sys.exit(1)
