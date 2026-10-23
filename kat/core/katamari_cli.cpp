// katamari_cli.cpp — text-format harness so the core can be driven from Python for
// verification against the reference implementation.
//
// stdin:   nv nf hasCols targetTris paletteK faceted
//          nv lines  "x y z"
//          nf lines  "i j k"
//          nv lines  "r g b"      (only if hasCols)
// stdout:  nv nf
//          nv lines  "x y z"
//          nf lines  "i j k"
//          nf lines  "r g b"      (per-face colour)
#include "katamari_core.h"
#include <cstdio>
#include <cstdlib>
#include <vector>

int main() {
    size_t nv = 0, nf = 0;
    int hasCols = 0, paletteK = 0, faceted = 1;
    size_t targetTris = 0;
    if (scanf("%zu %zu %d %zu %d %d", &nv, &nf, &hasCols, &targetTris, &paletteK, &faceted) != 6) {
        fprintf(stderr, "bad header\n");
        return 2;
    }

    std::vector<float> positions(nv * 3);
    for (size_t i = 0; i < nv; ++i)
        if (scanf("%f %f %f", &positions[i * 3], &positions[i * 3 + 1], &positions[i * 3 + 2]) != 3) return 2;

    std::vector<uint32_t> faces(nf * 3);
    for (size_t i = 0; i < nf; ++i) {
        unsigned int a, b, c;
        if (scanf("%u %u %u", &a, &b, &c) != 3) return 2;
        faces[i * 3] = a; faces[i * 3 + 1] = b; faces[i * 3 + 2] = c;
    }

    std::vector<float> vcols;
    if (hasCols) {
        vcols.resize(nv * 3);
        for (size_t i = 0; i < nv; ++i)
            if (scanf("%f %f %f", &vcols[i * 3], &vcols[i * 3 + 1], &vcols[i * 3 + 2]) != 3) return 2;
    }

    KatMesh m = katamari_stylize(positions, faces, vcols, targetTris, paletteK, faceted != 0);

    printf("%zu %zu\n", m.positions.size() / 3, m.faces.size() / 3);
    for (size_t i = 0; i < m.positions.size(); i += 3)
        printf("%.6f %.6f %.6f\n", m.positions[i], m.positions[i + 1], m.positions[i + 2]);
    for (size_t i = 0; i < m.faces.size(); i += 3)
        printf("%u %u %u\n", m.faces[i], m.faces[i + 1], m.faces[i + 2]);
    for (size_t i = 0; i < m.faceColors.size(); i += 3)
        printf("%.6f %.6f %.6f\n", m.faceColors[i], m.faceColors[i + 1], m.faceColors[i + 2]);
    return 0;
}
