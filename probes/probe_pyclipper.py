# /// script
# requires-python = ">=3.12"
# dependencies = ["pyclipper"]
#
# [tool.orcaslicer.plugin]
# name = "Probe: pyclipper"
# description = "Checks whether OrcaSlicer installs pyclipper for a plugin and whether the polygon operations the wave generator needs work."
# author = "WaveOverHangsPlugin"
# version = "0.01"
# type = "script"
# ///
"""Dependency probe for step 3 (porting the wave generator).

Install through the Plugins dialog. If the install fails, OrcaSlicer shows
"Failed to install plugin dependencies: ..." -- that message is the answer.
If it loads, run it from the Plugins dialog: the result message lists each
operation the fork's generator relies on (WaveOverhangs.cpp) and a timing.
"""
import math
import platform
import sys
import time

import orca

SCALE = 1_000_000  # like OrcaSlicer: 1 mm = 1e6 units


def _circle(r_mm, n=128):
    return [(int(r_mm * SCALE * math.cos(2 * math.pi * i / n)),
             int(r_mm * SCALE * math.sin(2 * math.pi * i / n))) for i in range(n)]


def _area_mm2(paths, pyclipper):
    return sum(pyclipper.Area(p) for p in paths) / SCALE ** 2


def _checks():
    import pyclipper
    out = ["pyclipper %s" % getattr(pyclipper, "__version__", "?")]

    # offset with round joins (jtRound) -- a 10 mm square grown by 1 mm
    sq = [(0, 0), (10 * SCALE, 0), (10 * SCALE, 10 * SCALE), (0, 10 * SCALE)]
    co = pyclipper.PyclipperOffset()
    co.AddPath(sq, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    grown = co.Execute(1 * SCALE)
    out.append("round offset area %.2f (expect %.2f)" % (_area_mm2(grown, pyclipper), 100 + 40 + math.pi))

    # union of three overlapping discs, difference of a hole (union_ex / diff_ex)
    pc = pyclipper.Pyclipper()
    for dx in (0, 5, 10):
        pc.AddPath([(x + dx * SCALE, y) for x, y in _circle(5)], pyclipper.PT_SUBJECT, True)
    union = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    pc = pyclipper.Pyclipper()
    pc.AddPaths(union, pyclipper.PT_SUBJECT, True)
    pc.AddPath(_circle(2), pyclipper.PT_CLIP, True)
    tree = pc.Execute2(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    holes = sum(len(child.Childs) for child in tree.Childs)
    out.append("union+diff: %d island(s), %d hole(s)" % (len(tree.Childs), holes))

    # clip an open polyline by a polygon (intersection_pl)
    pc = pyclipper.Pyclipper()
    pc.AddPath([(-20 * SCALE, 0), (20 * SCALE, 0)], pyclipper.PT_SUBJECT, False)
    pc.AddPath(_circle(5), pyclipper.PT_CLIP, True)
    opened = pyclipper.OpenPathsFromPolyTree(
        pc.Execute2(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO))
    length = sum(math.dist(p[0], p[-1]) for p in opened) / SCALE
    out.append("polyline clip length %.2f (expect 10)" % length)

    # buffer an open polyline with round ends (etOpenRound)
    co = pyclipper.PyclipperOffset()
    co.AddPath([(0, 0), (10 * SCALE, 0)], pyclipper.JT_ROUND, pyclipper.ET_OPENROUND)
    out.append("open-path buffer area %.2f (expect %.2f)"
               % (_area_mm2(co.Execute(SCALE // 2), pyclipper), 10 + math.pi / 4))

    # timing: 200 concentric round offsets of a 50 mm disc, 0.2 mm apart (wave-like)
    t = time.perf_counter()
    base = _circle(25, 512)
    rings = 0
    for i in range(1, 201):
        co = pyclipper.PyclipperOffset()
        co.ArcTolerance = 0.01 * SCALE
        co.AddPath(base, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        rings += len(co.Execute(i * 0.2 * SCALE))
    out.append("200 round offsets: %.0f ms" % ((time.perf_counter() - t) * 1000))
    return out


class ProbePyclipper(orca.script.ScriptPluginCapabilityBase):
    def get_name(self):
        return "Probe: pyclipper"

    def execute(self):
        head = "Python %s on %s" % (sys.version.split()[0], platform.platform())
        try:
            return orca.ExecutionResult.success("OK -- " + "; ".join([head] + _checks()))
        except Exception as error:
            return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError,
                                                "FAILED -- %s; %s: %s" % (head, type(error).__name__, error))


@orca.plugin
class ProbePyclipperPackage(orca.base):
    def register_capabilities(self):
        orca.register_capability(ProbePyclipper)
