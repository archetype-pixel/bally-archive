"""Colourway studies: recolour the dark leather of an archive photo into new leather colours, keeping its shading and highlights.
Usage: colourways.py <in.jpg> <tag>. Writes cw_<tag>_<colour>.jpg (cropped to the object)."""
import numpy as np, sys
from PIL import Image
from skimage import color
src = Image.open(sys.argv[1]).convert("RGB"); a = np.asarray(src).astype(np.float32) / 255
lab = color.rgb2lab(a); L, A, B = lab[..., 0], lab[..., 1], lab[..., 2]
chroma = np.hypot(A, B)
upper = (L < 58) & (chroma < 14)  # black calf: dark, near-neutral
from scipy import ndimage
upper = ndimage.binary_opening(upper, iterations=1)
soft = ndimage.gaussian_filter(upper.astype(np.float32), 1.2)
g = np.asarray(src.convert("L")); yy, xx = np.where(g < 230)
BOX = (max(0, xx.min() - 30), max(0, yy.min() - 30), min(g.shape[1], xx.max() + 30), min(g.shape[0], yy.max() + 30))
src.crop(BOX).save(f"cw_{sys.argv[2]}_original.jpg", quality=90)
TARGETS = {"chestnut": (34, 18, 28), "oxblood": (24, 30, 12), "navy": (22, 2, -22), "tan": (52, 16, 34), "forest": (26, -14, 8)}
out = []
for name, (Lt, at, bt) in TARGETS.items():
    lab2 = lab.copy()
    # keep the original highlights and shading: shift lightness by a curve, set hue
    Ls = L[upper]
    k = (Lt - 10) / max(1, np.percentile(Ls, 50) - 0 + 1e-3)
    newL = np.clip(L * (1 + 0.9 * (k - 1) * (1 - L / 100)), 0, 100)
    shade = np.clip(L / 60, 0.25, 1.2)
    lab2[..., 0] = np.where(upper, newL, L)
    lab2[..., 1] = np.where(upper, at * shade, A)
    lab2[..., 2] = np.where(upper, bt * shade, B)
    rgb = np.clip(color.lab2rgb(lab2), 0, 1)
    rgb = a * (1 - soft[..., None]) + rgb * soft[..., None]
    im = Image.fromarray((rgb * 255).astype(np.uint8)).crop(BOX); im.save(f"cw_{sys.argv[2]}_{name}.jpg", quality=90); out.append(im)
w, h = out[0].size
s = Image.new("RGB", (w * len(out), h), "white")
for i, im in enumerate(out): s.paste(im, (i * w, 0))
s.thumbnail((2400, 800)); s.save(f"cw_{sys.argv[2]}_sheet.jpg", quality=85)
