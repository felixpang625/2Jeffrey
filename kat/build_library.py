"""
build_library.py — turn a folder of .glb models into a browsable library page.

Mirrors the reference site (katamari.andrew-boylan.com): a thumbnail grid where
tapping a tile opens a free-rotate 3D viewer (+ AR on supported devices).

  python build_library.py kat/out kat/library
"""
import os, sys, json, shutil, html
import numpy as np
import trimesh
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__))
from stylescan import render


def face_flat_colors(mesh):
    """Per-face colour for ANY glb: sample the texture at each face's UV centroid,
    else fall back to the material's base colour factor."""
    uv = getattr(mesh.visual, "uv", None)
    mat = getattr(mesh.visual, "material", None)
    tex = None
    if mat is not None:
        t = getattr(mat, "baseColorTexture", None) or getattr(mat, "image", None)
        if t is not None:
            tex = np.asarray(t.convert("RGB"), np.float32) / 255.0
    nf = len(mesh.faces)
    if uv is not None and tex is not None:
        fuv = uv[mesh.faces].mean(axis=1)          # (nf,2)
        H, W = tex.shape[:2]
        u = np.clip((fuv[:, 0] % 1.0) * (W - 1), 0, W - 1).astype(int)
        v = np.clip((fuv[:, 1] % 1.0) * (H - 1), 0, H - 1).astype(int)
        return tex[v, u]
    fac = getattr(mat, "baseColorFactor", None) if mat is not None else None
    if fac is not None:
        c = np.asarray(fac, np.float32)[:3]
        if c.max() > 1.5:                           # 0-255 form
            c = c / 255.0
        return np.tile(c, (nf, 1))
    return np.tile(np.array([0.72, 0.72, 0.75], np.float32), (nf, 1))


def thumbnail(path, out_png, size=512, yaw=32, pitch=-20):
    s = trimesh.load(path, force="scene")
    geoms = [g for g in s.geometry.values() if getattr(g, "faces", None) is not None and len(g.faces)]
    m = trimesh.util.concatenate(geoms)
    F = np.asarray(m.faces)
    if getattr(m.visual, "kind", None) == "vertex" and getattr(m.visual, "vertex_colors", None) is not None:
        vc = np.asarray(m.visual.vertex_colors)[:, :3].astype(np.float32) / 255.0
        N = np.asarray(m.vertex_normals)
        rgba = render(m.triangles, size=size, yaw=yaw, pitch=pitch, bg=(255, 255, 255),
                      light=(0.4, 0.75, 0.55), want_alpha=True,
                      vcol=vc[F], normals=N[F])
    else:
        cols = face_flat_colors(m)
        rgba = render(m.triangles, size=size, yaw=yaw, pitch=pitch, colors=cols,
                      bg=(255, 255, 255), light=(0.4, 0.75, 0.55), want_alpha=True)
    Image.fromarray(rgba, "RGBA").save(out_png)


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>My Scanned Objects</title>
<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.4.0/model-viewer.min.js"></script>
<style>
  :root{--bg:#fff;--ink:#1c1c1e;--muted:#8a8a8e;--card:#f4f4f6}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
       -webkit-font-smoothing:antialiased}
  header{padding:28px 22px 10px}
  h1{margin:0;font-size:24px;letter-spacing:-.02em}
  header p{margin:6px 0 0;color:var(--muted);font-size:14px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
        gap:14px;padding:18px 22px 40px}
  .tile{background:var(--card);border-radius:16px;overflow:hidden;cursor:pointer;
        border:1px solid transparent;transition:transform .12s ease,border-color .12s ease;
        display:flex;flex-direction:column}
  .tile:active{transform:scale(.97)}
  .tile:hover{border-color:#d8d8dc}
  .tile img{width:100%;aspect-ratio:1;object-fit:contain;background:#fff;display:block}
  .tile .meta{padding:9px 11px 11px}
  .tile .nm{font-size:13px;font-weight:600;line-height:1.25;
            overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .tile .sz{font-size:11px;color:var(--muted);margin-top:2px}
  /* viewer overlay */
  #view{position:fixed;inset:0;background:#fff;display:none;flex-direction:column;z-index:10}
  #view.on{display:flex}
  #view model-viewer{flex:1;width:100%;background:#fff;--poster-color:transparent}
  #bar{display:flex;align-items:center;gap:12px;padding:14px 18px;
       padding-top:calc(14px + env(safe-area-inset-top));border-bottom:1px solid #ececf0}
  #bar button{border:0;background:var(--card);border-radius:10px;width:38px;height:38px;
              font-size:18px;cursor:pointer;color:var(--ink)}
  #bar .t{font-weight:600;font-size:16px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .empty{padding:60px 22px;color:var(--muted)}
  code{background:var(--card);padding:1px 5px;border-radius:5px;font-size:13px}
</style>
</head>
<body>
<header>
  <h1>My Scanned Objects</h1>
  <p>__COUNT__ model__S__ &middot; tap one and spin it around</p>
</header>
<div class="grid" id="grid"></div>
<div id="view">
  <div id="bar">
    <button id="close" aria-label="Close">&#8592;</button>
    <div class="t" id="vt"></div>
  </div>
  <model-viewer id="mv" camera-controls touch-action="pan-y" auto-rotate
      rotation-per-second="16deg" shadow-intensity="0.8" exposure="1.05"
      camera-orbit="32deg 72deg auto" environment-image="neutral"
      ar ar-modes="webxr scene-viewer quick-look"></model-viewer>
</div>
<script>
const MODELS = __DATA__;
const grid = document.getElementById('grid');
const view = document.getElementById('view');
const mv   = document.getElementById('mv');
if (MODELS.length === 0) {
  grid.outerHTML = '<div class="empty">No models yet. Export a scan into ' +
                   '<code>models/</code> and re-run the builder.</div>';
}
MODELS.forEach(m => {
  const t = document.createElement('div');
  t.className = 'tile';
  t.innerHTML = `<img loading="lazy" src="${m.thumb}" alt="${m.name}">
                 <div class="meta"><div class="nm">${m.name}</div>
                 <div class="sz">${m.tris} tris &middot; ${(m.bytes/1024).toFixed(1)} KB</div></div>`;
  t.onclick = () => { mv.src = m.src; document.getElementById('vt').textContent = m.name;
                      view.classList.add('on'); mv.setAttribute('auto-rotate',''); };
  grid.appendChild(t);
});
document.getElementById('close').onclick = () => {
  view.classList.remove('on'); mv.removeAttribute('src');
};
</script>
</body>
</html>
"""


def build(src_dir, out_dir):
    models_dir = os.path.join(out_dir, "models")
    thumbs_dir = os.path.join(out_dir, "thumbs")
    os.makedirs(models_dir, exist_ok=True)
    os.makedirs(thumbs_dir, exist_ok=True)
    entries = []
    for f in sorted(os.listdir(src_dir)):
        if not f.lower().endswith(".glb"):
            continue
        src = os.path.join(src_dir, f)
        stem = os.path.splitext(f)[0]
        name = stem.replace("_", " ").replace("-", " ").strip()
        out_glb = os.path.join(models_dir, f)
        shutil.copyfile(src, out_glb)
        thumb = os.path.join(thumbs_dir, stem + ".png")
        try:
            thumbnail(src, thumb)
        except Exception as e:                      # noqa: BLE001
            print(f"  ! thumbnail failed for {f}: {e}")
            continue
        m = trimesh.load(src, force="scene")
        tris = sum(len(g.faces) for g in m.geometry.values() if getattr(g, "faces", None) is not None)
        entries.append({"name": name, "src": f"models/{f}", "thumb": f"thumbs/{stem}.png",
                        "tris": tris, "bytes": os.path.getsize(src)})
        print(f"  + {name:34s} {tris:5d} tris  {os.path.getsize(src)/1024:5.1f} KB")

    data = json.dumps(entries).replace("</", "<\\/")
    page = (PAGE.replace("__DATA__", data)
                .replace("__COUNT__", str(len(entries)))
                .replace("__S__", "" if len(entries) == 1 else "s"))
    with open(os.path.join(out_dir, "index.html"), "w") as fh:
        fh.write(page)
    print(f"\nwrote {out_dir}/index.html  ({len(entries)} models)")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "kat/out"
    out = sys.argv[2] if len(sys.argv) > 2 else "kat/library"
    build(src, out)
