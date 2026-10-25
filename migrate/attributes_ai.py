"""Design-research passes over the whole Collection, one AI call per object (photos + catalogue record + earlier AI description):
activity / use, construction and design attributes, signature Bally details, and model family. Results go to object_attributes
(never into catalogue fields). Resumable: skips objects already done. Env: SUPABASE_*, ANTHROPIC_API_KEY, MODEL, WORKERS, LIMIT,
SHARD, SHARDS."""
import base64, io, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic, requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/"); KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
MODEL = os.environ.get("MODEL") or "claude-sonnet-5-5"
WORKERS = int(os.environ.get("WORKERS", "10")); LIMIT = int(os.environ.get("LIMIT") or 0)
SHARD, SHARDS = int(os.environ.get("SHARD", "0")), int(os.environ.get("SHARDS", "1"))
RUN = int(os.environ.get("RUN_SECONDS", "19800")); T0 = time.time()
WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
client = anthropic.Anthropic(max_retries=8, default_headers={"anthropic-workspace-id": WS} if WS else None)

ACT = ["beach-resort", "golf", "tennis", "skiing", "apres-ski-winter", "hiking-mountaineering", "riding", "sailing-water", "other-sport",
       "dance-ballet", "evening-formal", "bridal", "business-dress", "city-everyday", "work-safety", "military-uniform", "children",
       "travel", "home-slippers", "comfort-orthopaedic"]
SIG = ["bally-stripe", "b-monogram", "bally-wordmark", "brogue-perforation", "medallion-toe", "wingtip", "cap-toe", "apron-toe", "tassel",
       "horsebit", "buckle", "decorative-stitching", "quilting", "woven-leather", "embossed-reptile", "exotic-skin", "metallic", "two-tone",
       "kiltie-fringe", "bow", "cut-outs", "platform", "contrast-sole", "swiss-cross", "other"]
E = lambda vals: {"type": "string", "enum": vals}
TOOL = {"name": "record_attributes", "description": "Structured design attributes for one archive object.", "input_schema": {"type": "object", "properties": {
    "activities": {"type": "array", "items": E(ACT), "description": "What the object is for or depicts. Several allowed. Empty only if truly none applies."},
    "activity_evidence": {"type": "string", "description": "Short note: which words in the record or what in the photo decided the activities."},
    "is_footwear": {"type": "boolean"},
    "footwear": {"type": "object", "description": "Fill only for footwear; use 'unknown' when not visible.", "properties": {
        "style": E(["oxford", "derby", "brogue", "monk", "loafer", "moccasin", "boat", "pump", "slingback", "mary-jane", "t-bar", "mule", "slipper",
                    "sandal", "espadrille", "sneaker", "ankle-boot", "chelsea-boot", "lace-up-boot", "knee-boot", "ski-boot", "hiking-boot",
                    "riding-boot", "golf-shoe", "clog", "other", "unknown"]),
        "construction": E(["goodyear-welted", "blake-stitched", "cemented", "stitchdown", "moccasin", "vulcanised", "sandal", "other", "unknown"]),
        "toe_shape": E(["round", "almond", "square", "pointed", "chisel", "open", "unknown"]),
        "heel_type": E(["flat", "block", "stacked-leather", "cuban", "kitten", "stiletto", "wedge", "platform", "cone", "spool", "louis", "unknown"]),
        "heel_height": E(["flat-under-2cm", "low-2-4cm", "mid-4-7cm", "high-7-10cm", "very-high-over-10cm", "unknown"]),
        "closure": E(["lace-up", "monk-strap", "buckle", "slip-on", "zip", "elastic-gusset", "strap", "hook-and-loop", "none", "unknown"]),
        "sole_material": E(["leather", "rubber", "leather-and-rubber", "crepe", "cork", "wood", "synthetic", "unknown"]),
        "upper_materials": {"type": "array", "items": {"type": "string"}, "description": "e.g. calf, patent, suede, nubuck, reptile, textile, satin"},
        "lining": {"type": "string"}}},
    "signature_details": {"type": "array", "items": E(SIG)},
    "model_family": {"type": "string", "description": "Product line or model family if identifiable from the record or markings (e.g. Scribe, Trainspotting, Bally Stripe, Swiss Ski). Empty if none."},
    "distinctive": {"type": "string", "description": "One plain sentence on what makes this design distinctive, for a designer."},
    "confidence": E(["high", "medium", "low"])}, "required": ["activities", "is_footwear", "signature_details", "confidence"]}}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def get_all(table, params):
    out, off = [], 0
    while True:
        r = requests.get(f"{SB}/rest/v1/{table}", headers=H, params={**params, "limit": 1000, "offset": off}, timeout=120)
        r.raise_for_status(); page = r.json(); out += page; off += 1000
        if len(page) < 1000:
            return out


def img(path, px=700):
    try:
        r = requests.get(f"{SB}/storage/v1/object/public/web/{path}", timeout=60)
        if not r.ok:
            return None
        im = Image.open(io.BytesIO(r.content)).convert("RGB"); im.thumbnail((px, px))
        b = io.BytesIO(); im.save(b, "JPEG", quality=84)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b.getvalue()).decode()}}
    except Exception:
        return None


def main():
    done = {r["object_guid"] for r in get_all("object_attributes", {"select": "object_guid"})}
    objs = get_all("archive_object_current", {"select": "guid,object_code,record_kind,category,subcategory,model_name,year_label,date_from,date_to,description,wording,remarks,object_notes,author,gender,product_line,tags,main_asset_guid", "order": "guid"})
    objs = [o for i, o in enumerate(objs) if o["guid"] not in done and i % SHARDS == SHARD]
    if LIMIT:
        objs = objs[:LIMIT]
    log(len(objs), "objects to process")
    desc = {a["object_guid"]: a for a in get_all("ai_descriptions", {"select": "object_guid,description,fields"})}
    extra = {}
    for q in get_all("embed_queue", {"select": "object_guid,thumb_path", "order": "object_guid,asset_guid"}):
        extra.setdefault(q["object_guid"], []).append(q["thumb_path"])

    def one(o):
        if time.time() - T0 > RUN:
            return "timeout"
        rec = {k: o.get(k) for k in ("object_code", "category", "subcategory", "model_name", "year_label", "date_from", "description", "wording", "remarks", "object_notes", "author", "gender", "product_line", "tags") if o.get(k)}
        a = desc.get(o["guid"]) or {}
        content = [{"type": "text", "text": "You are a footwear and accessories design researcher cataloguing the Bally heritage archive (Swiss shoe house, founded 1851) "
                    "so designers can search it and build new products from it. Look at the photos and read the record, then call record_attributes. "
                    "The old catalogue's box and category labels are reliable evidence (e.g. 'Golfschuhe' = golf shoes, 'Tennisschuhe' = tennis, "
                    "'Bergschuhe' = mountaineering, 'Abend' = evening, 'Hausschuhe' = slippers, 'Kinder' = children, 'Après Ski'). For posters, photos "
                    "and documents, activities describe what is depicted or advertised. Do not guess construction details you cannot see."}]
        paths = []
        if o.get("main_asset_guid"):
            paths.append(f"thumb/{o['main_asset_guid']}.jpg")
        paths += [p for p in extra.get(o["guid"], []) if p and p not in paths][:1]
        for p in paths[:2]:
            b = img(p)
            if b:
                content.append(b)
        content.append({"type": "text", "text": "Catalogue record: " + json.dumps(rec, ensure_ascii=False)[:3000] +
                        ("\nEarlier AI photo description: " + (a.get("description") or "") + " | fields: " + json.dumps(a.get("fields") or {}, ensure_ascii=False)[:1200])})
        for t in range(3):
            try:
                m = client.messages.create(model=MODEL, max_tokens=1500, tools=[TOOL], tool_choice={"type": "auto"}, messages=[{"role": "user", "content": content}])
                data = next((b.input for b in m.content if b.type == "tool_use"), None)
                if not data:
                    continue
                r = requests.post(f"{SB}/rest/v1/object_attributes", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=60,
                                  json={"object_guid": o["guid"], "data": data, "model": MODEL, "input_tokens": m.usage.input_tokens, "output_tokens": m.usage.output_tokens})
                r.raise_for_status()
                return "ok"
            except Exception as e:
                log("error", o.get("object_code"), type(e).__name__, str(e)[:160]); time.sleep(4)
        return "fail"

    from collections import Counter
    c = Counter(); n = 0
    with ThreadPoolExecutor(WORKERS) as ex:
        for f in as_completed([ex.submit(one, o) for o in objs]):
            c[f.result()] += 1; n += 1
            if n % 500 == 0:
                log(n, dict(c))
    log("done", dict(c))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300]); sys.exit(1)
