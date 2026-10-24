"""AI pass for the interwar-footwear book plates: confirm/assign captions (English original), describe each photo,
decide image rights from the caption credit, and publish Bally-owned images (copy from the private bucket to web).
Env: as library_ai.py plus DATA (folder with plates/ and thumbs/)."""
import base64, io, json, os, re, sys
from concurrent.futures import ThreadPoolExecutor
import requests
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import library_ai as L  # noqa: E402

PUB = "interwar-footwear"
DATA = os.environ.get("DATA", "data/interwar")
THIRD = re.compile(r"Condé|Ferragamo|gta Archives|Nebelspalter|Landesausstellung im Werden|Exhibition Bally—Swiss Shoes|Museum für Gestaltung")


def is_bally(credit, caption):
    s = f"{credit or ''} {caption or ''}"
    if THIRD.search(s):
        return False
    return bool(re.search(r"Bally|Arola Hauszeitung|spring catalogues|Frau Mode spielt auf", s))


def local_block(pid, px=1100):
    im = Image.open(f"{DATA}/plates/{pid}.jpg").convert("RGB")
    im.thumbnail((px, px))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=88)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b.getvalue()).decode()}}


def put(local, remote):
    body = open(local, "rb").read()
    r = requests.post(f"{L.SB}/storage/v1/object/web/{remote}", data=body, timeout=120,
                      headers={"apikey": L.KEY, "Authorization": f"Bearer {L.KEY}", "Content-Type": "image/jpeg", "x-upsert": "true", "cache-control": "max-age=31536000"})
    r.raise_for_status()


def main():
    plates = L.get("library_plates", {"select": "*", "pub": f"eq.{PUB}", "order": "sort"})
    passages = L.get("library_passages", {"select": "page,pages,text_en", "pub": f"eq.{PUB}", "order": "seq"})
    L.log(len(plates), "plates")

    def ctx(page):
        return "\n".join(p["text_en"] or "" for p in passages if set(p.get("pages") or [p["page"]]) & {page - 1, page, page + 1})[:2500]

    def one(pl):
        pid = pl["id"][3:]
        try:
            cands = pl["caption_candidates"] or []
            lines = "\n".join(f"[{i}] (p. {c['page']}, figure {c['label']}) {c['en']}" for i, c in enumerate(cands))
            hint = f"Layout matching suggests: {pl['caption_en'][:200]!r} (figure {pl.get('fig_label')}). Confirm only if it fits." if pl.get("caption_en") else ""
            text = (f"Book: Schlittler/Tietze (eds.), Bally – A History of Footwear in the Interwar Period (2021). Photo on page {pl['page']}, "
                    f"position {pl['idx']} on the page.\n{hint}\nCandidate captions (English original):\n{lines or '(none)'}\n\n"
                    f"Surrounding text:\n{ctx(pl['page'])}\n\nThe book is in English: set caption_en to the chosen caption text exactly as printed "
                    "(without the figure number), not a translation.")
            data, _ = L.call(model=L.MODEL, max_tokens=2000,
                             system="You catalogue photographs from a scholarly book on Bally shoes in the interwar period. " + L.GLOSSARY,
                             tools=[L.PL_TOOL], messages=[{"role": "user", "content": [local_block(pid), {"type": "text", "text": text}]}])
            ch = data.get("caption_choice", -1)
            cand = cands[ch] if isinstance(ch, int) and 0 <= ch < len(cands) else None
            caption = cand["en"] if cand else (pl.get("caption_en") if pid in ("cover-1", "p006-1", "p191-1") else None)
            credit = (cand or {}).get("credit") or pl.get("credit")
            if pid in ("cover-1", "p006-1", "p191-1"):
                credit = credit or "Historical Archives of Bally Schuhfabriken AG; photo: Manuel Fabritz, © Bally"
            bally = is_bally(credit, caption) or pid in ("cover-1", "p006-1", "p191-1")
            rights = "public" if bally else "restricted"
            if rights == "public" and pl.get("rights") != "public":
                put(f"{DATA}/plates/{pid}.jpg", pl["path"])
                put(f"{DATA}/thumbs/{pid}.jpg", pl["thumb_path"])
            body = {"caption_en": caption, "caption_source": ("ai-" + data.get("choice_confidence", "low")) if cand else (pl.get("caption_source") or "none"),
                    "fig_label": (cand or {}).get("label") or pl.get("fig_label"), "credit": credit, "rights": rights,
                    "title_en": data.get("title_en"), "description_en": data.get("description_en"), "picture_type": data.get("picture_type"),
                    "era": data.get("era"), "date_text": data.get("date_text") or None,
                    "photographer": data.get("photographer") or ("Manuel Fabritz" if credit and "Fabritz" in credit else None),
                    "ai": data, "ai_model": L.MODEL, "ai_status": "done"}
            L.patch("library_plates", {"id": f"eq.{pl['id']}"}, body)
            return rights
        except Exception as e:
            L.log("plate error", pl["id"], type(e).__name__, str(e)[:200])
            return "error"

    with ThreadPoolExecutor(L.WORKERS) as ex:
        res = list(ex.map(one, plates))
    from collections import Counter
    L.log("plates", dict(Counter(res)))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        L.log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
