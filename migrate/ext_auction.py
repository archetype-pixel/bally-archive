"""Auction records (Christie's sold lots) collected via Firecrawl into DATA/christies_*.jsonl.
AI filter keeps only the Swiss luxury house Bally (posters, shoes, bags, advertising), never pinball/rum/places/people named Bally.
Stores a 400px reference thumb (credit Christie's) plus hammer price and sale details; the source page is linked."""
import glob, hashlib, html, io, json, os, re, sys, time
import anthropic, requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
DATA = os.environ.get("DATA", "data/external")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
UA = {"User-Agent": "Mozilla/5.0 (BallyHeritageArchive research)"}
WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
client = anthropic.Anthropic(max_retries=8, default_headers={"anthropic-workspace-id": WS} if WS else None)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


TOOL = {"name": "classify", "description": "Classify auction lots.", "input_schema": {"type": "object", "properties": {
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "i": {"type": "integer"},
        "relevance": {"type": "number", "description": "0 to 1: is a Bally item from the Swiss shoe and luxury house (C. F. Bally, Schönenwerd) a principal or named part of this lot? 0 for Bally pinball/arcade/gaming/Midway, J. Bally rum, Irish places (Bally-), Bally the carver, clockmakers, gunsmiths (Bally, Lausanne rifles), painters or people named Bally, Balli/Ballie."},
        "kind": {"type": "string", "enum": ["poster", "advertisement", "product", "document", "other"]},
        "year": {"type": "integer", "description": "Year of the object if stated, else 0."},
        "artist": {"type": "string", "description": "Poster artist or maker if stated, else empty."},
        "title_en": {"type": "string", "description": "Short English catalogue title, e.g. 'Bally poster by Bernard Villemot, 1982'."}},
        "required": ["i", "relevance", "kind", "year", "artist", "title_en"]}}}, "required": ["items"]}}


def classify(batch):
    lines = [f"[{i}] {b.get('title') or ''} | artist {b.get('artist') or ''} | {(b.get('description') or '')[:400]}" for i, b in enumerate(batch)]
    for _ in range(3):
        m = client.messages.create(model="claude-haiku-5-5", max_tokens=8000, tools=[TOOL], tool_choice={"type": "tool", "name": "classify"},
                                   messages=[{"role": "user", "content": "Classify each auction lot:\n" + "\n".join(lines)}])
        for b in m.content:
            if b.type == "tool_use":
                return {x["i"]: x for x in b.input["items"]}
    return {}


def store_thumb(house, key, url):
    """Prefer a thumbnail pre-fetched into DATA/auction_images/<house>/<key>.jpg; else one quick direct try."""
    local = f"{DATA}/auction_images/{house}/{key}.jpg"
    data = None
    if os.path.exists(local):
        data = open(local, "rb").read()
    elif url and "NoImage" not in url and "WineLot" not in url:
        try:
            r = requests.get(url, headers=UA, timeout=(5, 15))
            r.raise_for_status(); data = r.content
        except Exception as e:
            log("image fail", house, key, type(e).__name__)
    if not data:
        return None, None, None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((400, 400))
        b = io.BytesIO(); im.save(b, "JPEG", quality=84)
        path = f"external/{house}/{key}.jpg"
        x = requests.post(f"{SB}/storage/v1/object/web/{path}", data=b.getvalue(), timeout=120,
                          headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "image/jpeg", "x-upsert": "true", "cache-control": "max-age=31536000"})
        x.raise_for_status()
        return path, im.width, im.height
    except Exception as e:
        log("image store fail", house, key, type(e).__name__)
        return None, None, None


def norm_christies(l):
    sale = l.get("sale") or {}
    return {"house": "christies", "house_name": "Christie's", "lot_id": l["object_id"], "url": l["url"],
            "title": " | ".join(x for x in [l.get("title_primary"), l.get("title_secondary")] if x),
            "description": strip(l.get("description_html")), "estimate": l.get("estimate_text"), "result": l.get("price_realised_text"),
            "sale_name": None, "sale_number": l.get("sale_number"), "lot_number": l.get("lot_number"),
            "sale_date": (l.get("end_date") or "")[:10], "location": sale.get("location"), "image_url": l.get("image_url")}


def load_lots():
    lots, seen = [], set()
    for f in sorted(glob.glob(f"{DATA}/christies_*.jsonl")) + sorted(glob.glob(f"{DATA}/auction/*.jsonl")):
        for line in open(f):
            if not line.strip():
                continue
            l = json.loads(line)
            l = norm_christies(l) if "object_id" in l else l
            k = (l["house"], str(l["lot_id"]))
            if k not in seen:
                seen.add(k); lots.append(l)
    return lots


def main():
    only = os.environ.get("HOUSES")
    lots = load_lots()
    if only:
        lots = [l for l in lots if l["house"] in only.split(",")]
    log("lots", len(lots))
    houses = {}
    for l in lots:
        houses.setdefault(l["house"], l.get("house_name") or l["house"])
    for h, name in houses.items():
        requests.post(f"{SB}/rest/v1/ext_sources", headers={**H, "Prefer": "resolution=ignore-duplicates,return=minimal"},
                      json={"id": h, "name": f"{name} auction results", "url": None, "about": f"Past auction lots of Bally items at {name}, with estimates and results. Small reference images only; full photographs on the lot page.",
                            "license_default": "Reference image, rights reserved", "sort": 30}, timeout=60)
    kept = []
    for i in range(0, len(lots), 40):
        batch = lots[i:i + 40]
        res = classify(batch)
        for j, b in enumerate(batch):
            c = res.get(j)
            if c and c["relevance"] >= 0.6:
                b["_c"] = c; kept.append(b)
    log("relevant", len(kept))
    rows = []
    for b in kept:
        c = b["_c"]
        key = re.sub(r"[^0-9A-Za-z.-]", "", str(b["lot_id"])).replace(".", "-")[:80]
        p, w, h = store_thumb(b["house"], key, b.get("image_url"))
        desc = (b.get("description") or "")[:1500]
        bits = [x for x in [b.get("medium"), b.get("dimensions"), ("printed by " + b["printer"]) if b.get("printer") else None] if x]
        if bits:
            desc += " " + "; ".join(bits) + "."
        name = houses[b["house"]]
        desc += f" Offered at {name}" + (f", {b['location']}" if b.get("location") else "") + (f", {b['sale_date']}" if b.get("sale_date") else "")
        desc += (f", {b['sale_name']}" if b.get("sale_name") else "") + (f", lot {b['lot_number']}" if b.get("lot_number") else "") + "."
        desc += f" Result: {b['result']}." if b.get("result") else (f" Estimate: {b['estimate']}." if b.get("estimate") else "")
        yr = c["year"] or b.get("year") or None
        rows.append({"id": f"{b['house']}:{b['lot_id']}", "source": b["house"], "source_id": str(b["lot_id"]), "source_url": b["url"],
                     "title": c["title_en"], "description": desc[:2200], "kind": c["kind"], "date_text": b.get("object_date") or (str(yr) if yr else None),
                     "year": yr, "creator": c["artist"] or b.get("artist") or None, "license": "Reference image, rights reserved",
                     "credit": name, "image_url": b.get("image_url"), "image_path": p, "thumb_path": p, "width": w, "height": h,
                     "relevance": c["relevance"], "ai_status": "filtered",
                     "data": {k: b.get(k) for k in ("result", "estimate", "currency", "sale_date", "location", "sale_name", "sale_number", "lot_number", "medium", "dimensions", "printer")}})
    for i in range(0, len(rows), 200):
        r = requests.post(f"{SB}/rest/v1/ext_records", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, json=rows[i:i + 200], timeout=120)
        if not r.ok:
            log("upsert", r.status_code, r.text[:300]); r.raise_for_status()
    from collections import Counter
    log("stored", len(rows), dict(Counter(r["source"] for r in rows)), dict(Counter(r["kind"] for r in rows)))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
