# /// script
# requires-python = ">=3.12"
# dependencies = ["shapely==2.2.0"]
#
# [tool.orcaslicer.plugin]
# name = "Probe: shapely"
# description = "Checks whether OrcaSlicer installs shapely for a plugin and whether the polygon operations the wave generator needs work."
# author = "WaveOverHangsPlugin"
# version = "0.01"
# type = "script"
# ///
# SPDX-License-Identifier: AGPL-3.0-or-later
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


def _checks():
    import shapely
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union
    out = ["shapely %s (GEOS %s)" % (shapely.__version__, getattr(shapely, "geos_version_string", "?"))]

    # offset with round joins (jtRound) -- a 10 mm square grown by 1 mm
    sq = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    out.append("round offset area %.2f (expect %.2f)"
               % (sq.buffer(1, join_style="round", quad_segs=32).area, 100 + 40 + math.pi))

    # union of three overlapping discs, difference of a hole (union_ex / diff_ex)
    shape = unary_union([Point(dx, 0).buffer(5, quad_segs=32) for dx in (0, 5, 10)]).difference(
        Point(0, 0).buffer(2, quad_segs=32))
    parts = list(getattr(shape, "geoms", [shape]))
    out.append("union+diff: %d island(s), %d hole(s)" % (len(parts), sum(len(p.interiors) for p in parts)))

    # clip an open polyline by a polygon (intersection_pl)
    out.append("polyline clip length %.2f (expect 10)"
               % LineString([(-20, 0), (20, 0)]).intersection(Point(0, 0).buffer(5, quad_segs=64)).length)

    # buffer an open polyline with round ends (etOpenRound)
    out.append("open-path buffer area %.2f (expect %.2f)"
               % (LineString([(0, 0), (10, 0)]).buffer(0.5, cap_style="round", quad_segs=32).area,
                  10 + math.pi / 4))

    # timing: 200 concentric round offsets of a 50 mm disc, 0.2 mm apart (wave-like)
    t = time.perf_counter()
    base = Point(0, 0).buffer(25, quad_segs=128)
    for i in range(1, 201):
        base.buffer(i * 0.2, join_style="round", quad_segs=16)
    out.append("200 round offsets: %.0f ms" % ((time.perf_counter() - t) * 1000))
    return out


class ProbeShapely(orca.script.ScriptPluginCapabilityBase):
    def get_name(self):
        return "Probe: shapely"

    def execute(self):
        head = "Python %s on %s" % (sys.version.split()[0], platform.platform())
        try:
            return orca.ExecutionResult.success("OK -- " + "; ".join([head] + _checks()))
        except Exception as error:
            return orca.ExecutionResult.failure(orca.PluginResult.RecoverableError,
                                                "FAILED -- %s; %s: %s" % (head, type(error).__name__, error))


@orca.plugin
class ProbeShapelyPackage(orca.base):
    def register_capabilities(self):
        orca.register_capability(ProbeShapely)
