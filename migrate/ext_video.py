"""Load the YouTube/Vimeo Bally video catalogue (DATA/video/videos.jsonl, collected via Firecrawl) into ext_records.
Stores metadata plus a copied thumbnail; the video itself plays from the platform (embed)."""
import hashlib, io, json, os, re, sys, time
import requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
DATA = os.environ.get("DATA", "data/external")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
UA = {"User-Agent": "BallyHeritageArchive/1.0 (non-commercial research archive; archetypegroup@gmail.com)"}
NOW = 2026


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def thumb(url, src, vid):
    if not url:
        return None, None, None
    for u in [url, url.replace("hq720", "hqdefault")]:
        try:
            r = requests.get(u, headers=UA, timeout=(5, 30))
            r.raise_for_status()
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            im.thumbnail((1280, 1280))
            b = io.BytesIO(); im.save(b, "JPEG", quality=84)
            path = f"external/{src}/{re.sub(r'[^0-9A-Za-z_-]', '', vid)}.jpg"
            x = requests.post(f"{SB}/storage/v1/object/web/{path}", data=b.getvalue(), timeout=120,
                              headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "image/jpeg", "x-upsert": "true", "cache-control": "max-age=31536000"})
            x.raise_for_status()
            return path, im.width, im.height
        except Exception as e:
            log("thumb fail", vid, type(e).__name__)
    return None, None, None


def year_of(p):
    p = str(p or "")
    m = re.match(r"(\d{4})", p)
    if m:
        return int(m.group(1))
    m = re.match(r"(\d+)\s+year", p)
    if m:
        return NOW - int(m.group(1))
    if re.search(r"month|week|day|hour", p):
        return NOW
    return None


def main():
    rows = []
    for line in open(f"{DATA}/video/videos.jsonl"):
        if not line.strip():
            continue
        v = json.loads(line)
        src = v["platform"]
        p, w, h = thumb(v.get("thumbnail_url"), src, v["video_id"])
        yr = year_of(v.get("published"))
        desc = (v.get("description") or "").strip()
        meta = f"{'Official Bally channel' if v.get('official') else 'Channel: ' + (v.get('channel') or '?')}. Duration {v.get('duration') or '?'}."
        if v.get("published") and not re.match(r"\d{4}", str(v["published"])):
            meta += f" Uploaded {v['published']} (as of Oct 2026)."
        rows.append({"id": f"{src}:{v['video_id']}", "source": src, "source_id": v["video_id"], "source_url": v["url"],
                     "title": v["title"], "description": (desc + " " + meta).strip()[:2000], "kind": "film",
                     "date_text": v.get("published"), "year": yr, "creator": v.get("channel"),
                     "license": "© Bally" if v.get("official") else "Rights per uploader / rights holder",
                     "credit": f"{'YouTube' if src == 'youtube' else 'Vimeo'}: {v.get('channel') or ''}",
                     "image_url": v.get("thumbnail_url"), "image_path": p, "thumb_path": p, "width": w, "height": h,
                     "relevance": 0.6 if v.get("uncertain") else 1.0, "ai_status": "curated",
                     "data": {k: v.get(k) for k in ("platform", "video_id", "channel_id", "duration", "views", "kind", "official", "format", "uncertain")}})
    for i in range(0, len(rows), 200):
        r = requests.post(f"{SB}/rest/v1/ext_records", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, json=rows[i:i + 200], timeout=120)
        if not r.ok:
            log("upsert", r.status_code, r.text[:300]); r.raise_for_status()
    from collections import Counter
    log("stored", len(rows), dict(Counter(r["source"] for r in rows)), "with thumbs", sum(1 for r in rows if r["thumb_path"]))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
