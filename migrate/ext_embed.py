"""Fingerprint external images and suggest links to archive objects (same CLIP model as the archive)."""
import io, os, sys, time
import open_clip, requests, torch
from PIL import Image

SB = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def main():
    recs = requests.get(f"{SB}/rest/v1/ext_records", headers=H, params={"select": "id,thumb_path,kind", "thumb_path": "not.is.null", "kind": "neq.trademark", "limit": 5000}, timeout=60).json()
    have = {h["record_id"] for h in requests.get(f"{SB}/rest/v1/ext_embeddings", headers=H, params={"select": "record_id", "limit": 10000}, timeout=60).json()}
    recs = [r for r in recs if r["id"] not in have]
    log(len(recs), "records to fingerprint")
    model, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
    model.eval()
    emb, links = [], []
    for r in recs:
        img = requests.get(f"{SB}/storage/v1/object/public/web/{r['thumb_path']}", timeout=60)
        if img.status_code != 200:
            continue
        with torch.no_grad():
            f = model.encode_image(pre(Image.open(io.BytesIO(img.content)).convert("RGB")).unsqueeze(0))
            f = (f / f.norm(dim=-1, keepdim=True))[0].tolist()
        emb.append({"record_id": r["id"], "embedding": f})
        m = requests.post(f"{SB}/rest/v1/rpc/match_image", headers=H, json={"p_embedding": f, "p_limit": 5}, timeout=60).json()
        for hit in m:
            if hit["distance"] <= 0.13:
                links.append({"record_id": r["id"], "object_guid": hit["object_guid"], "score": round(1 - hit["distance"], 4),
                              "method": "visual-same-photo" if hit["distance"] <= 0.06 else "visual", "status": "suggested"})
    for table, rows in (("ext_embeddings", emb), ("ext_links", links)):
        for i in range(0, len(rows), 100):
            x = requests.post(f"{SB}/rest/v1/{table}", headers={**H, "Prefer": "resolution=ignore-duplicates,return=minimal"}, json=rows[i:i + 100], timeout=120)
            x.raise_for_status()
        log(table, len(rows))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
