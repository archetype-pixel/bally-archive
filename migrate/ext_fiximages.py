"""Second pass for external records still missing an image: source-specific ways to find the real picture.
- WIPO marks/designs are skipped: their image host refuses automated requests.
- Patents: first drawing from the public Google Patents page.
- Memobase: the media locator itself (photos) or its -poster still (films).
- Bonhams: the direct img*.bonhams.com URL inside the Next.js image proxy link.
- CCSA: any alternative image link on the record. Europeana: isShownBy / preview.
Copies 1600px + 400px thumbs into web/external/<source>/ and patches the record."""
import hashlib, io, os, re, sys, time, uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, parse_qs, unquote
import requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
UA = {"User-Agent": "BallyHeritageArchive/1.0 (non-commercial research archive; archetypegroup@gmail.com)"}
ONLY = [s for s in (os.environ.get("SOURCES") or "").split(",") if s]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def gpatent_drawing(num):
    try:
        r = requests.get(f"https://patents.google.com/patent/{num}/en", headers=UA, timeout=60)
        if not r.ok:
            return []
        imgs = re.findall(r"https://patentimages\.storage\.googleapis\.com/[^\"'\s>]+?\.(?:png|jpg)", r.text)
        full = [u for u in imgs if "/thumbnails/" not in u]
        return list(dict.fromkeys(full + imgs))[:3]
    except Exception:
        return []


def depatis_drawing(num):
    """German Patent Office DEPATISnet: public PDF of the patent; render the drawing sheet (last page) as an image."""
    m = re.match(r"^([A-Z]{2})(\d+)([A-Z]\d?)$", num or "")
    if not m:
        return None
    docid = f"{m.group(1)}{m.group(2).zfill(12)}{m.group(3)}"
    try:
        r = requests.get("https://depatisnet.dpma.de/DepatisNet/depatisnet", params={"action": "pdf", "firstdoc": 0, "docid": docid}, headers=UA, timeout=90)
        if not r.ok or not r.content.startswith(b"%PDF"):
            return None
        import fitz
        doc = fitz.open(stream=r.content, filetype="pdf")
        page = doc[doc.page_count - 1] if doc.page_count > 1 else doc[0]
        pix = page.get_pixmap(dpi=110)
        time.sleep(1)
        return pix.tobytes("png")
    except Exception:
        return None


def candidates(r):
    d = r.get("data") or {}
    s = r["source"]
    out = []  # (url, extra headers)
    if s in ("wipo-tm", "wipo-design"):
        return []  # WIPO image host refuses automated requests; not worked around
    if s in ("patents", "uspto-pat"):
        num = r["source_id"] if s == "patents" else re.sub(r"[^0-9A-Z]", "", str(r.get("source_id") or ""))
        if s == "uspto-pat" and num and not num.startswith("US"):
            num = "US" + num
        out += [(u, {}) for u in gpatent_drawing(num)]
    elif s == "memobase":
        m = d.get("media")
        if m:
            out += [(m, {}), (m + "-poster", {})]
    elif s == "bonhams":
        u = r.get("image_url") or ""
        q = parse_qs(urlparse(u).query).get("url")
        if q:
            inner = unquote(q[0])
            out += [(re.sub(r"&height=\d+", "&height=1600", inner), {}), (inner, {})]
    elif s == "ccsa":
        out += [(u, {}) for u in (d.get("images") or []) if re.search(r"\.(jpe?g|png|tif)", u, re.I)]
    elif s == "europeana":
        for k in ("edmIsShownBy", "edmPreview"):
            out += [(u, {}) for u in (d.get(k) or [])]
    if r.get("image_url") and "NoImage" not in r["image_url"] and "WineLot" not in r["image_url"]:
        out.append((r["image_url"], {}))
    seen, uniq = set(), []
    for u, h in out:
        if u and u not in seen:
            seen.add(u); uniq.append((u, h))
    return uniq


def put(data, path, ctype="image/jpeg"):
    x = requests.post(f"{SB}/storage/v1/object/web/{path}", data=data, timeout=120,
                      headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": ctype, "x-upsert": "true", "cache-control": "max-age=31536000"})
    x.raise_for_status()


def fix(r):
    for u, extra in candidates(r):
        try:
            g = requests.get(u, headers={**UA, **extra}, timeout=(8, 60))
            if not g.ok:
                continue
            im = Image.open(io.BytesIO(g.content))
            im = im.convert("RGB") if im.mode not in ("RGB",) else im
            if im.width < 60:
                continue
            key = hashlib.sha1(r["id"].encode()).hexdigest()[:16]
            big = im.copy(); big.thumbnail((1600, 1600))
            b = io.BytesIO(); big.save(b, "JPEG", quality=86, optimize=True)
            put(b.getvalue(), f"external/{r['source']}/{key}.jpg")
            t = im.copy(); t.thumbnail((400, 400)); b2 = io.BytesIO(); t.save(b2, "JPEG", quality=82)
            put(b2.getvalue(), f"external/{r['source']}/thumbs/{key}.jpg")
            p = requests.patch(f"{SB}/rest/v1/ext_records", headers={**H, "Prefer": "return=minimal"}, params={"id": f"eq.{r['id']}"},
                               json={"image_path": f"external/{r['source']}/{key}.jpg", "thumb_path": f"external/{r['source']}/thumbs/{key}.jpg",
                                     "width": big.width, "height": big.height}, timeout=60)
            p.raise_for_status()
            return r["source"], True, None
        except Exception as e:
            last = type(e).__name__
            continue
    if r["source"] in ("patents", "uspto-pat"):
        png = depatis_drawing(r["source_id"] if r["source"] == "patents" else "US" + re.sub(r"[^0-9]", "", str(r.get("source_id") or "")) + "A")
        if png:
            try:
                im = Image.open(io.BytesIO(png)).convert("RGB")
                key = hashlib.sha1(r["id"].encode()).hexdigest()[:16]
                big = im.copy(); big.thumbnail((1600, 1600)); b = io.BytesIO(); big.save(b, "JPEG", quality=86)
                put(b.getvalue(), f"external/{r['source']}/{key}.jpg")
                t = im.copy(); t.thumbnail((400, 400)); b2 = io.BytesIO(); t.save(b2, "JPEG", quality=82)
                put(b2.getvalue(), f"external/{r['source']}/thumbs/{key}.jpg")
                requests.patch(f"{SB}/rest/v1/ext_records", headers={**H, "Prefer": "return=minimal"}, params={"id": f"eq.{r['id']}"},
                               json={"image_path": f"external/{r['source']}/{key}.jpg", "thumb_path": f"external/{r['source']}/thumbs/{key}.jpg", "width": big.width, "height": big.height}, timeout=60).raise_for_status()
                return r["source"], True, None
            except Exception:
                pass
    return r["source"], False, f"{len(candidates(r))} candidates"


def main():
    params = {"select": "id,source,source_id,image_url,data", "thumb_path": "is.null", "relevance": "gte.0.5", "limit": 5000}
    if ONLY:
        params["source"] = f"in.({','.join(ONLY)})"
    recs = requests.get(f"{SB}/rest/v1/ext_records", headers=H, params=params, timeout=60).json()
    recs = [r for r in recs if r["source"] not in ("shab", "wikidata")]
    log(len(recs), "records missing images")
    from collections import Counter
    got, miss = Counter(), Counter()
    with ThreadPoolExecutor(3) as ex:
        for src, ok, why in ex.map(fix, recs):
            (got if ok else miss)[src] += 1
    log("fixed", dict(got))
    log("still missing", dict(miss))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
