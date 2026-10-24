"""Load Roman Wild, 'Auf Schritt und Tritt. Der schweizerische Schuhmarkt 1918-1948' (NZZ Libro 2019) into the Library.
German original (text_de); the English translation is produced afterwards by library_ai.py (translate / notes / plates modes).
Licence CC BY-NC-ND 4.0; English translation with the rights holder's permission. All plates public, credits verbatim.
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, DATA (folder with library.json, plates/, thumbs/, pages/), SKIP_STORAGE=1 to skip uploads"""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
import requests

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
DATA = os.environ.get("DATA", "data/book3")
PUB = "schritt-und-tritt"
PREFIX = f"library/{PUB}"
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def upsert(table, rows, conflict=None):
    for i in range(0, len(rows), 200):
        r = requests.post(f"{SB}/rest/v1/{table}", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"},
                          params={"on_conflict": conflict} if conflict else None, json=rows[i:i + 200], timeout=120)
        if not r.ok:
            log(table, r.status_code, r.text[:300])
            r.raise_for_status()
    log(table, len(rows))


def delete(table, params):
    r = requests.delete(f"{SB}/rest/v1/{table}", headers=H, params=params, timeout=120)
    r.raise_for_status()


def put(local, remote):
    body = open(local, "rb").read()
    for t in range(5):
        r = requests.post(f"{SB}/storage/v1/object/web/{remote}", data=body, timeout=120,
                          headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "image/jpeg", "x-upsert": "true", "cache-control": "max-age=31536000"})
        if r.ok:
            return True
        time.sleep(2 * (t + 1))
    log("put failed", remote, r.status_code, r.text[:120])
    return False


def main():
    b = json.load(open(f"{DATA}/library.json"))
    if os.environ.get("SKIP_STORAGE") != "1":
        jobs = [(f"{DATA}/{k}/{f}", f"{PREFIX}/{k}/{f}") for k in ("plates", "thumbs", "pages") for f in sorted(os.listdir(f"{DATA}/{k}"))]
        with ThreadPoolExecutor(16) as ex:
            ok = list(ex.map(lambda j: put(*j), jobs))
        log("storage", sum(ok), "of", len(jobs))

    lic = "CC BY-NC-ND 4.0 (https://creativecommons.org/licenses/by-nc-nd/4.0/), Roman Wild / NZZ Libro 2019; English translation by the Bally Heritage Archive with the rights holder's permission. Images: rights as credited (Bildnachweis)."
    upsert("library_publications", [{
        "id": PUB, "title": "Auf Schritt und Tritt", "subtitle": "Der schweizerische Schuhmarkt 1918–1948",
        "title_en": "Every Step of the Way", "subtitle_en": "The Swiss Shoe Market 1918–1948",
        "authors": ["Roman Wild"], "publisher": "NZZ Libro (Schwabe Verlagsgruppe), Basel", "year": 2019,
        "isbn": "978-3-03810-406-3 (print); 978-3-03810-460-5 (PDF)", "language": "de", "languages": ["de", "en"], "sort": 3, "license": lic,
        "credit_note": "Roman Wild: Auf Schritt und Tritt. Der schweizerische Schuhmarkt 1918–1948. Basel: NZZ Libro, 2019. Dissertation, University of Zurich, 2017. Licensed CC BY-NC-ND 4.0.",
        "rights_note": "Picture credits as listed in the book's Bildnachweis. Photographs of Bally evening shoes on the cover and pp. 186–200: © Franco Garlaschelli, reproduced with permission of Bally-Schuhfabriken AG.",
        "about_de": "Zwischen dem Ersten und Zweiten Weltkrieg war der schweizerische Schuhmarkt fest in die Gesellschaft eingebettet. Der Historiker Roman Wild untersucht die marktspezifischen Herausforderungen für Schuhmacher, Schuhhändler, Schuhfabriken wie Bally, Konsumentinnen, Verbände und Wissenschaft in den Jahren 1918–1948.",
        "about_en": "A history of the Swiss shoe market between 1918 and 1948: shoemakers and retailers who lost ground, Bally's rise to market power and its push into retailing, organized housewives as consumers, cartel regulation against Bata, and the birth of market research. Includes a photo essay of Bally evening shoes, 1922–1939.",
        "cover_plate": "st-cover-1", "published": True}])

    chapters = b["chapters"]
    end = {c["no"]: (chapters[i + 1]["page_from"] - 1 if i + 1 < len(chapters) else 476) for i, c in enumerate(chapters)}
    upsert("library_parts", [{"pub": PUB, "no": c["no"], "title_de": c["title"], "title_en": None, "years": None, "author": "Roman Wild",
                              "page_from": c["page_from"], "page_to": end[c["no"]]} for c in chapters])

    pages = sorted(f for f in os.listdir(f"{DATA}/pages") if re.fullmatch(r"\d{3}\.jpg", f))
    upsert("library_pages", [{"pub": PUB, "page": int(f[:3]), "path": f"{PREFIX}/pages/{f}", "width": None, "height": None} for f in pages])

    # passages: replace this book's rows wholesale so re-runs stay clean
    delete("library_passages", {"pub": f"eq.{PUB}"})
    titles = {c["no"]: c["title"] for c in chapters}
    rows, seq = [], 0
    front = [("Den namenlosen Störschuhmachern gewidmet", 2, "body"),
             ("«I have an odd idea that most general questions can be expressed in terms of footwear — which is perhaps why cobblers are often such philosophical men. Accident it may be, gave me this persuasion. A very considerable part of my childhood was spent in an underground kitchen; the window opened upon a bricked-in space, surmounted by a grating before my father's shop window. So that, when I looked out of the window, instead of seeing — as children of a higher upbringing would do — the heads and bodies of people, I saw their under side. I got acquainted indeed with all sorts of social types as boots simply, indeed, as the soles of boots; and only subsequently, and with care, have I fitted heads, bodies, and legs to these pediments.» H. G. Wells, This Misery of Boots, 1907", 5, "quote")]
    for t, pg, k in front:
        seq += 10
        rows.append({"pub": PUB, "seq": seq, "part": None, "page": pg, "pages": [pg], "kind": k, "heading_de": None, "sub_de": None,
                     "text_de": t, "text_en": t if k == "quote" else None})
    cur_part, section = None, None
    for p in b["passages"]:
        if p["part"] != cur_part:
            cur_part, section = p["part"], None
            seq += 10
            rows.append({"pub": PUB, "seq": seq, "part": p["part"], "page": next(c["page_from"] for c in chapters if c["no"] == p["part"]),
                         "pages": None, "kind": "heading", "heading_de": titles[p["part"]], "sub_de": None,
                         "text_de": f"{p['part']} {titles[p['part']]}" if p["part"] <= 7 else titles[p["part"]], "text_en": None})
        if p["kind"] == "subheading" and p.get("level") == 1:
            section = p["de"]
        seq += 10
        rows.append({"pub": PUB, "seq": seq, "part": p["part"], "page": p["page"], "pages": p.get("pages") or [p["page"]], "kind": p["kind"],
                     "heading_de": titles[p["part"]], "sub_de": section, "text_de": p["de"], "text_en": None})
    for r in rows:
        r["pages"] = r["pages"] or [r["page"]]
    upsert("library_passages", rows, "pub,seq")

    # notes: key = chapter*1000 + number (part 8 = the appendix essay 'Forschungsstand')
    delete("library_notes", {"pub": f"eq.{PUB}"})
    merged = {}
    for n in b["notes"]:
        k = n["part"] * 1000 + n["no"]
        merged[k] = (merged[k] + " " + n["de"]) if k in merged else n["de"]
    upsert("library_notes", [{"pub": PUB, "no": k, "part": k // 1000, "text_de": v, "text_en": None} for k, v in sorted(merged.items())], "pub,no")

    delete("library_bibliography", {"pub": f"eq.{PUB}"})
    names = {"Unpublizierte Quellen": "Unpublished sources (Unpublizierte Quellen)", "Publizierte Quellen": "Published sources (Publizierte Quellen)",
             "Forschungsliteratur": "Research literature (Forschungsliteratur)"}
    upsert("library_bibliography", [{"pub": PUB, "ord": x["ord"], "section": names.get(x["section"], x["section"]), "text": x["text"]} for x in b["bibliography"]])

    if os.environ.get("RESET_PLATES") == "1":
        delete("library_plates", {"pub": f"eq.{PUB}"})   # also drops fingerprints and links of this book
    have = requests.get(f"{SB}/rest/v1/library_plates", headers=H, params={"select": "id", "pub": f"eq.{PUB}"}, timeout=60).json()
    if have:
        log("plates already loaded, kept", len(have))
        log("done")
        return
    prow = []
    for k, pl in enumerate(sorted(b["plates"], key=lambda x: (x["page"], x["idx"])), 1):
        cap = pl.get("caption")
        credit = pl.get("credit")
        if pl.get("credit_source") and credit:
            credit = f"{pl['credit_source']}: {credit}"
        prow.append({"id": f"st-{pl['id']}", "pub": PUB, "page": pl["page"], "idx": pl["idx"], "fig_label": pl.get("fig"), "fig_no": pl.get("fig_no"),
                     "essay": pl["type"] == "essay", "bbox": pl["bbox"], "width": pl["w"], "height": pl["h"],
                     "path": f"{PREFIX}/plates/{pl['id']}.jpg", "thumb_path": f"{PREFIX}/thumbs/{pl['id']}.jpg",
                     "caption_de": cap, "caption_en": None, "caption_source": "layout" if cap else None,
                     "caption_candidates": [{"ref": pl["page"], "de": cap}] if cap else [],
                     "credit": credit, "rights": "public", "date_text": pl.get("date_text"), "sort": k, "ai_status": "pending",
                     "picture_type": {"table": "chart", "chart": "chart", "opener": "drawing"}.get(pl["type"])})
    upsert("library_plates", prow)
    log("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
