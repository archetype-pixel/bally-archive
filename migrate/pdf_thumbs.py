"""Give every catalogued PDF a picture: render page 1 to display (1600px) and thumb (400px) JPEGs in
the public web bucket, at the same paths photos use (display/<GUID>.jpg, thumb/<GUID>.jpg), and
record them on file_copy so the catalogue shows the cover instead of "No photograph".
The original PDF is untouched. Env: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
import io, os, subprocess, sys, tempfile
import requests
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}


def rows():
    r = requests.get(f"{SB}/rest/v1/file_copy", headers=H, timeout=60, params={
        "select": "asset_id,guid,dest_original,dest_display", "status": "eq.copied",
        "dest_original": "ilike.*.pdf"})
    r.raise_for_status()
    return r.json()


def put(key, data):
    r = requests.post(f"{SB}/storage/v1/object/web/{key}", data=data, timeout=300,
                      headers={**H, "Content-Type": "image/jpeg", "x-upsert": "true"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"{key}: {r.status_code} {r.text[:120]}")


def main():
    todo = rows()
    print(len(todo), "pdfs")
    done = 0
    for rec in todo:
        guid = rec["guid"].upper()
        try:
            r = requests.get(f"{SB}/storage/v1/object/archive/{rec['dest_original']}", headers=H, timeout=600)
            r.raise_for_status()
            with tempfile.TemporaryDirectory() as d:
                pdf = os.path.join(d, "f.pdf")
                open(pdf, "wb").write(r.content)
                subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-r", "150", "-jpeg", pdf, os.path.join(d, "p")], check=True)
                page = [f for f in os.listdir(d) if f.startswith("p") and f.endswith(".jpg")][0]
                im = Image.open(os.path.join(d, page)).convert("RGB")
            for px, key in ((1600, f"display/{guid}.jpg"), (400, f"thumb/{guid}.jpg")):
                c = im.copy(); c.thumbnail((px, px)); b = io.BytesIO(); c.save(b, "JPEG", quality=88, optimize=True)
                put(key, b.getvalue())
            w, h = im.size
            u = requests.patch(f"{SB}/rest/v1/file_copy", headers={**H, "Content-Type": "application/json", "Prefer": "return=minimal"},
                               params={"asset_id": f"eq.{rec['asset_id']}"},
                               json={"dest_display": f"display/{guid}.jpg", "dest_thumb": f"thumb/{guid}.jpg", "width": w, "height": h}, timeout=60)
            u.raise_for_status()
            done += 1
            print("ok", rec["dest_original"])
        except Exception as e:
            print("FAIL", rec["dest_original"], type(e).__name__, str(e)[:200])
    print("done", done, "of", len(todo))


if __name__ == "__main__":
    main()
