# Katamari Scanner — iOS Implementation Plan

> **For Hermes:** implement task-by-task. Tasks marked 🖥️ can be built/validated on Linux; tasks marked 📱 require Xcode + a LiDAR iPhone.

**Goal:** An iPhone app where you scan a real object (LiDAR-guided Object Capture), and it comes out as a
Katamari-Damacy-style 3D model — a tiny low-poly mesh with a flat ~12-colour palette atlas — viewable and exportable.

**Architecture:** 100% on-device, no backend.
`ObjectCaptureSession` (guided capture) → `PhotogrammetrySession` (on-device reconstruction → `.usdz`) →
mesh extract (ModelIO) → **stylize** (meshoptimizer simplify → per-face colour sample → k-means palette → swatch atlas)
→ `.usdz` (RealityKit) and/or `.glb` (custom minimal writer).

**Tech stack:** SwiftUI, RealityKit (`ObjectCaptureSession`, `PhotogrammetrySession`), ModelIO, Metal (texture sampling),
meshoptimizer (C, MIT), SwiftPM.

---

## The target format (measured, not guessed)

From six real assets on katamari.andrew-boylan.com:

| Property | Value |
|---|---|
| Triangles | 44–168 (soccer ball = 44) |
| Texture | single atlas, 64×32 – 128×128 px |
| Distinct colours | 7–15, flat/posterised |
| File size | 7–16 KB |

The reference textures are **flat painted colour blocks with hard pixel edges** — no photographic detail.
This is why fidelity is unimportant and the whole pipeline fits on a phone.

---

## The Katamari rules (measured — this is the part that's easy to get wrong)

Triangle count alone does NOT make a Katamari object. Inspecting the reference glbs:

| Signal | Reference | Naive flat-shaded | Meaning |
|---|---|---|---|
| vertex / triangle ratio | **1.64–2.02** | ~3.0 | vertices are **welded/shared** |
| corner-normal variance per face | **0.35–0.93** | ~0 | normals are **interpolated (smooth)** |
| texture layout | 128px atlas, ~5% black margin | — | a **UV unwrap with margin**, not a swatch grid |

So the rules are: **weld → decimate → SMOOTH normals → posterised colour.** Because smooth normals hide
faceting, a *lower* triangle count is needed than flat shading would allow — exactly the "smoother AND
lower" combination. A 44-triangle ball reads as a smooth ball.

Validated result: duck 4212 → **109 tris** (vs 199 for the flat version) and visibly *smoother*.

### Pipeline variants — chosen default: FACETED
User picked the **faceted / Google-Blocks look** (as in Sketchfab "Katamari Pre-Roll", Mr. 68000:
3450 tris whole scene, tx/0, 10 solid materials, v/tri 0.62, visibly faceted).

1. **`katamari_faceted` — THE DEFAULT.** Flat-shaded, one posterised solid colour per face, no texture.
   Mechanism: split every face into its own 3 vertices so each vertex normal IS the face normal →
   hard facets. v/tri = 3.0. Cheaper to build than the texture path and needs no atlas.
2. **`katamari_vcol`** — per-vertex colours + smooth normals (game-rip style). Keep for objects where a
   softer look is wanted.
3. **`katamari_inherit`** — inherited UVs + posterised atlas. Use when crisp baked detail (text on signs)
   must survive.
4. **`katamari` (original flat swatch atlas)** — reference only.

### Triangle budgets (measured on the Duck)
Faceting costs you recognisability, so the faceted style needs a *higher* budget than the smooth one:
109 tris = abstract, **~200 = clearly a duck**, 350–600 = comfortable. Use per-object budgets:
simple/round objects ≈ 130–150 (avocado, balls); complex objects ≈ 230–260 (duck, boombox, bottle).

### Bake pitfalls (each cost a debugging round)
* Basing an atlas on **per-texel projection** onto the source mesh makes coarse facets jump across the
  source's UV seams and sample its black margin → **black shard artifacts**. Render a UV *checkerboard*
  as the texture to prove UVs are fine before blaming the bake.
* **Inherited UVs still shard** at faces that span a source seam.
* Vertex colours avoid both, at the cost of fine detail.

---

## Repo / project layout

```
KatamariScanner/
  KatamariScanner.xcodeproj
  KatamariScanner/
    App.swift                       # @main, SwiftUI App
    Capture/
      CaptureView.swift             # ObjectCaptureView + dial UI (SwiftUI)
      CaptureModel.swift            # @Observable wrapper around ObjectCaptureSession
    Reconstruct/
      ReconstructionService.swift   # PhotogrammetrySession -> .usdz
    Stylize/
      MeshIO.swift                  # MDLAsset <-> plain vertex/index/uv/texture buffers
      Simplifier.swift              # meshoptimizer bridging (meshopt_simplify)
      PaletteQuantizer.swift        # k-means over face colours -> palette
      SwatchAtlas.swift             # build ~128px swatch atlas + per-face UVs
      Stylizer.swift                # orchestrates the 4 steps above
    Export/
      GLBWriter.swift               # minimal glTF 2.0 binary writer (we control the content)
      ExportService.swift           # .usdz via RealityKit, .glb via GLBWriter
    Viewer/
      ModelViewerView.swift         # RealityKit ARQuickLook-style preview
    MeshOptimizer/
      include/meshoptimizer.h       # vendored single header
      simplify.cpp                  # MESHOPTIMIZER_IMPLEMENTATION translation unit
      MeshOptimizer-Bridging-Header.h
  Package.swift                     # (if modularising) meshoptimizer as a C target
```

---

## Scope (confirmed)

**In:** scan an object → stylize to Katamari format → **store locally → browse a gallery → orbit/spin each model**
(exactly the reference site's experience: thumbnail grid → tap → free-rotate viewer, plus AR placement).
**Out (deferred):** the "roll objects up into a growing ball" game mechanic.

This makes Task 10 (Viewer + Gallery) a first-class deliverable, and Task 9's export must include `.usdz`
for native iOS viewing. A working web gallery already exists at `kat/library/` (see below) and is the
running reference for what the in-app gallery must do.

---

## Task 0: Verify the Python reference pipeline 🖥️ — DONE

**Status:** complete. `kat/stylescan.py` implements the exact stylize algorithm and was validated on realistic
textured models. Results: avocado 682→198 tris, 142→12 colours, 15,744-byte `.glb` (inside the 7–16 KB target band).

**This file is the normative spec for tasks 5–8.** Keep it as the reference oracle: the Swift port must reproduce
its behaviour (modulo the simplifier library).

---

## Task 1: Project skeleton + capability gate 📱

**Objective:** An app that launches and tells the user whether their device can scan.

**Files:** Create `App.swift`, `Capture/CaptureModel.swift`, `Capture/CaptureView.swift`.

**Code (core gate):**

```swift
import RealityKit

struct CaptureView: View {
    @State private var session: ObjectCaptureSession?
    var body: some View {
        if ObjectCaptureSession.isSupported {
            Text("Ready to scan")           // replaced in Task 2
        } else {
            ContentUnavailableView(
                "LiDAR required",
                systemImage: "camera.metering.unknown",
                description: Text("Object Capture needs a LiDAR iPhone (12 Pro or newer)."))
        }
    }
}
```

**Verify:** runs on an iPhone 15/16 Pro; shows "LiDAR required" on a non-Pro/simulator.

---

## Task 2: Guided capture 📱

**Objective:** `ObjectCaptureView` drives buttery capture with auto-capture + flip pass.

**Files:** Modify `Capture/CaptureModel.swift`, `Capture/CaptureView.swift`.

**Steps:**
1. `start(imagesDirectory:configuration:)` with a fresh temp dir.
2. Present `ObjectCaptureView(session:)`; observe `stateUpdates` for the dial.
3. On `.completed`, move to Task 3.
4. Handle `feedback` (`ObjectCaptureSession.Feedback`) → show hints ("move closer", "more light", "object too close to edge").

**Verify:** complete a scan of a mug; confirm the images directory fills with HEICs.

---

## Task 3: On-device reconstruction 📱

**Objective:** Turn captured images into a textured mesh file, on the phone.

**Files:** Create `Reconstruct/ReconstructionService.swift`.

```swift
func reconstruct(imagesDirectory: URL, outputUSDZ: URL, detail: PhotogrammetrySession.Request.Detail) async throws {
    var cfg = PhotogrammetrySession.Configuration()
    cfg.sampleOrdering = .sequential          // set to .unordered if capture wasn't a smooth pass
    let session = try PhotogrammetrySession(input: imagesDirectory, configuration: cfg)
    let request = PhotogrammetrySession.Request.modelFile(url: outputUSDZ, detail: detail)
    try session.process(requests: [request])
    for try await output in session.outputs {
        if case .requestComplete(_, .modelFile(let url)) = output {
            // done: url == outputUSDZ
        }
        // also handle .requestProgress -> show a bar
    }
}
```

**Notes / risk:** on-device detail ceiling is lower than the Mac pipeline; start at `.reduced`.
Respect `PhotogrammetrySession.limits` and `ObjectCaptureSession.maximumNumberOfInputImages`.

**Verify:** `outputUSDZ` opens in Xcode/Preview as a recognisable, textured model of the scanned object.

---

## Task 4: Mesh extraction 📱

**Objective:** Get plain arrays out of the `.usdz` so the stylizer can touch them.

**Files:** Create `Stylize/MeshIO.swift`.

```swift
import ModelIO
import MetalKit

struct MappedMesh {
    var positions: [SIMD3<Float>]
    var indices: [UInt32]
    var uvs: [SIMD2<Float>]
    var baseColor: MTLTexture        // source texture to sample from
}

func loadMesh(_ url: URL, device: MTLDevice) throws -> MappedMesh { /* MDLAsset -> MDLMesh -> bufferViews -> MTLTexture */ }
```

**Verify:** print vertex/face counts; the counts should roughly match a `usdz` inspection of the same file.

---

## Task 5: Simplify (meshoptimizer) 📱

**Objective:** Decimate to a Katamari tri budget (~200 tris).

**Files:** Create `MeshOptimizer/` vendored C + `Stylize/Simplifier.swift`.

```cpp
// MeshOptimizer/simplify.cpp
#define MESHOPTIMIZER_IMPLEMENTATION
#include "meshoptimizer.h"
```

```swift
func simplify(_ m: MappedMesh, targetTriangles: Int) -> MappedMesh {
    let targetIndices = targetTriangles * 3
    var out = [UInt32](repeating: 0, count: m.indices.count)
    var dstVerts = [Float](repeating: 0, count: m.positions.count * 3)
    var resultCount = m.indices.count
    var error: Float = 0
    m.positions.withUnsafeBufferPointer { p in
        meshopt_simplify(&out, m.indices, m.indices.count, p.baseAddress, m.positions.count,
                         MemoryLayout<Float>.stride * 3, targetIndices, 1.0, 0, &resultCount, &error)
    }
    // NOTE: meshopt_simplify operates on positions only. Carry UVs/colours by re-projecting
    // onto the ORIGINAL mesh per new face (same approach as stylescan.py face_colors_from_texture).
    ...
}
```

**Pitfall:** meshoptimizer drops UVs. Do **not** try to interpolate attribute UVs — instead, for each output face,
find the closest point on the original mesh and sample the source texture there. This is exactly what the Python
oracle does and it is robust to topology changes.

**PITFALL (measured): meshes are multi-body and `simplify` plateaus.** Real scans and most glTF assets are
non-manifold soup — the Lantern sample has **176 disconnected shells**, DamagedHelmet 942. A single quadric pass
*refuses to collapse further* on these: the Lantern sticks at 1326 faces for *any* target, no matter how low.
Two required mitigations:
  1. **`meshopt_simplifySloppy`** instead of `meshopt_simplify` for scans. Sloppy ignores topology validation and
     actually hits the triangle budget — which is fine here, since we only want a 100-triangle toy silhouette.
     (`meshopt_SimplifySloppy` is marked experimental; pin the version.)
  2. **Repeat passes** — re-run simplify on its own output until near budget or progress stalls. This alone took
     DamagedHelmet 498 → 285 faces (see `decimate()` in `stylescan.py`).
  Optionally also decimate **per connected component** so one big part can't eat the whole budget.

**Verify:** tri count lands in 150–300; visually the silhouette still matches.

---

## Task 6: Per-face colour sampling 📱

**Objective:** Give every output triangle a flat colour from the source texture.

**Files:** Modify `Stylize/Simplifier.swift` (or `Stylize/Stylizer.swift`).

- For each output face centroid (world space), find closest point on the original mesh (build a small BVH; or reuse
  ModelIO's `MDLMesh`) → interpolate the original UV → sample `baseColor` (Nearest, wrap) → face colour.
- Equivalent Swift port of `face_colors_from_texture()` in `stylescan.py`.

**Verify:** export an intermediate debug mesh with per-vertex face colours; colours should match the object.

---

## Task 7: Palette quantize 📱

**Objective:** Collapse to 12 flat colours — this IS the Katamari look.

**Files:** Create `Stylize/PaletteQuantizer.swift`.

- k-means (k=12, ~8 iterations is plenty at ≤300 samples) over face colours in RGB.
- Snap each face to nearest cluster.
- Port of `quantise()` in `stylescan.py`.

**Verify:** ≤12 distinct colours in the result; clustering is stable across runs (fixed seed).

---

## Task 8: Swatch atlas + hard-edged mesh 📱

**Objective:** Produce the authentic format — flat faces + a small palette texture atlas.

**Files:** Create `Stylize/SwatchAtlas.swift`.

- `grid = ceil(sqrt(k))`, `cell = 16`, atlas `= grid*cell` (k=12 → 64 px).
- Render each palette colour into its swatch.
- **Split every face into 3 unique vertices** (per-face split) so faces don't share normals → hard edges.
- Assign all 3 UVs of a face to the centre of its swatch cell (tiny inset to avoid bleed).
- Flat normals per face.

Port of `build_atlas_mesh()` + use `meshopt_generateNormals` (experimental) or compute per-face normals manually.

**PITFALL (cost a real bug, now fixed in the Python oracle):** do NOT collapse each face's 3 UVs to a
single point (flat face colour). A zero-area UV triangle gives the GPU zero derivatives, so mip selection
breaks and it samples the most-blurred mip — which averages in the atlas's *empty* texels and renders as
large **black patches**. Fix, all three required:
  1. Map the 3 corners of each face to the 3 corners of its swatch cell (non-degenerate UV triangle).
  2. Pre-fill the whole atlas with the palette mean so unused texels aren't black.
  3. Set the glTF material `doubleSided: true` (scans are open shells — culling backfaces makes holes).
Also set `doubleSided` via `PBRMaterial`, not `SimpleMaterial` — the latter silently ignores it.

**Verify:** output `.usdz`/`.glb` renders as flat coloured facets with NO black patches; ≤16 KB.

---

## Task 9: Export 📱

**Objective:** Get the model out in usable formats.

**Files:** Create `Export/ExportService.swift`, `Export/GLBWriter.swift`.

- **`.usdz`**: build `MeshResource` from the atlas mesh + `SimpleMaterial`/`PhysicallyBasedMaterial` (roughness 1,
  metallic 0) → `ModelEntity` → `ModelEntity.write(to:)`. Native, one line, best for a game/AR.
- **`.glb`**: there is **no native glTF exporter on iOS** — write a minimal one. Our output is tiny and fully under
  our control (positions, normals, per-corner UVs, one PNG texture, no skinning/animation), so a ~200-line writer is
  enough. Port the container/JSON rules from the glTF 2.0 spec; validate the result by loading it in `stylescan.py`
  (`trimesh.load`) — cross-validating Swift output with the Python reader closes the loop. 🖥️

**Verify:** exported `.glb` loads in trimesh here and in the reference site's viewer; `.usdz` loads in Reality Composer.

---

## Task 10: Viewer + gallery 📱

**Objective:** See your scans; re-scan; export.

**Files:** Create `Viewer/ModelViewerView.swift`.

- `RealityView` (RealityKit) or `ARQuickLook` for the in-app preview.
- Persist to `Documents/Scans/<uuid>/model.usdz` + metadata (name, date, tri count, palette size).
- Grid gallery with thumbnails; share sheet for `.glb`/`.usdz`.

**Verify:** kill and relaunch → gallery still populated.

---

## Validation strategy

- 🖥️ **Linux here:** the Python oracle (`kat/stylescan.py`) is the ground-truth algorithm and the `.glb` reader
  used to validate Swift-exported files. Any change to the style should round-trip through it first.
- 📱 **Needs Xcode + LiDAR iPhone:** Tasks 1–10. I cannot compile or run iOS from this environment — those steps
  must be executed on your Mac.
- **Golden test:** scan one object (a mug / fruit), confirm output is 150–300 tris, ≤16 colours, ≤32 KB, and
  recognisable in the reference site's viewer.

---

## Risks & open questions

1. **On-device detail ceiling** — `PhotogrammetrySession` on iPhone gives coarser meshes than the Mac/Object
   Capture CLI. Good news: we decimate to ~200 tris anyway, so this is largely a non-issue. Confirm by capturing
   a detail-rich object.
2. **meshoptimizer drops UVs** — solved by re-projecting per-face colours (Task 5/6). Verified approach in Python.
3. **Convex vs concave objects** — the Python prototype stylised a simple avocado beautifully but struggled on a
   concave/metallic helmet (shard-y facets). May need `meshopt_SimplifyPreserveFolds` or a planar-decimation
   variant for thin-shelled objects. Budget time to tune.
4. **`.glb` writer correctness** — mitigated by cross-validating with the Python reader.
5. **Open:** end goal — collect/export `.glb` files (like the reference library) **vs** roll them up in a
   Katamari-style game? This only changes Task 10 and the export priority, not Tasks 1–9.

---

## First three things to do

1. Create the Xcode project (iOS 17+ target) and complete Task 1's capability gate on your iPhone.
2. Run one real scan end-to-end (Tasks 2–3) and inspect the `.usdz` — this de-risks the whole project.
3. Port Task 5 (simplify) and compare its tri output against `stylescan.py` on the same mesh.
