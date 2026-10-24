"""Extract 'Bally - A History of Footwear in the Interwar Period' (transcript 2021, English, CC BY 4.0 text).
Writes out/library.json, out/web/{plates,thumbs,pages}/ ready for the Library loader."""
import io, json, os, re, sys
import pymupdf
from PIL import Image

SRC, OUT = sys.argv[1], sys.argv[2]
for s in ("plates", "thumbs", "pages"):
    os.makedirs(f"{OUT}/web/{s}", exist_ok=True)
d = pymupdf.open(SRC)
OFF = 1  # pdf page = printed page + 1

# chapters from the contents pages (printed page numbers)
CHAPTERS = [
    (1, "Introduction", None, "Anna-Brigitte Schlittler, Katharina Tietze", 9),
    (2, "Business Cycles in the Fashion-Shoe Industry and the Controversies Surrounding Footwear Fashion in Switzerland (1920–1940)", None, "Roman Wild", 21),
    (3, "“Bally Shoes Are Trend-Setting Fashion Creations”", "Shoe Design in the Second World War", "Anna-Brigitte Schlittler", 39),
    (4, "Schönenwerd—New York", "Bally Evening Shoes", "Katharina Tietze", 61),
    (5, "Functional Shoes", "The Development of Utility Shoes, Their Differentiation and Interfaces with Fashion", "Daniel Späti", 77),
    (6, "Enter the Shoe", "Design in the Making", "Anna-Brigitte Schlittler", 95),
    (7, "Real Gold?", "A Material for 1930s Shoes", "Katharina Tietze", 127),
    (8, "Notes on Window-Dressing as a New Means of Communication", None, "Henriette-Friederike Herm", 143),
    (9, "“A Fairy-Tale Affair…!”", "Bally Shoes at the Swiss National Exposition of 1939", "Katharina Tietze", 155),
    (10, "Contributors", None, None, 179),
    (11, "Bibliography", None, None, 181),
]
LAST_PRINTED = 192


def chapter_of(printed):
    cur = None
    for c in CHAPTERS:
        if printed >= c[4]:
            cur = c
    return cur


def clean(lines):
    out = ""
    for ln in lines:
        ln = ln.replace("\xad", "­")
        if not out:
            out = ln
        elif out.endswith("­") or (re.search(r"[a-zäöü]-$", out) and ln[:1].islower()):
            out = out.rstrip("­").rstrip("-") + ln.lstrip()
        else:
            out = out.rstrip() + " " + ln.lstrip()
    return re.sub(r"\s+", " ", out.replace("­", "")).strip()


def lines_of(b, drop_sup=True):
    return ["".join(s["text"] for s in l["spans"] if not (drop_sup and s["size"] < 7.5 and s["text"].strip().isdigit())) for l in b["lines"]]


passages, notes, plates, captions = [], [], [], []
seq = 0
for i, pg in enumerate(d):
    pdfp = i + 1
    printed = pdfp - OFF
    ch = chapter_of(printed)
    if not ch or printed > LAST_PRINTED:
        ch = (0, None, None, None, 0)
        if printed not in (0, 5, 6, 191, 192):
            continue
    W, Hh = pg.rect.width, pg.rect.height
    blocks = pg.get_text("dict", sort=True)["blocks"] if ch[0] else []
    texts = []
    for b in blocks:
        if b["type"] != 0:
            continue
        sp = [s for l in b["lines"] for s in l["spans"] if s["text"].strip()]
        if not sp:
            continue
        y0 = b["bbox"][1]
        raw = "".join(s["text"] for s in sp).strip()
        sizes = [s["size"] for s in sp]
        main = max(set(round(x) for x in sizes), key=lambda z: sum(len(s["text"]) for s in sp if round(s["size"]) == z))
        bold = all("Bold" in s["font"] for s in sp)
        if y0 < 40 and main <= 7:          # running head
            continue
        if y0 > 550 and re.fullmatch(r"\d{1,3}", raw):  # folio
            continue
        texts.append((b, raw, main, bold, sizes))
    for b, raw, main, bold, sizes in texts:
        t = clean(lines_of(b))
        if re.match(r"^\[[0-9ivxl ,–-]+\]", raw) and main <= 8.5:
            captions.append({"pdf_page": pdfp, "page": printed, "bbox": [round(x) for x in b["bbox"]], "text": clean(lines_of(b, False))})
            continue
        if main >= 18 or (main >= 12 and b["bbox"][1] < 200):  # chapter title on opening page
            continue
        if ch[0] in (10, 11):
            seq += 1
            passages.append({"seq": seq, "page": printed, "part": ch[0], "kind": "body" if ch[0] == 10 else "bibliography", "en": t})
            continue
        m = re.match(r"^(\d{1,3})\s*\t?\s*(\S.*)$", "\n".join(lines_of(b, False)), re.S)
        if main <= 8.5 and m:
            # endnote block may contain several notes: split on line-start numbers
            joined = "\n".join(lines_of(b, False))
            for nm in re.finditer(r"(?m)^(\d{1,3})\s*\t?\s*(.+?)(?=^\d{1,3}\s*\t|\Z)", joined, re.S):
                notes.append({"part": ch[0], "no": int(nm.group(1)), "en": clean(nm.group(2).split("\n"))})
            continue
        if main <= 8.5 and notes and notes[-1]["part"] == ch[0]:
            notes[-1]["en"] = clean([notes[-1]["en"], t])  # note continuation
            continue
        if b["bbox"][1] < 200 and ch[3] and t.replace(" and ", ", ") in (ch[3], ch[3].replace(", ", " and ")):
            continue  # author line under the chapter title
        kind = "subheading" if bold and len(t) < 120 else ("quote" if main <= 9.4 else "body")
        prev = passages[-1] if passages else None
        if prev and kind in ("body", "quote") and prev["kind"] == kind and prev["part"] == ch[0] and not re.search(r"[.!?”:;)\]…]\s*$", prev["en"]):
            prev["en"] = clean([prev["en"], t])
            prev.setdefault("pages", [prev["page"]])
            if printed not in prev["pages"]:
                prev["pages"].append(printed)
            continue
        seq += 1
        passages.append({"seq": seq, "page": printed, "part": ch[0], "kind": kind, "en": t})

    # images on this page
    infos = [x for x in pg.get_image_info(xrefs=True) if (x["bbox"][2] - x["bbox"][0]) > 40 and (x["bbox"][3] - x["bbox"][1]) > 40]
    infos.sort(key=lambda x: (round(x["bbox"][1] / 20), x["bbox"][0]))
    for k, info in enumerate(infos, 1):
        pid = f"p{printed:03d}-{k}" if printed > 0 else f"cover-{k}"
        try:
            pix = pymupdf.Pixmap(d, info["xref"])
            if pix.n - pix.alpha >= 4:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            clip = pymupdf.Rect(info["bbox"])
            px = pg.get_pixmap(clip=clip, dpi=200)
            im = Image.frombytes("RGB", (px.width, px.height), px.samples)
        im.save(f"{OUT}/web/plates/{pid}.jpg", "JPEG", quality=88, optimize=True, progressive=True)
        t = im.copy(); t.thumbnail((400, 400)); t.save(f"{OUT}/web/thumbs/{pid}.jpg", "JPEG", quality=82, optimize=True)
        plates.append({"id": pid, "page": printed, "pdf_page": pdfp, "idx": k, "bbox": [round(x) for x in info["bbox"]], "w": im.width, "h": im.height, "part": ch[0]})

for i, pg in enumerate(d):
    printed = i + 1 - OFF
    pix = pg.get_pixmap(dpi=150)
    Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(f"{OUT}/web/pages/{max(printed,0):03d}.jpg" if printed >= 1 else f"{OUT}/web/pages/front-{i+1}.jpg", "JPEG", quality=78, optimize=True)

# captions -> plates: same page, caption numbering lists figures in order (top-left to bottom-right)
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13}
for c in captions:
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", c["text"], re.S)
    labels = [x.strip() for x in re.split(r",", m.group(1))] if m else []
    body = m.group(2) if m else c["text"]
    credit = None
    cm = re.search(r"\(([^()]*(?:©|Archives|Courtesy|photo|Nebelspalter|Vogue|Hauszeitung|Exhibition|Landesausstellung)[^()]*)\)\s*$", body)
    if cm:
        credit = cm.group(1).strip()
    c["labels"], c["body"], c["credit"] = labels, body.strip(), credit
by_page = {}
for pl in plates:
    by_page.setdefault(pl["page"], []).append(pl)
for c in captions:
    pls = [p for p in by_page.get(c["page"], []) if not p.get("caption")]
    for lab, pl in zip(c["labels"] or [""], pls if len(c["labels"]) > 1 else pls[:1]):
        pl["caption"], pl["fig"], pl["credit_raw"] = c["body"], lab, c["credit"]


def owner(credit, caption):
    s = (credit or "") + " " + (caption or "")
    if re.search(r"Bally|Arola Hauszeitung|spring catalogues", s):
        if re.search(r"Condé|Ferragamo|gta Archives|Nebelspalter|Landesausstellung im Werden", s):
            return "third-party"
        return "bally"
    if re.search(r"Frau Mode spielt auf", s):
        return "bally"   # Bally's own 1939 cinema commercial
    return "third-party" if credit else "unknown"


for pl in plates:
    pl["owner"] = owner(pl.get("credit_raw"), pl.get("caption"))
# uncaptioned full-page images listed in the imprint: cover, p.6 ski boot 1944, p.193 ladies' shoe 1935 (all © Bally)
for pl in plates:
    if not pl.get("caption") and pl["page"] in (-1, 0, 5, 6, 192, 193):
        pl["owner"] = "bally"
        pl["credit_raw"] = "Historical Archives of Bally Schuhfabriken AG, photo: Manuel Fabritz, © Bally"

json.dump({"chapters": [{"no": c[0], "title": c[1], "subtitle": c[2], "author": c[3], "page_from": c[4]} for c in CHAPTERS],
           "passages": passages, "notes": notes, "plates": plates, "captions": captions},
          open(f"{OUT}/library.json", "w"), ensure_ascii=False, indent=1)
import collections
print(len(passages), "passages", len(notes), "notes", len(plates), "plates", len(captions), "captions")
print(collections.Counter(p["owner"] for p in plates))
print("uncaptioned", [p["id"] for p in plates if not p.get("caption")])
print(collections.Counter(p["kind"] for p in passages))
