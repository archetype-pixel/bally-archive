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
        "relevance": {"type": "number", "description": "0 to 1: is a Bally item from the Swiss shoe and luxury house (C. F. Bally, Schönenwerd) a principal or named part of this lot? 0 for Bally pinball/arcade/gaming, J. Bally rum, Irish places (Bally-), Bally the carver, clockmakers, painters or people named Bally, Balli/Ballie."},
        "kind": {"type": "string", "enum": ["poster", "advertisement", "product", "document", "other"]},
        "year": {"type": "integer", "description": "Year of the object if stated, else 0."},
        "artist": {"type": "string", "description": "Poster artist or maker if stated, else empty."},
        "title_en": {"type": "string", "description": "Short English catalogue title, e.g. 'Bally poster by Bernard Villemot, 1982'."}},
        "required": ["i", "relevance", "kind", "year", "artist", "title_en"]}}}, "required": ["items"]}}


def classify(batch):
    lines = [f"[{i}] {b['title_primary']} | {b.get('title_secondary') or ''} | {strip(b.get('description_html'))[:400]}" for i, b in enumerate(batch)]
    for _ in range(3):
        m = client.messages.create(model="claude-haiku-5-5", max_tokens=8000, tools=[TOOL], tool_choice={"type": "tool", "name": "classify"},
                                   messages=[{"role": "user", "content": "Classify each Christie's lot:\n" + "\n".join(lines)}])
        for b in m.content:
            if b.type == "tool_use":
                return {x["i"]: x for x in b.input["items"]}
    return {}


def thumb(url, key):
    if not url or "NoImage" in url or "WineLot" in url:
        return None, None, None
    try:
        r = requests.get(url, headers=UA, timeout=60)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        im.thumbnail((400, 400))
        b = io.BytesIO(); im.save(b, "JPEG", quality=84)
        path = f"external/christies/{key}.jpg"
        x = requests.post(f"{SB}/storage/v1/object/web/{path}", data=b.getvalue(), timeout=120,
                          headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "image/jpeg", "x-upsert": "true", "cache-control": "max-age=31536000"})
        x.raise_for_status()
        return path, im.width, im.height
    except Exception as e:
        log("image fail", key, type(e).__name__)
        return None, None, None


def main():
    lots, seen = [], set()
    for f in sorted(glob.glob(f"{DATA}/christies_*.jsonl")):
        for line in open(f):
            if line.strip():
                l = json.loads(line)
                if l["object_id"] not in seen:
                    seen.add(l["object_id"]); lots.append(l)
    log("lots", len(lots))
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
        key = re.sub(r"[^0-9A-Za-z.]", "", b["object_id"]).replace(".", "-")
        p, w, h = thumb(b.get("image_url"), key)
        price = b.get("price_realised_text")
        date = (b.get("end_date") or "")[:10]
        sale = b.get("sale") or {}
        desc = strip(b.get("description_html"))
        desc += f" Sold at Christie's {sale.get('location') or ''}, {date}, sale {b.get('sale_number')}, lot {b.get('lot_number')}."
        desc += f" Price realised: {price}." if price else f" Estimate: {b.get('estimate_text')}."
        rows.append({"id": f"christies:{b['object_id']}", "source": "christies", "source_id": b["object_id"], "source_url": b["url"],
                     "title": c["title_en"], "description": desc[:2000], "kind": c["kind"], "date_text": str(c["year"]) if c["year"] else None,
                     "year": c["year"] or None, "creator": c["artist"] or None, "license": "Reference image, rights reserved",
                     "credit": "Christie's", "image_url": b.get("image_url"), "image_path": p, "thumb_path": p, "width": w, "height": h,
                     "relevance": c["relevance"], "ai_status": "filtered",
                     "data": {"price_realised": price, "estimate": b.get("estimate_text"), "sale_date": date, "sale_location": sale.get("location"),
                              "sale_number": b.get("sale_number"), "lot_number": b.get("lot_number")}})
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
