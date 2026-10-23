"""
stylescan.py — prototype of the "Katamari-izer" core.

Takes any textured .glb (the kind a phone scan / image-to-3D API produces)
and converts it into the Katamari object aesthetic:

  ~50-400 triangles  +  one <=128px 8-16 colour palette atlas  +  hard edges

Measured target format (from katamari.andrew-boylan.com real assets):
  tris: 44-168   tex: 64x32 .. 128x128   unique colours: 7-15   file: 7-16 KB
"""
import os, sys, math
import numpy as np
import trimesh
from trimesh.visual.texture import TextureVisuals
from trimesh.visual.material import SimpleMaterial, PBRMaterial
from PIL import Image
import fast_simplification
from sklearn.cluster import KMeans


# ---------------------------------------------------------------- load / merge
def load_merge(path):
    scene = trimesh.load(path, force="scene")
    geoms = [g for g in scene.geometry.values() if getattr(g, "faces", None) is not None and len(g.faces)]
    m = trimesh.util.concatenate(geoms)
    # keep a copy of the textured original for colour sampling
    return m


# ------------------------------------------------------- decimate (fast-simplify)
def decimate(mesh, target, max_passes=10):
    """Quadric decimation in repeat passes.

    PITFALL: a single `simplify()` call plateaus on multi-body / non-manifold meshes
    (real scans are exactly this). e.g. the Lantern glb has 176 disconnected shells and
    a single call sticks at 1372 faces no matter the target. Re-running on the result
    keeps chipping away. On-device use meshopt_simplifySloppy for the same reason:
    it hits the budget instead of refusing on topological grounds.
    """
    v, f = np.asarray(mesh.vertices, np.float32), np.asarray(mesh.faces, np.int32)
    for _ in range(max_passes):
        if len(f) <= target * 1.15:
            break
        nv, nf = fast_simplification.simplify(v, f, target_reduction=1.0 - target / len(f))
        if len(nf) >= len(f) * 0.98:          # no further progress -> stop
            v, f = nv, nf
            break
        v, f = nv, nf
    return trimesh.Trimesh(vertices=v, faces=f, process=False)


# --------------------------------------------- sample texture colour per new face
def face_colors_from_texture(orig, dec):
    """For each face of the decimated mesh, project its centroid onto the original
    textured surface, interpolate UV, and sample the source texture."""
    tex = None
    uv = getattr(orig.visual, "uv", None)
    mat = getattr(orig.visual, "material", None)
    if mat is not None:
        t = getattr(mat, "baseColorTexture", None) or getattr(mat, "image", None)
        if t is not None:
            tex = np.asarray(t.convert("RGB"), np.float32) / 255.0
    if tex is None or uv is None:
        # no texture -> fall back to vertex/face colours
        return np.tile(np.array([[0.6, 0.6, 0.6]]), (len(dec.faces), 1))

    prox = trimesh.proximity.ProximityQuery(orig)
    pts = dec.triangles_center
    closest, dist, tri_id = prox.on_surface(pts)
    otris = orig.triangles[tri_id]                      # (n,3,3)
    bary = trimesh.triangles.points_to_barycentric(otris, closest)  # (n,3)
    ouv = uv[orig.faces[tri_id]]                        # (n,3,2)
    fuv = np.einsum("ni,nij->nj", bary, ouv)            # (n,2)
    H, W = tex.shape[:2]
    # glTF UV origin is top-left, image arrays are top-left -> v maps straight to row
    u = np.clip((fuv[:, 0] % 1.0) * (W - 1), 0, W - 1)
    v = np.clip((fuv[:, 1] % 1.0) * (H - 1), 0, H - 1)
    cols = tex[v.astype(int), u.astype(int)]
    return cols


# ------------------------------------------------------------------ quantise
def quantise(colors, k):
    k = int(min(k, max(2, len(np.unique(colors, axis=0)))))
    km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(colors)
    idx = km.labels_
    pal = np.clip(km.cluster_centers_, 0, 1)
    return idx, pal


# --------------------------------------------------- build palette-atlas mesh
def build_atlas_mesh(dec, idx, pal):
    """Per-face split vertices + a small swatch atlas -> exactly the reference format.

    Gets two things right that naive versions get wrong:
      * each face's 3 UVs span its swatch cell as a NON-degenerate triangle
        (identical UVs => zero UV area => broken mip selection => black patches)
      * the atlas is pre-filled with the palette mean so mip-level averaging
        blends palette colours instead of bleeding in empty black texels
    """
    k = len(pal)
    grid = int(math.ceil(math.sqrt(k)))
    cell = 32                                   # 12 colours -> grid 4 -> 128px atlas
    size = grid * cell
    atlas = np.tile(pal.mean(0), (size, size, 1))   # pad: no black gaps between swatches
    for i, c in enumerate(pal):
        r, cc = divmod(i, grid)
        atlas[r * cell:(r + 1) * cell, cc * cell:(cc + 1) * cell] = c
    img = Image.fromarray((atlas * 255).astype(np.uint8))

    F = dec.faces
    V = dec.vertices
    nf = len(F)
    nv = V[F].reshape(-1, 3)                      # split so each face gets flat colour + flat normal
    nf_faces = np.arange(nf * 3).reshape(nf, 3)
    inset = 0.5 / cell
    uvs = np.zeros((nf * 3, 2))
    for i in range(nf):
        r, cc = divmod(int(idx[i]), grid)
        u0, u1 = (cc + inset) / grid, (cc + 1 - inset) / grid
        v0, v1 = (r + inset) / grid, (r + 1 - inset) / grid
        # three distinct corners of the swatch -> non-degenerate UV triangle
        uvs[i * 3 + 0] = (u0, v0)
        uvs[i * 3 + 1] = (u1, v0)
        uvs[i * 3 + 2] = (u0, v1)
    mesh = trimesh.Trimesh(vertices=nv, faces=nf_faces, process=False)
    mat = PBRMaterial(baseColorTexture=img, metallicFactor=0.0, roughnessFactor=1.0,
                      doubleSided=True)           # open shells: don't cull backfaces
    mesh.visual = TextureVisuals(uv=uvs, material=mat)
    return mesh, img


# ------------------------------------------------------------ tiny renderer
def _rot(yaw, pitch):
    y, p = math.radians(yaw), math.radians(pitch)
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    return Rx @ Ry


def render(tris, size=520, yaw=35, pitch=-22, colors=None, uv=None, image=None,
           flat=True, bg=(22, 22, 28), light=(0.45, 0.7, 0.6), want_alpha=False,
           normals=None, vcol=None):
    """Orthographic z-buffer rasteriser.
    colors: (nf,3) flat per-face colour.
    uv (nf,3,2) + image: textured.
    normals (nf,3,3): per-corner normals -> SMOOTH (Gouraud-interpolated) shading,
    which is how Katamari assets render a welded low-poly mesh as a smooth toy."""
    W = H = size
    img = np.zeros((H, W, 3), np.float32)
    img[:] = np.array(bg, np.float32)
    zbuf = np.full((H, W), 1e9, np.float32)
    alpha = np.zeros((H, W), np.float32)

    R = _rot(yaw, pitch)
    V = tris.reshape(-1, 3) @ R.T
    mn, mx = V.min(0), V.max(0)
    ctr = (mn + mx) / 2
    scale = 2.0 / max(1e-6, (mx - mn).max())
    P = (V - ctr) * scale
    # to screen: x right, y up -> flip y
    sx = (P[:, 0] * 0.5 + 0.5) * (W - 1)
    sy = (1 - (P[:, 1] * 0.5 + 0.5)) * (H - 1)
    sz = P[:, 2]
    sx = sx.reshape(-1, 3); sy = sy.reshape(-1, 3); sz = sz.reshape(-1, 3)

    nf = len(tris)
    # face normals (after rotation) for simple lambert
    a = tris[:, 0] @ R.T; b = tris[:, 1] @ R.T; c = tris[:, 2] @ R.T
    nrm = np.cross(b - a, c - a)
    nrm /= (np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9)
    L = np.array(light, np.float32); L /= np.linalg.norm(L)
    shade = np.clip(0.48 + 0.62 * np.abs(nrm @ L), 0, 1.3)   # double-sided flat lambert + ambient
    NR = None
    if normals is not None:
        NR = normals @ R.T                                    # rotate corner normals
        NR = NR / (np.linalg.norm(NR, axis=2, keepdims=True) + 1e-9)

    if image is None:
        tex = None
    elif hasattr(image, "convert"):
        tex = np.asarray(image.convert("RGB"), np.float32) / 255.0
    else:                                                    # already an array
        arr = np.asarray(image)
        tex = arr.astype(np.float32) / 255.0 if arr.dtype == np.uint8 else arr.astype(np.float32)
    Ht, Wt = (tex.shape[:2] if tex is not None else (1, 1))

    for i in range(nf):
        x0, x1, x2 = sx[i]; y0, y1, y2 = sy[i]; z0, z1, z2 = sz[i]
        minx = max(int(np.floor(min(x0, x1, x2))), 0); maxx = min(int(np.ceil(max(x0, x1, x2))), W - 1)
        miny = max(int(np.floor(min(y0, y1, y2))), 0); maxy = min(int(np.ceil(max(y0, y1, y2))), H - 1)
        if minx > maxx or miny > maxy:
            continue
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        xs = np.arange(minx, maxx + 1) + 0.5
        ys = np.arange(miny, maxy + 1) + 0.5
        gx, gy = np.meshgrid(xs, ys)
        w0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / area
        w1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / area
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        z = w0 * z0 + w1 * z1 + w2 * z2
        sub = zbuf[miny:maxy + 1, minx:maxx + 1]
        amask = alpha[miny:maxy + 1, minx:maxx + 1]
        mask = inside & (z < sub)
        if not mask.any():
            continue
        if vcol is not None:                                 # smooth interpolated vertex colour
            c3 = vcol[i]
            col = (w0[..., None] * c3[0] + w1[..., None] * c3[1] + w2[..., None] * c3[2])[mask]
        elif tex is not None and uv is not None:
            u = w0 * uv[i][0][0] + w1 * uv[i][1][0] + w2 * uv[i][2][0]
            vv = w0 * uv[i][0][1] + w1 * uv[i][1][1] + w2 * uv[i][2][1]
            uu = np.clip((u % 1.0) * (Wt - 1), 0, Wt - 1)[mask].astype(int)
            vp = np.clip((vv % 1.0) * (Ht - 1), 0, Ht - 1)[mask].astype(int)
            col = tex[vp, uu]
        else:
            col = np.tile(colors[i], (mask.sum(), 1))
        if NR is not None:                                   # smooth Gouraud shading
            n3 = NR[i]
            n = (w0[..., None] * n3[0] + w1[..., None] * n3[1] + w2[..., None] * n3[2])[mask]
            n = n / (np.linalg.norm(n, axis=1, keepdims=True) + 1e-9)
            sh = np.clip(0.48 + 0.62 * np.abs(n @ L), 0, 1.3)[:, None]
            col = np.clip(col * sh, 0, 1)
        else:
            col = np.clip(col * shade[i], 0, 1)
        sub[mask] = z[mask]
        amask[mask] = 1.0
        img[miny:maxy + 1, minx:maxx + 1][mask] = col
    out = (img * 255).astype(np.uint8)
    if want_alpha:
        return np.concatenate([out, (alpha * 255).astype(np.uint8)[..., None]], axis=2)
    return out


# ------------------------------------------------------------------- pipeline
def katamari(path, out_glb, target_tris=220, k=12, tag=""):
    orig = load_merge(path)
    dec = decimate(orig, target_tris)
    cols = face_colors_from_texture(orig, dec)
    idx, pal = quantise(cols, k)
    out, atlas = build_atlas_mesh(dec, idx, pal)
    out.export(out_glb)
    return orig, dec, cols, idx, pal, atlas


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "kat/src/DamagedHelmet.glb"
    name = os.path.splitext(os.path.basename(path))[0]
    target = int(sys.argv[2]) if len(sys.argv) > 2 else 220
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    os.makedirs("kat/out", exist_ok=True)
    orig, dec, cols, idx, pal, atlas = katamari(path, f"kat/out/{name}_kat.glb", target, k, name)

    # ---- BEFORE render (textured, high detail)
    ouv = orig.visual.uv
    tex = orig.visual.material.baseColorTexture
    before = render(orig.triangles, size=520, yaw=35, pitch=-22,
                    uv=ouv[orig.faces], image=tex)
    # ---- AFTER render (palette atlas, low poly)
    after = render(dec.triangles, size=520, yaw=35, pitch=-22,
                   colors=pal[idx])
    Image.fromarray(before).save(f"kat/out/{name}_before.png")
    Image.fromarray(after).save(f"kat/out/{name}_after.png")
    # side-by-side
    sheet = Image.new("RGB", (520 * 2 + 30, 520), (12, 12, 15))
    sheet.paste(Image.fromarray(before), (0, 0))
    sheet.paste(Image.fromarray(after), (550, 0))
    sheet.save(f"kat/out/{name}_compare.png")

    print(f"{name}: orig_tris={len(orig.faces)} -> {len(dec.faces)}  "
          f"colours={len(np.unique(cols,axis=0))} -> palette={len(pal)}  "
          f"glb={os.path.getsize(f'kat/out/{name}_kat.glb')} bytes  atlas={atlas.size}")
