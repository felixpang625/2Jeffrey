# Katamari-izer

Turn a real-world 3D scan into a **Katamari Damacy–style object** — a tiny, low-poly,
flat-shaded toy model — and browse the results in a spin-it-around gallery.

This repo is the **engine and the research** behind that pipeline. The iPhone capture app
(Apple Object Capture / LiDAR) is specced in [`docs/PLAN.md`](docs/PLAN.md); everything
here runs today on a laptop and is the reference implementation the Swift port must match.

---

## What a Katamari object actually is (measured, not guessed)

We measured real Katamari assets two ways — the [Katamari Object
Library](https://katamari.andrew-boylan.com/) game-asset rips, and a Google-Blocks model
([Katamari Pre-Roll](https://sketchfab.com/3d-models/katamari-pre-roll-bf67be13b0604f6ea2c8cea4168aff57)):

| Signal | Game rips | Google Blocks | Naive flat-shaded |
|---|---|---|---|
| triangles | 44–168 / object | 3,450 / whole scene | — |
| vertex ÷ triangle | 1.64–2.02 (**welded**) | 0.62 (**welded**) | ~3.0 |
| textures | 1 atlas, **7–15 flat colours** | **0** (10 solid materials) | — |
| shading | **smooth** | **faceted** | flat split |
| file size | **7–16 KB** | — | — |

Two conclusions drove the design:

1. **You do not need a high-fidelity scanner.** The target is ~100–300 triangles and a
   handful of flat colours, so photogrammetry detail is thrown away anyway. Rough shape +
   base colours is enough.
2. **Welded geometry is the tell.** Both references share vertices; the shading style
   (smooth vs faceted) is a *normals* choice, not a geometry one.

## The pipeline

```
decimate (quadric, repeat-pass)  →  posterise colours to a ~12-colour palette
        →  split faces + flat normals   (faceted / Blocks look)   ← current default
        →  export .glb with vertex colours (no texture)
```

Three interchangeable stylisers, all sharing the decimate + posterise front end:

| Function | Look | Use when |
|---|---|---|
| `katamari_faceted` | **faceted / Google-Blocks** *(default)* | bold chunky objects |
| `katamari_vcol` | smooth / game-asset rip | organic, soft objects |
| `katamari_inherit` | inherited UVs + posterised atlas | crisp baked detail (text on signs) |

### Triangle budgets matter, and they're counter-intuitive

Faceting costs recognisability, so the faceted style needs a **higher** budget than the
smooth one. Measured on the same duck: **109 tris = abstract mush, ~200 = clearly a duck**,
350–600 = comfortable. Budget roughly 130–150 for simple/round objects, 230–260 for complex.

---

## Files

| Path | What |
|---|---|
| `kat/core/` | **the shipping core**, in C/C++ — see below. Verified against the Python reference. |
| `kat/stylescan.py` | Python reference: mesh load/merge, decimation, texture→colour sampling, posterisation, and a numpy z-buffer rasteriser used for previews and thumbnails |
| `kat/stylescan_uv.py` | Python reference stylisers (`katamari_faceted` / `katamari_vcol` / `katamari_inherit`); CLI |
| `kat/build_library.py` | renders a thumbnail per model and generates the browsable gallery page |
| `kat/viewer.html` | single-file `<model-viewer>` page — open on any phone to spin a `.glb` |
| `docs/PLAN.md` | the full iOS build plan, with the measured findings and the pitfalls hit |

---

## The shipping core is C/C++, not Python

Python was the right language to *discover* the algorithm in, but it was never going to
ship. The styliser now lives in `kat/core/` as portable C/C++:

| File | What |
|---|---|
| `kat/core/katamari_core.h` | the interface (`katamari_stylize`) — callable from Swift via a bridging header |
| `kat/core/katamari_core.cpp` | decimate (meshoptimizer) + per-face colour + k-means posterise |
| `kat/core/vendor/meshoptimizer.{h,cpp}` | vendored meshoptimizer 1.3 (MIT) |
| `kat/core/katamari_cli.cpp` | text-format harness so Python can drive the core |
| `kat/core/test_core.py` | **verification** against the Python reference |
| `kat/core/core_to_glb.py` | end-to-end: glb → core → `.glb` |

### Why C, and why it's better than the Python path

`meshopt_simplify` collapses edges into **existing** vertices — it never invents new ones.
So surviving vertices keep their **original UVs**, and colours are sampled directly instead
of being projected back onto the source. That makes the "black shard" artifact class
*structurally impossible*, rather than patched around.

Proof over four models, both paths fed the same mesh and per-vertex colours:

| model | source tris | Python oracle | **C core** | palette |
|---|---|---|---|---|
| Avocado | 682 | 198 | **199** | 12 |
| Duck | 4,212 | 199 | **200** | 12 |
| Water Bottle | 4,510 | 199 | **200** | 12 |
| BoomBox | 6,036 | 198 | **200** | 12 |

The C core hits the target exactly on every model (meshoptimizer is more reliable at this
than the Python decimator), and is *cleaner* — the Duck, which shards under the Python
projection path, comes out coherent.

```bash
make -C kat/core          # builds in seconds, no Swift or macOS needed
python3 kat/core/test_core.py                    # verify vs the Python reference
python3 kat/core/core_to_glb.py kat/src/Duck.glb 200 12 kat/out/Duck_core.glb
```

Being C means it is testable on any machine (this was developed and verified on Linux),
reusable server-side, and wrappable from Swift with a two-line bridging header.

---

## Usage

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# stylise one model  (input.glb  target_tris  palette_colours)
python3 kat/stylescan_uv.py input.glb 200 12
#   -> kat/out/input_kat.glb, plus _before.png / _after.png previews

# build a browsable gallery from a folder of .glb
python3 kat/build_library.py kat/out kat/library
#   -> kat/library/index.html  (open it, or serve the folder)
```

To view on a phone: serve the folder (`python3 -m http.server 8000` in `kat/library/`)
and open `http://<your-computer-ip>:8000` — or drop the folder on any static host.

---

## Pitfalls recorded (each cost a debugging round)

- **Per-texel projection bake → black shards.** Projecting every atlas texel onto the source
  makes coarse facets jump across the source's UV seams and sample its black margin. Prove
  UVs are fine first by rendering a *checkerboard* as the texture.
- **Inherited UVs still shard** on faces that span a source seam.
- **Decimation plateaus on multi-body meshes.** Real scans / glTF assets are triangle soup
  (one sample has 176 disconnected shells); a single quadric pass refuses to go below a
  floor no matter the target. Use repeat passes, and `meshopt_simplifySloppy` on-device.
- **Degenerate UVs break mip selection.** Giving a face one flat UV point (zero UV area)
  makes the GPU pick the blurriest mip → black patches. Maps to faces must have real UV area.

See [`docs/PLAN.md`](docs/PLAN.md) for the long form.

---

## Roadmap

- [x] Python engine — faceted default, gallery, phone viewer
- [x] Portable **C/C++ styliser core** (`kat/core/`) — verified against the Python reference on 4 models
- [ ] Swift wrapper + iOS capture app: `ObjectCaptureSession` → `PhotogrammetrySession` (on-device) → core → in-app gallery. See `docs/PLAN.md` for the 11-task breakdown.

## Notes

Downloaded source models and generated outputs are **not** committed (see `.gitignore`) —
they're third-party assets. Drop your own `.glb` files into a folder and run the tools.
