"""Join captions to plates, parse picture credits and notes. Writes out/library.json."""
import json, re, sys
import pymupdf

OUT = sys.argv[2]
d = pymupdf.open(sys.argv[1])
b = json.load(open(f"{OUT}/book.json"))
plates = b["plates"]

# ---- chapter captions: caption on page p, number n -> plate labelled n in the same spread
def spread(p):
    return (p, p + 1) if p % 2 == 0 else (p - 1, p)

by_spread = {}
for pl in plates:
    if not pl["essay"]:
        by_spread.setdefault(spread(pl["page"]), []).append(pl)
used = set()
unmatched_caps = []
for c in b["captions"]:
    cands = [pl for pl in by_spread.get(spread(c["page"]), []) if pl["no"] == c["no"] and pl["id"] not in used]
    if not cands:
        unmatched_caps.append(c)
        continue
    # prefer plate on the same page, then the facing page
    cands.sort(key=lambda pl: pl["page"] != c["page"])
    pl = cands[0]
    pl["caption_de"] = c["de"]
    pl["caption_source"] = "label"
    used.add(pl["id"])
# second pass: plates on a spread with no label but exactly one unmatched caption
for c in list(unmatched_caps):
    free = [pl for pl in by_spread.get(spread(c["page"]), []) if pl["id"] not in used]
    if len(free) == 1:
        free[0]["caption_de"] = c["de"]
        free[0]["caption_source"] = "spread"
        used.add(free[0]["id"])
        unmatched_caps.remove(c)

# ---- essay captions: candidates only; vision model assigns (book page refs contain errors)
for pl in plates:
    if pl["essay"]:
        a = max(e for e in (43, 99, 207, 267) if e <= pl["page"])
        pl["essay_start"] = a

# ---- credits (p.309)
txt = d[308].get_text()
txt = txt.split("Bildnachweis", 1)[1]
photog = re.search(r"Sämtliche Fotografien von Schuhen.*", txt, re.S)
photog = re.sub(r"\s+", " ", photog.group(0)).strip() if photog else None
txt = txt[: txt.find("Sämtliche Fotografien")] if "Sämtliche Fotografien" in txt else txt
entries = re.split(r"\n(?=[A-ZÄÖÜ][^\n:]{3,60}:\s*\n)", "\n" + txt)
credit_map = {}  # (page, no or None) -> source
for e in entries:
    e = e.strip()
    if ":" not in e:
        continue
    src, body = e.split(":", 1)
    src = src.strip()
    body = re.sub(r"\s+", " ", body.replace("\t", " "))
    body = body.replace("Sämtliche Bilder Umschlag und Klappen,", "")
    body = re.sub(r"Seiten?\s", "", body)
    body = re.sub(r"\([^)]*\)", "", body)
    # split into items at commas not inside a number list
    items = re.findall(r"(\d{1,3})(?:[–-](\d{1,3}))?(?:/(\d{1,3}))?(?:\s*Nr\.\s*([\d–\-, und]+?))?(?=,\s*\d{1,3}(?:\s|[–/,.]|$)|\.$|$)", body.strip().rstrip("."))
    for p, p2, p3, nrs in items:
        pages = list(range(int(p), int(p2) + 1)) if p2 else [int(p)] + ([int(p3)] if p3 else [])
        nos = None
        if nrs:
            nos = set()
            for part in re.split(r",|und", nrs):
                part = part.strip()
                if not part:
                    continue
                if re.match(r"\d+[–-]\d+", part):
                    x, y = re.split(r"[–-]", part)
                    nos |= set(range(int(x), int(y) + 1))
                elif part.isdigit():
                    nos.add(int(part))
        for pg in pages:
            if nos:
                for n in nos:
                    credit_map[(pg, n)] = src
            else:
                credit_map.setdefault((pg, None), src)

for pl in plates:
    src = credit_map.get((pl["page"], pl["no"])) or credit_map.get((pl["page"], None))
    if not src:
        srcs = {v for (pg, n), v in credit_map.items() if pg == pl["page"]}
        src = srcs.pop() if len(srcs) == 1 else None
        pl["credit_options"] = sorted({v for (pg, n), v in credit_map.items() if pg == pl["page"]})
    pl["credit"] = src
missing_credit = [pl["id"] for pl in plates if not pl.get("credit")]

# ---- notes (Anmerkungen): "N\t text" entries, per part numbering restarts
notes = []
raw = b["notes_raw"]
raw = re.sub(r"(?m)^\s*Anmerkungen\s*$", "", raw)
chunks = re.split(r"(?m)^(\d{1,3})\t\s*\n?", raw)
part = 0
last = 10 ** 6
for i in range(1, len(chunks), 2):
    n = int(chunks[i])
    if n <= last and n == 1:
        part += 1
    last = n
    t = chunks[i + 1]
    t = re.sub(r"-\n(?=[a-zäöü])", "", t.replace("\xad\n", "").replace("\xad", ""))
    t = re.sub(r"\s+", " ", t).strip()
    notes.append({"part": part, "no": n, "de": t})

bib = None
if notes and "Bibliografie" in notes[-1]["de"]:
    notes[-1]["de"], bib = notes[-1]["de"].split("Bibliografie", 1)
    notes[-1]["de"] = notes[-1]["de"].strip()
bib_entries = []
if bib:
    sec = "Allgemeine Literatur"
    for chunk in re.split(r"(Allgemeine Literatur|Gedruckte Quellen|Ungedruckte Quellen)", bib):
        chunk = chunk.strip()
        if chunk in ("Allgemeine Literatur", "Gedruckte Quellen", "Ungedruckte Quellen"):
            sec = chunk
            continue
        for e in re.split(r"(?<=\.)\s+(?=[A-ZÄÖÜ][\w’'\-]+(?: [\w.]+)?,\s|[A-Z][\w.\- ]{1,40}\([A-Z][^)]*\):|\d{2,3} Jahre )", chunk):
            if e.strip():
                bib_entries.append({"section": sec, "de": e.strip()})
b["bibliography"] = bib_entries
b["plates"] = plates
b["notes"] = notes
b["credit_map"] = [[k[0], k[1], v] for k, v in credit_map.items()]
b["credit_photographer"] = photog
b.pop("notes_raw", None)
json.dump(b, open(f"{OUT}/library.json", "w"), ensure_ascii=False, indent=1)
cap = sum(1 for pl in plates if pl.get("caption_de"))
print("plates", len(plates), "chapter captioned", cap, "of", sum(1 for pl in plates if not pl["essay"]),
      "unmatched captions", len(unmatched_caps), "no credit", len(missing_credit), "notes", len(notes),
      "parts in notes", part)
print("unmatched", [(c["page"], c["no"], c["de"][:50]) for c in unmatched_caps][:20])
print("uncaptioned chapter plates", [pl["id"] for pl in plates if not pl["essay"] and not pl.get("caption_de")])
print("no credit", missing_credit[:40])
import collections
print(collections.Counter(pl["credit"] for pl in plates))
