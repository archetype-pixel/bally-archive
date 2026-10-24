"""Load WIPO Global Brand Database trademarks and Global Design Database designs (collected via Firecrawl) into ext_records.
Input: DATA/registry/wipo_brands.jsonl, DATA/registry/wipo_designs.jsonl. Only Bally-house applicants are kept."""
import io, json, os, re, sys, time
import requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
DATA = os.environ.get("DATA", "data/external")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
UA = {"User-Agent": "BallyHeritageArchive/1.0 (non-commercial research archive; archetypegroup@gmail.com)"}
KEEP = re.compile(r"bally\s+schuhfabrik|c\.\s*f\.\s*bally|bally\s+international|bally\s+schweiz|bally\s+wiener|bally\s+shoe\s+factor|bally\s+holding|bally\s+management", re.I)
DROP = re.compile(r"gaming|wulff|midway|manufacturing|technolog|fitness|entertainment|ricosta|rohde|rhum", re.I)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def put_img(url, path):
    if not url:
        return None, None, None
    try:
        r = requests.get(url, headers=UA, timeout=(5, 30))
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGBA")
        b = io.BytesIO(); im.save(b, "PNG", optimize=True)
        x = requests.post(f"{SB}/storage/v1/object/web/{path}", data=b.getvalue(), timeout=120,
                          headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "image/png", "x-upsert": "true", "cache-control": "max-age=31536000"})
        x.raise_for_status()
        return path, im.width, im.height
    except Exception as e:
        log("image fail", path, type(e).__name__)
        return None, None, None


def ok(applicants):
    # files are pre-filtered to Bally-house applicants (many spellings); only guard against gaming/fitness leaks
    a = " ".join(applicants or [])
    return "bally" in a.lower() and not DROP.search(a)


def year(s):
    m = re.match(r"(\d{4})", str(s or ""))
    return int(m.group(1)) if m else None


def main():
    rows = []
    f = f"{DATA}/registry/wipo_brands.jsonl"
    if os.path.exists(f):
        for line in open(f):
            if not line.strip():
                continue
            t = json.loads(line)
            if not ok(t.get("applicants")):
                continue
            st = t["st13"]
            name = ", ".join(t.get("brand_names") or []) or "(figurative mark)"
            thumb = (t.get("thumbnail_urls") or [None])[0]
            p, w, h = put_img(thumb, f"external/wipo-tm/{st}.png")
            desc = (f"{t.get('office')} trademark, {t.get('mark_feature') or ''} mark. Holder: {'; '.join(sorted(set(t.get('applicants') or [])))}. "
                    f"Filed {t.get('application_date') or '?'}" + (f", registered {t['registration_date']}" if t.get("registration_date") else "")
                    + (f" (no. {t['registration_number']})" if t.get("registration_number") else "") + f". Status: {t.get('status')}"
                    + (f", expiry {t['expiry_date']}" if t.get("expiry_date") else "") + ". Nice classes: " + ", ".join(str(c) for c in (t.get("nice_classes") or []))
                    + (". Designations: " + ", ".join(t.get("designations") or []) if len(t.get("designations") or []) > 1 else "") + ".")
            rows.append({"id": f"wipo-tm:{st}", "source": "wipo-tm", "source_id": st, "source_url": t.get("record_url"),
                         "title": f"{t.get('office')} trademark “{name}”", "description": desc, "kind": "trademark",
                         "date_text": t.get("application_date"), "year": year(t.get("application_date")), "creator": (t.get("applicants") or [None])[0],
                         "license": "Public record", "credit": "WIPO Global Brand Database", "image_url": thumb, "image_path": p, "thumb_path": p,
                         "width": w, "height": h, "relevance": 1.0, "ai_status": "curated", "data": t})
    f = f"{DATA}/registry/wipo_designs.jsonl"
    if os.path.exists(f):
        for line in open(f):
            if not line.strip():
                continue
            d = json.loads(line)
            holder = d.get("holder") or d.get("holders") or ""
            holder = "; ".join(holder) if isinstance(holder, list) else holder
            if not ok([holder]):
                continue
            num = str(d.get("registration_number") or d.get("design_number") or d.get("id"))
            sid = re.sub(r"[^0-9A-Za-z-]", "", f"{d.get('office','')}-{num}")
            img = d.get("image_url") or d.get("thumbnail_url")
            p, w, h = put_img(img, f"external/wipo-design/{sid}.png")
            date = d.get("registration_date") or d.get("filing_date") or d.get("date")
            rows.append({"id": f"wipo-design:{sid}", "source": "wipo-design", "source_id": sid, "source_url": d.get("record_url") or d.get("url"),
                         "title": f"{d.get('office') or ''} registered design {num}: {d.get('product') or d.get('title') or 'design'}".strip(),
                         "description": f"Holder: {holder}. " + " ".join(f"{k.replace('_',' ').capitalize()}: {v}." for k, v in d.items() if k in ("filing_date", "registration_date", "status", "product", "locarno_class") and v),
                         "kind": "design", "date_text": date, "year": year(date), "creator": holder or None, "license": "Public record",
                         "credit": "WIPO Global Design Database", "image_url": img, "image_path": p, "thumb_path": p, "width": w, "height": h,
                         "relevance": 1.0, "ai_status": "curated", "data": d})
    keys = set().union(*[r.keys() for r in rows]) if rows else set()
    rows = [{k: r.get(k) for k in keys} for r in rows]
    for i in range(0, len(rows), 200):
        r = requests.post(f"{SB}/rest/v1/ext_records", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, json=rows[i:i + 200], timeout=120)
        if not r.ok:
            log("upsert", r.status_code, r.text[:300]); r.raise_for_status()
    from collections import Counter
    log("stored", len(rows), dict(Counter(r["source"] for r in rows)))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
