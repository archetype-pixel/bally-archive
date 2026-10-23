"""AI work for the Library (book) section. Writes only to library_* tables.
MODE=plates     confirm each plate's caption from its candidates, translate it, describe the photo
MODE=translate  faithful English translation of passages (PART=n, or 0 = front matter + essay intros)
MODE=notes      translate endnotes
MODE=headings   fill heading_en / sub_en from translated heading passages
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, MODEL, PART, WORKERS"""
import base64, io, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
import anthropic, requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
MODE = os.environ.get("MODE", "plates")
MODEL = os.environ.get("MODEL", "claude-opus-5-5")
PART = os.environ.get("PART", "")
WORKERS = int(os.environ.get("WORKERS", "4"))
PUB = "hier-und-jetzt"
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
client = anthropic.Anthropic(max_retries=8, default_headers={"anthropic-workspace-id": WS} if WS else None)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def get(table, params):
    out, off = [], 0
    while True:
        r = requests.get(f"{SB}/rest/v1/{table}", headers=H, params={**params, "offset": off, "limit": 1000}, timeout=120)
        r.raise_for_status()
        rows = r.json()
        out += rows
        if len(rows) < 1000:
            return out
        off += 1000


def patch(table, match, body):
    for t in range(5):
        r = requests.patch(f"{SB}/rest/v1/{table}", headers={**H, "Prefer": "return=minimal"}, params=match, json=body, timeout=120)
        if r.ok:
            return
        log("patch", table, r.status_code, r.text[:200])
        time.sleep(3 * (t + 1))
    raise RuntimeError(table)


GLOSSARY = """Conventions for this book (Swiss company history of Bally, Schönenwerd):
- Translate faithfully and completely: no omissions, no additions, no summarising, no modernising of the authors' judgements. Keep the authors' tone (measured, factual, occasionally wry).
- Keep paragraph content one-to-one with the German. Keep numbers, dates, sums and units exactly (Franken -> francs; Mio. -> million).
- Keep proper names, company names, place names and product/model names as in the original (Schönenwerd, Niederamt, C. F. Bally AG, Bally Arola, «Scribe», «Vasano»). Write Swiss place names without translation.
- House terms used at Bally stay in their original form, in quotation marks where the German quotes them, with a short English gloss in brackets the first time in a passage only if the German itself explains nothing: Création, Créateur, Richelieu, Molière, Ecossaise, Trotteur, Lamballe, Bride, Ballyaner, Büezer, Contremaître, Compagnon, Finissage, Stepperei, Zwickerei. Shoe construction terms: rahmengenäht = welted (Goodyear-welted where the German says so), durchgenäht = Blake-stitched / stitched through, gewendet = turnshoe (turned construction), genagelt = pegged/nailed, geklebt = cemented. Schaft = upper; Schäftefabrik = upper (closing) factory; Leisten = last; Brandsohle = insole; Laufsohle = outsole; Besetz = vamp; Bottine = ankle boot / bottine; Pumps = pump(s).
- Guillemets «...» become English double quotes “...”; nested ‹...› become ‘...’. Quotations from sources: translate them, keep quotation marks.
- Swiss-German words quoted in the original («Lismen») stay in the original with the English meaning added in brackets if the German gives it.
- Use American spelling (color, center) and plain, natural English."""


# ---------------------------------------------------------------- translation
TR_TOOL = {"name": "record_translation", "description": "Return the English translation of each passage, same ids, same order.",
           "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {
               "type": "object", "properties": {"id": {"type": "integer"}, "en": {"type": "string"}}, "required": ["id", "en"]}}},
               "required": ["items"]}}


def translate_batch(items, context_en, header):
    payload = [{"id": i, "kind": k, "de": t} for i, k, t in items]
    msg = client.messages.create(
        model=MODEL, max_tokens=16000,
        system="You are the literary translator of a German-language Swiss business history into English. " + GLOSSARY,
        tools=[TR_TOOL], tool_choice={"type": "tool", "name": "record_translation"},
        messages=[{"role": "user", "content":
                   f"{header}\n\nPreceding English text, for continuity only (do not translate again):\n{context_en or '(start)'}\n\n"
                   f"Translate each item. Headings stay short headings. Return every id.\n{json.dumps(payload, ensure_ascii=False)}"}])
    data = next(b.input for b in msg.content if b.type == "tool_use")
    got = {it["id"]: it["en"] for it in data["items"]}
    return got, msg.usage


def run_translate():
    params = {"select": "id,seq,kind,text_de,text_en,page", "pub": f"eq.{PUB}", "order": "seq"}
    if PART == "0":
        params["part"] = "is.null"
    else:
        params["part"] = f"eq.{PART}"
    rows = get("library_passages", params)
    todo = [r for r in rows if not r["text_en"]]
    log("part", PART, len(rows), "passages", len(todo), "to translate")
    parts = {p["no"]: p for p in get("library_parts", {"select": "*", "pub": f"eq.{PUB}"})}
    head = f"Book: Abegg/Matter, Bally – Hier und Jetzt (2025). Part {PART}: {parts.get(int(PART), {}).get('title_de', '')}" if PART != "0" else "Book front matter and picture-essay introductions."
    ctx = ""
    done_en = {r["id"]: r["text_en"] for r in rows if r["text_en"]}
    batch, size = [], 0
    tin = tout = 0

    def flush():
        nonlocal batch, size, ctx, tin, tout
        if not batch:
            return
        got, u = translate_batch([(r["id"], r["kind"], r["text_de"]) for r in batch], ctx[-2500:], head)
        tin += u.input_tokens
        tout += u.output_tokens
        missing = [r["id"] for r in batch if not got.get(r["id"])]
        if missing:
            log("missing ids, retrying singly", missing)
            for r in batch:
                if r["id"] in missing:
                    g2, _ = translate_batch([(r["id"], r["kind"], r["text_de"])], ctx[-2500:], head)
                    got[r["id"]] = g2.get(r["id"])
        for r in batch:
            if got.get(r["id"]):
                patch("library_passages", {"id": f"eq.{r['id']}"}, {"text_en": got[r["id"]], "translation_model": MODEL})
                ctx += "\n\n" + got[r["id"]]
        log(f"translated through seq {batch[-1]['seq']} tokens in {tin} out {tout}")
        batch, size = [], 0

    for r in rows:
        if r["text_en"]:
            ctx += "\n\n" + r["text_en"]
            continue
        batch.append(r)
        size += len(r["text_de"])
        if size > 9000 or len(batch) >= 14:
            flush()
    flush()


def run_notes():
    rows = [r for r in get("library_notes", {"select": "no,text_de,text_en", "pub": f"eq.{PUB}", "order": "no"}) if not r["text_en"]]
    log(len(rows), "notes to translate")
    sys_extra = ("These are endnotes. Translate the prose. Keep titles of cited works, archive signatures and newspaper names "
                 "exactly as in the original; translate apparatus: Ebd. -> Ibid., S. -> p., Zit. nach -> Quoted from, vgl. -> cf., "
                 "siehe -> see, Hg. -> ed., o. J. -> n.d.")

    def one(chunk):
        got, _ = translate_batch([(r["no"], "note", r["text_de"]) for r in chunk], "", "Endnotes. " + sys_extra)
        for r in chunk:
            if got.get(r["no"]):
                patch("library_notes", {"pub": f"eq.{PUB}", "no": f"eq.{r['no']}"}, {"text_en": got[r["no"]]})
        return len(chunk)

    chunks = [rows[i:i + 25] for i in range(0, len(rows), 25)]
    with ThreadPoolExecutor(WORKERS) as ex:
        n = sum(ex.map(one, chunks))
    log("notes done", n)


def run_headings():
    rows = get("library_passages", {"select": "id,kind,text_de,text_en,heading_de,sub_de", "pub": f"eq.{PUB}", "order": "seq"})
    hmap = {r["text_de"]: r["text_en"] for r in rows if r["kind"] in ("heading", "subheading") and r["text_en"]}
    n = 0
    for r in rows:
        body = {}
        if r["heading_de"] and hmap.get(r["heading_de"]):
            body["heading_en"] = hmap[r["heading_de"]]
        if r["sub_de"] and hmap.get(r["sub_de"]):
            body["sub_en"] = hmap[r["sub_de"]]
        if body:
            patch("library_passages", {"id": f"eq.{r['id']}"}, body)
            n += 1
    parts = get("library_parts", {"select": "no,title_de", "pub": f"eq.{PUB}"})
    got, _ = translate_batch([(p["no"], "part title", p["title_de"]) for p in parts], "", "Part titles of the book.")
    for p in parts:
        if got.get(p["no"]):
            patch("library_parts", {"pub": f"eq.{PUB}", "no": f"eq.{p['no']}"}, {"title_en": got[p["no"]]})
    log("headings filled", n)


# ---------------------------------------------------------------- plates
PICTURE = ["product-photo", "poster", "advertisement", "catalogue-page", "shop-window", "store-interior", "people-photo",
           "group-photo", "portrait", "event", "factory", "machine", "packaging", "drawing", "diagram", "document", "building",
           "landscape", "map", "chart", "other"]
PL_TOOL = {"name": "record_plate", "description": "Record the caption choice and description for one book photograph.",
           "input_schema": {"type": "object", "properties": {
               "caption_choice": {"type": "integer", "description": "Index of the candidate caption that describes THIS photo, or -1 if none fits."},
               "choice_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
               "caption_en": {"type": "string", "description": "Faithful, complete English translation of the chosen German caption. Empty if none."},
               "title_en": {"type": "string", "description": "Short catalogue title in English, max 10 words, e.g. 'Oxford lace-up, brown calf, 1947' or 'Cutting upper leather, Schönenwerd, 1948'."},
               "description_en": {"type": "string", "description": "2 to 3 plain sentences describing what is visible. No speculation."},
               "picture_type": {"type": "string", "enum": PICTURE},
               "date_text": {"type": "string", "description": "Date as stated in the caption (e.g. '1947', 'um 1945' -> 'c. 1945'), else ''."},
               "era": {"type": "string", "description": "Decade, from caption if stated, else visual estimate, e.g. '1940s', or 'unclear'."},
               "photographer": {"type": "string", "description": "Photographer or artist named in the caption, else ''."},
               "themes": {"type": "array", "items": {"type": "string"}},
               "materials": {"type": "array", "items": {"type": "string"}},
               "colours": {"type": "array", "items": {"type": "string"}},
               "details": {"type": "array", "items": {"type": "string"}},
               "shoe_type": {"type": "string"}, "bag_type": {"type": "string"},
               "models": {"type": "array", "items": {"type": "string"}, "description": "Bally model or line names stated or legible (e.g. Scribe, Vasano, Granados)."},
               "places": {"type": "array", "items": {"type": "string"}},
               "people_named": {"type": "array", "items": {"type": "string"}},
               "visible_text": {"type": "string"},
               "people": {"type": "boolean"}},
               "required": ["caption_choice", "choice_confidence", "caption_en", "title_en", "description_en", "picture_type", "era", "people"]}}


def img_block(path):
    r = requests.get(f"{SB}/storage/v1/object/public/web/{path}", timeout=60)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    im.thumbnail((1100, 1100))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=88)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b.getvalue()).decode()}}


def plate_one(pl, ctx_text):
    cands = pl["caption_candidates"] or []
    lines = []
    for i, c in enumerate(cands):
        ref = f"(caption no. {c['no']} on p. {c['page']})" if "no" in c else f"(listed for p. {c['ref']})"
        lines.append(f"[{i}] {ref} {c['de']}")
    hint = ""
    if pl.get("caption_de"):
        hint = f"Layout matching suggests this caption: {pl['caption_de'][:200]!r}. Confirm only if it truly fits the photo."
    text = (f"Book: Abegg/Matter, Bally – Hier und Jetzt (2025). This photo is on page {pl['page']}"
            f"{' (picture essay)' if pl['essay'] else ''}, position {pl['idx']} on the page, figure label {pl['fig_no']}.\n"
            f"{hint}\nCandidate German captions:\n" + ("\n".join(lines) or "(none)") +
            f"\n\nSurrounding book text (German, for context):\n{ctx_text[:2500]}")
    msg = client.messages.create(
        model=MODEL, max_tokens=2000,
        system=("You catalogue photographs from a Swiss company history of Bally. Pick the caption that describes this exact photo "
                "using what you see (object type, colours, people, setting, posters' text) and the figure numbering. "
                "Several photos in a picture essay share one caption list; each caption belongs to exactly one photo. " + GLOSSARY),
        tools=[PL_TOOL], tool_choice={"type": "tool", "name": "record_plate"},
        messages=[{"role": "user", "content": [img_block(pl["path"]), {"type": "text", "text": text}]}])
    return next(b.input for b in msg.content if b.type == "tool_use"), msg.usage


def run_plates():
    plates = get("library_plates", {"select": "*", "pub": f"eq.{PUB}", "order": "sort"})
    if os.environ.get("ONLY_PENDING", "1") == "1":
        plates = [p for p in plates if p["ai_status"] != "done"]
    passages = get("library_passages", {"select": "page,pages,text_de", "pub": f"eq.{PUB}", "order": "seq"})
    log(len(plates), "plates")

    def ctx_for(page):
        s = (page, page + 1) if page % 2 == 0 else (page - 1, page)
        return "\n".join(p["text_de"] for p in passages if set(p.get("pages") or [p["page"]]) & set(s))

    def one(pl):
        try:
            data, u = plate_one(pl, ctx_for(pl["page"]))
            cands = pl["caption_candidates"] or []
            ch = data.get("caption_choice", -1)
            cap = cands[ch]["de"] if isinstance(ch, int) and 0 <= ch < len(cands) else None
            body = {"caption_de": cap, "caption_en": data.get("caption_en") or None,
                    "caption_source": ("ai-" + data.get("choice_confidence", "low")) if cap else "none",
                    "title_en": data.get("title_en"), "description_en": data.get("description_en"),
                    "picture_type": data.get("picture_type"), "era": data.get("era"), "date_text": data.get("date_text") or None,
                    "photographer": data.get("photographer") or None, "ai": data, "ai_model": MODEL, "ai_status": "done"}
            if cap and "no" in cands[ch]:
                body["fig_no"] = cands[ch]["no"]
            patch("library_plates", {"id": f"eq.{pl['id']}"}, body)
            return 1
        except Exception as e:
            log("plate error", pl["id"], type(e).__name__, str(e)[:200])
            patch("library_plates", {"id": f"eq.{pl['id']}"}, {"ai_status": "error"})
            return 0

    with ThreadPoolExecutor(WORKERS) as ex:
        n = sum(ex.map(one, plates))
    log("plates done", n, "of", len(plates))


if __name__ == "__main__":
    try:
        {"plates": run_plates, "translate": run_translate, "notes": run_notes, "headings": run_headings}[MODE]()
    except anthropic.AuthenticationError as e:
        log("auth failed", str(e)[:200])
        sys.exit(1)
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
