"""AI descriptions: a vision model looks at up to 3 photos of each object (lead first) together with
its catalogue record and returns a short description plus structured, controlled-vocabulary fields.
Results go to ai_descriptions (never into the catalogue fields). Resumable, sharded.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY, MODEL, SHARD, SHARDS, LIMIT, RUN_SECONDS, WORKERS
"""
import base64
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import anthropic
import requests
from PIL import Image

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
MODEL = os.environ.get("MODEL", "claude-haiku-5-5")
SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "1"))
LIMIT = int(os.environ.get("LIMIT", "0"))  # 0 = no limit
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "20400"))
WORKERS = int(os.environ.get("WORKERS", "6"))
IMG_PX = 768
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
T0 = time.time()
WORKSPACE = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
client = anthropic.Anthropic(max_retries=6, default_headers={"anthropic-workspace-id": WORKSPACE} if WORKSPACE else None)

THEMES = ["golf", "tennis", "skiing", "apres-ski", "mountaineering", "hiking", "beach", "sailing", "ballet", "dance",
          "riding", "hunting", "football", "cycling", "running", "ice-skating", "motoring", "travel", "evening",
          "wedding", "military", "workwear", "children", "none"]
PICTURE = ["product-photo", "poster", "advertisement", "catalogue-page", "shop-window", "store-interior",
           "people-photo", "portrait", "event", "factory", "packaging", "drawing", "document", "building",
           "landscape", "logo", "film-still", "other"]
SHOE = ["oxford", "derby", "brogue", "loafer", "monk-strap", "pump", "slingback", "mary-jane", "ballet-flat", "sandal",
        "mule", "espadrille", "moccasin", "boat-shoe", "sneaker", "ankle-boot", "chelsea-boot", "lace-up-boot",
        "knee-boot", "snow-boot", "ski-boot", "golf-shoe", "riding-boot", "slipper", "clog", "other", "not-a-shoe"]
BAG = ["tote", "shoulder-bag", "crossbody", "clutch", "top-handle", "bucket-bag", "backpack", "briefcase",
       "duffle", "suitcase", "trunk", "wallet", "belt", "other", "not-a-bag"]
BRANDING = ["bally-stripe", "b-monogram", "crest", "swiss-cross", "bally-wordmark", "none-visible"]

TOOL = {
    "name": "record_object",
    "description": "Record what is visible in the photos of one archive object.",
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "2 to 3 plain sentences in English describing the object as seen: what it is, materials, colours, notable details. No speculation about value. No marketing language."},
            "picture_type": {"type": "string", "enum": PICTURE},
            "themes": {"type": "array", "items": {"type": "string", "enum": THEMES}, "description": "Sports, activities or occasions the object is clearly made for or depicts. Use none if nothing clear."},
            "shoe_type": {"type": "string", "enum": SHOE},
            "bag_type": {"type": "string", "enum": BAG},
            "materials": {"type": "array", "items": {"type": "string"}, "description": "Visible materials, lower case, e.g. suede, patent leather, calf leather, crocodile, canvas, fur, raffia, metal, wood, paper."},
            "colours": {"type": "array", "items": {"type": "string"}, "description": "Main colours, lower case."},
            "details": {"type": "array", "items": {"type": "string"}, "description": "Visible construction or design details, lower case, e.g. perforated wingtip, buckle, tassel, fringe, block heel, lug sole, quilting."},
            "branding": {"type": "array", "items": {"type": "string", "enum": BRANDING}},
            "visible_text": {"type": "string", "description": "Any text legible in the photos, transcribed exactly, separated by ' / '. Empty if none."},
            "people": {"type": "boolean", "description": "True if people appear in the photos."},
            "era_guess": {"type": "string", "description": "Decade the design looks like, e.g. 1950s, or 'unclear'."},
            "conflicts": {"type": "array", "items": {"type": "string"}, "description": "Short notes where the photos clearly contradict the catalogue record (wrong category, wrong colour, wrong gender, photo of a different object). Empty if none."},
        },
        "required": ["description", "picture_type", "themes", "materials", "colours", "details", "branding", "visible_text", "people", "era_guess", "conflicts"],
    },
}

SYSTEM = ("You catalogue the Bally heritage archive (Swiss shoes, bags, accessories, posters, advertising and "
          "photographs since 1851). Describe only what you can see. Use the catalogue record for context, but report "
          "clear contradictions in 'conflicts' rather than repeating the record. Pick shoe_type only for footwear and "
          "bag_type only for bags and small leather goods; otherwise use not-a-shoe / not-a-bag.")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def rpc(name, body):
    for i in range(5):
        try:
            r = requests.post(f"{SB_URL}/rest/v1/rpc/{name}", headers=H, json=body, timeout=300)
            if r.ok:
                return r.json()
            log("rpc", name, r.status_code, r.text[:150])
        except requests.RequestException as e:
            log("rpc net", type(e).__name__)
        time.sleep(3 * (i + 1))
    raise RuntimeError(name)


def store(row):
    for i in range(5):
        r = requests.post(f"{SB_URL}/rest/v1/ai_descriptions", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, json=row, timeout=60)
        if r.ok:
            return
        log("store", r.status_code, r.text[:150])
        time.sleep(3 * (i + 1))


def image_block(path):
    r = requests.get(f"{SB_URL}/storage/v1/object/public/web/{path}", timeout=60)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    im.thumbnail((IMG_PX, IMG_PX))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b.getvalue()).decode()}}


def record_text(o):
    keep = {k: o.get(k) for k in ["code", "class", "category", "subcategory", "model", "logo", "gender", "season",
                                  "date_from", "date_to", "tags", "colors", "description", "notes", "wording"] if o.get(k)}
    return "Catalogue record:\n" + json.dumps(keep, ensure_ascii=False)


def one(o):
    photos = list(dict.fromkeys(o.get("photos") or []))[:3]
    try:
        blocks = [image_block(p) for p in photos]
        msg = client.messages.create(
            model=MODEL, max_tokens=900, system=SYSTEM, tools=[TOOL], tool_choice={"type": "tool", "name": "record_object"},
            messages=[{"role": "user", "content": blocks + [{"type": "text", "text": record_text(o)}]}])
        data = next(b.input for b in msg.content if b.type == "tool_use")
        store({"object_guid": o["guid"], "model": MODEL, "description": data.get("description"), "fields": data,
               "photos_used": photos, "input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens,
               "status": "done", "error": None})
        return msg.usage.input_tokens, msg.usage.output_tokens, None
    except anthropic.AuthenticationError:
        raise
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:200]}"
        store({"object_guid": o["guid"], "model": MODEL, "status": "error", "error": err, "photos_used": photos})
        return 0, 0, err


def main():
    done = tin = tout = errs = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        while RUN_SECONDS - (time.time() - T0) > 300:
            want = WORKERS * 4 if not LIMIT else min(WORKERS * 4, LIMIT - done)
            if want <= 0:
                break
            batch = rpc("vision_batch", {"p_shard": SHARD, "p_shards": SHARDS, "p_limit": want})
            if not batch:
                log("nothing left")
                break
            for i, o, e in ((x[0], x[1], x[2]) for x in ex.map(one, batch)):
                tin += i
                tout += o
                errs += 1 if e else 0
                if e:
                    log("error", e)
            done += len(batch)
            log(f"described {done} (errors {errs}) tokens in {tin} out {tout} avg/object in {tin // max(1, done - errs)} out {tout // max(1, done - errs)}")


if __name__ == "__main__":
    try:
        main()
    except anthropic.AuthenticationError as e:
        log("auth failed: check ANTHROPIC_API_KEY", str(e)[:200])
        sys.exit(1)
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
