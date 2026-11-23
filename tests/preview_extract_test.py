"""Test of extract_preview(), the preview panel's data, outside OrcaSlicer.

    python3 tests/preview_extract_test.py [real.gcode ...]

Needs pyclipper and numpy. Checks on synthetic G-code (always) and on any real exported files
given (or found in the Downloads folder of this machine):
  - the wave and kept-wall lengths equal what the report in the file says was injected;
  - one entry per layer, in order, with the right z; waves only on the layers that have blocks;
  - polylines are flat lists of x, y pairs inside the bounding box.
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcode_instances_test as gi  # noqa: E402  (stubs orca, loads the plugin, builds G-code)

w = gi.w
ok = True


def check(cond, what):
    global ok
    ok &= bool(cond)
    print("%s %s" % ("OK  " if cond else "FAIL", what))


def close(a, b, tol):
    return abs(a - b) <= tol


def structure_ok(pv, n_layer_tags):
    good = len(pv["layers"]) == n_layer_tags
    zs = [l["z"] for l in pv["layers"]]
    good &= zs == sorted(zs)
    box = pv["bbox"]
    for layer in pv["layers"]:
        for kind in ("w", "k", "c"):
            for flat in layer[kind]:
                good &= len(flat) % 2 == 0 and len(flat) >= 4
                if box:
                    good &= all(box[0] - 0.01 <= x <= box[2] + 0.01 and box[1] - 0.01 <= y <= box[3] + 0.01
                                for x, y in zip(flat[0::2], flat[1::2]))
    return good


def synthetic():
    lines = gi.build("klipper", gi.INSTANCES)
    out, _ = w._post_process(list(lines), gi.captures(), (True, True), dict(gi.CFG))
    pv = w.extract_preview(out, True)
    # objects: A (two copies) 10 mm wave + 10 mm kept wall each, B 6 mm wave, on z = 0.6
    check(close(pv["stats"]["wave_mm"], 26.0, 0.05), "synthetic: wave length %.2f mm, expected 26"
          % pv["stats"]["wave_mm"])
    check(close(pv["stats"]["wall_mm"], 20.0, 0.05), "synthetic: kept wall %.2f mm, expected 20"
          % pv["stats"]["wall_mm"])
    check(structure_ok(pv, len(gi.ZS)), "synthetic: %d layers, polylines well formed" % len(pv["layers"]))
    waved = [l["z"] for l in pv["layers"] if l["w"]]
    check(waved == [gi.WAVE_Z], "synthetic: waves only on z=%s (got %s)" % (gi.WAVE_Z, waved))
    check(all(len(l["c"]) == len(gi.INSTANCES) for l in pv["layers"]),
          "synthetic: the slicer's outer wall of every copy is on every layer as context")
    plain = w.extract_preview(lines, True)
    check(plain["stats"]["wave_mm"] == 0 and not any(l["w"] or l["k"] for l in plain["layers"]),
          "synthetic: a file without waves has none")


def real(path):
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
        lines = f.readlines()
    report = "\n".join(l for l in lines[:60] if l.startswith(w._TAG + " "))
    m = re.search(r"([\d.]+) mm of wave line", report)
    k = re.search(r"([\d.]+) mm of kept wall at", report)   # the total on the "injected" line
    if not m:
        print("skip %s: no wave report in it" % os.path.basename(path))
        return
    e_rel = any(l.startswith("; use_relative_e_distances = 1") for l in lines)
    pv = w.extract_preview(lines, e_rel)
    name = os.path.basename(path)
    check(close(pv["stats"]["wave_mm"], float(m.group(1)), 0.5 + 1e-3 * float(m.group(1))),
          "%s: wave length %.1f mm vs report %s" % (name, pv["stats"]["wave_mm"], m.group(1)))
    if k:
        check(close(pv["stats"]["wall_mm"], float(k.group(1)), 3.0 + 0.01 * float(k.group(1))),
              "%s: kept wall %.1f mm vs report %s (simplified)" % (name, pv["stats"]["wall_mm"], k.group(1)))
    tags = sum(1 for l in lines if w._is_layer_tag(l.strip()))
    check(structure_ok(pv, tags), "%s: %d layers, polylines well formed" % (name, len(pv["layers"])))
    blocks = sum(1 for l in lines if l.startswith(w._TAG + "_START"))
    waved = sum(1 for l in pv["layers"] if l["w"])
    check(waved <= blocks and (waved >= 1 or blocks == 0),
          "%s: waves on %d layer(s), %d block(s)" % (name, waved, blocks))
    print("     %d numbers to send (%s of context-free JSON)" % (pv["stats"]["numbers"],
          "%.1f MB" % (pv["stats"]["numbers"] * 7 / 1e6)))


def main():
    synthetic()
    files = sys.argv[1:] or sorted(glob.glob("/path/to/your/exports/OverhangWaveTest_PLA_*.gcode")
                                   + glob.glob("/path/to/your/exports/waveoverhangs-207-wall-loop.gcode"))
    for path in files:
        real(path)
    print("all OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
