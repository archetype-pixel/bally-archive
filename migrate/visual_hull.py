"""Draft 3D model of a turntable object from its 24 spin frames by silhouette carving (visual hull), coloured from the photos.
Our own code; numpy + scikit-image + trimesh only. Good for visual review; not dimensionally accurate: hollows (the shoe opening)
are filled and size is in image units unless a real measurement is supplied.
Usage: visual_hull.py <frames_dir_or_url_prefix> <out.glb> [n_frames=24]"""
import io, sys, math, json
import numpy as np
import requests
from PIL import Image
from scipy import ndimage
from skimage import measure
import trimesh

SRC, OUT = sys.argv[1], sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 24


def load(i):
    if SRC.startswith("http"):
        r = requests.get(f"{SRC}/{i}.jpg", timeout=60); r.raise_for_status(); return Image.open(io.BytesIO(r.content)).convert("RGB")
    return Image.open(f"{SRC}/f{i}.jpg").convert("RGB")


imgs = [np.asarray(load(i)).astype(np.float32) for i in range(N)]
H, W = imgs[0].shape[:2]


def mask_of(im):
    mn = im.min(2); sat = im.max(2) - im.min(2)
    m = (mn < 228) | (sat > 22)
    m = ndimage.binary_opening(m, iterations=1)
    lab, n = ndimage.label(m)
    if n:
        sizes = ndimage.sum(m, lab, range(1, n + 1)); m = lab == (1 + int(np.argmax(sizes)))
    return ndimage.binary_fill_holes(ndimage.binary_closing(m, iterations=2))


masks = [mask_of(im) for im in imgs]
# turntable axis column: midpoint of opposite views' silhouette centres
cxs = []
for k in range(N // 2):
    xa = np.where(masks[k].any(0))[0]; xb = np.where(masks[k + N // 2].any(0))[0]
    cxs.append((xa.min() + xa.max() + xb.min() + xb.max()) / 4)
CX = float(np.median(cxs))
bottom = max(np.where(m.any(1))[0].max() for m in masks)
top = min(np.where(m.any(1))[0].min() for m in masks)
half = max(max(abs(np.where(m.any(0))[0].min() - CX), abs(np.where(m.any(0))[0].max() - CX)) for m in masks) * 1.05
G = 160


def carve(elev_deg, sign, cy):
    e = math.radians(elev_deg)
    xs = np.linspace(-half, half, G); zs = xs.copy()
    ys = np.linspace(0, (bottom - top) * 1.15, G)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    occ = np.ones(X.shape, bool)
    for k in range(N):
        th = sign * 2 * math.pi * k / N
        u = X * math.cos(th) + Z * math.sin(th)
        d = -X * math.sin(th) + Z * math.cos(th)
        v = Y * math.cos(e) - d * math.sin(e)
        px = np.round(CX + u).astype(int); py = np.round(cy - v).astype(int)
        inside = (px >= 0) & (px < W) & (py >= 0) & (py < H)
        hit = np.zeros(X.shape, bool); hit[inside] = masks[k][py[inside], px[inside]]
        occ &= hit
    return occ, (xs, ys, zs, e, th)


def score(occ, elev_deg, sign, cy, xs, ys, zs):
    e = math.radians(elev_deg); ious = []
    idx = np.argwhere(occ)
    if len(idx) < 100:
        return 0
    X = xs[idx[:, 0]]; Y = ys[idx[:, 1]]; Z = zs[idx[:, 2]]
    for k in range(0, N, 3):
        th = sign * 2 * math.pi * k / N
        u = X * math.cos(th) + Z * math.sin(th); d = -X * math.sin(th) + Z * math.cos(th)
        v = Y * math.cos(e) - d * math.sin(e)
        px = np.clip(np.round(CX + u).astype(int), 0, W - 1); py = np.clip(np.round(cy - v).astype(int), 0, H - 1)
        r = np.zeros((H, W), bool); r[py, px] = True; r = ndimage.binary_closing(r, iterations=3)
        ious.append((r & masks[k]).sum() / max(1, (r | masks[k]).sum()))
    return float(np.mean(ious))


import os
best = json.loads(os.environ["FIT"]) if os.environ.get("FIT") else None
if best: best = (1, best["sign"], best["elev"], best["cy"])
for sign in ((1, -1) if not os.environ.get("FIT") else ()):
    for elev in (0, 4, 8, 12, 16, 20, 24):
        for dy in (0, 20, 40, 60, 80):
            cy = bottom - dy
            occ, (xs, ys, zs, e, _) = carve(elev, sign, cy)
            s = score(occ, elev, sign, cy, xs, ys, zs)
            if not best or s > best[0]:
                best = (s, sign, elev, cy)
print("best fit", json.dumps({"iou": round(best[0], 3), "sign": int(best[1]), "elev": int(best[2]), "cy": int(best[3])}), flush=True)
s, sign, elev, cy = best
G = int(os.environ.get("GRID", "300"))
occ, (xs, ys, zs, e, _) = carve(elev, sign, cy)
occ = ndimage.binary_opening(occ, iterations=1)
vol = ndimage.gaussian_filter(occ.astype(np.float32), 1.4)
verts, faces, _, _ = measure.marching_cubes(vol, 0.5)
step = xs[1] - xs[0]
P = np.stack([xs[0] + verts[:, 0] * step, ys[0] + verts[:, 1] * (ys[1] - ys[0]), zs[0] + verts[:, 2] * step], 1)
mesh = trimesh.Trimesh(vertices=P, faces=faces, process=True)
mesh.fix_normals()
trimesh.smoothing.filter_taubin(mesh, lamb=0.5, nu=-0.53, iterations=12)
if len(mesh.faces) > 150000:
    mesh = mesh.simplify_quadric_decimation(face_count=150000)
mesh.fix_normals()
P = mesh.vertices; Nn = mesh.vertex_normals
# colour each vertex from the photos that face it: median of the best-facing views (removes glare on polished leather)
samples = np.full((N, len(P), 3), np.nan, np.float32); dots = np.full((N, len(P)), -2.0, np.float32)
for k in range(N):
    th = sign * 2 * math.pi * k / N
    view = np.array([-math.sin(th) * math.cos(e), math.sin(e), math.cos(th) * math.cos(e)])
    dot = Nn @ view
    u = P[:, 0] * math.cos(th) + P[:, 2] * math.sin(th); d = -P[:, 0] * math.sin(th) + P[:, 2] * math.cos(th)
    v = P[:, 1] * math.cos(e) - d * math.sin(e)
    px = np.clip(np.round(CX + u).astype(int), 0, W - 1); py = np.clip(np.round(cy - v).astype(int), 0, H - 1)
    er = ndimage.binary_erosion(masks[k], iterations=4)[py, px]
    good = er & (dot > 0.2)
    px_ = imgs[k][py, px]
    glare = (px_.min(1) > 170) & ((px_.max(1) - px_.min(1)) < 25)  # white reflections of the studio floor on polished leather
    good &= ~glare if os.environ.get("DEGLARE", "1") == "1" else good
    samples[k][good] = px_[good]; dots[k][good] = dot[good]
order = np.argsort(-dots, axis=0)[:5]
top = np.take_along_axis(samples, order[:, :, None], axis=0)
col = np.nanmedian(top, axis=0)
col = np.where(np.isnan(col), 28, col)
mesh.visual = trimesh.visual.ColorVisuals(mesh, vertex_colors=np.c_[col.clip(0, 255).astype(np.uint8), np.full(len(P), 255, np.uint8)])
mesh.apply_scale(1 / 100.0)
mesh.export(OUT)
print("mesh", len(mesh.vertices), "vertices", len(mesh.faces), "faces ->", OUT)
