// katamari_core.h — portable styliser core, callable from Swift (bridging header) and C++.
//
// Same contract as the Python reference (kat/stylescan_uv.py: katamari_faceted):
//   decimate -> per-face colour -> posterise palette -> flat/faceted output.
//
// Why C: meshoptimizer is C, this compiles and is testable on any platform, and Swift
// wraps it with a two-line bridging header. No Swift toolchain needed to verify it.
#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>

struct KatMesh {
    std::vector<float>    positions;   // 3 * vertexCount
    std::vector<uint32_t> faces;       // 3 * triangleCount, indexes positions
    std::vector<float>    faceColors;  // 3 * triangleCount, rgb 0..1 (one colour per face)
};

// positions/faces: indexed input mesh.
// vcols: per-vertex rgb 0..1 (3 * vertexCount) — sampled from the source texture upstream.
//        Used to GUIDE simplification (attribute preservation) and as the colour source.
// targetTris: desired triangle count. paletteK: posterised palette size (0 = skip).
// faceted: true  -> one flat colour per face (Blocks look)
//          false -> smooth per-vertex colours carried through (game-rip look)
KatMesh katamari_stylize(const std::vector<float>&    positions,
                         const std::vector<uint32_t>& faces,
                         const std::vector<float>&    vcols,
                         size_t targetTris,
                         int    paletteK,
                         bool   faceted);
