"""Second, stricter AI check over every visible external record: is this really the Swiss luxury house Bally?
Uses Opus with the record text and, where present, its thumbnail. Hides clear non-matches (relevance 0.3) and stores
the verdict in data.verify. Env: SOURCES (optional comma list), MODEL (default claude-opus-5-5)."""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
import anthropic, requests

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
client = anthropic.Anthropic(max_retries=8, default_headers={"anthropic-workspace-id": WS} if WS else None)
MODEL = os.environ.get("MODEL") or "claude-opus-5-5"
ONLY = [s for s in (os.environ.get("SOURCES") or "").split(",") if s]
PUB = f"{SB}/storage/v1/object/public/web/"

RULES = """You are verifying records for a heritage archive of BALLY, the Swiss luxury shoe and fashion house
(founded 1851 by Carl Franz Bally in Schönenwerd; Bally Schuhfabriken AG; later Bally International, Caslano/Lugano;
its factories, shops, products, posters and advertising, designers and creative directors, the Bally family, Bally-Park,
Ballyana, the Bally shoe museum, its trademarks, patents and company filings).
NOT the house: Bally Manufacturing / Midway / Bally Gaming / pinball / slot machines / arcade games; Bally's casinos and
hotels; Bally Sports; Bally Total Fitness / Bally Spas; Bally Technologies; Bally Wulff; J. Bally rum (Martinique);
the botanist P. R. O. Bally and plant specimens; places named Bally (Ireland, India: Bally/Howrah); the Bally carver
(Pennsylvania walking sticks); clockmakers, gunsmiths, painters, politicians, athletes, musicians or anyone else simply
surnamed Bally; Ballymena, Ballycastle etc. A venue that is merely in a Bally building (e.g. an exhibition at Bally-Halle)
is related but weak. Shoes or bags stamped Bally are the house. Look at the image too when given.
For videos and press items, also judge archival value: Bally's own films, shows, ads, documentaries, broadcast archive
footage and interviews with Bally people are "house". Third-party commentary, news-desk talking heads, reaction or review
videos, unboxings, resale/authentication clips and low-effort reposts ABOUT Bally are "commentary" (correct company, but
not archive material)."""

TOOL = {"name": "verdicts", "description": "Verdict per record.", "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {
        "i": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["house", "related", "commentary", "not_house", "unsure"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string", "description": "One short sentence."}},
    "required": ["i", "verdict", "confidence", "reason"]}}}, "required": ["items"]}}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def yt_enrich(r):
    """Exact upload date and full description from the public watch page (metadata only)."""
    if r["source"] != "youtube":
        return r
    try:
        import re as _re, html as _h, json as _j
        g = requests.get(f"https://www.youtube.com/watch?v={r['id'].split(':', 1)[1]}", headers={"User-Agent": "BallyHeritageArchive/1.0", "Accept-Language": "en"}, timeout=30)
        d = _re.search(r'"uploadDate"\s*:\s*"([0-9-]{10})', g.text) or _re.search(r'itemprop="uploadDate" content="([0-9-]{10})', g.text)
        sd = _re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', g.text)
        patch = {}
        if d:
            patch["date_text"] = d.group(1); patch["year"] = int(d.group(1)[:4])
        if sd:
            desc = _j.loads('"' + sd.group(1) + '"').strip()
            if desc:
                meta = (r.get("description") or "").split("Channel:")[-1]
                patch["description"] = (desc[:1500] + " Channel:" + meta) if "Channel:" in (r.get("description") or "") else desc[:1800]
        if patch:
            requests.patch(f"{SB}/rest/v1/ext_records", headers={**H, "Prefer": "return=minimal"}, params={"id": f"eq.{r['id']}"}, json=patch, timeout=60)
            r = {**r, **patch}
    except Exception:
        pass
    return r


def review(batch):
    content = [{"type": "text", "text": RULES + "\n\nGive a verdict for each record below, then call the verdicts tool."}]
    for i, r in enumerate(batch):
        content.append({"type": "text", "text": f"[{i}] source={r['source']} | kind={r.get('kind')} | title={r.get('title')} | creator={r.get('creator')} | date={r.get('date_text')} | "
                                                f"description={(r.get('description') or '')[:600]}"})
        if r.get("thumb_path") and r["source"] not in ("wipo-tm", "uspto-tm"):
            content.append({"type": "image", "source": {"type": "url", "url": PUB + r["thumb_path"]}})
    for t in range(3):
        try:
            m = client.messages.create(model=MODEL, max_tokens=6000, tools=[TOOL], tool_choice={"type": "auto"}, messages=[{"role": "user", "content": content}])
            for b in m.content:
                if b.type == "tool_use":
                    return {x["i"]: x for x in b.input["items"]}
        except anthropic.BadRequestError as e:
            # an image URL the API could not fetch: retry text-only
            log("bad request, retrying without images", str(e)[:120])
            content = [c for c in content if c["type"] == "text"]
        except Exception as e:
            log("review error", type(e).__name__); time.sleep(5)
    return {}


def main():
    recs, off = [], 0
    while True:
        params = {"select": "id,source,kind,title,creator,date_text,year,description,thumb_path,relevance,data", "relevance": "gte.0.5", "order": "id", "limit": 1000, "offset": off}
        if ONLY:
            params["source"] = f"in.({','.join(ONLY)})"
        page = requests.get(f"{SB}/rest/v1/ext_records", headers=H, params=params, timeout=60).json()
        recs += page; off += 1000
        if len(page) < 1000:
            break
    recs = [r for r in recs if not ((r.get("data") or {}).get("verify"))]
    log(len(recs), "records to verify")
    with ThreadPoolExecutor(4) as ex:
        recs = list(ex.map(yt_enrich, recs))
    log("youtube dates found", sum(1 for r in recs if r["source"] == "youtube" and str(r.get("date_text") or "")[:2] in ("19", "20")))
    batches = [recs[i:i + 20] for i in range(0, len(recs), 20)]
    from collections import Counter
    tally, hidden = Counter(), []
    with ThreadPoolExecutor(6) as ex:
        for batch, res in zip(batches, ex.map(review, batches)):
            for i, r in enumerate(batch):
                v = res.get(i)
                if not v:
                    tally["no_answer"] += 1; continue
                tally[v["verdict"]] += 1
                patch = {"data": {**(r.get("data") or {}), "verify": {"verdict": v["verdict"], "confidence": v["confidence"], "reason": v["reason"], "model": MODEL}}}
                if v["verdict"] in ("not_house", "commentary") and v["confidence"] in ("high", "medium"):
                    patch["relevance"] = 0.3; patch["ai_status"] = "hidden"
                    hidden.append(f"{r['source']}: {r.get('title')} ({v['reason']})")
                elif v["verdict"] in ("related", "unsure"):
                    patch["relevance"] = min(r.get("relevance") or 1, 0.55)
                requests.patch(f"{SB}/rest/v1/ext_records", headers={**H, "Prefer": "return=minimal"}, params={"id": f"eq.{r['id']}"}, json=patch, timeout=60)
    log("verdicts", dict(tally))
    log("hidden", len(hidden))
    for h in hidden:
        log("  -", h[:220])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
