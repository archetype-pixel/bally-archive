"""Load 'Bally - Hier und Jetzt' into the separate Library tables and web/library/ storage.
Never touches objects, assets or the catalogue. Idempotent (upserts).
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, DATA (path to library-data checkout)"""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
import requests

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
DATA = os.environ.get("DATA", "data")
PUB = "hier-und-jetzt"
PREFIX = f"library/{PUB}"
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def upsert(table, rows, conflict=None):
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        params = {"on_conflict": conflict} if conflict else None
        for t in range(5):
            r = requests.post(f"{SB}/rest/v1/{table}", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"},
                              params=params, json=chunk, timeout=120)
            if r.ok:
                break
            log(table, r.status_code, r.text[:300])
            time.sleep(3 * (t + 1))
        else:
            raise RuntimeError(table)
    log(table, len(rows))


def put(local, remote):
    with open(local, "rb") as f:
        body = f.read()
    for t in range(5):
        try:
            r = requests.post(f"{SB}/storage/v1/object/web/{remote}", data=body, timeout=120,
                              headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "image/jpeg",
                                       "x-upsert": "true", "cache-control": "max-age=31536000"})
            if r.ok:
                return True
            log("put", remote, r.status_code, r.text[:150])
        except requests.RequestException as e:
            log("put net", remote, type(e).__name__)
        time.sleep(2 * (t + 1))
    return False


def main():
    b = json.load(open(f"{DATA}/library.json"))
    dims = json.load(open(f"{DATA}/page_dims.json"))

    if os.environ.get("SKIP_STORAGE") != "1":
        jobs = []
        for f in sorted(os.listdir(f"{DATA}/plates")):
            jobs.append((f"{DATA}/plates/{f}", f"{PREFIX}/plates/{f}"))
            jobs.append((f"{DATA}/thumbs/{f}", f"{PREFIX}/thumbs/{f}"))
        for f in sorted(os.listdir(f"{DATA}/pages")):
            jobs.append((f"{DATA}/pages/{f}", f"{PREFIX}/pages/{f}"))
        with ThreadPoolExecutor(16) as ex:
            ok = list(ex.map(lambda j: put(*j), jobs))
        log("storage", sum(ok), "of", len(jobs))
        if not all(ok):
            raise RuntimeError("storage uploads failed")

    upsert("library_publications", [{
        "id": PUB, "title": "Bally – Hier und Jetzt", "subtitle": "Geschichte eines Schweizer Unternehmens",
        "title_en": "Bally – Here and Now", "subtitle_en": "The History of a Swiss Company",
        "authors": ["Philipp Abegg", "Martin Matter"], "publisher": "Hier und Jetzt, Verlag für Kultur und Geschichte, Zürich",
        "year": 2025, "isbn": "978-3-03919-649-4", "language": "de",
        "credit_note": "Philipp Abegg, Martin Matter: Bally – Hier und Jetzt. Geschichte eines Schweizer Unternehmens. Zürich: Hier und Jetzt, 2025.",
        "about_de": "Die Geschichte von Bally von den Anfängen der Bandweberei in Schönenwerd bis 1999, in fünf Teilen.",
        "about_en": "The history of Bally from the ribbon-weaving beginnings in Schönenwerd to 1999, in five parts.",
        "cover_plate": "hj-p118-1", "published": True}])
    upsert("library_parts", [{"pub": PUB, "no": p["no"], "title_de": p["title_de"], "years": p["years"], "author": p["author"],
                              "page_from": p["page_from"], "page_to": p["page_to"]} for p in b["parts"]])
    upsert("library_pages", [{"pub": PUB, "page": int(k), "path": f"{PREFIX}/pages/{int(k):03d}.jpg", "width": v[0], "height": v[1]}
                             for k, v in dims.items()])

    # passages: renumber in reading order (essay intros sit after their essay)
    ps = sorted(b["passages"], key=lambda p: (p["page"], p["seq"]))
    rows = []
    for i, p in enumerate(ps, 1):
        rows.append({"pub": PUB, "seq": i, "part": p["part"], "page": p["page"], "pages": p.get("pages") or [p["page"]],
                     "kind": p["kind"], "heading_de": p.get("heading"), "sub_de": p.get("sub"), "text_de": p["de"]})
    upsert("library_passages", rows, "pub,seq")
    upsert("library_notes", [{"pub": PUB, "no": n["no"], "part": n["part"], "text_de": n["de"]} for n in b["notes"]], "pub,no")
    upsert("library_bibliography", [{"pub": PUB, "ord": i, "section": e["section"], "text": e["de"]}
                                    for i, e in enumerate(b["bibliography"], 1)], "pub,ord")

    # plates with caption candidates for AI confirmation
    caps_by_spread = {}
    for c in b["captions"]:
        s = (c["page"], c["page"] + 1) if c["page"] % 2 == 0 else (c["page"] - 1, c["page"])
        caps_by_spread.setdefault(s, []).append({"page": c["page"], "no": c["no"], "de": c["de"]})
    essay_caps = {}
    for c in b["essay_caps"]:
        essay_caps.setdefault(c["essay"], []).append({"ref": c["ref"], "de": c["de"]})
    photog = "Nicole Hänni"
    prow = []
    for k, pl in enumerate(sorted(b["plates"], key=lambda x: (x["page"], x["idx"])), 1):
        s = (pl["page"], pl["page"] + 1) if pl["page"] % 2 == 0 else (pl["page"] - 1, pl["page"])
        cands = essay_caps.get(pl.get("essay_start"), []) if pl["essay"] else caps_by_spread.get(s, [])
        # dedupe group captions
        seen, uniq = set(), []
        for c in cands:
            if c["de"] not in seen:
                seen.add(c["de"])
                uniq.append(c)
        prow.append({"id": f"hj-{pl['id']}", "pub": PUB, "page": pl["page"], "idx": pl["idx"], "fig_no": pl["no"],
                     "essay": pl["essay"], "essay_start": pl.get("essay_start"), "bbox": pl["bbox"], "width": pl["w"], "height": pl["h"],
                     "path": f"{PREFIX}/plates/{pl['id']}.jpg", "thumb_path": f"{PREFIX}/thumbs/{pl['id']}.jpg",
                     "caption_de": pl.get("caption_de"), "caption_source": pl.get("caption_source"), "caption_candidates": uniq,
                     "credit": pl.get("credit"), "sort": k})
    upsert("library_plates", prow)
    log("done", len(rows), "passages", len(prow), "plates")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
