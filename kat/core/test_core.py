"""
test_core.py — verify the C++/meshoptimizer core against the Python reference.

Feeds the same mesh + per-vertex source colours to both implementations and compares
triangle count, palette size, and the rendered result.

  make -C kat/core && python3 kat/core/test_core.py
"""
import os, subprocess, sys
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import stylescan as S
import stylescan_uv as U


def vertex_colours(mesh):
    uv = getattr(mesh.visual, "uv", None)
    mat = getattr(mesh.visual, "material", None)
    tex = None
    if mat is not None:
        t = getattr(mat, "baseColorTexture", None) or getattr(mat, "image", None)
        if t is not None:
            tex = np.asarray(t.convert("RGB"), np.float32) / 255.0
    if uv is None or tex is None:
        return None
    uv = np.asarray(uv, np.float64) % 1.0
    H, W = tex.shape[:2]
    u = np.clip(uv[:, 0] * (W - 1), 0, W - 1).astype(int)
    v = np.clip(uv[:, 1] * (H - 1), 0, H - 1).astype(int)
    return tex[v, u]


def run_core(positions, faces, vcols, target, k, faceted, exe):
    lines = [f"{len(positions)} {len(faces)} {1 if vcols is not None else 0} {target} {k} {1 if faceted else 0}"]
    lines += [f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f}" for p in positions]
    lines += [f"{f[0]} {f[1]} {f[2]}" for f in faces]
    if vcols is not None:
        lines += [f"{c[0]:.6f} {c[1]:.6f} {c[2]:.6f}" for c in vcols]
    inp = "\n".join(lines) + "\n"
    r = subprocess.run([exe], input=inp, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"core failed: {r.stderr[:400]}")
    tok = r.stdout.split()
    it = iter(tok)
    nv, nf = int(next(it)), int(next(it))
    pos = np.array([[float(next(it)) for _ in range(3)] for _ in range(nv)], np.float32)
    fcs = np.array([[int(next(it)) for _ in range(3)] for _ in range(nf)], np.int64)
    nfc = 0
    rest = []
    while True:
        try:
            rest.append(float(next(it)))
        except StopIteration:
            break
    col = np.array(rest, np.float32).reshape(-1, 3)
    return pos, fcs, col


def main():
    exe = os.path.join(HERE, "katamari")
    if not os.path.exists(exe):
        sys.exit("build first:  make -C kat/core")
    models = sys.argv[1:] or ["Avocado", "Duck", "WaterBottle", "BoomBox"]
    os.makedirs("kat/out", exist_ok=True)
    print(f"{'model':14s} {'src':>6s} {'py-oracle':>10s} {'C core':>7s} {'tris ok':>8s} {'palette':>8s}")
    for name in models:
        path = f"kat/src/{name}.glb"
        mesh = S.load_merge(path)
        vc = vertex_colours(mesh)
        if vc is None:
            print(f"{name:14s} (no UV/texture — skipped)")
            continue
        target = 200
        pos = np.asarray(mesh.vertices, np.float32)
        fcs = np.asarray(mesh.faces, np.int64)

        # reference
        py_cols = S.face_colors_from_texture(mesh, S.decimate(mesh, target))
        from sklearn.cluster import KMeans
        km = KMeans(n_clusters=12, n_init=4, random_state=0).fit(py_cols)
        py = S.decimate(mesh, target)

        # C core
        cpos, cfcs, ccol = run_core(pos, fcs, vc, target, 12, True, exe)

        py_uniq = len(np.unique(km.cluster_centers_.round(3), axis=0))
        c_uniq = len(np.unique(ccol.round(3), axis=0))
        ok = "yes" if abs(len(cfcs) - target) <= target * 0.35 else "NO"
        print(f"{name:14s} {len(mesh.faces):6d} {len(py.faces):10d} {len(cfcs):7d} {ok:>8s} {c_uniq:8d}")

        a = S.render(np.asarray(py.triangles), size=360, yaw=34, pitch=-20, colors=km.cluster_centers_[km.labels_])
        b = S.render(np.asarray(cpos[cfcs]), size=360, yaw=34, pitch=-20, colors=ccol)
        sheet = Image.new("RGB", (360 * 2 + 20, 360), (255, 255, 255))
        sheet.paste(Image.fromarray(a), (0, 0))
        sheet.paste(Image.fromarray(b), (380, 0))
        sheet.save(f"kat/out/core_vs_python_{name}.png")


if __name__ == "__main__":
    main()
