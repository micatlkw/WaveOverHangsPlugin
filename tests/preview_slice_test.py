# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test of the preview built from the slice (before any export), outside OrcaSlicer.

    python3 tests/preview_slice_test.py

Needs pyclipper and numpy. Checks:
  - build_slice_preview() (what was captured while slicing, moved by each instance's plate
    position) gives the same waves, kept walls and outer-wall outlines as extract_preview() on the
    exported G-code of the same plate (3 copies of 2 objects), so the two routes agree;
  - _object_instances() picks the instances that share the PrintObject's transform, and uses the
    centre of the world bounding box;
  - _ring_flat() closes and simplifies an outline.
"""
import os
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcode_instances_test as gi  # noqa: E402  (stubs orca, loads the plugin, builds G-code)

w = gi.w
ok = True


def check(cond, what):
    global ok
    ok &= bool(cond)
    print("%s %s" % ("OK  " if cond else "FAIL", what))


def key(flat):
    return tuple(round(v, 2) for v in flat)


def layer_at(pv, z):
    return next(l for l in pv["layers"] if abs(l["z"] - z) < 1e-6)


def synthetic_captures():
    caps = gi.captures()
    for oid, cap in caps.items():
        h = gi.OBJECTS[oid]["half"]
        ring = [-h, -h, h, -h, h, h, -h, h, -h, -h]
        for entry in cap["layers"].values():
            entry["ctx"] = [list(ring)]
        cap["instances"] = [c for o, _, c in gi.INSTANCES if o == oid]
    return caps


def routes():
    caps = synthetic_captures()
    out, _ = w._post_process(gi.build("klipper", gi.INSTANCES), caps, (True, True), dict(gi.CFG))
    gcode = w.extract_preview(out, True)
    sliced = w.build_slice_preview(synthetic_captures())
    check(len(sliced["layers"]) == len(gcode["layers"]) == len(gi.ZS),
          "both routes have %d layers" % len(gi.ZS))
    for kind, name in (("w", "waves"), ("k", "kept walls"), ("c", "outer-wall outlines")):
        for z in gi.ZS:
            a = sorted(key(f) for f in layer_at(sliced, z)[kind])
            b = sorted(key(f) for f in layer_at(gcode, z)[kind])
            if a != b:
                check(False, "%s at z=%s differ: slice %s vs G-code %s" % (name, z, a[:2], b[:2]))
                break
        else:
            check(True, "%s agree with the exported G-code on every layer" % name)
    check(abs(sliced["stats"]["wave_mm"] - gcode["stats"]["wave_mm"]) < 0.05
          and abs(sliced["stats"]["wall_mm"] - gcode["stats"]["wall_mm"]) < 0.05,
          "wave and wall lengths agree (%.1f / %.1f mm)" % (sliced["stats"]["wave_mm"], sliced["stats"]["wall_mm"]))
    box = sliced["bbox"]
    check(box and box[0] == 45.0 and box[2] == 105.0, "bounding box covers the copies: %s" % box)


class Inst:
    def __init__(self, matrix, lo, hi, printable=True):
        self._m, self._lo, self._hi, self.printable = np.array(matrix, dtype=float), lo, hi, printable

    def matrix(self):
        return self._m

    def bounding_box(self):
        return types.SimpleNamespace(defined=True, min=self._lo, max=self._hi)


def mat(tx, ty, tz=0.0, rot90=False):
    m = np.eye(4)
    if rot90:
        m[:2, :2] = [[0, -1], [1, 0]]
    m[0, 3], m[1, 3], m[2, 3] = tx, ty, tz
    return m


def instances():
    plain = np.eye(4)
    objs = [Inst(mat(50, 50), (45, 45, 0), (55, 55, 5)),
            Inst(mat(100, 50), (95, 45, 0), (105, 55, 5)),
            Inst(mat(10, 10, rot90=True), (4, 4, 0), (16, 16, 5)),          # rotated: another PrintObject
            Inst(mat(70, 70), (65, 65, 0), (75, 75, 5), printable=False)]  # not printed
    model_object = types.SimpleNamespace(instances=lambda: objs)
    ctx = types.SimpleNamespace(object=types.SimpleNamespace(trafo=lambda: plain, model_object=lambda: model_object))
    got = w._object_instances(ctx)
    check(got == [(50.0, 50.0), (100.0, 50.0)], "instances sharing the transform, at their bbox centres: %s" % got)
    ctx.object.trafo = lambda: mat(0, 0, rot90=True)
    check(w._object_instances(ctx) == [(10.0, 10.0)], "the rotated instance belongs to the rotated object")
    ctx.object.trafo = lambda: np.diag([2.0, 2.0, 2.0, 1.0])       # matches none: fall back to every printable one
    check(len(w._object_instances(ctx)) == 3, "no match: every printable instance is used")
    check(w._object_instances(types.SimpleNamespace(object=None)) == [], "no object: no instances, no error")


def ring():
    flat = w._ring_flat([(0, 0), (10000000, 0), (10000000, 5000000), (5000000, 5000000), (0, 5000000)], 1e-6)
    check(flat[:2] == flat[-2:] == [0.0, 0.0] and len(flat) == 10,
          "ring is closed, the collinear point simplified away: %s" % flat)
    flat = w._ring_flat([(i * 1000000, 0) for i in range(6)] + [(5000000, 5000000), (0, 5000000)], 1e-6)
    check(len(flat) == 2 * 5, "collinear points are simplified: %s" % flat)
    check(w._ring_flat([(0, 0), (1, 1)], 1e-6) == [], "a degenerate ring is dropped")


def main():
    routes()
    instances()
    ring()
    print("all OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
