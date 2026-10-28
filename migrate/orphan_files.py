"""Look inside every stored file that has no catalogue record (orphan_files): make a cover picture
(PDF page 1, film still, the PNG itself) in the public web bucket under orphans/, and record what
it is (pages, length, first-page text, zip contents) so it can be attached to the right record.
Originals untouched. Env: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
import hashlib, json, os, subprocess, tempfile, urllib.parse, zipfile
import requests

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}


def run(cmd, timeout=900):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(cmd[0] + ": " + (p.stderr or p.stdout)[-300:])
    return p.stdout


def put(key, path):
    with open(path, "rb") as f:
        r = requests.post(f"{SB}/storage/v1/object/web/{key}", data=f, timeout=300,
                          headers={**H, "Content-Type": "image/jpeg", "x-upsert": "true"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"{key}: {r.status_code} {r.text[:120]}")


def patch(path, body):
    r = requests.patch(f"{SB}/rest/v1/orphan_files", headers={**H, "Content-Type": "application/json", "Prefer": "return=minimal"},
                       params={"path": f"eq.{path}"}, json=body, timeout=60)
    r.raise_for_status()


def main():
    rows = requests.get(f"{SB}/rest/v1/orphan_files", headers=H, params={"select": "path,kind", "display_path": "is.null", "zip_listing": "is.null"}, timeout=60).json()
    print(len(rows), "files", flush=True)
    for i, rec in enumerate(rows):
        path, kind = rec["path"], rec["kind"]
        slug = "orphans/" + hashlib.sha1(path.encode()).hexdigest()[:16]
        try:
            with tempfile.TemporaryDirectory() as d:
                src = os.path.join(d, "src" + os.path.splitext(path)[1].lower())
                url = f"{SB}/storage/v1/object/archive/{urllib.parse.quote(path)}"
                with requests.get(url, headers=H, timeout=1800, stream=True) as r:
                    r.raise_for_status()
                    with open(src, "wb") as f:
                        for c in r.iter_content(1 << 20):
                            f.write(c)
                body = {}
                if kind == "zip":
                    with zipfile.ZipFile(src) as z:
                        names = [f"{x.filename}  {x.file_size // 1024} KB" for x in z.infolist()]
                    body["zip_listing"] = f"{len(names)} files\n" + "\n".join(names[:300])
                    patch(path, body); print("zip", path, len(names), flush=True); continue
                pic = src
                if kind == "document":
                    info = run(["pdfinfo", src])
                    body["pages"] = int(next((l.split()[-1] for l in info.splitlines() if l.startswith("Pages:")), "0"))
                    body["text_excerpt"] = run(["pdftotext", "-f", "1", "-l", "2", "-layout", src, "-"])[:2000]
                    run(["pdftoppm", "-f", "1", "-l", "1", "-r", "150", "-jpeg", src, os.path.join(d, "p")])
                    pic = os.path.join(d, [f for f in os.listdir(d) if f.startswith("p") and f.endswith(".jpg")][0])
                elif kind == "video":
                    dur = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", src]).strip()
                    body["duration_s"] = round(float(dur), 1) if dur and dur != "N/A" else None
                    pic = os.path.join(d, "still.png")
                    t = "5" if (body["duration_s"] or 0) > 10 else "0"
                    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", t, "-i", src, "-frames:v", "1", pic])
                w = int(run(["vipsheader", "-f", "width", pic]).strip()); h = int(run(["vipsheader", "-f", "height", pic]).strip())
                for px, name in ((1600, "display"), (400, "thumb")):
                    out = os.path.join(d, f"{name}.jpg")
                    run(["vipsthumbnail", pic, "--size", f"{px}x{px}", "--export-profile", "srgb", "-o", f"{out}[Q=88,strip]"])
                    put(f"{slug}-{name}.jpg", out)
                body.update({"display_path": f"{slug}-display.jpg", "thumb_path": f"{slug}-thumb.jpg", "width": w, "height": h})
                patch(path, body)
                print("ok", path, json.dumps({k: v for k, v in body.items() if k != "text_excerpt"}), flush=True)
        except Exception as e:
            print("FAIL", path, type(e).__name__, str(e)[:300], flush=True)
            patch(path, {"note": ("failed: " + str(e))[:500]})


if __name__ == "__main__":
    main()
