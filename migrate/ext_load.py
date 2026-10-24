"""Load pre-collected external records (USPTO trademarks and patents) from DATA/uspto.json into ext_records."""
import io, json, os, sys, time
import requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
DATA = os.environ.get("DATA", "data/external")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
UA = {"User-Agent": "BallyHeritageArchive/1.0 (research archive)"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def put(data, path):
    r = requests.post(f"{SB}/storage/v1/object/web/{path}", data=data, timeout=120,
                      headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "image/png", "x-upsert": "true", "cache-control": "max-age=31536000"})
    r.raise_for_status()


def mark_image(url, key):
    try:
        r = requests.get(url, headers=UA, timeout=60)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGBA")
        b = io.BytesIO(); im.save(b, "PNG", optimize=True)
        put(b.getvalue(), f"external/uspto/{key}.png")
        return f"external/uspto/{key}.png", im.width, im.height
    except Exception as e:
        log("mark image fail", key, type(e).__name__)
        return None, None, None


def main():
    d = json.load(open(f"{DATA}/uspto.json"))
    rows = []
    for t in d["trademarks"]:
        sn = t["serial_number"]
        path, w, h = mark_image(t["mark_image_url"], sn) if t.get("mark_image_url") else (None, None, None)
        filed = (t.get("filed_date") or "")[:10]
        reg = t.get("registration_date")
        status = "live" if t.get("alive") else ("cancelled " + t["cancel_date"] if t.get("cancel_date") else "dead")
        rows.append({"id": f"uspto-tm:{sn}", "source": "uspto-tm", "source_id": sn, "source_url": f"https://tsdr.uspto.gov/#caseNumber={sn}&caseType=SERIAL_NO&searchType=statusSearch",
                     "title": f"US trademark “{t['wordmark']}”" + (f", reg. no. {t['registration_number']}" if t.get("registration_number") else ""),
                     "description": f"Owner: {'; '.join(t.get('owner_names') or [])}. Filed {filed}" + (f", registered {reg}" if reg else "") + f". Status: {status}. Goods: " + " ".join(t.get("goods_and_services") or [])[:1200],
                     "kind": "trademark", "date_text": filed, "year": int(filed[:4]) if filed else None, "creator": (t.get("owner_names") or [None])[0],
                     "license": "Public record", "credit": "United States Patent and Trademark Office", "image_url": t.get("mark_image_url"),
                     "image_path": path, "thumb_path": path, "width": w, "height": h, "relevance": 1.0, "ai_status": "curated", "data": t})
    for p in d["patents"]:
        num = p.get("document_id")
        pub = (p.get("date_published") or "")[:10]
        rows.append({"id": f"uspto-pat:{p['guid']}", "source": "uspto-pat", "source_id": num, "source_url": p.get("source_url"),
                     "title": f"US patent {num}: {p.get('invention_title')}", "description": f"Assignee: {', '.join(p.get('assignee_names') or [])}. Inventors: {p.get('inventors_short') or ''}. Filed {(p.get('application_filing_date') or '')[:10]}, published {pub}.",
                     "kind": "patent", "date_text": pub, "year": int(pub[:4]) if pub else None, "creator": ", ".join(p.get("assignee_names") or []),
                     "license": "Public record", "credit": "United States Patent and Trademark Office", "relevance": 1.0, "ai_status": "curated", "data": p})
    gp = f"{DATA}/gpatents_bally.json"
    if os.path.exists(gp):
        for p in json.load(open(gp)):
            num = p["publication_number"]
            date = p.get("priority_date") or p.get("filing_date") or p.get("publication_date") or p.get("grant_date") or ""
            img = None
            if p.get("thumbnail"):
                try:
                    rr = requests.get("https://patentimages.storage.googleapis.com/" + p["thumbnail"], headers=UA, timeout=60)
                    rr.raise_for_status()
                    im = Image.open(io.BytesIO(rr.content)).convert("RGB")
                    b = io.BytesIO(); im.save(b, "PNG", optimize=True)
                    put(b.getvalue(), f"external/patents/{num}.png")
                    img = (f"external/patents/{num}.png", im.width, im.height)
                except Exception as e:
                    log("patent image fail", num, type(e).__name__)
            rows.append({"id": f"patent:{num}", "source": "patents", "source_id": num, "source_url": f"https://patents.google.com/patent/{num}",
                         "title": f"Patent {num}: {p.get('title','').strip()}", "description": (p.get("snippet") or "").strip()[:1500] + f" Assignee: {p.get('assignee','').strip()}." + (f" Inventor: {p['inventor'].strip()}." if p.get("inventor") else ""),
                         "kind": "patent", "date_text": date or None, "year": int(date[:4]) if date[:4].isdigit() else None, "creator": (p.get("assignee") or "").strip() or None,
                         "license": "Public record", "credit": "Patent office record via Google Patents",
                         "image_url": ("https://patentimages.storage.googleapis.com/" + p["pdf"]) if p.get("pdf") else None,
                         "image_path": img[0] if img else None, "thumb_path": img[0] if img else None, "width": img[1] if img else None, "height": img[2] if img else None,
                         "relevance": 1.0, "ai_status": "curated", "data": p})
    keys = set().union(*[r.keys() for r in rows])
    rows = [{k: r.get(k) for k in keys} for r in rows]
    r = requests.post(f"{SB}/rest/v1/ext_records", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, json=rows, timeout=120)
    if not r.ok:
        log(r.status_code, r.text[:300]); r.raise_for_status()
    log("stored", len(rows))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
