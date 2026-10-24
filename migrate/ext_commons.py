"""External sources: Bally media on Wikimedia Commons (includes ETH Library image archive uploads).
1) collect candidates from Bally categories and searches, 2) AI relevance filter (is this the Swiss shoe company Bally?),
3) copy kept images (1600px + 400px thumb) into web/external/commons/, 4) upsert ext_records.
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID"""
import hashlib, html, io, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
import anthropic, requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "BallyHeritageArchive/1.0 (research archive; archetypegroup@gmail.com)"}
WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
client = anthropic.Anthropic(max_retries=8, default_headers={"anthropic-workspace-id": WS} if WS else None)
FILTER_MODEL = os.environ.get("FILTER_MODEL", "claude-haiku-5-5")

CATEGORIES = ["Category:Bally (company)", "Category:Bally Schuhfabriken", "Category:Bally shoes", "Category:Bally (brand)",
              "Category:Bally-Park", "Category:Carl Franz Bally", "Category:Ballyana", "Category:Bally Schuhmuseum",
              "Category:Bally advertisements", "Category:Bally posters"]
SEARCHES = ["Bally Schuhfabrik", "Bally Schuhfabriken", "Bally Schuh", "Bally Schuhe", "Bally shoes", "Bally shoe", "Bally Schönenwerd",
            "Schönenwerd Bally", "Bally Dottikon", "Bally Aarau Schuh", "Bally Plakat", "Bally poster", "Bally affiche", "Bally Arola",
            "Bally Capitol", "Bally Vasano", "Bally Scribe", "Carl Franz Bally", "Eduard Bally", "Iwan Bally", "Max Bally", "Ballyana",
            "Bally-Park", "Bally store", "Bally boutique", "Bally Schuhmuseum", "Bally Laubi", "Bally Villemot", "Bally Cardinaux",
            "ETH-BIB Bally", "Bally Gösgen", "Bally Schöftland", "Bally Brugg", "Bally Fabrik", "Bally Bottine"]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def api(params):
    for t in range(5):
        try:
            r = requests.get(API, params={**params, "format": "json", "formatversion": 2}, headers=UA, timeout=60)
            if r.ok:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(2 * (t + 1))
    return {}


def cat_files(cat, depth=2, seen=None):
    seen = seen if seen is not None else set()
    if cat in seen or depth < 0:
        return []
    seen.add(cat)
    out, cont = [], {}
    while True:
        d = api({"action": "query", "list": "categorymembers", "cmtitle": cat, "cmlimit": 500, "cmtype": "file|subcat", **cont})
        for m in d.get("query", {}).get("categorymembers", []):
            if m["ns"] == 6:
                out.append(m["title"])
            elif m["ns"] == 14:
                out += cat_files(m["title"], depth - 1, seen)
        if "continue" not in d:
            return out
        cont = d["continue"]


def search_files(q, cap=500):
    out, off = [], 0
    while off < cap:
        d = api({"action": "query", "list": "search", "srsearch": q, "srnamespace": 6, "srlimit": 100, "sroffset": off})
        hits = d.get("query", {}).get("search", [])
        out += [h["title"] for h in hits]
        if "continue" not in d or not hits:
            break
        off = d["continue"]["sroffset"]
    return out


def strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def info(titles):
    d = api({"action": "query", "titles": "|".join(titles), "prop": "imageinfo|categories", "iiprop": "url|size|extmetadata|mime",
             "iiurlwidth": 1600, "cllimit": 50})
    out = []
    for p in d.get("query", {}).get("pages", []):
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata", {})
        g = lambda k: strip(md.get(k, {}).get("value"))
        out.append({"title": p["title"], "pageid": p.get("pageid"), "url": ii.get("thumburl") or ii.get("url"), "orig": ii.get("url"),
                    "page_url": ii.get("descriptionurl"), "mime": ii.get("mime"), "w": ii.get("width"), "h": ii.get("height"),
                    "desc": g("ImageDescription")[:1500], "date": g("DateTimeOriginal") or g("DateTime"), "artist": g("Artist")[:300],
                    "license": g("LicenseShortName"), "license_url": g("LicenseUrl"), "credit": g("Credit")[:300],
                    "categories": [c["title"].replace("Category:", "") for c in p.get("categories", [])]})
    return out


FILTER_TOOL = {"name": "classify", "description": "Classify candidate files.", "input_schema": {"type": "object", "properties": {
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "i": {"type": "integer"},
        "relevance": {"type": "number", "description": "0 to 1: is this about the Swiss shoe and fashion company Bally (C. F. Bally, Bally Schuhfabriken, its factories, stores, products, advertising, people, the Bally family, Bally-Park, Ballyana)? 0 for Bally Sports, Bally casinos/pinball/fitness, Bally (Howrah, India), aircraft, unrelated people named Bally."},
        "kind": {"type": "string", "enum": ["factory", "store", "product", "poster", "advertisement", "person", "aerial", "document", "logo", "place", "other"]},
        "year": {"type": "integer", "description": "Best year if stated, else 0."},
        "title_en": {"type": "string", "description": "Short English catalogue title."}},
        "required": ["i", "relevance", "kind", "year", "title_en"]}}}, "required": ["items"]}}


def classify(batch):
    lines = [f"[{i}] {b['title']} | {b['desc'][:300]} | date {b['date']} | categories: {', '.join(b['categories'][:12])}" for i, b in enumerate(batch)]
    for _ in range(3):
        msg = client.messages.create(model=FILTER_MODEL, max_tokens=8000, tools=[FILTER_TOOL], tool_choice={"type": "tool", "name": "classify"},
                                     messages=[{"role": "user", "content": "Classify each Wikimedia Commons file:\n" + "\n".join(lines)}])
        for b in msg.content:
            if b.type == "tool_use":
                return {x["i"]: x for x in b.input["items"]}
    return {}


def put(data, path, ctype="image/jpeg"):
    r = requests.post(f"{SB}/storage/v1/object/web/{path}", data=data, timeout=120,
                      headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": ctype, "x-upsert": "true", "cache-control": "max-age=31536000"})
    r.raise_for_status()


def copy_image(rec):
    try:
        r = requests.get(rec["url"], headers=UA, timeout=120)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        im.thumbnail((1600, 1600))
        key = hashlib.sha1(rec["title"].encode()).hexdigest()[:16]
        b = io.BytesIO(); im.save(b, "JPEG", quality=86, optimize=True, progressive=True)
        put(b.getvalue(), f"external/commons/{key}.jpg")
        t = im.copy(); t.thumbnail((400, 400)); b2 = io.BytesIO(); t.save(b2, "JPEG", quality=82)
        put(b2.getvalue(), f"external/commons/thumbs/{key}.jpg")
        return f"external/commons/{key}.jpg", f"external/commons/thumbs/{key}.jpg", im.width, im.height
    except Exception as e:
        log("image fail", rec["title"][:80], type(e).__name__)
        return None, None, None, None


def main():
    titles = set()
    for c in CATEGORIES:
        f = cat_files(c)
        titles.update(f)
        log(c, len(f))
    for q in SEARCHES:
        f = search_files(q, 300)
        titles.update(f)
        log("search", q, len(f))
    titles = sorted(t for t in titles if re.search(r"\.(jpe?g|png|tiff?|gif|webp)$", t, re.I))
    log("candidates", len(titles))
    infos = []
    for i in range(0, len(titles), 50):
        infos += info(titles[i:i + 50])
    log("metadata", len(infos))
    kept = []
    batches = [infos[i:i + 40] for i in range(0, len(infos), 40)]
    with ThreadPoolExecutor(6) as ex:
        for batch, res in zip(batches, ex.map(classify, batches)):
            for i, b in enumerate(batch):
                c = res.get(i)
                if c:
                    b.update(c)
                    if c["relevance"] >= 0.5:
                        kept.append(b)
    log("relevant", len(kept), "of", len(infos))
    with ThreadPoolExecutor(8) as ex:
        paths = list(ex.map(copy_image, kept))
    rows = []
    for b, (p, t, w, h) in zip(kept, paths):
        if not p:
            continue
        yr = b.get("year") or (int(m.group(0)) if (m := re.search(r"\b(1[6-9]\d\d|20[0-2]\d)\b", b["date"] or "")) else None)
        rows.append({"id": f"commons:{b['pageid']}", "source": "commons", "source_id": b["title"], "source_url": b["page_url"],
                     "title": b.get("title_en") or b["title"].replace("File:", ""), "description": b["desc"] or None, "kind": b.get("kind"),
                     "date_text": b["date"] or None, "year": yr or None, "creator": b["artist"] or None,
                     "license": b["license"] or None, "credit": b["credit"] or b["artist"] or None, "image_url": b["orig"],
                     "image_path": p, "thumb_path": t, "width": w, "height": h, "relevance": b["relevance"],
                     "data": {"categories": b["categories"], "license_url": b["license_url"], "original_title": b["title"]}, "ai_status": "filtered"})
    for i in range(0, len(rows), 200):
        r = requests.post(f"{SB}/rest/v1/ext_records", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, json=rows[i:i + 200], timeout=120)
        if not r.ok:
            log("upsert", r.status_code, r.text[:300]); r.raise_for_status()
    from collections import Counter
    log("stored", len(rows), dict(Counter(r["kind"] for r in rows)))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
