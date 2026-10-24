"""Load 'Bally - A History of Footwear in the Interwar Period' (Schlittler/Tietze eds., transcript 2021) into the Library.
English original; text CC BY 4.0; images credited per caption. Third-party images are loaded with rights='restricted'
and not shown publicly until the owner decides. Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, DATA (folder with library.json, plates/, thumbs/, pages/)"""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
import requests

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
DATA = os.environ.get("DATA", "data/interwar")
PUB = "interwar-footwear"
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


def put(local, remote, bucket="web"):
    body = open(local, "rb").read()
    for t in range(5):
        r = requests.post(f"{SB}/storage/v1/object/{bucket}/{remote}", data=body, timeout=120,
                          headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "image/jpeg", "x-upsert": "true", "cache-control": "max-age=31536000"})
        if r.ok:
            return True
        time.sleep(2 * (t + 1))
    log("put failed", remote, r.status_code, r.text[:120])
    return False


def main():
    b = json.load(open(f"{DATA}/library.json"))
    restricted = {p["id"] for p in b["plates"] if p.get("owner") != "bally"}
    jobs = []
    for k in ("plates", "thumbs", "pages"):
        for f in sorted(os.listdir(f"{DATA}/{k}")):
            priv = k != "pages" and f[:-4] in restricted
            jobs.append((f"{DATA}/{k}/{f}", f"{PREFIX}/{k}/{f}", "archive" if priv else "web"))
    with ThreadPoolExecutor(16) as ex:
        ok = list(ex.map(lambda j: put(*j), jobs))
    log("storage", sum(ok), "of", len(jobs))

    upsert("library_publications", [{
        "id": PUB, "title": "Bally – A History of Footwear in the Interwar Period", "title_en": "Bally – A History of Footwear in the Interwar Period",
        "subtitle": "Fashion Studies, vol. 10", "subtitle_en": "Fashion Studies, vol. 10",
        "authors": ["Anna-Brigitte Schlittler (ed.)", "Katharina Tietze (ed.)"], "publisher": "transcript Verlag, Bielefeld", "year": 2021,
        "isbn": "978-3-8376-5738-8", "language": "en", "languages": ["en"], "sort": 2,
        "license": "Text CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Images: rights as credited in each caption.",
        "credit_note": "Anna-Brigitte Schlittler, Katharina Tietze (eds.): Bally – A History of Footwear in the Interwar Period. Bielefeld: transcript, 2021. https://doi.org/10.14361/9783839457382",
        "about_en": "Eight research essays on Bally in the 1920s to 1940s: fashion cycles, wartime design, evening shoes for New York, utility shoes, the design process, gold leather, window-dressing and the 1939 Swiss National Exposition.",
        "cover_plate": "iw-cover-1", "published": True}])
    upsert("library_parts", [{"pub": PUB, "no": c["no"], "title_de": None, "title_en": c["title"] + (f": {c['subtitle']}" if c.get("subtitle") else ""),
                              "years": None, "author": c["author"], "page_from": c["page_from"],
                              "page_to": (b["chapters"][i + 1]["page_from"] - 1) if i + 1 < len(b["chapters"]) else 192}
                             for i, c in enumerate(b["chapters"])])
    pages = sorted(f for f in os.listdir(f"{DATA}/pages") if re.fullmatch(r"\d{3}\.jpg", f))
    upsert("library_pages", [{"pub": PUB, "page": int(f[:3]), "path": f"{PREFIX}/pages/{f}", "width": None, "height": None} for f in pages])

    parts = {c["no"]: c for c in b["chapters"]}
    rows = []
    for i, p in enumerate(b["passages"], 1):
        ch = parts.get(p["part"], {})
        rows.append({"pub": PUB, "seq": i * 10, "part": p["part"], "page": p["page"], "pages": p.get("pages") or [p["page"]],
                     "kind": p["kind"], "heading_en": ch.get("title"), "sub_en": None, "text_de": None, "text_en": p["en"]})
    # chapter heading passages so the reader shows titles
    for c in b["chapters"]:
        first = next((r for r in rows if r["part"] == c["no"]), None)
        if first:
            rows.append({"pub": PUB, "seq": first["seq"] - 5, "part": c["no"], "page": c["page_from"], "pages": [c["page_from"]], "kind": "heading",
                         "heading_en": c["title"], "sub_en": c.get("subtitle"), "text_de": None,
                         "text_en": c["title"] + (f": {c['subtitle']}" if c.get("subtitle") else "") + (f" ({c['author']})" if c.get("author") else "")})
    upsert("library_passages", rows, "pub,seq")
    # notes restart per chapter: key them as chapter*1000+no
    upsert("library_notes", [{"pub": PUB, "no": n["part"] * 1000 + n["no"], "part": n["part"], "text_de": None, "text_en": n["en"]} for n in b["notes"]], "pub,no")

    caps = b["captions"]
    prow = []
    for k, pl in enumerate(sorted(b["plates"], key=lambda x: (x["page"], x["idx"])), 1):
        near = [{"page": c["page"], "label": ",".join(c.get("labels") or []), "en": c["body"], "credit": c.get("credit")}
                for c in caps if abs(c["page"] - pl["page"]) <= 1]
        owner = pl.get("owner", "unknown")
        prow.append({"id": f"iw-{pl['id']}", "pub": PUB, "page": pl["page"], "idx": pl["idx"], "fig_label": pl.get("fig"), "essay": False,
                     "bbox": pl["bbox"], "width": pl["w"], "height": pl["h"], "path": f"{PREFIX}/plates/{pl['id']}.jpg",
                     "thumb_path": f"{PREFIX}/thumbs/{pl['id']}.jpg", "caption_en": pl.get("caption"), "caption_de": None,
                     "caption_source": "layout" if pl.get("caption") else None, "caption_candidates": near,
                     "credit": pl.get("credit_raw"), "rights": "public" if owner == "bally" else "restricted", "sort": k, "ai_status": "pending"})
    upsert("library_plates", prow)
    log("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
