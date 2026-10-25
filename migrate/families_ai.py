"""Consolidate the free-text model_family values from the attribute pass into canonical Bally product lines.
Reads names used by 2+ objects (with counts and sample model names), asks the model to map each to one canonical line or to
none (box labels, stores, generic words, single model names, departments), and writes model_family_map(raw, family).
Env: SUPABASE_*, ANTHROPIC_API_KEY, MODEL."""
import json, os, sys, time
from collections import defaultdict
import anthropic, requests

SB = os.environ["SUPABASE_URL"].rstrip("/"); KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
MODEL = os.environ.get("MODEL") or "claude-opus-5-5"
WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
client = anthropic.Anthropic(max_retries=8, default_headers={"anthropic-workspace-id": WS} if WS else None)


def get_all(table, params):
    out, off = [], 0
    while True:
        r = requests.get(f"{SB}/rest/v1/{table}", headers=H, params={**params, "limit": 1000, "offset": off}, timeout=120)
        r.raise_for_status(); page = r.json(); out += page; off += 1000
        if len(page) < 1000:
            return out


rows = get_all("object_facets", {"select": "object_guid,model_family", "model_family": "not.is.null"})
objs = {o["guid"]: o for o in get_all("archive_object_current", {"select": "guid,model_name,product_line,category,date_from"})}
by = defaultdict(list)
for r in rows:
    by[r["model_family"].strip()].append(r["object_guid"])
names = sorted([n for n, g in by.items() if len(g) >= 2], key=lambda n: -len(by[n]))
print(len(names), "names to map", flush=True)
TOOL = {"name": "map_families", "description": "Canonical product line for each raw name.", "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {"i": {"type": "integer"}, "family": {"type": "string", "description": "Canonical Bally product line name in its usual spelling, or empty string if not a product line."}},
    "required": ["i", "family"]}}}, "required": ["items"]}}
canon = {}
for start in range(0, len(names), 150):
    chunk = names[start:start + 150]
    lines = []
    for i, n in enumerate(chunk):
        sample = [objs.get(g, {}) for g in by[n][:4]]
        ex = "; ".join(f"{s.get('category','')}/{s.get('model_name') or '-'}/{(s.get('date_from') or '')[:4]}/{s.get('product_line') or ''}" for s in sample)
        lines.append(f"[{i}] {n!r} ({len(by[n])} objects; e.g. {ex})")
    prompt = ("These are product-line or model-family labels that an AI assigned to objects in the Bally (Swiss shoe house) archive. "
              "Map each to ONE canonical Bally product line or collection name, merging spelling variants and sub-lines into their parent "
              "where that is clearly the same line (e.g. 'Mandarin/California' and 'Mandarin' -> 'Mandarin'; 'Bally Scribe' -> 'Scribe'; "
              "'Trainspotting' stays 'Trainspotting'; 'Bally Madeleine' and 'Madeleine' are the Paris Madeleine label -> 'Madeleine'). "
              "Return an empty family for anything that is not a product line: empty or junk values, 'not identifiable', storage box labels "
              "(e.g. 'Boxes Women's 1930s'), store or department names, generic descriptions (e.g. 'evening pump', 'sole stamp'), "
              "and names that are just one model. Call map_families.\n\n" + "\n".join(lines))
    for t in range(3):
        try:
            m = client.messages.create(model=MODEL, max_tokens=8000, tools=[TOOL], tool_choice={"type": "auto"}, messages=[{"role": "user", "content": prompt}])
            items = next(b.input["items"] for b in m.content if b.type == "tool_use")
            for it in items:
                if 0 <= it["i"] < len(chunk):
                    canon[chunk[it["i"]]] = it["family"].strip()
            break
        except Exception as e:
            print("error", type(e).__name__, str(e)[:200], flush=True); time.sleep(5)
    print(start + len(chunk), "mapped", flush=True)
rows = [{"raw": n, "family": canon.get(n) or None, "objects": len(by[n])} for n in names]
for i in range(0, len(rows), 500):
    r = requests.post(f"{SB}/rest/v1/model_family_map", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, json=rows[i:i + 500], timeout=120)
    r.raise_for_status()
fams = defaultdict(int)
for r in rows:
    if r["family"]:
        fams[r["family"]] += r["objects"]
print(len(fams), "canonical lines; top:", sorted(fams.items(), key=lambda x: -x[1])[:40], flush=True)
