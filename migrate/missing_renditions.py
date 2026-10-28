"""Make web pictures for catalogued files that were copied but never got one (mostly 16-bit, CMYK
or very large TIFF scans, plus a film). Writes display/<GUID>.jpg (1600px) and thumb/<GUID>.jpg
(400px) to the public web bucket and records them on file_copy, same as every other photo.
Originals are untouched. Tries libvips first (handles CMYK, 16-bit, huge files), then ImageMagick.
Films get a still frame from ffmpeg. Env: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
import os, subprocess, tempfile, urllib.parse
import requests

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
VIDEO = (".m4v", ".mp4", ".mov")


def rows():
    r = requests.get(f"{SB}/rest/v1/file_copy", headers=H, timeout=60, params={
        "select": "asset_id,guid,dest_original,azure_path", "status": "eq.copied", "dest_display": "is.null",
        "dest_original": "not.ilike.*.pdf", "or": "(error.is.null,error.like.rendition:*)"})
    r.raise_for_status()
    return r.json()


def put(key, path):
    with open(path, "rb") as f:
        r = requests.post(f"{SB}/storage/v1/object/web/{key}", data=f, timeout=300,
                          headers={**H, "Content-Type": "image/jpeg", "x-upsert": "true", "cache-control": "max-age=604800"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"{key}: {r.status_code} {r.text[:120]}")


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if p.returncode:
        raise RuntimeError(" ".join(cmd[:2]) + ": " + (p.stderr or p.stdout)[-300:])
    return p.stdout


def size_of(path):
    try:
        return int(run(["vipsheader", "-f", "width", path]).strip()), int(run(["vipsheader", "-f", "height", path]).strip())
    except Exception:
        w, h = run(["identify", "-format", "%w %h\n", path + "[0]"]).split()[:2]
        return int(w), int(h)


def render(src, out, px):
    try:
        run(["vipsthumbnail", src, "--size", f"{px}x{px}", "--export-profile", "srgb", "-o", f"{out}[Q=88,strip]"])
    except Exception as e1:
        try:
            run(["convert", src + "[0]", "-auto-orient", "-colorspace", "sRGB", "-depth", "8", "-resize", f"{px}x{px}>", "-quality", "88", out])
        except Exception as e2:
            raise RuntimeError(f"vips: {e1} | magick: {e2}")


def main():
    todo = rows()
    print(len(todo), "files", flush=True)
    done = 0
    for rec in todo:
        guid = rec["guid"].upper()
        name = rec["dest_original"]
        try:
            with tempfile.TemporaryDirectory() as d:
                src = os.path.join(d, "src" + os.path.splitext(name)[1].lower())
                with requests.get(f"{SB}/storage/v1/object/archive/{name}", headers=H, timeout=900, stream=True) as r:
                    r.raise_for_status()
                    with open(src, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                if os.path.getsize(src) == 0 and "2. PostProcessed" in (rec.get("azure_path") or ""):
                    # The post-processed export is empty at source; the camera original sits beside it.
                    alt = "azure-mirror/" + rec["azure_path"].replace("2. PostProcessed", "1. Originali")
                    with requests.get(f"{SB}/storage/v1/object/archive/{urllib.parse.quote(alt)}", headers=H, timeout=900) as r:
                        r.raise_for_status()
                        open(src, "wb").write(r.content)
                    print("used camera original", alt, flush=True)
                if src.endswith(VIDEO):
                    still = os.path.join(d, "still.png")
                    try:
                        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "5", "-i", src, "-frames:v", "1", still])
                    except Exception:
                        run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-frames:v", "1", still])
                    src = still
                w, h = size_of(src)
                disp, thumb = os.path.join(d, "display.jpg"), os.path.join(d, "thumb.jpg")
                render(src, disp, 1600)
                render(src, thumb, 400)
                put(f"display/{guid}.jpg", disp)
                put(f"thumb/{guid}.jpg", thumb)
            u = requests.patch(f"{SB}/rest/v1/file_copy", headers={**H, "Content-Type": "application/json", "Prefer": "return=minimal"},
                               params={"asset_id": f"eq.{rec['asset_id']}"},
                               json={"dest_display": f"display/{guid}.jpg", "dest_thumb": f"thumb/{guid}.jpg", "width": w, "height": h, "error": None}, timeout=60)
            u.raise_for_status()
            done += 1
            print("ok", name, w, h, flush=True)
        except Exception as e:
            print("FAIL", name, type(e).__name__, str(e)[:300], flush=True)
            requests.patch(f"{SB}/rest/v1/file_copy", headers={**H, "Content-Type": "application/json", "Prefer": "return=minimal"},
                           params={"asset_id": f"eq.{rec['asset_id']}"}, json={"error": ("rendition: " + str(e))[:500]}, timeout=60)
    print("done", done, "of", len(todo), flush=True)


if __name__ == "__main__":
    main()
