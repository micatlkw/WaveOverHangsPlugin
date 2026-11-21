"""Smoke test of the wave generator outside OrcaSlicer.

    python3 tests/generator_smoke.py [out.png]

Needs pyclipper and numpy (and matplotlib to write the optional picture). Stubs the
`orca` module, runs generate() on three synthetic overhangs, checks basic properties
and prints a summary.
"""
import importlib.util
import math
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..", "wave_overhangs_plugin.py")

orca = types.ModuleType("orca")
sl = types.ModuleType("orca.slicing")
sl.SlicingPipelineCapabilityBase = object
sl.Step = types.SimpleNamespace()
sl.unscale = lambda v: v * 1e-6
orca.slicing = sl
orca.ExecutionResult = object
orca.base = object
orca.plugin = lambda c: c
orca.register_capability = lambda *a: None
sys.modules["orca"] = orca
sys.modules["orca.slicing"] = sl
spec = importlib.util.spec_from_file_location("wave_overhangs_plugin", PLUGIN)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

S = 1e6


def circle(r, n=180):
    return [(int(r * S * math.cos(2 * math.pi * i / n)), int(r * S * math.sin(2 * math.pi * i / n))) for i in range(n)]


def rect(x0, y0, x1, y1):
    return [(int(x0 * S), int(y0 * S)), (int(x1 * S), int(y0 * S)), (int(x1 * S), int(y1 * S)), (int(x0 * S), int(y1 * S))]


P = {"base_spacing": int(0.45e6), "overhang_width": int(0.4e6), "wave_width": int(0.4e6), "wave_spacing": int(0.35e6),
     "perimeter_overlap": int(0.1e6), "minimum_wave_width": int(0.7e6), "min_new_area": 0.01e12, "min_length": 0,
     "max_iterations": 0, "pattern": "smart", "corner_taper_enable": False, "wave_spacing_corner": int(0.18e6),
     "corner_taper_distance": int(3e6), "corner_angle_threshold": 90.0, "external_infill_margin": int(3e6),
     "scaled_resolution": int(0.012e6), "perimeter_count": 2}

CASES = {
    "ring: disc r24 on disc r16": ([(circle(24), [])], [circle(16)]),
    "cantilever 30x10 on 10x10": ([(rect(0, 0, 30, 10), [])], [rect(0, 0, 10, 10)]),
    "T: bar on a post, with a neck": ([(w.union([rect(-15, 0, 15, 4), rect(-15, 4, -13, 12), rect(-3, 4, 3, 12)])[0], [])],
                                      [rect(-3, 0, 3, 12)]),
}


def main():
    results = {}
    for name, (island, lower_raw) in CASES.items():
        lower = w.expand(lower_raw, int(0.2e6))
        regions, residual = w.generate(island, lower, P)
        paths = [p for r in regions for p in r]
        assert paths, name + ": no waves"
        # every wave point lies inside the island (grown by the perimeter overlap)
        grown = w.expand(w.to_polygons(island), int(0.15e6))
        for p in paths:
            for pt in p:
                assert any(pc_in(pt, g) for g in grown), name + ": wave point outside the island"
        length = sum(w.polyline_length(p) for p in paths) / S
        results[name] = (island, lower_raw, residual, paths)
        print("%-32s %d region(s), %3d path(s), %6.0f mm of wave line" % (name, len(regions), len(paths), length))
    if len(sys.argv) > 1:
        draw(results, sys.argv[1])


def pc_in(pt, poly):
    import pyclipper
    return pyclipper.PointInPolygon(pt, poly) != 0


def draw(results, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, len(results), figsize=(6 * len(results), 6))
    for ax, (name, (island, lower_raw, residual, paths)) in zip(axes, results.items()):
        for poly in w.to_polygons(island):
            ax.fill(*zip(*[(x / S, y / S) for x, y in poly]), color="#ddd")
        for poly in lower_raw:
            ax.fill(*zip(*[(x / S, y / S) for x, y in poly]), color="#9bd")
        for poly in residual:
            ax.plot(*zip(*[(x / S, y / S) for x, y in poly + [poly[0]]]), color="r", lw=.6, ls="--")
        cmap = plt.get_cmap("viridis")
        for i, p in enumerate(paths):
            ax.plot([x / S for x, _ in p], [y / S for _, y in p], color=cmap(i / max(1, len(paths) - 1)), lw=.7)
        ax.set_aspect("equal")
        ax.set_title(name, fontsize=9)
    plt.tight_layout()
    plt.savefig(out, dpi=110)
    print("picture:", out)


if __name__ == "__main__":
    main()
