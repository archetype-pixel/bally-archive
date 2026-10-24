"""External sources, batch 2: museum and library collections with open APIs.
Sources: V&A, The Met, Europeana (aggregates Memoriav/Swiss TV, European museums), Library of Congress, Internet Archive.
Only the Swiss luxury house Bally (C. F. Bally / Bally Schuhfabriken) is kept: an AI filter rejects Bally Gaming,
Bally's casinos, Bally Sports, Bally Total Fitness, Boris Bally, places called Bally, etc.
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, SOURCES (comma list), EUROPEANA_KEY"""
import hashlib, html, io, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
import anthropic, requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
UA = {"User-Agent": "BallyHeritageArchive/1.0 (non-commercial research archive; archetypegroup@gmail.com)"}
WS = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
client = anthropic.Anthropic(max_retries=8, default_headers={"anthropic-workspace-id": WS} if WS else None)
SOURCES = [s for s in (os.environ.get("SOURCES") or "vam,met,europeana,loc,ia").split(",") if s]
EKEY = os.environ.get("EUROPEANA_KEY", "api2demo")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def jget(url, params=None):
    for t in range(5):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=60)
            if r.status_code == 429:
                time.sleep(10 * (t + 1)); continue
            if r.ok:
                return r.json()
            log("http", r.status_code, url[:80])
            return None
        except (requests.RequestException, ValueError):
            time.sleep(3 * (t + 1))
    return None


def strip(s):
    if isinstance(s, list):
        s = " / ".join(str(x) for x in s)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))).strip()


def year_of(s):
    m = re.search(r"\b(1[7-9]\d\d|20[0-2]\d)\b", str(s or ""))
    return int(m.group(0)) if m else None


# ------------------------------------------------------------------ source plugins
def src_vam():
    out = []
    for q in ["bally", "bally shoe", "bally schuhfabriken", "c. f. bally"]:
        page = 1
        while True:
            d = jget("https://api.vam.ac.uk/v2/objects/search", {"q": q, "page_size": 100, "page": page})
            if not d:
                break
            for r in d.get("records", []):
                img = r.get("_images", {}).get("_iiif_image_base_url")
                out.append({"source": "vam", "source_id": r["systemNumber"], "source_url": f"https://collections.vam.ac.uk/item/{r['systemNumber']}/",
                            "title": r.get("_primaryTitle") or r.get("objectType"), "description": f"{r.get('objectType','')}. Maker: {(r.get('_primaryMaker') or {}).get('name','')}. Place: {r.get('_primaryPlace','')}.",
                            "date_text": r.get("_primaryDate"), "creator": (r.get("_primaryMaker") or {}).get("name"),
                            "image_url": (img + "full/!1600,1600/0/default.jpg") if img else None, "license": "© Victoria and Albert Museum, London",
                            "credit": "Victoria and Albert Museum, London", "data": r})
            if page >= d.get("info", {}).get("pages", 1):
                break
            page += 1
    return out


def src_met():
    out = []
    for q in ["Bally", "Bally shoes"]:
        d = jget("https://collectionapi.metmuseum.org/public/collection/v1.1/search", {"q": q, "limit": 100})
        for oid in (d or {}).get("objectIDs", [])[:100]:
            o = jget(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}")
            if not o:
                continue
            out.append({"source": "met", "source_id": str(oid), "source_url": o.get("objectURL"), "title": o.get("title"),
                        "description": f"{o.get('objectName','')}. {o.get('medium','')}. {o.get('creditLine','')}. Department: {o.get('department','')}.",
                        "date_text": o.get("objectDate"), "creator": o.get("artistDisplayName") or o.get("culture"),
                        "image_url": o.get("primaryImage") or o.get("primaryImageSmall") or None,
                        "license": "Public domain (Met Open Access)" if o.get("isPublicDomain") else "© The Metropolitan Museum of Art",
                        "credit": o.get("creditLine") or "The Metropolitan Museum of Art", "data": o})
            time.sleep(0.2)
    return out


def src_europeana():
    out = []
    for q in ["Bally Schuh", "Bally Schuhe", "Bally Schönenwerd", "Schönenwerd Bally", "Bally shoes", "Bally shoe", "chaussures Bally",
              "Bally Schuhfabrik", "Bally Schuhfabriken", "Bally Plakat", "Bally affiche", "Bally poster", "Bally Arola", "\"C. F. Bally\"",
              "Bally Capitol", "Bally Vasano", "Bally Scribe", "Ballyana", "Bally Zürich", "Bally Laden", "Bally Werbung", "Bally"]:
        cursor = "*"
        n = 0
        while cursor and n < 1000:
            d = jget("https://api.europeana.eu/record/v2/search.json", {"wskey": EKEY, "query": q, "rows": 100, "cursor": cursor, "profile": "rich"})
            if not d:
                break
            for it in d.get("items", []):
                out.append({"source": "europeana", "source_id": it["id"], "source_url": (it.get("guid") or "").split("?")[0],
                            "title": strip(it.get("title")), "description": strip(it.get("dcDescription"))[:1500],
                            "date_text": strip(it.get("year")) or None, "creator": strip(it.get("dcCreator"))[:300] or None,
                            "image_url": (it.get("edmIsShownBy") or it.get("edmPreview") or [None])[0],
                            "license": strip(it.get("rights")), "credit": strip(it.get("dataProvider")) + " via Europeana",
                            "kind_hint": strip(it.get("type")), "data": {k: it.get(k) for k in ("type", "dataProvider", "provider", "country", "rights", "edmPreview", "edmIsShownAt", "edmIsShownBy", "dcCreator", "year", "language")}})
                n += 1
            cursor = d.get("nextCursor")
    return out


def src_loc():
    out = []
    for q in ["bally shoes", "bally shoe company", "bally switzerland", "bally schuhe", "bally poster", "chaussures bally"]:
        for page in range(1, 4):
            d = jget("https://www.loc.gov/search/", {"q": q, "fo": "json", "c": 100, "sp": page})
            if not d:
                break
            for r in d.get("results", []):
                imgs = r.get("image_url") or []
                img = imgs[-1].split("#")[0] if imgs else None
                out.append({"source": "loc", "source_id": r.get("id") or r.get("url"), "source_url": r.get("url"), "title": strip(r.get("title")),
                            "description": strip(r.get("description"))[:1500], "date_text": r.get("date"), "creator": strip(r.get("contributor"))[:300] or None,
                            "image_url": img, "license": strip(r.get("rights")) or "See Library of Congress rights statement",
                            "credit": "Library of Congress", "kind_hint": strip(r.get("original_format")), "data": {k: r.get(k) for k in ("original_format", "subject", "location", "partof", "rights")}})
            if not d.get("pagination", {}).get("next"):
                break
    return out


def src_ia():
    out = []
    q = '(bally AND (shoe OR shoes OR schuh OR schuhe OR chaussures OR "schuhfabriken" OR schönenwerd OR schoenenwerd)) OR "c. f. bally" OR "bally schuhfabriken"'
    d = jget("https://archive.org/advancedsearch.php", {"q": q, "fl[]": ["identifier", "title", "description", "date", "creator", "mediatype"], "rows": 500, "output": "json"})
    for r in (d or {}).get("response", {}).get("docs", []):
        ident = r["identifier"]
        out.append({"source": "ia", "source_id": ident, "source_url": f"https://archive.org/details/{ident}", "title": strip(r.get("title")),
                    "description": strip(r.get("description"))[:1500], "date_text": strip(r.get("date"))[:30] or None, "creator": strip(r.get("creator"))[:300] or None,
                    "image_url": f"https://archive.org/services/img/{ident}", "license": "See Internet Archive item", "credit": "Internet Archive",
                    "kind_hint": r.get("mediatype"), "data": r})
    return out


def src_vamorg():
    """V&A objects whose maker is the Bally organisation record (A19964)."""
    out, page = [], 1
    while True:
        d = jget("https://api.vam.ac.uk/v2/objects/search", {"id_organisation": "A19964", "page_size": 100, "page": page})
        if not d:
            break
        for r in d.get("records", []):
            img = r.get("_images", {}).get("_iiif_image_base_url")
            out.append({"source": "vam", "source_id": r["systemNumber"], "source_url": f"https://collections.vam.ac.uk/item/{r['systemNumber']}/",
                        "title": r.get("_primaryTitle") or r.get("objectType"), "description": f"{r.get('objectType','')}. Maker: Bally. Place: {r.get('_primaryPlace','')}.",
                        "date_text": r.get("_primaryDate"), "creator": "Bally", "image_url": (img + "full/!1600,1600/0/default.jpg") if img else None,
                        "license": "© Victoria and Albert Museum, London", "credit": "Victoria and Albert Museum, London", "data": r})
        if page >= d.get("info", {}).get("pages", 1):
            break
        page += 1
    return out


def _marc(rec, tag, code=None, cond=None):
    import xml.etree.ElementTree as ET
    ns = "{http://www.loc.gov/MARC21/slim}"
    vals = []
    for df in rec.iter(f"{ns}datafield"):
        if df.get("tag") != tag:
            continue
        subs = {}
        for sf in df.findall(f"{ns}subfield"):
            subs.setdefault(sf.get("code"), []).append(sf.text or "")
        if cond and not cond(subs):
            continue
        vals.append(" ".join(subs.get(code, [])) if code else " ".join(" ".join(v) for v in subs.values()))
    return [v.strip() for v in vals if v.strip()]


def src_ccsa():
    """Swiss Poster Collective Catalogue (Swiss National Library, Alma SRU)."""
    import xml.etree.ElementTree as ET
    ns = "{http://www.loc.gov/MARC21/slim}"
    base = "https://nb-posters.alma.exlibrisgroup.com/view/sru/41SNL_53_INST"
    q = ('alma.all_for_ui=bally and (alma.all_for_ui=schuhe or alma.all_for_ui=chaussures or alma.all_for_ui=schuhfabriken '
         'or alma.all_for_ui=schoenenwerd or alma.all_for_ui=schönenwerd or alma.all_for_ui=scarpe or alma.all_for_ui=shoes)')
    out, start = [], 1
    for query in [q, 'alma.all_for_ui="Bally Schuhfabriken"', "alma.all_for_ui=bally"]:
        start = 1
        while start:
            try:
                r = requests.get(base, params={"version": "1.2", "operation": "searchRetrieve", "query": query, "maximumRecords": 50, "startRecord": start}, headers=UA, timeout=90)
                root = ET.fromstring(r.content)
            except Exception as e:
                log("ccsa fail", type(e).__name__); break
            for rec in root.iter(f"{ns}record"):
                cf = {c.get("tag"): c.text for c in rec.findall(f"{ns}controlfield")}
                rid = cf.get("001")
                if not rid:
                    continue
                imgs = _marc(rec, "856", "u")
                title = " ".join(_marc(rec, "245", "a") + _marc(rec, "245", "b"))
                creator = (_marc(rec, "100", "a") or _marc(rec, "700", "a") or [None])[0]
                date = (_marc(rec, "260", "c") or _marc(rec, "264", "c") or [(cf.get("008") or "")[7:11]])[0]
                desc = ". ".join(_marc(rec, "500", "a") + _marc(rec, "300") + ["Client: " + x for x in _marc(rec, "710", "a", lambda s: "cli" in s.get("4", []))]
                                 + ["Printer: " + x for x in _marc(rec, "710", "a", lambda s: "pop" in s.get("4", []) or "prt" in s.get("4", []))])
                out.append({"source": "ccsa", "source_id": rid, "source_url": f"https://nb-posters.primo.exlibrisgroup.com/discovery/fulldisplay?docid=alma{rid}&vid=41SNL_53_INST:posters",
                            "title": title, "description": desc[:1500], "date_text": date, "creator": creator, "image_url": imgs[0] if imgs else None,
                            "license": "See Swiss National Library / holding institution", "credit": "Swiss Poster Collective Catalogue (CCSA), Swiss National Library",
                            "kind_hint": "poster", "data": {"subjects": _marc(rec, "650", "a"), "holder": _marc(rec, "856", "x"), "images": imgs}})
            nxt = root.find(".//{http://www.loc.gov/zing/srw/}nextRecordPosition")
            start = int(nxt.text) if nxt is not None and nxt.text else None
            time.sleep(0.5)
    return out


SRG_KEEP = re.compile(r"schuh|schönenwerd|schoenenwerd|caslano|chaussure|calzat|scarp|luxe|lusso|mode|moda|fashion|ballyana|bally-park|thomke|textil|leder|cuir", re.I)


def src_srg():
    """SRF / RTS / RSI archive via the open SRG SSR Integration Layer."""
    out = []
    for bu in ["srf", "rts", "rsi"]:
        for q in ["Bally", "Bally Schönenwerd", "Bally Schuhe", "Bally chaussures", "Bally calzature"]:
            url = f"https://il.srgssr.ch/integrationlayer/2.0/{bu}/searchResultMediaList"
            params = {"q": q, "pageSize": 50, "includeAggregations": "false"}
            pages = 0
            while url and pages < 20:
                d = jget(url, params)
                params = None
                if not d:
                    break
                for m in d.get("searchResultMediaList", []):
                    text = f"{m.get('title','')} {m.get('description','')} {m.get('lead','')} {(m.get('show') or {}).get('title','')}"
                    if "bally" not in text.lower() or not SRG_KEEP.search(text):
                        continue
                    out.append({"source": "srg", "source_id": m.get("urn") or m.get("id"), "source_url": f"https://www.{bu}.ch/play/tv/redirect/detail/{m.get('id')}",
                                "title": m.get("title"), "description": strip(f"{(m.get('show') or {}).get('title','')}: {m.get('description') or m.get('lead') or ''}")[:1500],
                                "date_text": (m.get("date") or "")[:10], "creator": {"srf": "SRF", "rts": "RTS", "rsi": "RSI"}[bu],
                                "image_url": m.get("imageUrl"), "license": "© SRG SSR", "credit": f"{bu.upper()} archive (SRG SSR)", "kind_hint": m.get("mediaType"),
                                "data": {k: m.get(k) for k in ("urn", "mediaType", "duration", "playableAbroad", "date")} | {"show": (m.get("show") or {}).get("title"), "bu": bu}})
                url = d.get("next"); pages += 1
    return out


def src_memobase():
    """Memobase (Swiss audiovisual heritage) open API."""
    out = []
    for q in ["Bally", "Bally Schönenwerd", "Schuhfabrik Schönenwerd", "Ballyana", "Bally-Park", "Schuhmuseum Schönenwerd"]:
        off = 0
        while off < 1000:
            d = jget("https://api.memobase.ch/record/search", {"q": q, "format": "json", "size": 100, "offset": off})
            mem = (d or {}).get("hydra:member") or []
            if not mem:
                break
            for r in mem:
                rid = str(r.get("@id", "")).split(":")[-1]
                inst = r.get("hasInstantiation") or []
                inst = inst if isinstance(inst, list) else [inst]
                thumb = next((i.get("locator") for i in inst if isinstance(i, dict) and i.get("type") == "thumbnail"), None)
                media = next((i.get("locator") for i in inst if isinstance(i, dict) and i.get("type") == "digitalObject"), None)
                date = ((r.get("issued") or r.get("created") or {}) if isinstance(r.get("issued") or r.get("created"), dict) else {}).get("normalizedDateValue")
                out.append({"source": "memobase", "source_id": rid, "source_url": f"https://memobase.ch/object/{rid}", "title": strip(r.get("title")),
                            "description": strip(r.get("abstract") or r.get("descriptiveNote"))[:1500], "date_text": date, "creator": strip(r.get("hasOrHadHolder"))[:200] or None,
                            "image_url": thumb, "license": "See Memobase rights statement", "credit": "Memobase (Memoriav)", "kind_hint": strip(r.get("type")),
                            "data": {"media": media, "collection": r.get("isOrWasPartOf"), "type": r.get("type"), "sameAs": r.get("sameAs")}})
            off += 100
            if off >= (d.get("hydra:totalItems") or 0):
                break
    return out


WD_UA = {"User-Agent": "BallyHeritageArchive/1.0 (https://bally-archive-time-capsule.lovable.app; archetypegroup@gmail.com)",
         "Accept": "application/sparql-results+json"}
WD_QUERIES = [
    """SELECT DISTINCT ?item ?itemLabel ?itemDescription ?propLabel ?img ?inception ?birth WHERE {
  VALUES ?bally { wd:Q688195 wd:Q107150805 wd:Q27488796 wd:Q27488797 wd:Q99364026 wd:Q125449311 wd:Q29525229 }
  { ?item ?p ?bally } UNION { ?bally ?p ?item }
  ?prop wikibase:directClaim ?p . FILTER(STRSTARTS(STR(?item), "http://www.wikidata.org/entity/Q"))
  OPTIONAL { ?item wdt:P18 ?img } OPTIONAL { ?item wdt:P571 ?inception } OPTIONAL { ?item wdt:P569 ?birth }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,de,fr". } } LIMIT 3000""",
    """SELECT DISTINCT ?item ?itemLabel ?itemDescription ?img ?birth WHERE {
  { ?item wdt:P53 wd:Q99364026 } UNION { ?item wdt:P734 wd:Q37080030 ; wdt:P27 wd:Q39 }
  OPTIONAL { ?item wdt:P18 ?img } OPTIONAL { ?item wdt:P569 ?birth }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "de,en". } }""",
    """SELECT DISTINCT ?item ?itemLabel ?itemDescription ?img ?inception WHERE {
  ?item rdfs:label ?l . FILTER(LANG(?l) IN ("de","en","fr")) FILTER(CONTAINS(?l, "Bally"))
  ?item wdt:P131* wd:Q68120 .
  OPTIONAL { ?item wdt:P18 ?img } OPTIONAL { ?item wdt:P571 ?inception }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "de,en". } }""",
]
WD_SKIP = {"Q601897", "Q4851974", "Q805383", "Q109937311", "Q18638654", "Q13225111"}


def src_wikidata():
    """Wikidata entities linked to the Bally company, family, museum, archive and sites."""
    out, seen = [], set()
    for q in WD_QUERIES:
        d = None
        for t in range(4):
            try:
                r = requests.get("https://query.wikidata.org/sparql", params={"query": q, "format": "json"}, headers=WD_UA, timeout=90)
                if r.status_code == 429:
                    time.sleep(int(r.headers.get("Retry-After", 30))); continue
                r.raise_for_status(); d = r.json(); break
            except Exception as e:
                log("wikidata fail", type(e).__name__); time.sleep(10)
        time.sleep(2)
        for b in (d or {}).get("results", {}).get("bindings", []):
            qid = b["item"]["value"].rsplit("/", 1)[-1]
            if qid in seen or qid in WD_SKIP:
                continue
            seen.add(qid)
            g = lambda k: (b.get(k) or {}).get("value")
            img = g("img")
            date = (g("inception") or g("birth") or "")[:10]
            out.append({"source": "wikidata", "source_id": qid, "source_url": f"https://www.wikidata.org/wiki/{qid}", "title": g("itemLabel"),
                        "description": " ".join(x for x in [g("itemDescription"), f"(Wikidata relation: {g('propLabel')})" if g("propLabel") else None] if x),
                        "date_text": date or None, "creator": None,
                        "image_url": (img.replace("http://", "https://") + "?width=1600") if img else None,
                        "license": "CC0 (data); image per Wikimedia Commons", "credit": "Wikidata", "kind_hint": "entity", "data": {"qid": qid, "relation": g("propLabel")}})
    return out


def src_shab():
    """Swiss Official Gazette of Commerce (SHAB/FOSC), electronic edition: notices naming Bally entities."""
    import xml.etree.ElementTree as ET
    out, seen = [], set()
    for kw in ["Bally Schuhfabriken", "Bally International", "Bally Schweiz", "Bally Holding", "C. F. Bally", "Bally Management", "Bally Retail", "Bally Logistik", "Bally"]:
        page = 0
        while page < 20:
            try:
                r = requests.get("https://amtsblattportal.ch/api/v1/publications", params={"publicationStates": "PUBLISHED", "tenant": "shab", "keyword": kw,
                                 "pageRequest.page": page, "pageRequest.size": 200}, headers={**UA, "Accept": "application/xml"}, timeout=90)
                root = ET.fromstring(r.content)
            except Exception as e:
                log("shab fail", type(e).__name__); break
            metas = [m for m in root.iter() if m.tag.endswith("meta")]
            if not metas:
                break
            for m in metas:
                def f(path):
                    el = m.find(path)
                    return el.text.strip() if el is not None and el.text else None
                pid = f("id")
                titles = {c.tag: (c.text or "") for c in (m.find("title") or [])}
                title = titles.get("de") or titles.get("en") or titles.get("fr") or titles.get("it") or ""
                if not pid or pid in seen or "bally" not in title.lower():
                    continue
                seen.add(pid)
                out.append({"source": "shab", "source_id": pid, "source_url": f"https://amtsblattportal.ch/#!/search/publications/detail/{pid}",
                            "title": title, "description": f"SHAB/FOSC notice {f('publicationNumber') or ''}, rubric {f('rubric') or ''}/{f('subRubric') or ''}. " + (titles.get("en") or ""),
                            "date_text": (f("publicationDate") or "")[:10], "creator": "Swiss Official Gazette of Commerce", "image_url": None,
                            "license": "Public record", "credit": "SHAB / FOSC", "kind_hint": "document", "data": {"publicationNumber": f("publicationNumber"), "rubric": f("rubric"), "subRubric": f("subRubric"), "titles": titles}})
            total = next((int(e.text) for e in root.iter() if e.tag.endswith("total") and (e.text or "").isdigit()), 0)
            page += 1
            if page * 200 >= total:
                break
    return out


PLUGINS = {"wikidata": src_wikidata, "shab": src_shab, "vam": src_vam, "vamorg": src_vamorg, "met": src_met, "europeana": src_europeana, "loc": src_loc, "ia": src_ia,
           "ccsa": src_ccsa, "srg": src_srg, "memobase": src_memobase}

FILTER_TOOL = {"name": "classify", "description": "Classify candidate records.", "input_schema": {"type": "object", "properties": {
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "i": {"type": "integer"},
        "relevance": {"type": "number", "description": "0 to 1: is this record about the Swiss luxury shoe and fashion house Bally (C. F. Bally, Bally Schuhfabriken AG, its factories, stores, products, advertising and posters, people, the Bally family, Bally-Park, Ballyana)? Score 0 for Bally Gaming, Bally's casinos, Bally Manufacturing/pinball, Bally Sports, Bally Total Fitness, Boris Bally (metal artist), places named Bally (Ireland, India), the botanist Peter Bally (herbarium sheets, Kalanchoë), gunsmiths, politicians or athletes named Bally, and people merely surnamed Bally with no link to the company."},
        "kind": {"type": "string", "enum": ["factory", "store", "product", "poster", "advertisement", "person", "aerial", "document", "film", "logo", "place", "publication", "other"]},
        "year": {"type": "integer", "description": "Best year if stated, else 0."},
        "title_en": {"type": "string", "description": "Short English catalogue title."}},
        "required": ["i", "relevance", "kind", "year", "title_en"]}}}, "required": ["items"]}}


def classify(batch):
    s = lambda v: " ".join(map(str, v)) if isinstance(v, list) else str(v or "")
    lines = [f"[{i}] source {b['source']} | {s(b.get('title'))} | {s(b.get('description'))[:350]} | date {s(b.get('date_text'))} | creator {s(b.get('creator'))} | type {s(b.get('kind_hint'))}" for i, b in enumerate(batch)]
    for _ in range(3):
        try:
            msg = client.messages.create(model="claude-haiku-5-5", max_tokens=8000, tools=[FILTER_TOOL], tool_choice={"type": "tool", "name": "classify"},
                                         messages=[{"role": "user", "content": "Classify each record:\n" + "\n".join(lines)}])
            for b in msg.content:
                if b.type == "tool_use":
                    return {x["i"]: x for x in b.input["items"]}
        except Exception as e:
            log("classify error", type(e).__name__)
            time.sleep(5)
    return {}


def put(data, path):
    r = requests.post(f"{SB}/storage/v1/object/web/{path}", data=data, timeout=120,
                      headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "image/jpeg", "x-upsert": "true", "cache-control": "max-age=31536000"})
    r.raise_for_status()


def copy_image(rec):
    if not rec.get("image_url"):
        return None, None, None, None
    try:
        for t in range(5):
            r = requests.get(rec["image_url"], headers=UA, timeout=120)
            if r.status_code in (429, 503):
                time.sleep(5 * (t + 1)); continue
            break
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        if im.width < 120:
            return None, None, None, None
        im.thumbnail((1600, 1600))
        key = hashlib.sha1(f"{rec['source']}:{rec['source_id']}".encode()).hexdigest()[:16]
        b = io.BytesIO(); im.save(b, "JPEG", quality=86, optimize=True, progressive=True)
        put(b.getvalue(), f"external/{rec['source']}/{key}.jpg")
        t = im.copy(); t.thumbnail((400, 400)); b2 = io.BytesIO(); t.save(b2, "JPEG", quality=82)
        put(b2.getvalue(), f"external/{rec['source']}/thumbs/{key}.jpg")
        return f"external/{rec['source']}/{key}.jpg", f"external/{rec['source']}/thumbs/{key}.jpg", im.width, im.height
    except Exception as e:
        log("image fail", rec["source"], str(rec["source_id"])[:40], type(e).__name__)
        return None, None, None, None


def main():
    cands = []
    for s in SOURCES:
        try:
            got = PLUGINS[s]()
        except Exception as e:
            log("source fail", s, type(e).__name__, str(e)[:200]); got = []
        seen, uniq = set(), []
        for g in got:
            if g["source_id"] and g["source_id"] not in seen:
                seen.add(g["source_id"]); uniq.append(g)
        log(s, "candidates", len(uniq))
        cands += uniq
    have = {x["id"] for x in requests.get(f"{SB}/rest/v1/ext_records", headers=H, params={"select": "id", "limit": 20000}, timeout=60).json()}
    cands = [c for c in cands if f"{c['source']}:{c['source_id']}" not in have]
    junk = re.compile(r"herbari|holotype|isotype|specimen|kalancho|euphorbia|monadenium|sansevieria|P\.R\.O\.\s*Bally", re.I)
    cands = [c for c in cands if not str(c["source_id"]).startswith("/11614/") and not junk.search(f"{c.get('title')} {c.get('description')}")]
    log("new candidates", len(cands))
    kept = []
    batches = [cands[i:i + 40] for i in range(0, len(cands), 40)]
    with ThreadPoolExecutor(6) as ex:
        for batch, res in zip(batches, ex.map(classify, batches)):
            for i, b in enumerate(batch):
                c = res.get(i)
                if c and c["relevance"] >= 0.6:
                    b.update({"relevance": c["relevance"], "kind": c["kind"], "title_en": c["title_en"], "year": c["year"] or year_of(b.get("date_text"))})
                    kept.append(b)
    log("relevant", len(kept))
    with ThreadPoolExecutor(3) as ex:
        paths = list(ex.map(copy_image, kept))
    rows = []
    for b, (p, t, w, h) in zip(kept, paths):
        rows.append({"id": f"{b['source']}:{b['source_id']}", "source": b["source"], "source_id": str(b["source_id"]), "source_url": b.get("source_url"),
                     "title": b.get("title_en") or b.get("title"), "description": b.get("description") or None, "kind": b.get("kind"),
                     "date_text": b.get("date_text") or None, "year": b.get("year") or None, "creator": b.get("creator") or None,
                     "license": b.get("license") or None, "credit": b.get("credit"), "image_url": b.get("image_url"), "image_path": p, "thumb_path": t,
                     "width": w, "height": h, "relevance": b["relevance"], "ai_status": "filtered", "data": {"original_title": b.get("title"), **(b.get("data") or {})}})
    for i in range(0, len(rows), 100):
        r = requests.post(f"{SB}/rest/v1/ext_records", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, json=rows[i:i + 100], timeout=120)
        if not r.ok:
            log("upsert", r.status_code, r.text[:300]); r.raise_for_status()
    from collections import Counter
    log("stored", len(rows), dict(Counter(r["source"] for r in rows)), dict(Counter(r["kind"] for r in rows)))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
