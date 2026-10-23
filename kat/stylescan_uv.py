"""
stylescan_uv.py — the *correct* Katamari-izer.

Evidence from the reference assets (catamari.andrew-boylan.com):
  * v/tri ratio 1.8-2.0  -> vertices WELDED (flat-split would be 3.0)
  * corner-normal variance 0.35-0.93 -> SMOOTH interpolated shading
  * 128x128 atlas with ~5% black margin -> a real UV UNWRAP, not a swatch grid

So the Katamari rules are: weld -> decimate -> unwrap -> bake -> posterise.
Smooth normals hide faceting, which means far fewer triangles are needed.

  python stylescan_uv.py <input.glb> [target_tris] [palette_k] [atlas_px]
"""
import os, sys, math
import numpy as np
import trimesh
import xatlas
import fast_simplification
from trimesh.visual.texture import TextureVisuals
from trimesh.visual.material import PBRMaterial
from PIL import Image
from scipy import ndimage
from sklearn.cluster import KMeans
sys.path.insert(0, os.path.dirname(__file__))
from stylescan import load_merge, decimate, render, face_colors_from_texture


# ------------------------------------------------------------------ source tex
def source_texture_and_uv(mesh):
    uv = getattr(mesh.visual, "uv", None)
    mat = getattr(mesh.visual, "material", None)
    tex = None
    if mat is not None:
        t = getattr(mat, "baseColorTexture", None) or getattr(mat, "image", None)
        if t is not None:
            tex = np.asarray(t.convert("RGB"), np.float32) / 255.0
    return tex, (np.asarray(uv, np.float64) if uv is not None else None)


# ------------------------------------------------------------------ unwrap
def unwrap(mesh):
    """xatlas parametrisation -> (verts, faces, uvs, normals) with SMOOTH normals
    taken from the welded mesh so shading stays smooth across UV seams."""
    v = np.asarray(mesh.vertices, np.float32)
    f = np.asarray(mesh.faces, np.uint32)
    vmapping, indices, uvs = xatlas.parametrize(v, f)
    vmapping = np.asarray(vmapping, np.int64)
    nv = mesh.vertex_normals[vmapping]              # smooth, carried through the seam split
    return v[vmapping], np.asarray(indices, np.int64), np.asarray(uvs, np.float64), nv


# ------------------------------------------------------------------ bake
def bake_atlas(orig, V, F, UV, size):
    """Rasterise the new mesh in UV space; for every covered texel find the 3D point,
    project onto the ORIGINAL textured mesh, and sample its texture."""
    tex, ouv = source_texture_and_uv(orig)
    atlas = np.zeros((size, size, 3), np.float32)
    cov = np.zeros((size, size), bool)
    if tex is None or ouv is None:
        return np.tile(np.array([0.7, 0.7, 0.72], np.float32), (size, size, 1)), np.ones((size, size), bool)

    Ht, Wt = tex.shape[:2]
    P_list, ix_list = [], []
    for i in range(len(F)):
        t = UV[F[i]] * size                                    # (3,2) texel space
        minx = max(0, int(np.floor(t[:, 0].min()))); maxx = min(size - 1, int(np.ceil(t[:, 0].max())))
        miny = max(0, int(np.floor(t[:, 1].min()))); maxy = min(size - 1, int(np.ceil(t[:, 1].max())))
        if minx > maxx or miny > maxy:
            continue
        gx, gy = np.meshgrid(np.arange(minx, maxx + 1) + 0.5, np.arange(miny, maxy + 1) + 0.5)
        ax, ay = t[0]; bx, by = t[1]; cx, cy = t[2]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-9:
            continue
        w0 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
        w1 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-4) & (w1 >= -1e-4) & (w2 >= -1e-4)
        if not inside.any():
            continue
        tri = V[F[i]]
        P = (w0[..., None] * tri[0] + w1[..., None] * tri[1] + w2[..., None] * tri[2])[inside]
        P_list.append(P)
        ix_list.append((gy[inside].astype(int) * size + gx[inside].astype(int)))
    if not P_list:
        return atlas, cov

    P = np.concatenate(P_list); flat_ix = np.concatenate(ix_list)
    prox = trimesh.proximity.ProximityQuery(orig)
    closest, dist, tri_id = prox.on_surface(P)
    bary = trimesh.triangles.points_to_barycentric(orig.triangles[tri_id], closest)
    suv = np.einsum("ni,nij->nj", bary, ouv[orig.faces[tri_id]])
    u = np.clip((suv[:, 0] % 1.0) * (Wt - 1), 0, Wt - 1).astype(int)
    v = np.clip((suv[:, 1] % 1.0) * (Ht - 1), 0, Ht - 1).astype(int)
    cols = tex[v, u]

    flat = atlas.reshape(-1, 3)
    covf = cov.reshape(-1)
    flat[flat_ix] = cols
    covf[flat_ix] = True

    # fill uncovered texels with the nearest covered colour (margin, so mips don't bleed black)
    if cov.any() and not cov.all():
        idx = ndimage.distance_transform_edt(~cov, return_distances=False, return_indices=True)
        atlas = atlas[idx[0], idx[1]]
    return atlas, cov


# ------------------------------------------------------------------ posterise
def posterise(atlas, cov, k):
    cols = atlas[cov]
    uniq = np.unique((cols * 255).astype(np.uint8), axis=0)
    if len(uniq) <= k:
        return atlas, len(uniq)
    km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(cols)
    pal = np.clip(km.cluster_centers_, 0, 1)
    out = atlas.copy()
    out[cov] = pal[km.labels_]
    return out, k


# ------------------------------------------------------------------ inherit-UV
def katamari_inherit(path, out_glb, target=110, k=12, px=128):
    """The robust pipeline.

    The per-texel *projection* bake is unreliable: projecting every atlas texel onto the
    source mesh makes coarse facets jump across the source's UV seams and sample its
    black margin (that produced the shard artifacts). Instead:
      1. project each DECIMATED VERTEX once onto the source -> inherit its UV (few,
         reliable samples instead of one per texel)
      2. posterise the source texture down to a tiny k-colour atlas
      3. reuse the source UV parameterisation directly -- no bake at all
      4. smooth normals (welded) so a ~110-tri mesh still looks like a soft toy
    """
    orig = load_merge(path)
    tex, ouv = source_texture_and_uv(orig)
    dec = decimate(orig, target)

    prox = trimesh.proximity.ProximityQuery(orig)
    closest, dist, tri_id = prox.on_surface(dec.vertices)
    bary = trimesh.triangles.points_to_barycentric(orig.triangles[tri_id], closest)
    suv = np.einsum("ni,nij->nj", bary, ouv[orig.faces[tri_id]]) % 1.0

    # posterised mini-atlas derived from the source texture
    small = Image.fromarray((tex * 255).astype(np.uint8)).resize((px, px), Image.BILINEAR)
    flat = np.asarray(small, np.float32).reshape(-1, 3) / 255.0
    uniq = len(np.unique((flat * 255).astype(np.uint8), axis=0))
    if uniq > k:
        km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(flat)
        flat = np.clip(km.cluster_centers_, 0, 1)[km.labels_]
    atlas = Image.fromarray((flat.reshape(px, px, 3) * 255).astype(np.uint8))

    N = dec.vertex_normals
    mesh = trimesh.Trimesh(vertices=np.asarray(dec.vertices), faces=np.asarray(dec.faces), process=False)
    try:
        mesh._cache["vertex_normals"] = np.asarray(N, np.float64)
    except Exception:                                          # noqa: BLE001
        pass
    mesh.visual = TextureVisuals(uv=suv, material=PBRMaterial(
        baseColorTexture=atlas, metallicFactor=0.0, roughnessFactor=1.0, doubleSided=True))
    mesh.export(out_glb)
    return orig, dec, mesh, np.asarray(N), atlas


# ------------------------------------------------------------------ vertex-colour
def katamari_vcol(path, out_glb, target=110, k=12):
    """Cleanest of the three: per-VERTEX colours + smooth normals, no texture at all.

    Rationale: source models have UV seams; a decimated face that spans a seam gets
    interpolated UVs that sample the wrong texel -> black shards. Vertex colours have
    no seams and, with smooth shading, interpolate across faces, so the result looks
    *smoother* than a posterised atlas at the same triangle count. The reference
    textures carry only 7-15 flat colours anyway, which is what this reproduces.
    Trade-off: loses crisp baked detail (text on signs); use katamari_inherit for those.
    """
    from trimesh.visual.color import ColorVisuals
    orig = load_merge(path)
    tex, ouv = source_texture_and_uv(orig)
    dec = decimate(orig, target)

    prox = trimesh.proximity.ProximityQuery(orig)
    closest, dist, tri_id = prox.on_surface(dec.vertices)
    bary = trimesh.triangles.points_to_barycentric(orig.triangles[tri_id], closest)
    suv = np.einsum("ni,nij->nj", bary, ouv[orig.faces[tri_id]])
    Ht, Wt = tex.shape[:2]
    u = np.clip((suv[:, 0] % 1.0) * (Wt - 1), 0, Wt - 1).astype(int)
    v = np.clip((suv[:, 1] % 1.0) * (Ht - 1), 0, Ht - 1).astype(int)
    cols = tex[v, u]

    if len(np.unique((cols * 255).astype(np.uint8), axis=0)) > k:
        km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(cols)
        cols = np.clip(km.cluster_centers_, 0, 1)[km.labels_]

    N = np.asarray(dec.vertex_normals)
    mesh = trimesh.Trimesh(vertices=np.asarray(dec.vertices), faces=np.asarray(dec.faces), process=False)
    try:
        mesh._cache["vertex_normals"] = np.asarray(N, np.float64)
    except Exception:                                          # noqa: BLE001
        pass
    mesh.visual = ColorVisuals(mesh=mesh, vertex_colors=(cols * 255).astype(np.uint8))
    mesh.export(out_glb)
    return orig, dec, mesh, N, cols


# ------------------------------------------------------------------ faceted (DEFAULT)
def katamari_faceted(path, out_glb, target=110, k=12):
    """Google-Blocks-style faceted Katamari look -- the current default.

    Flat shading + one solid colour per face, no texture. Splitting every face into
    its own 3 vertices makes each vertex belong to exactly one triangle, so its normal
    IS the face normal -> guaranteed hard facets (this is the mechanism that produces
    the Blocks look). Colours are posterised so the palette stays Katamari-tight.

    Matches the reference 'Katamari Pre-Roll': welded geometry, textureCount 0, bold
    solid colours, clearly visible triangular facets.
    """
    from trimesh.visual.color import ColorVisuals
    orig = load_merge(path)
    dec = decimate(orig, target)
    cols = face_colors_from_texture(orig, dec)          # sample source per face

    if len(np.unique((cols * 255).astype(np.uint8), axis=0)) > k:
        km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(cols)
        cols = np.clip(km.cluster_centers_, 0, 1)[km.labels_]

    F = np.asarray(dec.faces)
    V = np.asarray(dec.vertices)
    tri = V[F]                                          # (nf,3,3)
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm = nrm / (np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9)

    verts = tri.reshape(-1, 3)
    faces = np.arange(len(F) * 3).reshape(-1, 3)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    try:
        mesh._cache["vertex_normals"] = np.repeat(nrm, 3, axis=0)   # flat, hard facets
    except Exception:                                              # noqa: BLE001
        pass
    mesh.visual = ColorVisuals(mesh=mesh, vertex_colors=np.repeat((cols * 255).astype(np.uint8), 3, axis=0))
    mesh.export(out_glb)
    return orig, dec, mesh, cols


# ------------------------------------------------------------------ assemble
def build(V, F, UV, N, atlas_img):
    mesh = trimesh.Trimesh(vertices=V, faces=F, process=False)
    try:
        mesh._cache["vertex_normals"] = np.asarray(N, np.float64)   # keep smooth shading
    except Exception:                                               # noqa: BLE001
        pass
    mat = PBRMaterial(baseColorTexture=atlas_img, metallicFactor=0.0, roughnessFactor=1.0,
                      doubleSided=True)
    mesh.visual = TextureVisuals(uv=UV, material=mat)
    return mesh


def katamari_uv(path, out_glb, target_tris=110, k=12, atlas_px=128):
    orig = load_merge(path)
    dec = decimate(orig, target_tris)
    V, F, UV, N = unwrap(dec)
    atlas, cov = bake_atlas(orig, V, F, UV, atlas_px)
    atlas, kk = posterise(atlas, cov, k)
    img = Image.fromarray((np.clip(atlas, 0, 1) * 255).astype(np.uint8))
    mesh = build(V, F, UV, N, img)
    mesh.export(out_glb)
    return orig, dec, mesh, N, img


if __name__ == "__main__":
    path = sys.argv[1]
    target = int(sys.argv[2]) if len(sys.argv) > 2 else 110
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    px = int(sys.argv[4]) if len(sys.argv) > 4 else 128
    name = os.path.splitext(os.path.basename(path))[0]
    os.makedirs("kat/out", exist_ok=True)
    orig, dec, mesh, cols = katamari_faceted(path, f"kat/out/{name}_kat.glb", target, k)

    tex, ouv = source_texture_and_uv(orig)
    before = render(orig.triangles, size=520, yaw=34, pitch=-20, uv=ouv[orig.faces], image=tex)
    after = render(np.asarray(mesh.triangles), size=520, yaw=34, pitch=-20, colors=cols)
    Image.fromarray(before).save(f"kat/out/{name}_before.png")
    Image.fromarray(after).save(f"kat/out/{name}_after.png")
    print(f"{name}: {len(orig.faces)} -> {len(mesh.faces)} tris  "
          f"verts={len(mesh.vertices)} (v/tri={len(mesh.vertices)/len(mesh.faces):.2f})  "
          f"faceted  glb={os.path.getsize(f'kat/out/{name}_kat.glb')} bytes")
