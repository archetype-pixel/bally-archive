"""Visual fingerprints for book plates (same CLIP model as the archive) and suggested links to archive objects.
Writes library_plate_embeddings and library_plate_links only.
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY"""
import io, os, sys, time
import open_clip, requests, torch
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
SAME_PHOTO = 0.06   # cosine distance: very likely the same photograph
LIKELY = 0.13       # likely the same object / closely related


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def main():
    r = requests.get(f"{SB}/rest/v1/library_plates", headers=H, params={"select": "id,thumb_path", "order": "sort", "limit": 2000}, timeout=60)
    r.raise_for_status()
    plates = r.json()
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
    model.eval()
    emb, links = [], []
    for pl in plates:
        img = requests.get(f"{SB}/storage/v1/object/public/web/{pl['thumb_path']}", timeout=60)
        if img.status_code != 200:
            log("missing", pl["id"])
            continue
        im = Image.open(io.BytesIO(img.content)).convert("RGB")
        with torch.no_grad():
            f = model.encode_image(preprocess(im).unsqueeze(0))
            f = (f / f.norm(dim=-1, keepdim=True))[0].tolist()
        emb.append({"plate_id": pl["id"], "embedding": f})
        m = requests.post(f"{SB}/rest/v1/rpc/match_image", headers=H, json={"p_embedding": f, "p_limit": 5}, timeout=60)
        m.raise_for_status()
        for hit in m.json():
            d = hit["distance"]
            if d <= LIKELY:
                links.append({"plate_id": pl["id"], "object_guid": hit["object_guid"], "score": round(1 - d, 4),
                              "method": "visual-same-photo" if d <= SAME_PHOTO else "visual",
                              "status": "auto" if d <= SAME_PHOTO else "suggested"})
    for table, rows in (("library_plate_embeddings", emb), ("library_plate_links", links)):
        for i in range(0, len(rows), 100):
            x = requests.post(f"{SB}/rest/v1/{table}", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, json=rows[i:i + 100], timeout=120)
            if not x.ok:
                log(table, x.status_code, x.text[:200])
                x.raise_for_status()
        log(table, len(rows))
    log("same-photo links", sum(1 for l in links if l["status"] == "auto"), "suggested", sum(1 for l in links if l["status"] == "suggested"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
