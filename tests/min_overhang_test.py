# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 WaveOverHangsPlugin contributors
"""Test of the min_overhang_mm setting (gentle slopes keep the slicer's walls).

    python3 tests/min_overhang_test.py [out.png]

Needs pyclipper and numpy (matplotlib for the optional picture). Three islands over a 10 mm
square, with min_overhang 0.5 mm:
  slope  -- the square grown 0.3 mm all round: no waves at all;
  shelf  -- the square plus a 6 mm shelf on one side: waved as without the setting;
  mixed  -- both: the shelf is waved as in "shelf", the 0.3 mm band away from it is not.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generator_smoke as g  # noqa: E402  (stubs orca and loads the plugin)

w = g.w
S = g.S
P = g.P
HALF_NOZZLE = int(0.2 * S)
MIN_OVERHANG = int(0.5 * S)
MIN_AREA = 0.01 * S * S

lower = [g.rect(0, 0, 10, 10)]
band = w.expand(lower, int(0.3 * S))
shelf = [g.rect(10, 2, 16, 8)]
CASES = {
    "slope": band,
    "shelf": w.union(lower + shelf),
    "mixed": w.union(band + shelf),
}


def waves(island, lower_raw, lower_grown):
    regions, _ = w.generate(w.union_ex(island), lower_grown, P)
    return [path for region in regions for path in region]


def extent(paths):
    xs = [x / S for path in paths for x, _ in path]
    ys = [y / S for path in paths for _, y in path]
    return (min(xs), max(xs), min(ys), max(ys)) if xs else None


def main():
    ok = True
    results = {}
    shelf_extent = None
    for name, island in CASES.items():
        plain = waves(island, lower, w.expand(lower, HALF_NOZZLE))
        raw, grown, kept = w.island_support(island, lower, HALF_NOZZLE, MIN_OVERHANG, MIN_AREA)
        gentle = waves(island, raw, grown) if w.diff(island, grown) else []
        results[name] = (island, raw, gentle)
        e = extent(gentle)
        if name == "slope":
            good = kept and not gentle and plain
        elif name == "shelf":
            good = (not kept) and len(gentle) == len(plain) and len(gentle) > 0
            shelf_extent = e
        else:  # mixed: waves only on the shelf and its root, starting where the shelf's do
            good = (kept and gentle and len(gentle) < len(plain) and shelf_extent is not None
                    and abs(e[0] - shelf_extent[0]) < 0.05 and abs(e[1] - shelf_extent[1]) < 0.05)
        ok &= bool(good)
        print("%s %-6s without setting: %3d path(s); with min_overhang 0.5: %3d path(s), extent %s, slope kept: %s"
              % ("OK  " if good else "FAIL", name, len(plain), len(gentle),
                 None if e is None else tuple(round(v, 2) for v in e), kept))
    if len(sys.argv) > 1:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 3, figsize=(15, 5))
        for ax, (name, (island, raw, gentle)) in zip(axes, results.items()):
            for poly in island:
                xs = [x / S for x, _ in poly] + [poly[0][0] / S]
                ys = [y / S for _, y in poly] + [poly[0][1] / S]
                ax.plot(xs, ys, color="#c33", lw=1)
            for poly in raw:
                xs = [x / S for x, _ in poly] + [poly[0][0] / S]
                ys = [y / S for _, y in poly] + [poly[0][1] / S]
                ax.fill(xs, ys, color="#ddd")
            for path in gentle:
                ax.plot([x / S for x, _ in path], [y / S for _, y in path], color="#1a9", lw=.6)
            ax.set_title(name)
            ax.set_aspect("equal")
        plt.tight_layout()
        plt.savefig(sys.argv[1], dpi=90)
    print("all OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
