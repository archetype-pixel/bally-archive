"""AI review of Selection candidates (table selection_members, status 'candidate').
Each object is shown once (photo + catalogue record + AI description) with every Selection it is a candidate for;
the model decides membership per Selection. Members need verdict 'member' at high or medium confidence.
Curated (old archive) memberships are never touched. Env: SUPABASE_*, ANTHROPIC_API_KEY, MODEL, WORKERS."""
import base64, io, json, os, sys, time
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
import anthropic, requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
MODEL = os.environ.get("MODEL") or "claude-sonnet-5-5"
WORKERS = int(os.environ.get("WORKERS", "8"))
WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
client = anthropic.Anthropic(max_retries=8, default_headers={"anthropic-workspace-id": WS} if WS else None)

THEMES = {
    1: ("Bernard Villemot", "Work designed by the French poster artist Bernard Villemot for Bally (posters, maquettes, prints, "
        "advertising), or Bally products shown in or made for his campaigns. His style: bold flat colour, sweeping brush lines, "
        "stylised women's legs and shoes. Do not include work by other artists, even if similar."),
    2: ("Bvd. de la Madeleine", "Bally's Paris shop on the Boulevard de la Madeleine (the 1928 Robert Mallet-Stevens store and its "
        "later incarnations): photographs of the shop, its windows and interior, advertising or documents naming it, and shoes "
        "documented as sold there. Not other Paris shops or other Bally stores."),
    3: ("Bally Icons", "Signature Bally designs that define the house and are widely recognised: e.g. the Scribe men's shoe (1951), "
        "the Bally Stripe / Trainspotter red-white stripe, and the archive's own icon models (Trocadero, Janelle, Havanna, Gavinia, "
        "Guard, Free). Include another example of one of these named icon models. Ordinary products that merely share a common "
        "word (e.g. a model called Free that is not the 2003 Free summer shoe) are not icons."),
    4: ("Après Ski", "Footwear and imagery for winter leisure in the mountains: après-ski boots, fur-lined or snow boots, ski boots, "
        "winter resort fashion, and advertising or photos of skiing and winter sports holidays. Not ordinary city boots."),
    5: ("70th Anniversary of Everest", "Bally and the Himalaya: mountaineering and expedition boots, high-altitude footwear, "
        "material on the 1953 Everest ascent or other Himalayan expeditions equipped by Bally. Ordinary hiking shoes only if clearly "
        "expedition or high-mountain mountaineering boots."),
    6: ("Summer Sports", "Footwear, posters and photos for summer sports: tennis, golf, gymnastics, athletics, sailing, rowing, "
        "cycling, football, beach and bathing. Not winter sports, not everyday casual shoes."),
    7: ("Craftmanship", "How Bally shoes are made: shoemaking components (lasts, soles, heels, uppers, embossing tools), workshops "
        "and factory production, cobblers at work, tools, machines, tanning, hand-finishing, and objects shown specifically to "
        "demonstrate construction or technique. Not finished products simply photographed for a catalogue."),
    8: ("Swissness", "Items whose imagery or subject celebrates Switzerland: Swiss landscapes and the Alps, the Swiss cross or flag, "
        "Swiss national exhibitions (e.g. Landi 1939), Swiss traditions, costumes, folklore, Swiss army, Swiss towns. A plain "
        "'Made in Switzerland' or 'Swiss made' marking alone is NOT enough."),
}

TOOL = {"name": "selection_verdicts", "description": "Membership verdict for each Selection asked about.",
        "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "selection_id": {"type": "integer"},
            "verdict": {"type": "string", "enum": ["member", "not_member"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "reason": {"type": "string", "description": "One short plain sentence naming the evidence."}},
            "required": ["selection_id", "verdict", "confidence", "reason"]}}}, "required": ["items"]}}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def get_all(table, params):
    out, off = [], 0
    while True:
        r = requests.get(f"{SB}/rest/v1/{table}", headers=H, params={**params, "limit": 1000, "offset": off}, timeout=120)
        r.raise_for_status(); page = r.json(); out += page; off += 1000
        if len(page) < 1000:
            return out


def img(path):
    try:
        r = requests.get(f"{SB}/storage/v1/object/public/web/{path}", timeout=60)
        if not r.ok:
            return None
        im = Image.open(io.BytesIO(r.content)).convert("RGB"); im.thumbnail((800, 800))
        b = io.BytesIO(); im.save(b, "JPEG", quality=85)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b.getvalue()).decode()}}
    except Exception:
        return None


def main():
    cands = get_all("selection_members", {"select": "collection_id,object_guid", "status": "eq.candidate", "source": "eq.ai"})
    by = defaultdict(list)
    for c in cands:
        by[c["object_guid"]].append(c["collection_id"])
    guids = list(by)
    log(len(cands), "candidates on", len(guids), "objects")
    meta, desc = {}, {}
    for i in range(0, len(guids), 150):
        chunk = ",".join(f'"{g}"' for g in guids[i:i + 150])
        for o in requests.get(f"{SB}/rest/v1/archive_object_current", headers=H, timeout=120, params={"select": "guid,object_code,category,subcategory,model_name,year_label,date_from,date_to,description,wording,author,remarks,object_notes,main_asset_guid", "guid": f"in.({chunk})"}).json():
            meta[o["guid"]] = o
        for a in requests.get(f"{SB}/rest/v1/ai_descriptions", headers=H, timeout=120, params={"select": "object_guid,description,fields", "object_guid": f"in.({chunk})"}).json():
            desc[a["object_guid"]] = a

    def one(g):
        o = meta.get(g)
        if not o:
            return []
        ids = by[g]
        rec = {k: o.get(k) for k in ("object_code", "category", "subcategory", "model_name", "year_label", "date_from", "date_to", "description", "wording", "author", "remarks", "object_notes") if o.get(k)}
        a = desc.get(g) or {}
        content = [{"type": "text", "text": "You curate themed Selections for the Bally heritage archive (Swiss shoe and fashion house). "
                    "Decide strictly, from the photo and the record, whether this object belongs in each Selection below. "
                    "When in doubt, say not_member."}]
        im = img(f"thumb/{o['main_asset_guid']}.jpg") if o.get("main_asset_guid") else None
        if im:
            content.append(im)
        content.append({"type": "text", "text": "Catalogue record: " + json.dumps(rec, ensure_ascii=False)[:2500] +
                        ("\nAI description of the photos: " + (a.get("description") or "") + " Visible text: " + str((a.get("fields") or {}).get("visible_text") or ""))[:1500] +
                        "\n\nSelections to judge:\n" + "\n".join(f"[{i}] {THEMES[i][0]}: {THEMES[i][1]}" for i in ids)})
        for t in range(3):
            try:
                m = client.messages.create(model=MODEL, max_tokens=1200, tools=[TOOL], tool_choice={"type": "tool", "name": "selection_verdicts"},
                                           messages=[{"role": "user", "content": content}])
                items = next(b.input["items"] for b in m.content if b.type == "tool_use")
                out = []
                for v in items:
                    if v.get("selection_id") not in ids:
                        continue
                    ok = v["verdict"] == "member" and v["confidence"] in ("high", "medium")
                    out.append((v["selection_id"], g, "member" if ok else "rejected", v["confidence"], v["reason"]))
                return out
            except Exception as e:
                log("error", o.get("object_code"), type(e).__name__, str(e)[:150]); time.sleep(4)
        return []

    tally = Counter()
    with ThreadPoolExecutor(WORKERS) as ex:
        for res in ex.map(one, guids):
            for cid, g, status, conf, reason in res:
                tally[(cid, status)] += 1
                requests.patch(f"{SB}/rest/v1/selection_members", headers={**H, "Prefer": "return=minimal"}, timeout=60,
                               params={"collection_id": f"eq.{cid}", "object_guid": f"eq.{g}", "source": "eq.ai"},
                               json={"status": status, "confidence": conf, "reason": reason, "model": MODEL, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    for cid in sorted(THEMES):
        log(THEMES[cid][0], "members", tally[(cid, "member")], "rejected", tally[(cid, "rejected")])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300]); sys.exit(1)
