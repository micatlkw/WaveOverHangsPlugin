"""Test of the G-code half with several objects and copies, outside OrcaSlicer.

    python3 tests/gcode_instances_test.py

Needs pyclipper and numpy (the plugin imports them). Builds synthetic G-code in the shape
OrcaSlicer writes (layer tags, wall features, object labels of each family, lazy end labels),
with two objects: A (10 mm square, two copies) and B (6 mm square, one copy), each with a wave
path on z=0.6. Checks that every copy gets its own waves at its own offset, inside its own
labelled segment, and that files without labels are refused when there are several objects.
"""
import importlib.util
import os
import re
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
orca.script = types.SimpleNamespace(ScriptPluginCapabilityBase=object)
orca.plugin = lambda c: c
orca.register_capability = lambda *a: None
sys.modules["orca"] = orca
sys.modules["orca.slicing"] = sl
spec = importlib.util.spec_from_file_location("wave_overhangs_plugin", PLUGIN)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

ZS = [0.2, 0.4, 0.6, 0.8, 1.0]
WAVE_Z = 0.6
# object id -> half size, wave path (object space), position in the print's object list
OBJECTS = {
    101: {"name": "A.stl", "half": 5.0, "path": [(-5.0, 6.0), (5.0, 6.0)], "index": 0},
    202: {"name": "B.stl", "half": 3.0, "path": [(-3.0, 4.0), (3.0, 4.0)], "index": 1},
}
# (object id, copy, plate centre)
INSTANCES = [(101, 0, (50.0, 50.0)), (101, 1, (100.0, 50.0)), (202, 0, (50.0, 100.0))]
UNIQUE = {(101, 0): 0, (101, 1): 1, (202, 0): 2}


def captures():
    out = {}
    for oid, o in OBJECTS.items():
        h = o["half"]
        layers = {}
        for z in ZS:
            layers[w._zkey(z)] = {"z": z, "height": 0.2, "ref": (-h, -h, h, h),
                                  "paths": [list(o["path"])] if z == WAVE_Z else []}
        if oid == 101:  # object A keeps one wall along its overhang edge
            layers[w._zkey(WAVE_Z)]["walls"] = [{"role": "Outer wall", "width": 0.45, "mm3_per_mm": 0.08,
                                                 "pts": [(-5.0, 6.5), (5.0, 6.5)]}]
        out[oid] = {"layers": layers, "line_width": 0.4, "cut_layers": 1, "generated_at": "posSlice",
                    "stats": {"paths": 1, "regions": 1, "wave_layers": 1, "length_mm": 2 * h},
                    "index": o["index"], "name": o["name"]}
    return out


def label(family, oid, copy, start):
    o = OBJECTS[oid]
    name = "%s_id_%d_copy_%d" % (o["name"].replace(".", "_"), o["index"], copy)
    if family == "klipper":
        return ["EXCLUDE_OBJECT_%s NAME=%s" % ("START" if start else "END", name)]
    if family == "m486":
        return ["M486 S%d" % UNIQUE[(oid, copy)] if start else "M486 S-1"]
    if family == "comment":
        return ["; %s object %s id:%d copy %d" % ("printing" if start else "stop printing", o["name"], o["index"], copy)]
    if family == "bambu":
        return ["; %s printing object, unique label id: %d" % ("start" if start else "stop", 7 + UNIQUE[(oid, copy)])]
    return []


def build(family, instances):
    g = ["; HEADER_BLOCK_START", "M83"]
    if family == "m486":  # definitions in the header, before the first layer
        for oid, copy, _ in instances:
            g += ["M486 S%d" % UNIQUE[(oid, copy)], "M486 A%s" % OBJECTS[oid]["name"], "M486 S-1"]
    pending_end = []
    for z in ZS:
        # the previous layer's last instance already retracted (a second retract would leave the
        # first segment of the layer net-retracted)
        g += [";LAYER_CHANGE", ";Z:%s" % w._fmt(z), ";HEIGHT:0.2"] + (["G1 E-0.8 F1800"] if z == ZS[0] else []) \
             + ["G1 Z%s" % w._fmt(z)]
        g += pending_end  # lazy end label of the previous layer's last instance
        pending_end = []
        for n, (oid, copy, (cx, cy)) in enumerate(instances):
            h = OBJECTS[oid]["half"]
            pts = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h), (cx - h, cy - h)]
            g += ["G1 X%s Y%s F12000" % (w._fmt(pts[0][0]), w._fmt(pts[0][1]))] + label(family, oid, copy, True)
            g += ["G1 E0.8 F1800", ";TYPE:Outer wall", ";WIDTH:0.42", "G1 F3000"]
            g += ["G1 X%s Y%s E0.5" % (w._fmt(x), w._fmt(y)) for x, y in pts[1:]]
            g += ["G1 E-0.8 F1800"]
            end = label(family, oid, copy, False)
            if n == len(instances) - 1:
                pending_end = end
            else:
                g += end
    g += pending_end + ["; EXECUTABLE_BLOCK_END"]
    return [line + "\n" for line in g]


CFG = {"enabled": True, "e_relative": True, "filament_diameter": 1.75, "retract": 0.8, "flow_mm3_per_mm": 0.15,
       "print_speed": 2.0, "travel_speed": 40.0, "fan_speed": 100.0, "connector_limit": 0.5, "line_width": 0.4,
       "wall_speed": 15.0}


def blocks(lines):
    """[(start index, first G1 X/Y of the block)] of the injected wave blocks."""
    out = []
    for i, line in enumerate(lines):
        if line.startswith(w._TAG + "_START"):
            for nxt in lines[i:]:
                m = re.match(r"G1 X(\S+) Y(\S+) F", nxt)
                if m:
                    out.append((i, (float(m.group(1)), float(m.group(2)))))
                    break
    return out


def owner(lines, i, family):
    """Label key of the segment that contains line i."""
    key = None
    for line in lines[:i]:
        stripped = line.strip()
        if w._is_layer_tag(stripped):
            key = None
        ev = w._label_event(family, stripped)
        if ev is not None:
            key = ev[1] if ev[0] == "start" else None
    return key


def check_blocks(lines, instances):
    """Walls after the waves with their own tag, speed and flow; retraction balanced."""
    problems = []
    e_per_mm = 0.08 / (3.141592653589793 * 0.875 ** 2)
    starts = [i for i, l in enumerate(lines) if l.startswith(w._TAG + "_START")]
    walled = 0
    for i in starts:
        j = i
        while not lines[j].startswith(w._TAG + "_END"):
            j += 1
        block = [l.strip() for l in lines[i:j]]
        # the block ends by restoring the fan and the slicer's own tags; look before that
        last_fan = max(n for n, l in enumerate(block) if l.startswith("M106") or l.startswith("M107"))
        net = 0.0
        for l in block:
            m = re.match(r"G1 E(-?[\d.]+)", l)
            if m:
                net += float(m.group(1))
        if abs(net) > 1e-6:
            problems.append("block at line %d: retraction moves do not balance (%.5f)" % (i, net))
        if ";TYPE:Outer wall" in block[:last_fan]:
            walled += 1
            k = block.index(";TYPE:Outer wall")
            if any(l.startswith("G1 X") and " E" in l for l in block[k:]) is False:
                problems.append("wall tag without wall moves")
            if ";TYPE:Overhang wall" in block[k:]:
                problems.append("waves after the wall")
            if "G1 F900" not in block[k:]:
                problems.append("wall not at 15 mm/s")
            m = [l for l in block[k:] if re.match(r"G1 X\S+ Y\S+ E", l)]
            e = float(re.search(r"E([\d.]+)", m[0]).group(1)) if m else 0
            if abs(e - 10.0 * e_per_mm) > 1e-4:
                problems.append("wall extrusion %.5f, expected %.5f" % (e, 10.0 * e_per_mm))
    want = sum(1 for oid, _, _ in instances if oid == 101)
    if walled != want:
        problems.append("%d block(s) with a wall, expected %d" % (walled, want))
    return problems


def run(family, instances, expect_blocks):
    lines = build(family, instances)
    out, summary = w._post_process(list(lines), captures(), (True, True), dict(CFG))
    got = blocks(out)
    problems = []
    if len(got) != expect_blocks:
        problems.append("expected %d wave blocks, got %d" % (expect_blocks, len(got)))
    if expect_blocks:
        want = sorted((cx + OBJECTS[oid]["path"][0][0], cy + OBJECTS[oid]["path"][0][1])
                      for oid, _, (cx, cy) in instances)
        if sorted(p for _, p in got) != want:
            problems.append("wave starts %s, expected %s" % (sorted(p for _, p in got), want))
        if family is not None:
            owners = sorted(owner(out, i, family) for i, _ in got)
            if None in owners or len(set(owners)) != len(owners):
                problems.append("blocks not each inside their own instance's segment: %s" % owners)
    problems += check_blocks(out, instances if expect_blocks else [])
    again, summary2 = w._post_process(list(out), captures(), (True, True), dict(CFG))
    if again != out:
        problems.append("second run changed the file")
    status = "OK  " if not problems else "FAIL"
    print("%s %-8s %d instance(s): %d block(s)" % (status, family, len(instances), len(got)))
    for line in out[1:]:
        if not line.startswith(w._TAG + " "):
            break
        print("       " + line.rstrip())
    for p in problems:
        print("       !! " + p)
    return not problems


def main():
    ok = True
    for family in ("klipper", "m486", "comment", "bambu"):
        ok &= run(family, INSTANCES, 3)
    ok &= run(None, INSTANCES[:1], 1)    # no labels, one copy: whole-layer route still works
    ok &= run(None, INSTANCES, 0)        # no labels, several copies: refused
    print("all OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
