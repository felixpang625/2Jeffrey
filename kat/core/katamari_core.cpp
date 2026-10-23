// katamari_core.cpp — see katamari_core.h
#include "katamari_core.h"
#include "vendor/meshoptimizer.h"

#include <algorithm>
#include <cfloat>
#include <cmath>
#include <limits>
#include <random>

namespace {

// ---------------------------------------------------------------- k-means (RGB)
// k-means++ seeding with a fixed seed so results are reproducible run-to-run,
// matching the Python reference's fixed random_state.
void kmeansColours(const std::vector<float>& pts, int dim, int k, int iters,
                   std::vector<float>& centers, std::vector<int>& labels) {
    const int n = static_cast<int>(pts.size() / dim);
    if (n == 0 || k <= 0) return;
    if (k > n) k = n;

    labels.assign(n, 0);
    centers.assign(static_cast<size_t>(k) * dim, 0.0f);

    std::mt19937 rng(12345u);
    std::uniform_real_distribution<float> uni(0.0f, 1.0f);

    // ---- k-means++ init
    std::vector<float> best(n, std::numeric_limits<float>::max());
    int first = static_cast<int>(uni(rng) * n) % n;
    for (int c = 0; c < k; ++c) {
        int pick = first;
        if (c > 0) {
            double total = 0.0;
            for (int i = 0; i < n; ++i) total += best[i];
            if (total <= 0.0) {
                pick = static_cast<int>(uni(rng) * n) % n;
            } else {
                std::uniform_real_distribution<double> w(0.0, total);
                double r = w(rng), acc = 0.0;
                pick = n - 1;
                for (int i = 0; i < n; ++i) {
                    acc += best[i];
                    if (acc >= r) { pick = i; break; }
                }
            }
        }
        for (int d = 0; d < dim; ++d) centers[static_cast<size_t>(c) * dim + d] = pts[static_cast<size_t>(pick) * dim + d];
        for (int i = 0; i < n; ++i) {
            float dist = 0.0f;
            for (int d = 0; d < dim; ++d) {
                float diff = pts[static_cast<size_t>(i) * dim + d] - centers[static_cast<size_t>(c) * dim + d];
                dist += diff * diff;
            }
            best[i] = std::min(best[i], dist);
        }
    }

    // ---- Lloyd iterations
    for (int it = 0; it < iters; ++it) {
        std::vector<double> sum(static_cast<size_t>(k) * dim, 0.0);
        std::vector<int>    cnt(k, 0);
        for (int i = 0; i < n; ++i) {
            int bestC = 0; float bestD = std::numeric_limits<float>::max();
            for (int c = 0; c < k; ++c) {
                float dist = 0.0f;
                for (int d = 0; d < dim; ++d) {
                    float diff = pts[static_cast<size_t>(i) * dim + d] - centers[static_cast<size_t>(c) * dim + d];
                    dist += diff * diff;
                }
                if (dist < bestD) { bestD = dist; bestC = c; }
            }
            labels[i] = bestC;
            cnt[bestC]++;
            for (int d = 0; d < dim; ++d) sum[static_cast<size_t>(bestC) * dim + d] += pts[static_cast<size_t>(i) * dim + d];
        }
        for (int c = 0; c < k; ++c) {
            if (!cnt[c]) continue;
            for (int d = 0; d < dim; ++d)
                centers[static_cast<size_t>(c) * dim + d] = static_cast<float>(sum[static_cast<size_t>(c) * dim + d] / cnt[c]);
        }
    }
}

}  // namespace

KatMesh katamari_stylize(const std::vector<float>&    positions,
                         const std::vector<uint32_t>& faces,
                         const std::vector<float>&    vcols,
                         size_t targetTris,
                         int    paletteK,
                         bool   faceted) {
    KatMesh out;
    const size_t nv = positions.size() / 3;
    const size_t nf = faces.size() / 3;
    if (nv == 0 || nf == 0) return out;

    const bool haveCols = vcols.size() == nv * 3;

    // ---- 1. decimate. meshopt_simplify* returns indices into the ORIGINAL vertex
    //         array (collapsing edges, never inventing vertices), so surviving
    //         vertices keep their original UVs/colours. That is why no projection
    //         bake is needed here and why the Python shard problem disappears.
    size_t targetIndexCount = std::min(faces.size(), targetTris * 3);
    std::vector<unsigned int> simplified(faces.size());
    float attrWeights[3] = {1.0f, 1.0f, 1.0f};
    float resultError = 0.0f;

    size_t res = meshopt_simplifyWithAttributes(
        simplified.data(), faces.data(), faces.size(),
        positions.data(), nv, sizeof(float) * 3,
        haveCols ? vcols.data() : nullptr, sizeof(float) * 3,
        haveCols ? attrWeights : nullptr, haveCols ? 3u : 0u,
        /*vertex_lock*/ nullptr,
        targetIndexCount, FLT_MAX, /*options*/ 0, &resultError);

    // Fallback: some meshes refuse attribute-guided collapse. Sloppy mode ignores
    // topological validity and reliably reaches the budget — fine for a ~200-tri toy.
    const size_t floorTris = (targetIndexCount / 3) * 4 / 3;
    if (res > std::max<size_t>(targetIndexCount + 3, floorTris)) {
        size_t res2 = meshopt_simplifySloppy(
            simplified.data(), faces.data(), faces.size(),
            positions.data(), nv, sizeof(float) * 3,
            nullptr,
            targetIndexCount, FLT_MAX, &resultError);
        if (res2 > 0) res = res2;
    }
    simplified.resize(res);
    const size_t nf2 = simplified.size() / 3;

    // ---- 2. compact: remap referenced vertices to a dense array
    std::vector<int> remap(nv, -1);
    std::vector<uint32_t> newFaces(simplified.size());
    out.positions.reserve(res * 3);
    std::vector<float> newCols;
    if (haveCols) newCols.reserve(res * 3);

    for (size_t i = 0; i < simplified.size(); ++i) {
        uint32_t v = simplified[i];
        int& slot = remap[v];
        if (slot < 0) {
            slot = static_cast<int>(out.positions.size() / 3);
            out.positions.push_back(positions[v * 3 + 0]);
            out.positions.push_back(positions[v * 3 + 1]);
            out.positions.push_back(positions[v * 3 + 2]);
            if (haveCols) {
                newCols.push_back(vcols[v * 3 + 0]);
                newCols.push_back(vcols[v * 3 + 1]);
                newCols.push_back(vcols[v * 3 + 2]);
            }
        }
        newFaces[i] = static_cast<uint32_t>(slot);
    }
    out.faces = newFaces;

    // ---- 3. colour
    if (!haveCols) return out;

    if (faceted) {
        // one colour per face = mean of its 3 vertex colours (matches the Python oracle)
        out.faceColors.resize(nf2 * 3);
        std::vector<float> fc(nf2 * 3);
        for (size_t f = 0; f < nf2; ++f) {
            for (int d = 0; d < 3; ++d) {
                float s = 0.0f;
                for (int c = 0; c < 3; ++c) s += newCols[static_cast<size_t>(out.faces[f * 3 + c]) * 3 + d];
                fc[f * 3 + d] = s / 3.0f;
            }
        }
        if (paletteK > 0) {
            std::vector<float> centers; std::vector<int> labels;
            kmeansColours(fc, 3, paletteK, 12, centers, labels);
            for (size_t f = 0; f < nf2; ++f)
                for (int d = 0; d < 3; ++d) out.faceColors[f * 3 + d] = centers[static_cast<size_t>(labels[f]) * 3 + d];
        } else {
            out.faceColors = fc;
        }
    } else {
        // smooth: carry per-vertex colours out as face colours equal to vertex colour
        // (the Swift writer emits COLOR_0 + smooth normals for this path)
        out.faceColors.resize(nf2 * 3);
        for (size_t f = 0; f < nf2; ++f)
            for (int d = 0; d < 3; ++d)
                out.faceColors[f * 3 + d] = newCols[static_cast<size_t>(out.faces[f * 3]) * 3 + d];
    }
    return out;
}
