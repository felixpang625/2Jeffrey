"""
core_to_glb.py — full pipeline driven by the C++ core, producing a real .glb.

Proves the core's arrays are sufficient to emit the shipping asset format:
  glb -> per-vertex colours -> C core (simplify+posterise) -> split+faceted -> .glb

  python3 kat/core/core_to_glb.py kat/src/Duck.glb 200 12 kat/out/Duck_core.glb
"""
import os, sys
import numpy as np
import trimesh
from PIL import Image
from trimesh.visual.color import ColorVisuals

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import stylescan as S
from test_core import vertex_colours, run_core


def write_glb(path, pos, faces, facecols):
    """Faceted export: split each face into 3 unique vertices (each vertex then has
    exactly one adjacent face, so its normal IS the face normal -> hard facets),
    carry colour as COLOR_0, no texture."""
    tri = pos[faces]
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm /= (np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9)
    nf = len(faces)
    mesh = trimesh.Trimesh(vertices=tri.reshape(-1, 3),
                           faces=np.arange(nf * 3).reshape(-1, 3), process=False)
    try:
        mesh._cache["vertex_normals"] = np.repeat(nrm, 3, axis=0)
    except Exception:                                          # noqa: BLE001
        pass
    mesh.visual = ColorVisuals(mesh=mesh,
                               vertex_colors=np.repeat((facecols * 255).astype(np.uint8), 3, axis=0))
    mesh.export(path)
    return mesh


def main():
    src, target, k, out = (sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]) \
        if len(sys.argv) > 4 else ("kat/src/Duck.glb", 200, 12, "kat/out/Duck_core.glb")
    exe = os.path.join(HERE, "katamari")
    mesh = S.load_merge(src)
    vc = vertex_colours(mesh)
    pos = np.asarray(mesh.vertices, np.float32)
    fcs = np.asarray(mesh.faces, np.int64)
    cpos, cfcs, ccol = run_core(pos, fcs, vc, target, k, True, exe)

    # normalise scale + centre so viewers frame it well
    mn, mx = cpos.min(0), cpos.max(0)
    cpos = (cpos - (mn + mx) / 2) / max(1e-6, (mx - mn).max())

    m = write_glb(out, cpos, cfcs, ccol)
    print(f"{os.path.basename(src)} -> {out}: {len(fcs)} -> {len(cfcs)} tris, "
          f"{len(np.unique(ccol.round(3), axis=0))} colours, {os.path.getsize(out)} bytes")
    a = S.render(np.asarray(m.triangles), size=460, yaw=34, pitch=-20,
                 colors=ccol, bg=(255, 255, 255))
    Image.fromarray(a).save(out.replace(".glb", "_preview.png"))


if __name__ == "__main__":
    main()
