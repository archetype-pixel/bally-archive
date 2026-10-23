"""Extract 'Bally - Hier und Jetzt' (German original) into passages, plates and captions.
Outputs out/book.json and out/plates/<id>.jpg (full extracted image)."""
import io, json, os, re, sys
import pymupdf
from PIL import Image

SRC = sys.argv[1]
OUT = sys.argv[2]
os.makedirs(f"{OUT}/plates", exist_ok=True)
d = pymupdf.open(SRC)
SUP = re.compile(r"\s+")

PARTS = [  # (part no, title, years, author, first page, last page)
    (1, "Industrie in der solothurnischen Provinz", "1780–1870", "Philipp Abegg", 7, 63),
    (2, "Entstehung des Grossbetriebs", "1870–1914", "Philipp Abegg", 65, 123),
    (3, "Ein Konzern mit Weltmarke", "1914–1951", "Philipp Abegg", 125, 225),
    (4, "Hochkonjunktur und Verfall", "1951–1977", "Martin Matter", 227, 279),
    (5, "In fremden Händen", "1977–1999", "Philipp Abegg", 281, 295),
]
ESSAYS = [(43, 61, [62, 63]), (99, 121, [122, 123]), (207, 223, [224]), (267, 277, [278])]
ESSAY_PAGES = {p for a, b, _ in ESSAYS for p in range(a, b + 1)}
ESSAY_CAP_PAGES = {p for _, _, c in ESSAYS for p in c}


def part_of(p):
    for no, *_rest, a, b in PARTS:
        if a - 1 <= p <= b:
            return no
    return None


def clean(lines):
    out = ""
    for ln in lines:
        ln = ln.replace("\xad", "­")
        if not out:
            out = ln
            continue
        if out.endswith("­") or (re.search(r"[a-zäöüß]-$", out) and ln[:1].islower()):
            out = out.rstrip("­").rstrip("-") + ln.lstrip()
        else:
            out = out.rstrip() + " " + ln.lstrip()
    out = out.replace("­", "")
    return SUP.sub(" ", out).strip()


def block_lines(b, drop_sup=True):
    lines = []
    for l in b["lines"]:
        t = "".join(s["text"] for s in l["spans"] if not (drop_sup and s["size"] < 7))
        lines.append(t)
    return lines


def role(b):
    sizes = [round(s["size"], 1) for l in b["lines"] for s in l["spans"] if s["text"].strip()]
    fonts = [s["font"] for l in b["lines"] for s in l["spans"] if s["text"].strip()]
    if not sizes:
        return "empty"
    y0 = b["bbox"][1]
    mx = max(sizes)
    unica = any("Unica" in f for f in fonts)
    if y0 < 60 and unica:
        return "header"
    if mx >= 30:
        return "part"
    if mx >= 19 and unica:
        return "heading"
    if mx >= 13:
        return "display"
    if unica:
        return "unica"
    if mx < 9.5:
        return "small"
    return "body"


pages, passages, plates, captions = [], [], [], []
cur_heading = None
cur_sub = None
seq = 0
for i, pg in enumerate(d):
    p = i + 1
    blocks = pg.get_text("dict", sort=False)["blocks"]
    W = pg.rect.width
    texts, imgs, labels = [], [], []
    for b in blocks:
        if b["type"] == 1:
            x0, y0, x1, y1 = b["bbox"]
            if (x1 - x0) > 40 and (y1 - y0) > 40:
                imgs.append(b)
            continue
        r = role(b)
        raw = "".join(s["text"] for l in b["lines"] for s in l["spans"])
        flat = raw.replace("\t", " ").strip()
        if r == "unica" and re.fullmatch(r"\d{1,2}(\s+\d{1,2})*", flat) and b["bbox"][1] > 60:
            for n in flat.split():
                labels.append((int(n), b["bbox"]))
            continue
        texts.append((r, b))
    # reading order: column then y
    def key(t):
        x0, y0 = t[1]["bbox"][:2]
        return (0 if x0 < W / 2 - 5 else 1, y0)
    texts.sort(key=key)
    pages.append({"page": p, "part": part_of(p), "images": len(imgs), "text": clean([pg.get_text()])[:0]})
    for r, b in texts:
        lines = block_lines(b)
        t = clean(lines)
        if not t or r in ("header", "empty") or p in (2, 3, 4, 5, 6) or p in {7, 65, 125, 227, 281, 295}:
            continue
        if p in ESSAY_CAP_PAGES or p >= 296:
            # caption lists and notes are handled separately
            if p in ESSAY_CAP_PAGES and not re.match(r"^S\.\s*\d", t) and r == "body":
                pass
            else:
                continue
        if r == "part":
            cur_heading, cur_sub = None, None
            continue
        if r == "heading":
            if passages and passages[-1]["kind"] == "heading" and passages[-1]["page"] == p:
                h = passages[-1]
                h["de"] = (h["de"][:-1] + t) if h["de"].endswith("-") else h["de"] + " " + t
                h["heading"] = h["de"]
                cur_heading = h["de"]
                continue
            cur_heading, cur_sub = t, None
            seq += 1
            passages.append({"seq": seq, "page": p, "part": part_of(p), "kind": "heading", "heading": t, "sub": None, "de": t})
            continue
        if r == "unica":
            joined = "\n".join(lines)
            m = re.match(r"^\s*(\d{1,2}(?:\s*(?:,|–|-|und)\s*\d{1,2})*)\s*\t\s*(\S.*)$", joined, re.S)
            if m:
                spec, nos = m.group(1), set()
                for part in re.split(r",|und", spec):
                    part = part.strip()
                    if re.match(r"\d+\s*[–-]\s*\d+", part):
                        x, y = re.split(r"\s*[–-]\s*", part)
                        nos |= set(range(int(x), int(y) + 1))
                    elif part.isdigit():
                        nos.add(int(part))
                text = clean(m.group(2).split("\n"))
                for n in sorted(nos):
                    captions.append({"page": p, "no": n, "de": text, "group": sorted(nos) if len(nos) > 1 else None})
                continue
            cur_sub = t
            seq += 1
            passages.append({"seq": seq, "page": p, "part": part_of(p), "kind": "subheading", "heading": cur_heading, "sub": t, "de": t})
            continue
        kind = "quote" if r == "small" else "body"
        if r == "display":
            kind = "display"
        prev = passages[-1] if passages else None
        if prev and prev["kind"] == kind and kind == "body" and not re.search(r"[.!?»:;)\]…]\s*$", prev["de"]):
            prev["de"] = clean([prev["de"], t]) if not prev["de"].endswith("-") else prev["de"][:-1] + t
            prev.setdefault("pages", [prev["page"]])
            if p not in prev["pages"]:
                prev["pages"].append(p)
            continue
        seq += 1
        passages.append({"seq": seq, "page": p, "part": part_of(p), "kind": kind, "heading": cur_heading, "sub": cur_sub, "de": t})
    # images
    for k, b in enumerate(sorted(imgs, key=lambda b: (round(b["bbox"][1] / 20), b["bbox"][0]))):
        x0, y0, x1, y1 = b["bbox"]
        # nearest label
        no = None
        best = 1e9
        for n, lb in labels:
            cx, cy = (lb[0] + lb[2]) / 2, (lb[1] + lb[3]) / 2
            dx = max(x0 - cx, 0, cx - x1)
            dy = max(y0 - cy, 0, cy - y1)
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best and dist < 40:
                best, no = dist, n
        pid = f"p{p:03d}-{k + 1}"
        im = Image.open(io.BytesIO(b["image"]))
        if im.mode in ("CMYK",):
            im = Image.frombytes("CMYK", im.size, im.tobytes()).convert("RGB")
            from PIL import ImageChops
            im = ImageChops.invert(im) if b.get("ext") == "jpeg" and False else im
        im = im.convert("RGB")
        im.save(f"{OUT}/plates/{pid}.jpg", "JPEG", quality=92)
        plates.append({"id": pid, "page": p, "idx": k + 1, "no": no, "bbox": [round(x0), round(y0), round(x1), round(y1)],
                       "w": im.width, "h": im.height, "page_w": round(W), "page_h": round(pg.rect.height),
                       "essay": p in ESSAY_PAGES})

# essay caption lists: "S. 43\tcaption ..." possibly several per page
essay_caps = []
for a, b, cps in ESSAYS:
    txt = ""
    for cp in cps:
        pg = d[cp - 1]
        bl = [x for x in pg.get_text("dict", sort=False)["blocks"] if x["type"] == 0 and x["bbox"][1] > 60]
        W = pg.rect.width
        bl.sort(key=lambda x: (0 if x["bbox"][0] < W / 2 - 5 else 1, x["bbox"][1]))
        for x in bl:
            txt += "\n".join(block_lines(x)) + "\n"
    parts = re.split(r"S\.\u2009?\s?(\d{2,3})\t", txt)
    intro = clean(parts[0].split("\n"))
    for j in range(1, len(parts), 2):
        essay_caps.append({"essay": a, "ref": int(parts[j]), "de": clean(parts[j + 1].split("\n"))})
    if intro:
        passages.append({"seq": 100000 + a, "page": cps[0], "part": part_of(a), "kind": "essay-intro", "heading": None, "sub": None, "de": intro})

# notes
notes_txt = ""
for p in range(296, 309):
    pg = d[p - 1]
    bl = [x for x in pg.get_text("dict", sort=False)["blocks"] if x["type"] == 0 and x["bbox"][1] > 60]
    W = pg.rect.width
    bl.sort(key=lambda x: (0 if x["bbox"][0] < W / 2 - 5 else 1, x["bbox"][1]))
    for x in bl:
        notes_txt += "\n".join(block_lines(x, drop_sup=False)) + "\n"

PARTS_OUT = [{"no": a, "title_de": b, "years": c, "author": dd, "page_from": e, "page_to": f} for a, b, c, dd, e, f in PARTS]
json.dump({"parts": PARTS_OUT, "passages": passages, "plates": plates, "captions": captions, "essay_caps": essay_caps,
           "notes_raw": notes_txt}, open(f"{OUT}/book.json", "w"), ensure_ascii=False, indent=1)
print(len(passages), "passages", len(plates), "plates", len(captions), "captions", len(essay_caps), "essay captions")
