"""Extract Roman Wild, 'Auf Schritt und Tritt. Der schweizerische Schuhmarkt 1918-1948' (NZZ Libro 2019, German, CC BY-NC-ND 4.0).
Writes OUT/library.json and OUT/web/{plates,thumbs,pages}/ for the Library loader.
Layout: body 9.3pt GuardianTextEgyp; section headings 12.5 bold (17 bold on a chapter's first section page); sub-headings 11 Sans
Medium; captions start with bold 7.0 Sans 'Abbildung N' / 'Tabelle N' / 'Grafik N'; endnotes in the appendix 'Kurzbelege'
(two columns, 'Kapitel N'); picture credits in 'Bildnachweis'. Printed page = pdf page - 1."""
import io, json, os, re, sys
import pymupdf
from PIL import Image

SRC, OUT = sys.argv[1], sys.argv[2]
for s in ("plates", "thumbs", "pages"):
    os.makedirs(f"{OUT}/web/{s}", exist_ok=True)
d = pymupdf.open(SRC)
OFF = 1
CHAPTERS = [(1, "Einblick", 9), (2, "Marktverlierer", 33), (3, "Marktmacht", 99), (4, "Marktverantwortung", 163),
            (5, "Marktregulierung", 237), (6, "Markterfahrung", 299), (7, "Rundblick", 357), (8, "Anhang", 377)]
SECTIONS = {  # from the printed table of contents: chapter -> [(title, printed page)]
    2: [("Das Schuhgewerbe sinkt vom Massen- zum Randphänomen herab", 34), ("Vielgestaltiges Schuhgewerbe", 39), ("Gewerbliche Einheitsfront", 68),
        ("Statistische Erfassung", 81), ("Zusammenfassung", 97)],
    3: [("Eine exportorientierte Schuhfabrik fasst am Binnenmarkt Fuss", 100), ("Branchenprimus Bally", 106), ("Vertikale Organisation", 120),
        ("Eindringen in den Handel", 139), ("Zusammenfassung", 159)],
    4: [("Organisierte Hausfrauen gehen die «irrationale» Fussbekleidung an", 164), ("«Hausfrauisierung des Einkaufens»", 169),
        ("Konsumpolitische Ohnmacht", 206), ("Zivilisierung des Markts", 221), ("Zusammenfassung", 234)],
    5: [("Verbandssekretäre beschliessen die Aufhebung des Wettbewerbs", 238), ("Bata rückt vor", 243), ("Kommunikative Abwehr", 256),
        ("(Selbst-)Regulierung", 273), ("Transnationale Verständigung", 288), ("Zusammenfassung", 295)],
    6: [("Die Wissenschaft erkundet die Totalität des (Schuh-)Markts", 300), ("GfM und GREM als Schrittmacher", 305),
        ("Vorläufer, Vorbilder, Vorleistungen", 309), ("Begriffsarbeit", 321), ("Schuhwirtschaftlicher Erfahrungsschatz", 335), ("Zusammenfassung", 353)],
    8: [("Unpublizierte Quellen", 378), ("Publizierte Quellen", 381), ("Forschungsstand", 393), ("Forschungsliteratur", 398), ("Kurzbelege", 420),
        ("Bildnachweis", 470), ("Dank", 473), ("Der Autor", 475)],
}
OPENERS = {c[2] for c in CHAPTERS}  # printed pages that are full-page chapter openers
ESSAY = range(186, 201)              # printed pages 186-200: Bally evening-shoe photo essay (© Franco Garlaschelli, courtesy Bally)
KEEP_HYPHEN_BEFORE = {"und", "oder", "bis", "sowie", "als", "wie", "noch", "bzw.", "respektive", "resp."}
SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def chapter_of(printed):
    cur = None
    for c in CHAPTERS:
        if printed >= c[2]:
            cur = c
    return cur


def norm(s):
    s = s.replace(" ", "").replace(" ", " ").replace(" ", " ").replace(" ", " ").replace(" ", " ").replace("\t", " ")
    return re.sub(r"[ \xa0]+", " ", s)


def join(prev, nxt):
    """Join two printed lines handling soft hyphens and German line-end hyphenation."""
    prev, nxt = prev.rstrip(), nxt.lstrip()
    if not prev:
        return nxt
    if prev.endswith("\xad"):
        return prev[:-1] + nxt
    if prev.endswith("-") and not prev.endswith(" -") and nxt[:1].islower():
        w = re.match(r"[\w.]+", nxt)
        if w and w.group(0) in KEEP_HYPHEN_BEFORE:
            return prev + " " + nxt
        return prev[:-1] + nxt
    if prev.endswith("-") and nxt[:1].isupper():
        return prev + nxt          # e.g. "Schuh-Markt" style compounds keep the hyphen
    return prev + " " + nxt


def line_text(l, sup=True):
    out = ""
    for s in l["spans"]:
        t = s["text"]
        if sup and round(s["size"], 1) <= 5.6 and t.strip().isdigit():
            t = t.strip().translate(SUP)
        out += t
    return out


def clean(t):
    return re.sub(r"\s+", " ", norm(t).replace("\xad", "")).strip()


def lines_of_page(pg):
    out = []
    for b in pg.get_text("dict", sort=True)["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            sp = [s for s in l["spans"] if s["text"].strip()]
            if not sp:
                continue
            main = max(sp, key=lambda s: len(s["text"]))
            out.append({"x": l["bbox"][0], "y": l["bbox"][1], "x1": l["bbox"][2], "y1": l["bbox"][3], "size": round(main["size"], 1),
                        "font": main["font"], "bold": "Bold" in sp[0]["font"], "first": sp[0], "text": line_text(l), "raw": "".join(s["text"] for s in l["spans"])})
    return out


passages, notes, plates, captions, biblio = [], [], [], [], []
cur_section = {}
pending_head = None


def add_passage(part, printed, kind, text, heading=None, new=True):
    text = clean(text)
    if not text:
        return
    passages.append({"part": part, "page": printed, "pages": [printed], "kind": kind, "de": text, "section": heading})


# ------------------------------------------------------------------ body chapters 1-7
for i in range(d.page_count):
    printed = i + 1 - OFF
    if printed < 9 or printed > 376:
        continue
    ch = chapter_of(printed)
    pg = d[i]
    if printed in OPENERS:
        continue
    L = [l for l in lines_of_page(pg) if l["y"] > 40]
    body = [l for l in L if l["size"] == 9.3 and "Egyp" in l["font"]]
    margin = min((l["x"] for l in body), default=36)
    # captions on this page
    j = 0
    while j < len(L):
        l = L[j]
        f0 = l["first"]
        if round(f0["size"], 1) == 7.0 and "Bold" in f0["font"] and re.match(r"(Abbildung|Tabelle|Grafik)\s", norm(f0["text"]).strip()):
            txt = l["raw"]; y1 = l["y1"]; k = j + 1
            while k < len(L) and L[k]["size"] in (7.0, 7.5) and "Sans" in L[k]["font"] and L[k]["y"] - y1 < 6 and abs(L[k]["x"] - l["x"]) < 30:
                txt = join(txt, L[k]["raw"]); y1 = L[k]["y1"]; k += 1
            t = clean(txt)
            m = re.match(r"(Abbildung|Tabelle|Grafik)\s+(\d+)(?:\s*/\s*(\d+))?\s*(.*)", t)
            captions.append({"page": printed, "pdf_page": i + 1, "type": m.group(1), "no": int(m.group(2)), "no2": int(m.group(3)) if m.group(3) else None,
                             "text": m.group(4).strip(), "bbox": [round(l["x"]), round(l["y"]), round(max(x["x1"] for x in L[j:k])), round(y1)]})
            j = k
            continue
        j += 1
    # text flow
    for l in L:
        if l["size"] == 17.0 or (l["size"] == 12.5 and l["bold"]):
            t = clean(l["raw"])
            if passages and passages[-1]["kind"] == "subheading" and passages[-1]["page"] == printed and passages[-1].get("level") == 1 and passages[-1].get("_y1", 0) + 8 > l["y"]:
                passages[-1]["de"] = clean(join(passages[-1]["de"], l["raw"])); passages[-1]["_y1"] = l["y1"]
                continue
            add_passage(ch[0], printed, "subheading", t); passages[-1]["level"] = 1; passages[-1]["_y1"] = l["y1"]
            cur_section[ch[0]] = passages[-1]["de"]
            continue
        if l["size"] == 11.0 and "Sans" in l["font"]:
            add_passage(ch[0], printed, "subheading", l["raw"]); passages[-1]["level"] = 2; passages[-1]["_y1"] = l["y1"]
            continue
        if not (l["size"] == 9.3 and "Egyp" in l["font"]):
            continue          # captions, tables, chart labels
        indent = l["x"] - margin > 12
        prev = passages[-1] if passages else None
        cont = prev and prev["kind"] == "body" and prev["part"] == ch[0] and not indent
        if prev and prev["kind"] == "body" and prev["part"] == ch[0] and indent and not re.search(r"[.!?»:;)…]\s*$", re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+\s*$", "", clean(prev["_raw"]))):
            cont = True   # indented line but previous paragraph clearly unfinished (e.g. column jump)
        if cont:
            prev["_raw"] = join(prev["_raw"], l["text"])
            if printed not in prev["pages"]:
                prev["pages"].append(printed)
        else:
            passages.append({"part": ch[0], "page": printed, "pages": [printed], "kind": "body", "_raw": l["text"], "section": cur_section.get(ch[0])})
for p in passages:
    if "_raw" in p:
        p["de"] = clean(p.pop("_raw"))
    p.pop("_y1", None)


# ------------------------------------------------------------------ appendix (two columns)
def appendix_lines(pg):
    L = [l for l in lines_of_page(pg) if l["y"] > 40]
    left = [l for l in L if l["x"] < 240]
    right = [l for l in L if l["x"] >= 240]
    return left, right


def colstart(l):
    return l["x"] < 40 or 243 <= l["x"] < 249.5


section = None
cur_ch = None
note_buf = None
bib_buf = None
bib_ord = 0
arch_code = None
bild = []
for i in range(d.page_count):
    printed = i + 1 - OFF
    if printed < 378 or printed > 477:
        continue
    pg = d[i]
    for col in appendix_lines(pg):
        for l in col:
            t = norm(l["raw"])
            if l["size"] == 12.5 and l["bold"]:
                section = clean(t)
                if note_buf: notes.append(note_buf); note_buf = None
                if bib_buf: biblio.append(bib_buf); bib_buf = None
                if section in ("Forschungsstand", "Dank", "Der Autor"):
                    add_passage(8, printed, "subheading", section); passages[-1]["level"] = 1
                continue
            if section == "Kurzbelege":
                if l["size"] == 11.0:
                    m = re.match(r"Kapitel\s+(\d+)", t.strip())
                    if m:
                        if note_buf: notes.append(note_buf); note_buf = None
                        cur_ch = int(m.group(1))
                    elif t.strip() == "Forschungsstand":
                        if note_buf: notes.append(note_buf); note_buf = None
                        cur_ch = 8
                    continue
                m = re.match(r"^\s*(\d{1,3})\s*\t\s*(.*)$", l["raw"]) if round(l["first"]["size"], 1) == 5.5 else None
                if m:
                    if note_buf: notes.append(note_buf)
                    if int(m.group(1)) == 1 and notes and notes[-1]["part"] == cur_ch and notes[-1]["no"] > 1 and cur_ch == 7:
                        cur_ch = 8   # notes of the appendix essay 'Forschungsstand' follow chapter 7 without a heading
                    note_buf = {"part": cur_ch, "no": int(m.group(1)), "_raw": m.group(2)}
                elif note_buf:
                    note_buf["_raw"] = join(note_buf["_raw"], l["raw"])
                continue
            if section in ("Unpublizierte Quellen", "Publizierte Quellen", "Forschungsliteratur"):
                if section == "Unpublizierte Quellen" and "Sans" in l["font"]:
                    # archive heading line: code<TAB>name (code and name may come as two line objects on the same baseline)
                    if bib_buf: biblio.append(bib_buf); bib_buf = None
                    if biblio and biblio[-1].get("kind") == "archive" and abs(biblio[-1]["_y"] - l["y"]) < 3:
                        biblio[-1]["text"] = clean(biblio[-1]["text"] + " " + l["raw"])
                        continue
                    arch_code = clean(l["raw"])
                    bib_ord += 1
                    biblio.append({"section": section, "ord": bib_ord, "text": arch_code, "kind": "archive", "_y": l["y"]})
                    continue
                starts = colstart(l) or (section == "Unpublizierte Quellen" and 65 <= l["x"] < 75)
                if section == "Unpublizierte Quellen" and l["x"] < 60 and clean(l["raw"]).startswith("o. S."):
                    continue
                if starts:
                    if bib_buf: biblio.append(bib_buf)
                    bib_ord += 1
                    bib_buf = {"section": section, "ord": bib_ord, "_raw": l["raw"], "archive": arch_code if section == "Unpublizierte Quellen" else None}
                elif bib_buf:
                    bib_buf["_raw"] = join(bib_buf["_raw"], l["raw"])
                continue
            if section == "Bildnachweis":
                bild.append(l)
                continue
            if section in ("Forschungsstand", "Dank", "Der Autor"):
                if l["size"] != 7.5 or "Egyp" not in l["font"]:
                    continue
                prev = passages[-1]
                new_par = (l["x"] - (36 if l["x"] < 240 else 246)) > 12
                if prev["kind"] == "body" and prev["part"] == 8 and not new_par:
                    prev["_raw"] = join(prev["_raw"], l["text"])
                    if printed not in prev["pages"]:
                        prev["pages"].append(printed)
                else:
                    passages.append({"part": 8, "page": printed, "pages": [printed], "kind": "body", "_raw": l["text"], "section": section})
if note_buf: notes.append(note_buf)
if bib_buf: biblio.append(bib_buf)
for p in passages:
    if "_raw" in p:
        p["de"] = clean(p.pop("_raw"))
for n in notes:
    n["de"] = clean(n.pop("_raw"))
for b in biblio:
    if "_raw" in b:
        b["text"] = clean(b.pop("_raw"))
    b.pop("_y", None)
    if b.get("archive"):
        b["text"] = f"{b['archive'].split(' ')[0]}: {b['text']}" if not b["text"].startswith("Ballyana") else b["text"]

# picture credits: entries start with a number at the column start
credits = {}
alltxt = ""
for l in bild:
    alltxt = join(alltxt, l["raw"]) if alltxt else l["raw"]
alltxt = re.split(r"Fotos Umschlag", alltxt)[0]
for m in re.finditer(r"(?:^|\s)(\d{1,2})(?:\s*\u200a?/\u200a?\s*(\d{1,2}))?\u2002(.*?)(?=\s\d{1,2}(?:\s*\u200a?/\u200a?\s*\d{1,2})?\u2002|$)", alltxt, re.S):
    credits[int(m.group(1))] = {"no": int(m.group(1)), "no2": int(m.group(2)) if m.group(2) else None, "_raw": m.group(3)}
CODES = {"BAL Hist": "Historisches Archiv der Bally-Schuhfabriken AG, Schönenwerd", "BAL": "Ballyana Sammlung Industriekultur, Schönenwerd",
         "SWA": "Schweizerisches Wirtschaftsarchiv, Basel", "ZB": "Zentralbibliothek Zürich", "Sozarch": "Schweizerisches Sozialarchiv, Zürich",
         "StArZH": "Stadtarchiv Zürich"}
for c in list(credits.values()):
    full = clean(c.pop("_raw"))
    full = re.split(r"\s*Fotos Umschlag", full)[0]
    parts = full.rsplit(" : ", 1) if " : " in full else full.rsplit(":", 1)
    src, holder = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (full, "")
    code = next((k for k in ("BAL Hist", "Sozarch", "StArZH", "SWA", "ZB", "BAL") if re.match(rf"{re.escape(k)}\b", holder)), None)
    c["source"], c["holder"], c["full"] = src, holder, full
    c["holder_expanded"] = f"{holder} ({CODES[code]})" if code else holder
    c["owner"] = "bally" if (code == "BAL Hist" or re.search(r"Bally[- ](Schuhfabriken|Agor|Arola)[^,]*\(\s*Hg", src)) else "third-party"
    if c.get("no2"):
        credits.setdefault(c["no2"], dict(c, no=c["no2"]))


# ------------------------------------------------------------------ images
def save_plate(pid, im):
    im.save(f"{OUT}/web/plates/{pid}.jpg", "JPEG", quality=88, optimize=True, progressive=True)
    t = im.copy(); t.thumbnail((400, 400)); t.save(f"{OUT}/web/thumbs/{pid}.jpg", "JPEG", quality=82, optimize=True)


def xref_image(info, pg):
    try:
        pix = pymupdf.Pixmap(d, info["xref"])
        if pix.n - pix.alpha >= 4:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if im.width < 200:
            raise ValueError
        return im
    except Exception:
        px = pg.get_pixmap(clip=pymupdf.Rect(info["bbox"]) & pg.rect, dpi=220)
        return Image.frombytes("RGB", (px.width, px.height), px.samples)


SEEN = set()
caps_by_page = {}
for c in captions:
    caps_by_page.setdefault(c["page"], []).append(c)
for i in range(d.page_count):
    printed = i + 1 - OFF
    if printed < 0 or printed > 376:
        continue
    pg = d[i]
    def visible(x):
        r = pymupdf.Rect(x["bbox"]); v = r & pg.rect
        return (v.width * v.height) / max(r.width * r.height, 1)
    infos = [x for x in pg.get_image_info(xrefs=True) if (x["bbox"][2] - x["bbox"][0]) > 40 and (x["bbox"][3] - x["bbox"][1]) > 40 and visible(x) >= 0.12 and x["xref"] not in SEEN]
    for x in infos:
        SEEN.add(x["xref"])
    infos.sort(key=lambda x: (round(x["bbox"][1] / 20), x["bbox"][0]))
    pcaps = [c for c in caps_by_page.get(printed, []) if c["type"] == "Abbildung"]
    for k, info in enumerate(infos, 1):
        pid = f"p{printed:03d}-{k}" if printed > 0 else f"cover-{k}"
        im = xref_image(info, pg)
        save_plate(pid, im)
        bb = info["bbox"]
        pl = {"id": pid, "page": printed, "pdf_page": i + 1, "idx": k, "bbox": [round(x) for x in bb], "w": im.width, "h": im.height,
              "part": (chapter_of(printed) or (0,))[0], "type": "photo"}
        cap = None
        if len(pcaps) == 1:
            cap = pcaps[0]
        elif pcaps and all(re.match(r"\((oben|unten|links|rechts|Mitte)", c["text"]) for c in pcaps):
            cx = [((x["bbox"][0] + x["bbox"][2]) / 2, (x["bbox"][1] + x["bbox"][3]) / 2) for x in infos]
            mx = (min(c[0] for c in cx) + max(c[0] for c in cx)) / 2
            my = (min(c[1] for c in cx) + max(c[1] for c in cx)) / 2
            spread_x = max(c[0] for c in cx) - min(c[0] for c in cx) > 60
            spread_y = max(c[1] for c in cx) - min(c[1] for c in cx) > 60
            ccx, ccy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            tags = set()
            if spread_y: tags.add("oben" if ccy < my else "unten")
            if spread_x: tags.add("links" if ccx < mx else "rechts")
            best, score = None, -1
            for c in pcaps:
                w = re.match(r"\(([^)]+)\)", c["text"]).group(1)
                want = {t for t in ("oben", "unten", "links", "rechts") if t in w}
                if "links" in want and "rechts" in want:
                    want -= {"links", "rechts"}
                sc = len(want & tags) - 2 * len(want - tags - ({"links", "rechts"} if not spread_x else set()) - ({"oben", "unten"} if not spread_y else set()))
                if sc > score:
                    best, score = c, sc
            cap = best
        elif pcaps:
            # nearest caption below the image, else nearest overall
            below = [c for c in pcaps if c["bbox"][1] >= bb[3] - 5]
            cap = min(below or pcaps, key=lambda c: abs(c["bbox"][1] - bb[3]) + 0.3 * abs(c["bbox"][0] - bb[0]))
        if cap:
            pl.update(caption=cap["text"], fig=f"Abbildung {cap['no']}" + (f"/{cap['no2']}" if cap.get("no2") else ""), fig_no=cap["no"])
            cr = credits.get(cap["no"])
            if cr:
                pl.update(credit=cr["holder_expanded"], credit_source=cr["source"], owner=cr["owner"])
        if printed in OPENERS:
            pl.update(type="opener", caption=None, owner="unknown")
        if printed in ESSAY:
            pl.update(type="essay", owner="bally", credit="© Franco Garlaschelli; reproduced with permission of Bally-Schuhfabriken AG")
            yrs = [norm(l["raw"]).strip() for l in lines_of_page(pg) if l["size"] == 16.0 and re.fullmatch(r"\d{4}", norm(l["raw"]).strip())]
            if yrs:
                pl["date_text"] = yrs[0]
        if printed <= 0:
            pl.update(type="cover", owner="bally", credit="© Franco Garlaschelli; reproduced with permission of Bally-Schuhfabriken AG")
        pl.setdefault("owner", "unknown")
        plates.append(pl)
    # tables and charts are vector drawings: render the area above their caption
    tcaps = sorted([c for c in caps_by_page.get(printed, []) if c["type"] in ("Tabelle", "Grafik")], key=lambda c: c["bbox"][1])
    prev_bottom = 40
    for c in sorted(caps_by_page.get(printed, []), key=lambda c: c["bbox"][1]):
        if c["type"] == "Abbildung":
            prev_bottom = max(prev_bottom, c["bbox"][3]); continue
        top, bot = prev_bottom, c["bbox"][1]
        u = pymupdf.Rect()
        for dr in pg.get_drawings():
            r = dr["rect"]
            if r.y0 >= top - 2 and r.y1 <= bot + 2 and r.width < 470 and r.y0 > 35:
                u |= r
        for l in lines_of_page(pg):
            if top - 2 <= l["y"] and l["y1"] <= bot + 2 and "Sans" in l["font"] and l["size"] <= 7.5:
                u |= pymupdf.Rect(l["x"], l["y"], l["x1"], l["y1"])
        if u.is_empty or u.height < 20:
            u = pymupdf.Rect(30, top, 452, bot)
        u = (u + (-6, -6, 6, 6)) & pg.rect
        px = pg.get_pixmap(clip=u, dpi=220)
        im = Image.frombytes("RGB", (px.width, px.height), px.samples)
        pid = f"p{printed:03d}-{'t' if c['type'] == 'Tabelle' else 'g'}{c['no']}"
        save_plate(pid, im)
        plates.append({"id": pid, "page": printed, "pdf_page": i + 1, "idx": 50 + c["no"], "bbox": [round(x) for x in u], "w": im.width, "h": im.height,
                       "part": chapter_of(printed)[0], "type": "table" if c["type"] == "Tabelle" else "chart", "caption": c["text"],
                       "fig": f"{c['type']} {c['no']}", "owner": "author", "credit": "Roman Wild, Auf Schritt und Tritt (2019)"})
        prev_bottom = c["bbox"][3]

# full page images
for i, pg in enumerate(d):
    printed = i + 1 - OFF
    pix = pg.get_pixmap(dpi=150)
    name = f"{printed:03d}.jpg" if printed >= 1 else f"front-{i+1}.jpg"
    Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(f"{OUT}/web/pages/{name}", "JPEG", quality=78, optimize=True)

# captions whose figure was not found on the same page (picture on the facing page)
found = {p.get("fig_no") for p in plates}
orphans = [c for c in captions if c["type"] == "Abbildung" and c["no"] not in found]
for c in orphans:
    for pl in plates:
        if pl["type"] == "photo" and not pl.get("caption") and abs(pl["page"] - c["page"]) == 1:
            pl.update(caption=c["text"], fig=f"Abbildung {c['no']}", fig_no=c["no"])
            cr = credits.get(c["no"])
            if cr:
                pl.update(credit=cr["holder_expanded"], credit_source=cr["source"], owner=cr["owner"])
            break

sections = {k: [{"title": t, "page_from": p} for t, p in v] for k, v in SECTIONS.items()}
json.dump({"chapters": [{"no": c[0], "title": c[1], "page_from": c[2]} for c in CHAPTERS], "sections": sections,
           "passages": passages, "notes": notes, "plates": plates, "captions": captions, "bibliography": biblio, "credits": credits},
          open(f"{OUT}/library.json", "w"), ensure_ascii=False, indent=1)
import collections
print(len(passages), "passages", collections.Counter(p["kind"] for p in passages))
print(len(notes), "notes", collections.Counter(n["part"] for n in notes))
print(len(plates), "plates", collections.Counter(p["type"] for p in plates), collections.Counter(p["owner"] for p in plates))
print(len(captions), "captions;", "orphan captions", [c["no"] for c in captions if c["type"] == "Abbildung" and c["no"] not in {p.get("fig_no") for p in plates}])
print("uncaptioned photos", [p["id"] for p in plates if p["type"] == "photo" and not p.get("caption")])
print(len(biblio), "bibliography", collections.Counter(b["section"] for b in biblio))
print(len(credits), "credits")
