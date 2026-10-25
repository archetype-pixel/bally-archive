"""Read camera EXIF from a sample of original shoot files on the Azure share to identify the equipment used."""
import io, os, random, json
from collections import Counter
import requests
from PIL import Image, ExifTags
from azure.storage.fileshare import ShareServiceClient
SB = os.environ["SUPABASE_URL"].rstrip("/"); KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
share = ShareServiceClient(account_url=f"https://{os.environ['AZ_ACCOUNT']}.file.core.windows.net",
    credential={"account_name": os.environ["AZ_ACCOUNT"], "account_key": os.environ["AZ_KEY"]}).get_share_client(os.environ["AZ_SHARE"])
TAGS = {v: k for k, v in ExifTags.TAGS.items()}
def paths(like, n):
    r = requests.get(f"{SB}/rest/v1/azure_files", headers=H, params={"select": "path", "path": f"like.{like}", "limit": 4000}, timeout=120).json()
    random.seed(1); return [x["path"] for x in random.sample(r, min(n, len(r)))]
want = ["Make", "Model", "LensModel", "FocalLength", "FNumber", "ExposureTime", "ISOSpeedRatings", "DateTimeOriginal", "Software", "BodySerialNumber"]
out = Counter(); samples = []
for group, like in [("raw stills", "DAM/PRJ/SHOOTING/*1. Originali*"), ("processed", "DAM/PRJ/SHOOTING/*2. PostProcessed*"), ("heritage scans", "DAM/PRJ/HERITAGE/*")]:
    for p in paths(like, 25):
        try:
            sc = share.get_file_client(p).download_file(offset=0, length=262144).readall()
            im = Image.open(io.BytesIO(sc)); ex = im.getexif()
            d = {k: str(ex.get(TAGS[k])) for k in want if TAGS.get(k) in ex}
            try:
                sub = ex.get_ifd(0x8769); d.update({k: str(sub.get(TAGS[k])) for k in want if TAGS.get(k) in sub})
            except Exception: pass
            d["size"] = f"{im.width}x{im.height}"
            out[(group, d.get("Make"), d.get("Model"), d.get("LensModel"), d.get("Software"), d["size"])] += 1
            if len(samples) < 6: samples.append((group, p.split('/')[-1], d))
        except Exception as e:
            out[(group, "unreadable", type(e).__name__)] += 1
for k, v in out.most_common(): print(v, k)
for s in samples: print(json.dumps(s))
