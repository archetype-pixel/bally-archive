"""AI tagging pass: second image fingerprint (OpenAI CLIP ViT-B/32, the same weights the browser
can run for visual search) plus zero-shot tags from migrate/tag_vocab.py.

For every photo: embed the 400px thumb, store the fingerprint (image_embeddings.embedding2), then
score it against each tag group and store the top 3 tags per group with a probability (ai_tags).
SETUP=1 only writes the vocabulary (tag_vocab) and exits. Resumable, sharded (embed_queue.shard 0..15).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, SHARD, SHARDS, RUN_SECONDS, SETUP
"""
import io
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import open_clip
import requests
import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from tag_vocab import VOCAB  # noqa: E402

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "16"))
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "20400"))
SETUP = os.environ.get("SETUP") == "1"
BATCH = 32
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
T0 = time.time()
torch.set_num_threads(max(1, os.cpu_count() or 2))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def post(path, body, extra=None, tries=4):
    for i in range(tries):
        try:
            r = requests.post(f"{SB_URL}/rest/v1/{path}", headers={**H, **(extra or {})}, json=body, timeout=300)
            if r.status_code < 300:
                return r.json() if r.text.strip() else None
            log("http", r.status_code, r.text[:200])
        except requests.RequestException as e:
            log("net", type(e).__name__)
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"{path} failed")


def prompt_text(p):
    return p if p.lower().startswith("a photo") else f"a photo of {p}"


def text_features(model, tokenizer):
    with torch.no_grad():
        t = model.encode_text(tokenizer([prompt_text(p) for _, _, p in VOCAB]))
        return t / t.norm(dim=-1, keepdim=True)


def fetch(rec):
    try:
        r = requests.get(f"{SB_URL}/storage/v1/object/public/web/{rec['thumb_path']}", timeout=60)
        if r.status_code != 200:
            return rec, None
        return rec, Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        return rec, None


def main():
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    model.eval()
    tf = text_features(model, tokenizer)
    if SETUP:
        rows = [{"tag": tag, "grp": grp, "prompt": prompt_text(p), "embedding": v}
                for (grp, tag, p), v in zip(VOCAB, tf.tolist())]
        post("rpc/tag_vocab_store", {"p_rows": rows})
        log("vocabulary stored", len(rows))
        return
    groups = defaultdict(list)
    for i, (grp, _, _) in enumerate(VOCAB):
        groups[grp].append(i)
    scale = model.logit_scale.exp().item()
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        while RUN_SECONDS - (time.time() - T0) > 300:
            batch = post("rpc/embed_batch2", {"p_shard": SHARD, "p_shards": SHARDS, "p_limit": BATCH * 4})
            if not batch:
                log("nothing left")
                break
            loaded = list(ex.map(fetch, batch))
            emb_rows, tag_rows, failed = [], [], []
            for i in range(0, len(loaded), BATCH):
                part = [(r, im) for r, im in loaded[i:i + BATCH] if im is not None]
                failed += [r["asset_guid"] for r, im in loaded[i:i + BATCH] if im is None]
                if not part:
                    continue
                with torch.no_grad():
                    f = model.encode_image(torch.stack([preprocess(im) for _, im in part]))
                    f = f / f.norm(dim=-1, keepdim=True)
                    logits = scale * f @ tf.T
                for (r, _), v, lg in zip(part, f.tolist(), logits):
                    emb_rows.append({"asset_guid": r["asset_guid"], "embedding": v})
                    for grp, idx in groups.items():
                        p = torch.softmax(lg[idx], dim=0)
                        top = torch.topk(p, k=min(3, len(idx)))
                        for rank, (score, j) in enumerate(zip(top.values.tolist(), top.indices.tolist()), 1):
                            tag_rows.append({"asset_guid": r["asset_guid"], "object_guid": r["object_guid"], "grp": grp,
                                             "tag": VOCAB[idx[j]][1], "score": round(score, 4), "rank": rank})
            if tag_rows:
                post("ai_tags", tag_rows, {"Prefer": "resolution=merge-duplicates,return=minimal"})
            post("rpc/embed_store2", {"p_rows": emb_rows, "p_failed": failed})
            done += len(emb_rows)
            log(f"tagged {done} this run ({len(failed)} unreadable in batch)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal", type(e).__name__, str(e)[:300])
        sys.exit(1)
