"""Section covers chosen by AI, replacing the hand-cropped headers lifted from the old archive.
- Main sections and decades: one book plate (studio and documentary photography from Hier und Jetzt).
- Collections, categories, themes, product lines: 5 of the set's own objects, chosen for quality and variety.
Writes section_covers only. Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, MODEL, ONLY"""
import base64, io, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
import anthropic, requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
MODEL = os.environ.get("MODEL", "claude-opus-5-5")
ONLY = [x for x in os.environ.get("ONLY", "").split(",") if x]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
client = anthropic.Anthropic(max_retries=8, default_headers={"anthropic-workspace-id": WS} if WS else None)
PUBLIC = f"{SB}/storage/v1/object/public/web/"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def get(path, params):
    r = requests.get(f"{SB}/rest/v1/{path}", headers=H, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def rpc(name, body):
    r = requests.post(f"{SB}/rest/v1/rpc/{name}", headers=H, json=body, timeout=300)
    r.raise_for_status()
    return r.json()


def img(path, px):
    r = requests.get(PUBLIC + path, timeout=60)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    im.thumbnail((px, px))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=82)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b.getvalue()).decode()}}


def ask(content, tool):
    for _ in range(3):
        msg = client.messages.create(model=MODEL, max_tokens=1500, tools=[tool], tool_choice={"type": "auto"},
                                     system=("You are the art director of the Bally heritage archive (Swiss shoes, bags, posters and "
                                             "photography since 1851). The site is quiet, elegant and editorial, like a museum monograph. "
                                             f"Always answer by calling the {tool['name']} tool once, with no prose."),
                                     messages=[{"role": "user", "content": content}])
        for b in msg.content:
            if b.type == "tool_use":
                return b.input
    raise RuntimeError("no tool call")


PLATE_TOOL = {"name": "pick_hero", "description": "Choose the hero image for a page section.", "input_schema": {
    "type": "object", "properties": {
        "choice": {"type": "integer", "description": "Number of the best image."},
        "alternates": {"type": "array", "items": {"type": "integer"}, "description": "Two runner-up numbers."},
        "reason": {"type": "string"}}, "required": ["choice", "alternates", "reason"]}}

SET_TOOL = {"name": "pick_set", "description": "Choose the objects that represent a collection on its header.", "input_schema": {
    "type": "object", "properties": {
        "choices": {"type": "array", "items": {"type": "integer"}, "description": "5 image numbers in display order (left to right)."},
        "reason": {"type": "string"}}, "required": ["choices", "reason"]}}


def pick_plate(key, label, brief, plates):
    content = []
    for i, p in enumerate(plates, 1):
        content += [{"type": "text", "text": f"Image {i}: p. {p['page']}, {p['width']}x{p['height']}, {p.get('title_en') or ''}"}, img(p["thumb_path"], 360)]
    content.append({"type": "text", "text": (
        f"Pick the hero image for the section '{label}'. {brief} It is shown as a wide banner (about 3:1 on desktop, 4:3 on phones), "
        "so prefer images that still read when cropped to a band: a clear subject, strong composition, good tonal quality. "
        "Avoid text-heavy documents, diagrams and portraits of named family members unless the section is about them.")})
    d = ask(content, PLATE_TOOL)
    idx = [d["choice"]] + [a for a in d.get("alternates", []) if a != d["choice"]][:2]
    chosen = [plates[i - 1] for i in idx if 1 <= i <= len(plates)]
    return {"key": key, "kind": "plate", "label": label, "source": "book", "plate_ids": [p["id"] for p in chosen],
            "thumbs": [p["path"] for p in chosen], "note": d.get("reason"), "model": MODEL}


def pick_set(key, label, cands):
    content = []
    for i, c in enumerate(cands, 1):
        content += [{"type": "text", "text": f"Image {i}: {c['object_code']} {c.get('subcategory') or ''} {str(c.get('date_from') or '')[:4]}"}, img(c["thumb_path"], 256)]
    content.append({"type": "text", "text": (
        f"Pick 5 objects to represent '{label}' in a quiet row of 5 tiles on its header. Choose the strongest, most characteristic "
        "and best-photographed pieces, with variety (types, eras, colours) and no near-duplicates. Prefer clean studio shots of a "
        "single object; avoid blurry, cropped or cluttered images, and never pick raw scans that show colour-calibration bars, grey scales, rulers, scanner beds or tape. Put the single most beautiful and characteristic piece FIRST: it is used alone as the collection card image. Then order the rest so the row is balanced.")})
    d = ask(content, SET_TOOL)
    chosen = [cands[i - 1] for i in d["choices"] if 1 <= i <= len(cands)][:5]
    return {"key": key, "kind": "set", "label": label, "source": "archive", "object_guids": [c["guid"] for c in chosen],
            "thumbs": [c["thumb_path"] for c in chosen], "note": d.get("reason"), "model": MODEL}


def main():
    plates = get("library_plates", {"select": "id,page,width,height,path,thumb_path,title_en,picture_type,era", "order": "sort", "limit": 1000})
    good = [p for p in plates if p["picture_type"] not in ("diagram", "document", "chart", "map")]
    jobs = []
    MAIN = {
        "home": ("Home", "The opening image of the whole archive: it should say Bally, Swiss craft and 175 years of history at a glance."),
        "catalogue": ("Catalogue", "The entrance to 17,000 objects: favour a superb studio photograph of a single shoe or object."),
        "search": ("Search", "A calm, evocative image; favour a studio object photograph different from the catalogue choice."),
        "timeline": ("Timeline", "Should evoke history and the passage of time: factory, stores or people across eras."),
        "library": ("Library", "Represents the book and its documentary photography."),
        "stories": ("Stories", "Editorial and human: people, stores, events."),
    }
    for key, (label, brief) in MAIN.items():
        jobs.append(("plate", key, label, brief, good))
    for dec in range(1850, 2000, 10):
        cands = [p for p in good if (p.get("era") or "").startswith(str(dec))]
        if len(cands) >= 3:
            jobs.append(("plate", f"decade:{dec}s", f"The {dec}s", f"Must clearly belong to the {dec}s.", cands))
    for c in get("collections", {"select": "slug,title", "order": "sort_order"}):
        jobs.append(("set", f"collection:{c['slug']}", c["title"], "collection", c["slug"]))
    for cat in ["SHOES", "ACCESSORIES", "ADVERTISING", "PHOTOGRAPHY", "CORPORATE IDENTITY"]:
        jobs.append(("set", f"category:{cat}", cat.title(), "category", cat))
    THEMES = ["Mountain & Alpine", "Après Ski", "Hiking", "Skiing", "Sport", "Ballet & Dance", "Ice Skating", "Beach & Swim", "Golf",
              "Sailing & Boat", "Tennis", "Riding", "Everest", "Cycling", "Football"]
    LINES = ["Flexible", "International", "Seamless", "Broadway", "Boutique", "Mandarin", "Bellezza", "Capucines", "Souplewelt",
             "Miss Bally", "Super Aarau", "Scribe", "Vasano", "Boston", "Femina", "Haute Couture", "Bally Sport", "Prestige", "Granados",
             "Elégante", "Bally Golf", "Dernier Cri", "Expo 64", "Sole Mio", "Vogue", "Studio", "Lido", "Camping", "Junior", "Club",
             "Favore", "Solaria", "Flaneur", "Silver Wing", "Vasanova", "Sanoform", "Air Flex"]
    jobs += [("set", f"theme:{v}", v, "theme", v) for v in THEMES]
    jobs += [("set", f"line:{v}", v, "line", v) for v in LINES]
    if ONLY:
        jobs = [j for j in jobs if any(j[1].startswith(o) for o in ONLY)]
    log(len(jobs), "sections")

    def run(j):
        try:
            if j[0] == "plate":
                row = pick_plate(j[1], j[2], j[3], j[4][:40] if len(j[4]) <= 40 else j[4][::max(1, len(j[4]) // 40)][:40])
            else:
                cands = rpc("cover_candidates", {"p_kind": j[3], "p_value": j[4], "p_limit": 30})
                if len(cands) < 3:
                    return f"{j[1]}: too few"
                row = pick_set(j[1], j[2], cands)
            r = requests.post(f"{SB}/rest/v1/section_covers", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, json=row, timeout=60)
            r.raise_for_status()
            return f"{j[1]}: ok"
        except Exception as e:
            return f"{j[1]}: error {type(e).__name__} {str(e)[:150]}"

    with ThreadPoolExecutor(6) as ex:
        for res in ex.map(run, jobs):
            log(res)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
