# /// script
# requires-python = ">=3.12"
# dependencies = ["pyclipper", "numpy"]
#
# [tool.orcaslicer.plugin]
# name = "Wave Overhangs"
# description = "Prints overhangs as concentric waves grown from the supported edge (port of the OrcaSlicer-WaveOverhangs fork as a slicing-pipeline plugin)."
# author = "WaveOverHangsPlugin"
# version = "0.7.0"
# type = "slicing-pipeline"
# ///
"""Wave Overhangs -- slicing-pipeline plugin.

Wave overhangs algorithm: Janis A. Andersons (andersonsjanis).
Builds on the arc-overhang algorithm by Steven McCulloch (stmcculloch).
PrusaSlicer integration: Steven McCulloch.
Port to OrcaSlicer: Dennis Klappe (dennisklappe).
Python plugin port: WaveOverHangsPlugin.

Released under the terms of the AGPLv3 or higher (see LICENSE).

The generator below is a port of src/libslic3r/WaveOverhangs/WaveOverhangs.cpp of
https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs, using pyclipper (Clipper 6,
the library the original calls) on the same scaled integer coordinates.

How it works
------------
* Step.posSlice (per object): for every layer and island, the generator grows wave
  fronts from the supported edge over the unsupported area (rings of round
  offsets, clipped to the overhang). Every layer is computed from the slices as
  sliced, then the area the waves take over is cut out of the layer's slices.
  The slicer therefore prints no walls or infill there, and treats the layer
  above as hanging over air, so it prints a bridge floor on top of the waves.
* Step.posPerimeters (per object): a bounding box of each layer's walls is kept
  to measure the object -> plate offset later (it is not exposed to plugins).
* Step.psGCodePostProcess: the G-code is split per layer and per object copy (by the
  object labels the slicer writes with "Exclude objects" or "Label objects"), each
  copy is matched to its object by comparing wall boxes layer by layer, which also
  measures its offset on the plate, and the waves of each layer are inserted at the
  end of that copy's part of the layer, at the wave speed / flow / fan settings,
  restoring the slicer's state afterwards. Without labels only a plate with one
  object and one copy can be placed.

Differences from the fork (first version)
-----------------------------------------
* The walls kept along the overhang's edge (outer_walls, the fork's
  wave_overhang_outer_perimeters, default 1) are printed by the plugin right after
  the waves, along the island's edge where the slicer's own walls were cut away. The
  fork keeps the slicer's own wall paths instead. Walls are kept only along overhang
  that reaches past them plus two wave lines; the fork keeps them on every overhang,
  which leaves narrow (steep) overhangs with no waves at all.
* Every overhang gets waves (wave_overhangs_instead_of_bridges = on); the fork's
  bridgeability test needs the slicer's bridge detector, which plugins cannot call.
  Extra setting min_overhang_mm (default 0.5; 0 = the fork's behaviour): overhang that
  reaches no further than this beyond the layer below is left to the slicer's own walls,
  except at the root of an overhang that reaches further.
* Seeds follow Algorithm::wave_seeds (each anchoring contour expanded with square
  joins, clipped to the wave area); the generator's own fallback is kept as fallback.
* Waves are printed at the end of their copy's part of the layer, not before that
  island's walls.
* Floor layers, Hilbert floors, per-wave temperature and minimum wave/layer times
  are not implemented; the bridge the slicer prints above is the floor.
* Overhangs on the top layer get no waves (no following layer-change tag to
  anchor the insertion on). Works with wipe towers and by-object printing; when one
  object uses two filaments on one layer, its waves use the filament active at the
  end of that object's part of the layer.

A report is written as "; WAVE_OVERHANGS ..." comment lines after the first line
of the exported G-code.
"""
import json
import math
import os
import re
import threading
import time

import numpy as np
import orca
import pyclipper as pc

_DEFAULTS = {
    # Generator (names and defaults follow the fork's wave_overhang_* settings)
    "line_spacing_mm":          0.35,   # distance between wave fronts
    "perimeter_overlap_mm":     0.1,    # extend the wave area by this much
    "minimum_wave_width_mm":    0.7,    # split the wave area at necks narrower than this
    "min_new_area_mm2":         0.01,   # stop when a front adds less area than this
    "min_length_mm":            0.0,    # skip overhangs whose contour is shorter
    "max_iterations":           0,      # 0 = unlimited fronts per area
    "pattern":                  "smart",  # "smart", "monotonic" or "zigzag"
    "corner_taper_enable":      0,      # 1: denser fronts near sharp corners
    "line_spacing_corner_mm":   0.18,
    "corner_taper_distance_mm": 3.0,
    "corner_angle_threshold":   90.0,
    # Overhang areas that reach no further than this beyond the layer below keep the slicer's
    # own walls (gentle slopes). 0 = wave every overhang, as the fork does.
    "min_overhang_mm":          0.5,
    # Normal walls kept along the edge of wide overhangs, printed right after the waves and
    # against them (the fork's wave_overhang_outer_perimeters). Only where the overhang reaches
    # past the walls plus two wave lines; narrower (steep) overhangs are waved to the edge.
    # 0 = waves always run to the edge.
    "outer_walls":              1,
    # Speed of those walls; 0 = the slicer's speed for fully overhanging walls
    # (overhang_4_4_speed), or its bridge speed when that is not set.
    "wall_speed_mm_s":          0.0,
    # Printing
    "flow_mm3_per_mm":          0.15,   # extrusion of a wave line, independent of layer height
    "print_speed_mm_s":         2.0,
    "travel_speed_mm_s":        40.0,
    "fan_speed_percent":        100,
    # 0: measure and report only, change nothing (slices are left uncut too)
    "enabled":                  1,
    # "unsupported": objects with Orca's "Enable support" on are left alone, so the per-object
    # support toggle chooses between supports and waves. "all": wave every object.
    "apply_to":                 "unsupported",
    # 1: after an export, the notification offers "Show waves": a panel beside the 3D view that
    # draws the exported G-code's waves (the slicer's preview does not show them).
    "preview":                  1,
}
_PATTERNS = ("smart", "monotonic", "zigzag")
_CHOICES = {"pattern": _PATTERNS, "apply_to": ("unsupported", "all")}

_WALL_ROLES = ("Outer wall", "Inner wall", "Overhang wall")
_TAG = "; WAVE_OVERHANGS"

# Hand-over store, object id -> capture (plain values only). Kept at module level and
# on the capability instance; the spike verified both survive until post-processing.
_STATE = {}

# Data for the preview panel from the last export that added waves: {"data": extract_preview(),
# "title", "fresh"}. "fresh" is true until the next notification has offered it. Module global and
# capability instance, like the captures.
_PREVIEW = None

# Results shown as an OrcaSlicer notification when the slicing job completes. execute() runs on
# the slicing worker, where UI calls can deadlock; on_lifecycle_event runs on the UI thread.
_NOTES = []
_NOTES_LOCK = threading.Lock()
_NOTE_LINES = 8
_LOG_NAME = "wave_overhangs_log.jsonl"


_NOTE_MAX_AGE_S = 600


def _note(text, level="info"):
    """Queue one line for the next notification; level "info", "slice" (info about a slice)
    or "warning"."""
    with _NOTES_LOCK:
        _NOTES.append((text, level, time.time()))


def _take_notes():
    """The queued lines, oldest first, without those older than _NOTE_MAX_AGE_S (left by a
    step that ran outside a slicing job, such as saving a project with its G-code)."""
    now = time.time()
    with _NOTES_LOCK:
        notes = [(text, level) for text, level, t in _NOTES if now - t <= _NOTE_MAX_AGE_S]
        _NOTES.clear()
    return notes


def notification_text(notes):
    lines = [text for text, _ in notes[:_NOTE_LINES]]
    rest = len(notes) - _NOTE_LINES
    if rest > 0:
        lines.append("... and %d more (see %s next to the plugin)" % (rest, _LOG_NAME))
    return "\n".join(lines)


def notification_level(notes):
    """Warnings stay until closed; otherwise Important (20 s; Regular fades after 10 s)."""
    if any(level == "warning" for _, level in notes):
        return "WarningNotificationLevel"
    return "ImportantNotificationLevel"


def _minutes_text(minutes):
    return "under a minute" if minutes < 0.5 else "about %d min" % int(round(minutes))


def _write_log(entry):
    """Append one JSON line to the log next to the plugin. Best effort, never fails a slice."""
    try:
        entry = dict(entry, time=time.strftime("%Y-%m-%d %H:%M:%S"), version=_VERSION)
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _LOG_NAME)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


# ============================================================================
# small helpers
# ============================================================================

def _params(self):
    try:
        src = json.loads(self.get_config())
    except (AttributeError, TypeError, ValueError):
        src = {}
    if not isinstance(src, dict):
        src = {}
    out = {}
    for key, default in _DEFAULTS.items():
        if isinstance(default, str):
            value = str(src.get(key, default)).strip().lower()
            out[key] = value if value in _CHOICES.get(key, ()) else default
            continue
        try:
            out[key] = float(src[key])
        except (KeyError, TypeError, ValueError):
            out[key] = float(default)
    return out


def _first_float(value, default):
    """First number of a serialized config value such as "0.4,0.4" or "1.75"."""
    if value is None:
        return default
    try:
        v = float(str(value).replace(";", ",").split(",")[0].strip().rstrip("%"))
    except ValueError:
        return default
    return v if v > 0.0 else default


def _width_mm(value, nozzle, auto):
    """A line width setting: mm, % of the nozzle, or 0 for the slicer's automatic width."""
    if value is None:
        return auto
    text = str(value).replace(";", ",").split(",")[0].strip()
    try:
        if text.endswith("%"):
            v = float(text[:-1]) * nozzle / 100.0
        else:
            v = float(text)
    except ValueError:
        return auto
    return v if v > 0.0 else auto


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _zkey(z):
    return round(float(z), 3)


def _median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _fmt(v):
    s = "%.3f" % v
    return s.rstrip("0").rstrip(".") if "." in s else s


# ============================================================================
# Clipper layer (scaled integer coordinates, Orca's ClipperUtils semantics)
#
# Polygons: flat list of closed paths, outer contours CCW and holes CW.
# ExPolygon: (contour, [holes]).  Polylines: list of open paths.
# ============================================================================

JT_ROUND, JT_MITER, JT_SQUARE = pc.JT_ROUND, pc.JT_MITER, pc.JT_SQUARE


def _valid(paths, closed=True):
    need = 3 if closed else 2
    return [p for p in paths if len(p) >= need]


_SHORTEST_EDGE_FACTOR = 0.005  # Orca's ClipperOffsetShortestEdgeFactor


def _strip_short_edges(path, min_len, closed):
    """Orca's patched ClipperOffset::AddPath: drop vertices closer than min_len to the last
    kept one (and, for closed paths, trailing vertices close to the first). Without this,
    every round-join offset at least doubles the vertex count of the previous result."""
    if min_len <= 0 or len(path) < 2:
        return path
    lim2 = min_len * min_len
    x0, y0 = path[0]
    high = len(path) - 1
    if closed:
        while high > 0 and (path[high][0] - x0) ** 2 + (path[high][1] - y0) ** 2 < lim2:
            high -= 1
    out = [path[0]]
    lx, ly = x0, y0
    for i in range(1, high + 1):
        x, y = path[i]
        if (x - lx) ** 2 + (y - ly) ** 2 < lim2:
            continue
        out.append(path[i])
        lx, ly = x, y
    return out


def offset(paths, delta, join=JT_MITER, limit=3.0, end=pc.ET_CLOSEDPOLYGON):
    """Orca offset()/expand()/shrink(). Defaults are Orca's (jtMiter, miter limit 3)."""
    closed = end == pc.ET_CLOSEDPOLYGON
    min_len = abs(delta) * _SHORTEST_EDGE_FACTOR
    paths = _valid([_strip_short_edges(p, min_len, closed) for p in paths], closed)
    if not paths:
        return []
    co = pc.PyclipperOffset()
    if join == JT_ROUND:
        co.ArcTolerance = limit  # 0 -> Clipper's default tolerance, as in Orca
    else:
        co.MiterLimit = limit
    try:
        co.AddPaths(paths, join, end)
    except pc.ClipperException:
        return []
    return co.Execute(float(delta))


def expand(paths, delta, join=JT_MITER, limit=3.0):
    return offset(paths, abs(delta), join, limit)


def shrink(paths, delta, join=JT_MITER, limit=3.0):
    return offset(paths, -abs(delta), join, limit)


def offset_pl(polylines, delta, join=JT_ROUND, limit=0.0):
    """Orca offset(Polylines, ..., etOpenRound)."""
    return offset(polylines, delta, join, limit, pc.ET_OPENROUND)


def _boolean(op, subject, clip, tree=False):
    subject = _valid(subject)
    clip = _valid(clip)
    if not subject:
        return []
    c = pc.Pyclipper()
    try:
        c.AddPaths(subject, pc.PT_SUBJECT, True)
    except pc.ClipperException:
        return []
    if clip:
        try:
            c.AddPaths(clip, pc.PT_CLIP, True)
        except pc.ClipperException:
            pass
    if tree:
        return _tree_to_expolys(c.Execute2(op, pc.PFT_NONZERO, pc.PFT_NONZERO))
    return c.Execute(op, pc.PFT_NONZERO, pc.PFT_NONZERO)


def union(paths):
    return _boolean(pc.CT_UNION, paths, [])


def union_ex(paths):
    return _boolean(pc.CT_UNION, paths, [], tree=True)


def diff(a, b):
    return _boolean(pc.CT_DIFFERENCE, a, b) if b else union(a)


def diff_ex(a, b):
    return _boolean(pc.CT_DIFFERENCE, a, b, tree=True)


def intersection(a, b):
    return _boolean(pc.CT_INTERSECTION, a, b) if b else []


def intersection_pl(polylines, clip):
    """Clip open polylines by closed polygons (Orca intersection_pl)."""
    polylines = _valid(polylines, closed=False)
    clip = _valid(clip)
    if not polylines or not clip:
        return []
    c = pc.Pyclipper()
    added = False
    for pl in polylines:
        try:
            c.AddPath(pl, pc.PT_SUBJECT, False)
            added = True
        except pc.ClipperException:
            pass
    if not added:
        return []
    try:
        c.AddPaths(clip, pc.PT_CLIP, True)
    except pc.ClipperException:
        return []
    return [list(map(tuple, p)) for p in
            pc.OpenPathsFromPolyTree(c.Execute2(pc.CT_INTERSECTION, pc.PFT_NONZERO, pc.PFT_NONZERO))]


def _tree_to_expolys(tree):
    out = []

    def walk(node):
        for outer in node.Childs:
            out.append((outer.Contour, [h.Contour for h in outer.Childs]))
            for hole in outer.Childs:
                walk(hole)
    walk(tree)
    return out


def to_polygons(expolys):
    out = []
    for contour, holes in expolys:
        out.append(contour)
        out.extend(holes)
    return out


def to_polylines(polygons):
    return [list(p) + [p[0]] for p in polygons if len(p) >= 2]


def area(polygons):
    return sum(pc.Area(p) for p in polygons)


def opening_ex(expolys, delta, join=JT_MITER, limit=3.0):
    return union_ex(offset(offset(to_polygons(expolys), -delta, join, limit), delta, join, limit))


def closing_ex(polygons, delta, join=JT_MITER, limit=3.0):
    return union_ex(offset(offset(polygons, delta, join, limit), -delta, join, limit))


def offset_ex(expolys, delta, join=JT_MITER, limit=3.0):
    return union_ex(offset(to_polygons(expolys), delta, join, limit))


def polyline_length(pl):
    return sum(math.dist(pl[i - 1], pl[i]) for i in range(1, len(pl)))


def contour_length(poly):
    return polyline_length(list(poly) + [poly[0]]) if len(poly) >= 2 else 0.0


def expoly_contains(expoly, pt):
    contour, holes = expoly
    if pc.PointInPolygon(pt, contour) != 1:
        return False
    return not any(pc.PointInPolygon(pt, h) == 1 for h in holes)


def _segments(polys):
    """(starts, deltas) of the closed boundary segments of polygons, as float arrays."""
    a, b = [], []
    for p in polys:
        if len(p) < 2:
            continue
        arr = np.asarray(p, dtype=float)
        a.append(arr)
        b.append(np.roll(arr, -1, axis=0))
    if not a:
        return np.zeros((0, 2)), np.zeros((0, 2))
    a = np.concatenate(a)
    return a, np.concatenate(b) - a


def _project_points(points, seg_a, seg_d):
    """Closest boundary point (projection) for each of `points` -> (proj (n,2), dist2 (n,))."""
    p = np.asarray(points, dtype=float)
    l2 = (seg_d * seg_d).sum(axis=1)
    l2s = np.where(l2 > 0, l2, 1.0)
    # (n, m)
    rel_x = p[:, None, 0] - seg_a[None, :, 0]
    rel_y = p[:, None, 1] - seg_a[None, :, 1]
    t = np.clip((rel_x * seg_d[None, :, 0] + rel_y * seg_d[None, :, 1]) / l2s[None, :], 0.0, 1.0)
    t = np.where(l2[None, :] > 0, t, 0.0)
    fx = seg_a[None, :, 0] + t * seg_d[None, :, 0]
    fy = seg_a[None, :, 1] + t * seg_d[None, :, 1]
    d2 = (fx - p[:, None, 0]) ** 2 + (fy - p[:, None, 1]) ** 2
    j = d2.argmin(axis=1)
    rows = np.arange(len(p))
    return np.stack([fx[rows, j], fy[rows, j]], axis=1), d2[rows, j]


def douglas_peucker(pts, tol):
    """Orca Polyline::simplify (Douglas-Peucker against segments)."""
    n = len(pts)
    if n < 3 or tol <= 0:
        return list(pts)
    arr = np.asarray(pts, dtype=float)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    tol2 = tol * tol
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 - i0 < 2:
            continue
        a = arr[i0]
        d = arr[i1] - a
        seg = arr[i0 + 1:i1] - a
        l2 = float(d @ d)
        if l2 > 0:
            t = np.clip(seg @ d / l2, 0.0, 1.0)
            dist2 = ((seg - t[:, None] * d) ** 2).sum(axis=1)
        else:
            dist2 = (seg * seg).sum(axis=1)
        k = int(dist2.argmax())
        if dist2[k] > tol2:
            idx = i0 + 1 + k
            keep[idx] = True
            stack.append((i0, idx))
            stack.append((idx, i1))
    return [pts[i] for i in range(n) if keep[i]]


# ============================================================================
# The generator (port of WaveOverhangs.cpp)
# ============================================================================

def painted_overhang_zone(island, lower, filled_area, sliver_radius):
    """Geometric overhang of an island (island - lower), restricted to the connected
    pieces the wave painted; thin slivers are cut back near the piece's body."""
    zone = []
    painted = to_polygons(union_ex(filled_area))
    for piece in union_ex(diff(island, lower)):
        piece_polys = to_polygons([piece])
        if not intersection(piece_polys, painted):
            continue
        if sliver_radius > 0:
            body = opening_ex([piece], sliver_radius)
            if body:
                zone += intersection(piece_polys, expand(to_polygons(body), 2 * sliver_radius, JT_ROUND, 0.0))
                continue
        zone += piece_polys
    return zone


def reconnect_polylines(polylines, limit_distance):
    """Greedily chain polylines whose end points are closer than limit_distance."""
    chains = [list(p) for p in polylines]
    alive = [len(c) > 0 for c in chains]
    limit_sq = float(limit_distance) * float(limit_distance)

    def close(p1, p2):
        return (p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2 < limit_sq

    changed = True
    while changed:
        changed = False
        for a in range(len(chains)):
            if not alive[a]:
                continue
            for b in range(a + 1, len(chains)):
                if not alive[b]:
                    continue
                base, nxt = chains[a], chains[b]
                if close(base[-1], nxt[0]):
                    base.extend(nxt)
                elif close(base[-1], nxt[-1]):
                    base.extend(reversed(nxt))
                elif close(base[0], nxt[-1]):
                    chains[a] = nxt + base
                elif close(base[0], nxt[0]):
                    chains[a] = list(reversed(nxt)) + base
                else:
                    continue
                alive[b] = False
                changed = True
    return [chains[i] for i in range(len(chains)) if alive[i]]


def detect_overhang_corners(polys, angle_threshold_rad):
    corners = []
    for poly in polys:
        n = len(poly)
        if n < 3:
            continue
        is_ccw = pc.Orientation(poly)
        for i in range(n):
            ax, ay = poly[(i + n - 1) % n]
            bx, by = poly[i]
            cx, cy = poly[(i + 1) % n]
            v1 = (bx - ax, by - ay)
            v2 = (cx - bx, cy - by)
            if v1[0] ** 2 + v1[1] ** 2 < 1 or v2[0] ** 2 + v2[1] ** 2 < 1:
                continue
            turn = math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1])
            convex = turn > 0 if is_ccw else turn < 0
            if convex and math.pi - abs(turn) < angle_threshold_rad:
                corners.append((bx, by))
    return corners


def build_corner_influence(corners, radius):
    if not corners or radius <= 0:
        return []
    disks = []
    for cx, cy in corners:
        disks.append([(cx + int(math.cos(2 * math.pi * k / 16) * radius),
                       cy + int(math.sin(2 * math.pi * k / 16) * radius)) for k in range(16)])
    return union(disks)


def _find_closest_boundary_pair(a, b, container):
    """Closest pair of boundary points between expolygons a and b whose midpoint lies in
    `container`. Returns (pa, pb, dist2) or None."""
    best = None
    for src, other, src_is_a in ((a, b, True), (b, a, False)):
        pts = to_polygons([src])
        pts = [pt for poly in pts for pt in poly]
        if not pts:
            continue
        seg_a, seg_d = _segments(to_polygons([other]))
        if len(seg_a) == 0:
            continue
        proj, d2 = _project_points(pts, seg_a, seg_d)
        for i in np.argsort(d2, kind="stable"):
            if best is not None and d2[i] >= best[2]:
                break
            s = pts[i]
            q = (int(proj[i][0]), int(proj[i][1]))
            mid = (int(0.5 * (s[0] + q[0])), int(0.5 * (s[1] + q[1])))
            if not expoly_contains(container, mid):
                continue
            best = (s, q, float(d2[i])) if src_is_a else (q, s, float(d2[i]))
            break
    return best


def _make_split_slit(a, b, extension, half_width):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        return []
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    sx, sy = a[0] - ux * extension, a[1] - uy * extension
    ex, ey = b[0] + ux * extension, b[1] + uy * extension
    ox, oy = nx * max(1, half_width), ny * max(1, half_width)
    return [(int(sx + ox), int(sy + oy)), (int(ex + ox), int(ey + oy)),
            (int(ex - ox), int(ey - oy)), (int(sx - ox), int(sy - oy))]


def _slit_changes_topology(wave_cover, slit):
    if len(slit) < 3:
        return False
    result = union_ex(to_polygons(diff_ex(to_polygons([wave_cover]), [slit])))
    return len(result) != 1 or sum(len(h) for _, h in result) != len(wave_cover[1])


def _make_effective_split_slit(wave_cover, a, b, extension, wave_spacing):
    half_width = wave_spacing // 2 + 1
    for _ in range(6):
        slit = _make_split_slit(a, b, extension, half_width)
        if _slit_changes_topology(wave_cover, slit):
            return slit
        half_width = max(half_width + 1, half_width * 2)
    return []


def generate_narrow_split_slits(wave_cover, wave_spacing, minimum_wave_width):
    if minimum_wave_width <= 0:
        return []
    max_gap_sq = float(minimum_wave_width) ** 2
    slit_half_width = max(1, wave_spacing // 20)
    slit_extension = max(slit_half_width, minimum_wave_width)
    duplicate_radius_sq = (0.5 * wave_spacing) ** 2
    original_holes = len(wave_cover[1])
    candidates = []

    def add(pair):
        if pair is None or pair[2] > max_gap_sq:
            return
        slit = _make_effective_split_slit(wave_cover, pair[0], pair[1], wave_spacing + slit_extension, wave_spacing)
        if len(slit) < 3:
            return
        mid = (0.5 * (pair[0][0] + pair[1][0]), 0.5 * (pair[0][1] + pair[1][1]))
        candidates.append((pair[2], mid, slit))

    for fraction in (0.25, 0.5, 0.75, 1.0):
        depth = max(1, int(round(fraction * wave_spacing)))
        comps = offset_ex([wave_cover], -depth, JT_ROUND, 0.0)
        count_changed = len(comps) > 1
        holes_changed = sum(len(h) for _, h in comps) != original_holes
        if not count_changed and not holes_changed:
            continue
        if count_changed:
            for i in range(len(comps)):
                for j in range(i + 1, len(comps)):
                    add(_find_closest_boundary_pair(comps[i], comps[j], wave_cover))
        if holes_changed and wave_cover[1]:
            outer = (wave_cover[0], [])
            holes = wave_cover[1]
            for hi in range(len(holes)):
                hole = (holes[hi], [])
                add(_find_closest_boundary_pair(outer, hole, wave_cover))
                for hj in range(hi + 1, len(holes)):
                    add(_find_closest_boundary_pair(hole, (holes[hj], []), wave_cover))

    if not candidates:
        return []
    candidates.sort(key=lambda c: c[0])
    slits, kept = [], []
    for _, mid, slit in candidates:
        if any((mid[0] - m[0]) ** 2 + (mid[1] - m[1]) ** 2 <= duplicate_radius_sq for m in kept):
            continue
        kept.append(mid)
        slits.append(slit)
    return union(slits) if slits else []


def generate_wave_overhang_seeds(boundary, anchoring, seed_expansion):
    """Seed polylines: the contour of the anchoring, expanded by seed_expansion, where it
    runs inside the wave area. This is what Algorithm::wave_seeds() returns (it only adds
    Z-tracking to know which source/boundary each piece came from, which a single
    boundary does not need). The fork's fallback (the area's boundary inside the expanded
    anchoring) is used only if this finds nothing, as in the original."""
    if not anchoring:
        return []
    boundary_polys = to_polygons([boundary])
    # expolygons_to_zpaths_expanded_opened(): each contour on its own, jtSquare, holes by
    # the negated amount (so the solid grows), results not merged.
    expanded = []
    for contour, holes in union_ex(anchoring):
        expanded += offset([contour], seed_expansion, JT_SQUARE, 0.0)
        for hole in holes:
            expanded += offset([hole], -seed_expansion, JT_SQUARE, 0.0)
    seeds = intersection_pl(to_polylines(expanded), boundary_polys)
    if not seeds:
        seeds = intersection_pl(to_polylines(boundary_polys), offset(anchoring, seed_expansion, JT_ROUND, 0.0))
    return seeds


class _SupportIndex:
    """Segments of the wave paths laid so far, for the "smart" direction choice."""

    def __init__(self):
        self.a = np.zeros((0, 2))
        self.d = np.zeros((0, 2))
        self.owner = np.zeros(0, dtype=np.int64)
        self.count = 0

    def add(self, polyline):
        arr = np.asarray(polyline, dtype=float)
        if len(arr) < 2:
            return
        self.a = np.concatenate([self.a, arr[:-1]])
        self.d = np.concatenate([self.d, arr[1:] - arr[:-1]])
        self.owner = np.concatenate([self.owner, np.full(len(arr) - 1, self.count, dtype=np.int64)])
        self.count += 1

    def score(self, candidate, support_reach, prefix_length):
        if self.count == 0 or len(candidate) < 2:
            return -1.0
        cand_len = polyline_length(candidate)
        if cand_len <= 0:
            return -1.0
        sample_len = min(cand_len, float(max(1, prefix_length)))
        l2 = (self.d * self.d).sum(axis=1)
        l2s = np.where(l2 > 0, l2, 1.0)
        starts = np.flatnonzero(np.r_[True, self.owner[1:] != self.owner[:-1]])
        total = np.zeros(len(starts))
        for dist_along, weight in ((0.0, 3.0), (0.5 * sample_len, 2.0), (sample_len, 1.0)):
            s = np.asarray(_point_at_distance(candidate, dist_along), dtype=float)
            rel = s - self.a
            t = np.clip((rel * self.d).sum(axis=1) / l2s, 0.0, 1.0)
            t = np.where(l2 > 0, t, 0.0)
            foot = self.a + t[:, None] * self.d
            d2 = ((foot - s) ** 2).sum(axis=1)
            mins = np.minimum.reduceat(d2, starts)
            is_min = d2 == np.repeat(mins, np.diff(np.r_[starts, len(d2)]))
            idx = np.flatnonzero(is_min)
            _, first = np.unique(self.owner[idx], return_index=True)
            best = idx[first]  # first closest segment per path, as foot_pt does
            foot_r = np.round(foot[best])
            a = np.round(self.a[best])
            b = np.round(self.a[best] + self.d[best])
            interior = ~(np.all(foot_r == a, axis=1) | np.all(foot_r == b, axis=1))
            dist = np.sqrt(mins)
            normalized = np.maximum(0.0, 1.0 - dist / float(max(1, support_reach)))
            total += weight * (3.0 * normalized + np.where(interior, 1.5, 0.2))
        return float(total.max())


def _point_at_distance(line, distance):
    if not line:
        return (0, 0)
    if distance <= 0 or len(line) == 1:
        return line[0]
    walked = 0.0
    for i in range(1, len(line)):
        ax, ay = line[i - 1]
        bx, by = line[i]
        seg = math.hypot(bx - ax, by - ay)
        if seg <= 0:
            continue
        if walked + seg >= distance:
            t = (distance - walked) / seg
            return (int(ax + t * (bx - ax)), int(ay + t * (by - ay)))
        walked += seg
    return line[-1]


def append_wave_fronts(out, fronts, wave_width, connector_limit, pattern):
    """"smart" / "monotonic" ordering (append_wave_fronts)."""
    if not fronts:
        return
    if pattern == "monotonic":
        out.extend(list(f) for f in fronts if len(f) >= 2)
        return
    support = _SupportIndex()
    for path in out:
        support.add(path)
    reach = max(wave_width, connector_limit)
    prefix = max(wave_width, connector_limit // 2)
    for source in fronts:
        front = list(source)
        if len(front) < 2:
            continue
        reverse = front[::-1]
        if support.score(reverse, reach, prefix) > support.score(front, reach, prefix):
            front = reverse
        out.append(front)
        support.add(front)


def append_zig_zag_front_levels(out, levels, connector_limit):
    if not levels:
        return
    used = [[False] * len(level) for level in levels]
    max_d2 = float(connector_limit) ** 2

    def d2(p, q):
        return float(p[0] - q[0]) ** 2 + float(p[1] - q[1]) ** 2

    def append_or_start(front):
        if not out:
            out.append(front)
            return
        cur = out[-1]
        d_keep, d_flip = d2(cur[-1], front[0]), d2(cur[-1], front[-1])
        if min(d_keep, d_flip) > max_d2:
            out.append(front)
            return
        if d_flip < d_keep:
            front = front[::-1]
        if tuple(cur[-1]) == tuple(front[0]):
            cur.extend(front[1:])
        else:
            cur.extend(front)

    for li in range(len(levels)):
        for fi in range(len(levels[li])):
            if used[li][fi]:
                continue
            # follow_branch, iteratively
            level_idx, front_idx, rev = li, fi, False
            while True:
                used[level_idx][front_idx] = True
                cur = list(levels[level_idx][front_idx])
                if len(cur) < 2:
                    break
                if rev:
                    cur.reverse()
                append_or_start(cur)
                nxt = None
                for next_level in range(level_idx + 1, len(levels)):
                    best_idx, best_rev, best_d = -1, False, max_d2
                    anchor = out[-1][-1]
                    for ci, cand in enumerate(levels[next_level]):
                        if used[next_level][ci] or len(cand) < 2:
                            continue
                        dk = d2(anchor, cand[0])
                        if dk <= best_d:
                            best_d, best_idx, best_rev = dk, ci, False
                        df = d2(anchor, cand[-1])
                        if df <= best_d:
                            best_d, best_idx, best_rev = df, ci, True
                    if best_idx < 0 or best_d > max_d2:
                        break
                    nxt = (next_level, best_idx, best_rev)
                    break
                if nxt is None:
                    break
                level_idx, front_idx, rev = nxt


def generate(infill_area, lower, P):
    """Port of WaveOverhangs::generate(). `infill_area` is a list of ExPolygons, `lower` the
    lower layer's polygons (grown by half a nozzle, as the fork passes them). All values in
    P are scaled units except where noted. Returns (paths per overhang region, residual)."""
    if not infill_area:
        return [], []
    base_spacing = P["base_spacing"]
    wave_width = P["wave_width"]
    perimeter_overlap = max(0, P["perimeter_overlap"])
    wave_spacing = max(1, P["wave_spacing"] or base_spacing)
    min_wave_width = max(0, P["minimum_wave_width"])

    taper_corner = P["wave_spacing_corner"]
    taper_enabled = P["corner_taper_enable"] and 0 < taper_corner < wave_spacing and P["corner_taper_distance"] > 0
    taper_substeps = min(8, max(2, int(round(wave_spacing / taper_corner)))) if taper_enabled else 1
    taper_step = max(taper_corner, wave_spacing // taper_substeps) if taper_enabled else wave_spacing
    corner_angle = max(10.0, min(179.0, P["corner_angle_threshold"])) * math.pi / 180.0

    anchors_size = min(P["external_infill_margin"], base_spacing * (P["perimeter_count"] + 1))
    seed_expansion = max(1, base_spacing // 10)
    regularization = max(1, base_spacing // 2)
    connector_limit = max(wave_spacing, wave_width) + perimeter_overlap
    min_area_growth = P["min_new_area"] if P["min_new_area"] > 0 else 0.05 * wave_spacing * wave_spacing
    simplify_tol = min(0.05 * wave_spacing, P["scaled_resolution"])

    infill_polys = to_polygons(infill_area)
    overhangs = diff(infill_polys, lower)
    if not overhangs:
        return [], []
    anchors = intersection(infill_polys, lower)
    inset_anchors = diff(anchors, offset(overhangs, anchors_size + 0.1 * P["overhang_width"], JT_SQUARE, 0.0))
    inset_overhang_area = diff(infill_polys, inset_anchors)

    wave_paths = []
    filled_area = []
    for overhang in union_ex(inset_overhang_area):
        if P["min_length"] > 0 and contour_length(overhang[0]) < P["min_length"]:
            continue
        to_cover = to_polygons([overhang])
        wave_cover_area = expand(to_cover, perimeter_overlap, JT_ROUND, 0.0) if perimeter_overlap > 0 else to_cover

        corner_influence = []
        if taper_enabled:
            corners = detect_overhang_corners(to_cover, corner_angle)
            if corners:
                corner_influence = build_corner_influence(corners, P["corner_taper_distance"])
        if not intersection(wave_cover_area, overhangs):
            wave_cover_area = []
        # Bridgeable spans are waved too (use_instead_of_bridges): see the module docstring.

        region_paths = []
        filled_region = []
        for wave_cover in union_ex(wave_cover_area):
            split_covers = [wave_cover]
            slits = generate_narrow_split_slits(wave_cover, wave_spacing, min_wave_width)
            if slits:
                split_covers = union_ex(to_polygons(diff_ex(to_polygons([wave_cover]), slits)))

            full_cover = to_polygons([wave_cover])
            grown = expand(full_cover, 1.1 * base_spacing, JT_ROUND, 0.0)
            anchoring = (intersection(grown, inset_anchors) or intersection(grown, anchors)
                         or intersection(grown, lower))
            base_seeds = generate_wave_overhang_seeds(wave_cover, anchoring, seed_expansion)

            for split in split_covers:
                cover = to_polygons([split])
                seeds = intersection_pl(base_seeds, cover) if base_seeds else []
                if not seeds:
                    continue
                trim = (shrink(cover, max(1, wave_width // 2), JT_ROUND, 0.0)
                        or shrink(cover, 0.1 * base_spacing) or cover)
                accumulated = intersection(offset_pl(seeds, seed_expansion), cover)
                if not accumulated:
                    continue

                levels = []
                acc_area = area(accumulated)
                iteration = 0
                taper_active = taper_enabled and corner_influence and intersection(corner_influence, cover)
                while True:
                    if P["max_iterations"] > 0 and iteration >= P["max_iterations"]:
                        break
                    iteration += 1
                    if taper_active:
                        for sub in range(1, taper_substeps):
                            sub_region = intersection(expand(accumulated, sub * taper_step, JT_ROUND, 0.0), cover)
                            if not sub_region:
                                continue
                            sub_fronts = intersection_pl(to_polylines(sub_region), trim)
                            sub_fronts = intersection_pl(sub_fronts, corner_influence)
                            sub_fronts = [douglas_peucker(f, min(0.05 * taper_step, P["scaled_resolution"]))
                                          for f in sub_fronts]
                            sub_fronts = reconnect_polylines([f for f in sub_fronts if len(f) >= 2], taper_step)
                            if sub_fronts:
                                levels.append(sub_fronts)

                    next_region = intersection(expand(accumulated, wave_spacing, JT_ROUND, 0.0), cover)
                    if not next_region:
                        break
                    next_area = area(next_region)
                    if next_area <= acc_area + min_area_growth:
                        break
                    fronts = intersection_pl(to_polylines(next_region), trim)
                    fronts = [douglas_peucker(f, simplify_tol) for f in fronts]
                    fronts = reconnect_polylines([f for f in fronts if len(f) >= 2], wave_spacing)
                    if fronts:
                        levels.append(fronts)
                    accumulated = next_region
                    acc_area = next_area

                if levels:
                    split_paths = []
                    if P["pattern"] == "zigzag":
                        append_zig_zag_front_levels(split_paths, levels, connector_limit)
                    else:
                        append_wave_fronts(split_paths, [f for level in levels for f in level],
                                           wave_width, connector_limit, P["pattern"])
                    if split_paths:
                        region_paths += split_paths
                        filled_region += accumulated

        region_paths = [p for p in region_paths if len(p) >= 2]
        filled_region = union(filled_region)
        filled_area += filled_region
        if region_paths:
            wave_paths.append(region_paths)

    residual = offset(to_polygons(closing_ex(filled_area, regularization, JT_ROUND, 0.0)), 10.0)
    return wave_paths, residual


# ============================================================================
# slice time: generate waves, cut their area out of the slices
# ============================================================================

def _poly_from_host(poly):
    try:
        return [tuple(map(int, row)) for row in poly.as_array().tolist()]
    except Exception:
        return [(int(pt.x), int(pt.y)) for pt in poly.points]


def _expoly_from_host(ex):
    return (_poly_from_host(ex.contour), [_poly_from_host(h) for h in ex.holes])


def _host_expoly(expoly):
    contour, holes = expoly
    return orca.host.ExPolygon(np.asarray(contour, dtype=np.int64),
                               [np.asarray(h, dtype=np.int64) for h in holes])


def _gen_params(ctx, p, k):
    """Generator parameters in scaled units (k = mm per scaled unit)."""
    def sc(mm):
        return int(round(mm / k))
    nozzle = _first_float(ctx.config_value("nozzle_diameter"), 0.4)
    inner_w = _width_mm(ctx.config_value("inner_wall_line_width"), nozzle,
                        _width_mm(ctx.config_value("line_width"), nozzle, 1.125 * nozzle))
    thick = _truthy(ctx.config_value("thick_bridges"))
    bridge_flow = _first_float(ctx.config_value("bridge_flow"), 1.0)
    walls = int(_first_float(ctx.config_value("wall_loops"), 2))
    outer_w = _width_mm(ctx.config_value("outer_wall_line_width"), nozzle,
                        _width_mm(ctx.config_value("line_width"), nozzle, 1.125 * nozzle))
    return {
        "nozzle_mm": nozzle,
        "inner_width_mm": inner_w,
        "outer_width_mm": outer_w,
        "wall_loops": max(0, walls),
        "thick_bridges": thick,
        "bridge_dmr_mm": math.sqrt(bridge_flow) * nozzle,
        "perimeter_count": max(1, walls),
        "wave_width": sc(nozzle),  # the fork always uses the nozzle diameter
        "wave_spacing": sc(p["line_spacing_mm"]) if p["line_spacing_mm"] > 0 else 0,
        "perimeter_overlap": sc(p["perimeter_overlap_mm"]) if p["perimeter_overlap_mm"] > 0 else 0,
        "minimum_wave_width": sc(p["minimum_wave_width_mm"]) if p["minimum_wave_width_mm"] > 0 else 0,
        "min_new_area": p["min_new_area_mm2"] / (k * k) if p["min_new_area_mm2"] > 0 else 0.0,
        "min_length": sc(p["min_length_mm"]) if p["min_length_mm"] > 0 else 0,
        "max_iterations": int(p["max_iterations"]),
        "pattern": p["pattern"],
        "corner_taper_enable": p["corner_taper_enable"] != 0,
        "wave_spacing_corner": sc(p["line_spacing_corner_mm"]) if p["line_spacing_corner_mm"] > 0 else 0,
        "corner_taper_distance": sc(p["corner_taper_distance_mm"]) if p["corner_taper_distance_mm"] > 0 else 0,
        "corner_angle_threshold": p["corner_angle_threshold"],
        "external_infill_margin": sc(3.0),
        "scaled_resolution": sc(_first_float(ctx.config_value("resolution"), 0.012)),
    }


def _layer_flow(G, height_mm, k):
    """overhang_flow width/spacing and the perimeter spacing for one layer (scaled)."""
    def sc(mm):
        return int(round(mm / k))
    perim_spacing = G["inner_width_mm"] - height_mm * (1.0 - 0.25 * math.pi)
    if G["thick_bridges"]:
        ov_w, ov_s = G["bridge_dmr_mm"], G["bridge_dmr_mm"] + 0.05
    else:
        ov_w, ov_s = G["inner_width_mm"], perim_spacing
    return sc(ov_w), sc(ov_s), sc(perim_spacing)


def narrow_overhangs(island_polys, lower_raw, reach, min_area):
    """The part of an island's overhang to leave to the slicer as a gentle slope.

    The overhang (island minus the layer below) that lies within `reach` of the lower edge,
    except where it is within `reach` of overhang that reaches further: there it is the
    root of a shelf, which the waves of that shelf need as their anchor band.
    """
    if not lower_raw:
        return []
    overhang = diff(island_polys, lower_raw)
    if not overhang:
        return []
    far = diff(overhang, expand(lower_raw, reach, JT_ROUND, 0.0))
    narrow = diff(overhang, expand(far, reach, JT_ROUND, 0.0)) if far else overhang
    return [p for p in narrow if abs(pc.Area(p)) > min_area or pc.Area(p) < 0]


def island_support(island_polys, lower_raw, half_nozzle, min_overhang, min_area):
    """(lower_raw, lower grown by half a nozzle, gentle slope found) for one island, with
    the gentle-slope part of its overhang counted as supported, so the slicer keeps its own
    walls there and the waves only cover what reaches further."""
    narrow = narrow_overhangs(island_polys, lower_raw, min_overhang, min_area)
    if not narrow:
        return lower_raw, expand(lower_raw, half_nozzle) if lower_raw else [], False
    raw = union(lower_raw + narrow)
    return raw, expand(raw, half_nozzle), True


def _join_loop_pieces(pieces):
    """Rejoin pieces of one clipped closed loop that meet end to start (Clipper splits a
    loop at its first point)."""
    out = [list(p) for p in pieces if len(p) >= 2]
    joined = True
    while joined:
        joined = False
        for i in range(len(out)):
            for j in range(len(out)):
                if i != j and out[i][-1] == out[j][0]:
                    out[i] = out[i] + out[j][1:]
                    del out[j]
                    joined = True
                    break
            if joined:
                break
    return out


def kept_walls(island_polys, cut, count, height, ext_w, inner_w, perim_s):
    """Centre lines of the `count` outermost walls of an island where the cut removed the
    slicer's own walls. Scaled units in, list of (role, width, polylines) out, innermost
    wall first (the waves bond to it, the outer wall is laid last)."""
    squash = height * (1.0 - 0.25 * math.pi)
    ext_s = ext_w - squash
    d = ext_w / 2.0
    out = []
    for i in range(count):
        if i == 1:
            d += (ext_s + perim_s) / 2.0
        elif i > 1:
            d += perim_s
        loops = shrink(island_polys, d)
        if not loops:
            break
        width = ext_w if i == 0 else inner_w
        pieces = []
        for loop in to_polylines(loops):
            pieces += _join_loop_pieces(intersection_pl([loop], cut))
        pieces = [pl for pl in pieces if polyline_length(pl) > 2.0 * width]
        if pieces:
            out.append(("Outer wall" if i == 0 else "Inner wall", width, pieces))
    out.reverse()
    return out


def wall_side(island_polys, lower_raw, keep, perim_s, wave_spacing, min_area):
    """The part of an island's overhang that is wide enough to keep walls along its edge.

    A wall is kept only where the overhang reaches past the walls plus two wave lines, so
    the waves inside still carry it: shelves keep their wall; steep narrow overhangs (a cone
    growing half a millimetre per layer) would be all wall over air, so they are waved to
    the edge instead. Returns the overhang pieces that reach that far, with their roots.
    """
    if keep <= 0 or not lower_raw:
        return []
    reach = keep * perim_s + 2 * wave_spacing
    overhang = diff(island_polys, lower_raw)
    if not overhang:
        return []
    far = [p for p in diff(overhang, expand(lower_raw, reach, JT_ROUND, 0.0)) if abs(pc.Area(p)) > min_area]
    if not far:
        return []
    return intersection(overhang, expand(far, reach, JT_ROUND, 0.0))


def island_waves(island, island_polys, lower_raw, lower, P, keep, height, ext_w, inner_w, ov_w, perim_s,
                 min_area=0.0):
    """Waves, cut zone and kept walls of one island (all scaled).

    `keep` walls are kept along the edge of the wide parts of the overhang (wall_side());
    elsewhere the waves run to the edge. Returns (regions, cut polygons, walls); regions is
    empty when nothing is waved.
    """
    wall_loops = P.get("wall_loops", P["perimeter_count"] + keep)
    wide = wall_side(island_polys, lower_raw, keep, perim_s, P["wave_spacing"] or P["base_spacing"], min_area)
    regions = []
    if wide:
        # The waves fill the island minus the walls' band along the wide overhang (the
        # fork's wave_infill, restricted to where walls are kept).
        band = diff(island_polys, shrink(island_polys, keep * perim_s))
        wall_band = intersection(band, expand(wide, perim_s, JT_ROUND, 0.0))
        wave_area = union_ex(diff(island_polys, wall_band))
        if wave_area:
            regions, residual = generate(wave_area, lower, dict(P, perimeter_count=max(1, wall_loops - keep)))
    if not regions:
        wide = []
        regions, residual = generate([island], lower, dict(P, perimeter_count=max(1, wall_loops)))
    if not regions:
        return [], [], []
    zone = painted_overhang_zone(island_polys, lower_raw, residual, ov_w // 2)
    clip_region = union(expand(zone, 1.5 * perim_s, JT_ROUND, 0.0) + residual)
    fill_carve = (union(expand(zone, 1.25 * ov_w, JT_ROUND, 0.0) + expand(residual, ov_w // 2, JT_ROUND, 0.0))
                  if zone else residual)
    cut = clip_region + fill_carve
    walls = []
    if wide:
        wall_cut = intersection(union(cut), expand(wide, perim_s, JT_ROUND, 0.0))
        walls = kept_walls(island_polys, wall_cut, keep, height, ext_w, inner_w, perim_s)
    return regions, cut, walls


def _generate_object(ctx, p, waves=True):
    """Waves and cut zones for every layer of ctx.object, from the slices as they are now.
    With waves=False only the layers are recorded (an object left alone).

    Returns (capture, cuts): capture holds plain values; cuts maps a layer index to the
    scaled polygons to cut out of that layer's slices.
    """
    k = orca.slicing.unscale(1)
    G = _gen_params(ctx, p, k)
    half_nozzle = int(round(G["nozzle_mm"] * 0.5 / k))
    min_overhang = int(round(max(p["min_overhang_mm"], 0.0) / k))
    keep = min(max(0, int(p["outer_walls"])), G["wall_loops"])
    ext_w = G["outer_width_mm"] / k
    inner_w = G["inner_width_mm"] / k

    layers = {}
    cuts = {}
    stats = {"wave_layers": 0, "regions": 0, "paths": 0, "length_mm": 0.0, "narrow_kept": 0,
             "wall_mm": 0.0}
    obj_layers = list(ctx.object.layers())
    islands_of = [None] * len(obj_layers)
    for idx, layer in enumerate(obj_layers):
        if ctx.cancelled():
            break
        islands = [_expoly_from_host(ex) for ex in layer.lslices()]
        islands_of[idx] = islands
        entry = {"z": float(layer.print_z), "height": float(layer.height), "ref": None, "paths": [],
                 "walls": []}
        layers[_zkey(layer.print_z)] = entry
        if idx == 0 or not islands or not waves:
            continue
        lower_raw = to_polygons(islands_of[idx - 1] or [])
        lower = expand(lower_raw, half_nozzle) if lower_raw else []

        ov_w, ov_s, perim_s = _layer_flow(G, float(layer.height), k)
        P = dict(G, base_spacing=ov_s, overhang_width=ov_w)
        h = float(layer.height)
        layer_cut = []
        for island in islands:
            island_polys = to_polygons([island])
            if not diff(island_polys, lower):
                continue  # fully supported
            island_lower_raw, island_lower = lower_raw, lower
            if min_overhang > 0:
                island_lower_raw, island_lower, kept = island_support(
                    island_polys, lower_raw, half_nozzle, min_overhang, max(G["min_new_area"], 0.01 / (k * k)))
                if kept:
                    stats["narrow_kept"] += 1
                    if not diff(island_polys, island_lower):
                        continue
            regions, island_cut, walls = island_waves(island, island_polys, island_lower_raw, island_lower, P,
                                                      keep, h / k, ext_w, inner_w, ov_w, perim_s,
                                                      max(G["min_new_area"], 0.01 / (k * k)))
            if not regions:
                continue
            layer_cut += island_cut
            for role, width, pieces in walls:
                w_mm = width * k
                for pl in pieces:
                    mm = [(x * k, y * k) for x, y in pl]
                    entry["walls"].append({"role": role, "width": w_mm, "pts": mm,
                                           "mm3_per_mm": h * (w_mm - h * (1.0 - 0.25 * math.pi))})
                    stats["wall_mm"] += polyline_length(mm)
            for region in regions:
                stats["regions"] += 1
                for path in region:
                    mm = [(x * k, y * k) for x, y in path]
                    entry["paths"].append(mm)
                    stats["paths"] += 1
                    stats["length_mm"] += polyline_length(mm)
        if entry["paths"]:
            stats["wave_layers"] += 1
        if layer_cut:
            cuts[idx] = union(layer_cut)

    instance_offset = None
    try:
        instance_offset = [tuple(inst.offset()) for inst in ctx.object.model_object().instances()]
    except Exception:
        pass
    return {
        "layers": layers,
        "stats": stats,
        "line_width": G["nozzle_mm"],
        "instance_offset": instance_offset,
        "cut_layers": 0,
        "generated_at": "posSlice",
    }, cuts


def _cut_slices(ctx, cuts):
    """Cut the wave areas out of every region's slices. Returns the number of layers cut."""
    obj_layers = list(ctx.object.layers())
    done = 0
    for idx, polys in cuts.items():
        cut = [_host_expoly(ex) for ex in union_ex(polys)]
        if not cut:
            continue
        layer = obj_layers[idx]
        for region in layer.regions():
            by_type = {}
            for surface in region.slices.surfaces:
                pieces = [surface.expolygon]
                for c in cut:
                    pieces = [q for piece in pieces for q in piece.diff_ex(c)]
                    if not pieces:
                        break
                if pieces:
                    by_type.setdefault(surface.surface_type, []).extend(pieces)
            region.slices.clear()
            for surface_type, expolys in by_type.items():
                region.slices.append(expolys, surface_type)
        layer.make_slices()
        done += 1
    return done


def _toolpath_bbox(layer, k):
    """(min_x, min_y, max_x, max_y) in mm of the layer's wall toolpaths."""
    box = None
    for region in layer.regions():
        for path in region.perimeters.flatten_paths():
            if path.role not in _WALL_ROLES:
                continue
            pts = path.points()
            if len(pts) == 0:
                continue
            lo = pts.min(axis=0)
            hi = pts.max(axis=0)
            b = (float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1]))
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    return None if box is None else tuple(v * k for v in box)


def _capture_refs(ctx, cap):
    k = orca.slicing.unscale(1)
    for layer in ctx.object.layers():
        if ctx.cancelled():
            break
        entry = cap["layers"].get(_zkey(layer.print_z))
        if entry is not None:
            entry["ref"] = _toolpath_bbox(layer, k)


# ============================================================================
# G-code: parsing, calibration, injection
# ============================================================================

def _parse_words(code):
    parts = code.split()
    if not parts:
        return "", {}
    words = {}
    for tok in parts[1:]:
        try:
            words[tok[0].upper()] = float(tok[1:])
        except (ValueError, IndexError):
            pass
    return parts[0].upper(), words


class _Stream:
    """State of a G-code stream, advanced one line at a time.

    "retracted": filament (mm) pulled back by G1 E moves (wipes included) and not yet
    pushed back; "fw_retracted": between G10 and G11; "fan": last part-fan value (0-255)
    or None if not set yet.
    """
    _FIELDS = ("x", "y", "e", "f", "e_relative", "retracted", "fw_retracted", "fan",
               "feature", "feature_line", "width_line", "height_line")

    def __init__(self, e_relative, bbl=False, state=None):
        self.x = self.y = None
        self.e = 0.0
        self.f = None
        self.e_relative = e_relative
        self.retracted = 0.0
        self.fw_retracted = False
        self.fan = None
        self.feature = None
        self.feature_line = self.width_line = self.height_line = None
        self.fan_index = 1 if bbl else 0
        if state:
            for name in self._FIELDS:
                if name in state:
                    setattr(self, name, state[name])

    def snapshot(self):
        return {name: getattr(self, name) for name in self._FIELDS}

    def feed(self, raw):
        line = raw.strip()
        if not line:
            return None
        if line[0] == ";":
            body = line[1:]
            if body.startswith(" FEATURE: ") or body.startswith("TYPE:"):
                self.feature = body.split(":", 1)[1].strip()
                self.feature_line = raw
            elif body.startswith(" LINE_WIDTH: ") or body.startswith("WIDTH:"):
                self.width_line = raw
            elif body.startswith(" LAYER_HEIGHT: ") or body.startswith("HEIGHT:"):
                self.height_line = raw
            return None
        cmd, w = _parse_words(line.split(";", 1)[0])
        if cmd == "M82":
            self.e_relative = False
        elif cmd == "M83":
            self.e_relative = True
        elif cmd == "G92":
            if "E" in w:
                self.e = w["E"]
        elif cmd == "G10":
            self.fw_retracted = True
        elif cmd == "G11":
            self.fw_retracted = False
        elif cmd == "M106":
            if int(w.get("P", self.fan_index)) == self.fan_index:
                self.fan = w.get("S", 255.0)
        elif cmd == "M107":
            if int(w.get("P", self.fan_index)) == self.fan_index:
                self.fan = 0.0
        elif cmd in ("G0", "G1", "G2", "G3"):
            if "F" in w:
                self.f = w["F"]
            x0, y0 = self.x, self.y
            de = 0.0
            extruding = False
            if "E" in w:
                de = w["E"] if self.e_relative else w["E"] - self.e
                if not self.e_relative:
                    self.e = w["E"]
                if de < 0.0:
                    self.retracted -= de
                elif de > 0.0:
                    extruding = de > self.retracted + 1e-6
                    self.retracted = max(0.0, self.retracted - de)
            self.x = w.get("X", self.x)
            self.y = w.get("Y", self.y)
            return {"cmd": cmd, "w": w, "x0": x0, "y0": y0, "x1": self.x, "y1": self.y,
                    "de": de, "extruding": extruding, "moved": ("X" in w) or ("Y" in w)}
        return None


# Object labels, in order of preference. OrcaSlicer (GCode.cpp) writes "; printing object"
# comments when gcode_label_objects is on, and, when exclude_object is on, EXCLUDE_OBJECT_*
# (Klipper), M486 (Marlin, RepRapFirmware) or "unique label id" comments (Bambu). The start
# and end strings of the exclude-object families are written lazily (at the next travel), so
# an instance's end label can come after the next layer-change tag.
_LABEL_FAMILIES = (
    ("comment", "; printing object labels"),
    ("klipper", "EXCLUDE_OBJECT_START/END"),
    ("m486", "M486"),
    ("bambu", "Bambu label ids"),
)


def _label_event(family, line):
    """("start", key) / ("end", None) if `line` (stripped) is a label of `family`, else None."""
    if family == "comment":
        if line.startswith("; printing object "):
            return "start", line[len("; printing object "):].strip()
        if line.startswith("; stop printing object ") and "unique label id" not in line:
            return "end", None
    elif family == "klipper":
        if line.startswith("EXCLUDE_OBJECT_START"):
            return "start", line.split("NAME=", 1)[-1].strip()
        if line.startswith("EXCLUDE_OBJECT_END"):
            return "end", None
    elif family == "m486":
        if line.startswith("M486 S"):
            try:
                n = int(float(line[6:].split(";", 1)[0].split()[0]))
            except (ValueError, IndexError):
                return None
            return ("end", None) if n < 0 else ("start", "M486 S%d" % n)
    elif family == "bambu":
        if line.startswith("; start printing object, unique label id:"):
            return "start", "label id " + line.split(":", 1)[1].strip()
        if line.startswith("; stop printing object, unique label id:"):
            return "end", None
    return None


def _is_layer_tag(line):
    return line.startswith("; CHANGE_LAYER") or line.startswith(";LAYER_CHANGE")


def _label_family(lines):
    """The preferred label family used inside the layers, or None."""
    seen = set()
    in_layers = False
    for raw in lines:
        line = raw.strip()
        if not in_layers:
            in_layers = _is_layer_tag(line)
            continue
        if not line or line[0] not in ";EM":
            continue
        for family, _ in _LABEL_FAMILIES:
            if family not in seen and _label_event(family, line) is not None:
                seen.add(family)
        if "comment" in seen:
            break
    for family, _ in _LABEL_FAMILIES:
        if family in seen:
            return family
    return None


def _scan_gcode(lines, e_relative):
    """Split the G-code into segments: one per layer and labelled object instance (or one per
    layer when the file has no object labels).

    Returns (segments, info). Each segment: key (label of the instance, None without labels),
    z, box (wall extrusion bbox), end (line index where the segment ends: its end label, the
    next instance's start label or the next layer-change tag; None at the end of the file),
    end_state (_Stream snapshot at end). info: bbl, family, layers.
    """
    bbl = any(l.startswith("; CHANGE_LAYER") for l in lines[:20000])
    family = _label_family(lines)
    segments = []
    seg = None
    key = None
    z = None
    want_z = False
    n_layers = 0
    s = _Stream(e_relative, bbl)

    def close(i):
        if seg is not None:
            seg["end"] = i
            seg["end_state"] = s.snapshot()

    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith(";"):
            body = line[1:]
            if _is_layer_tag(line):
                close(i)
                seg = None
                key = None
                z = None
                want_z = True
                n_layers += 1
                continue
            if body.startswith(" Z_HEIGHT: ") or body.startswith("Z:"):
                if want_z:
                    try:
                        z = float(body.split(":", 1)[1])
                    except ValueError:
                        pass
                    want_z = False
                    if seg is not None and seg["z"] is None:
                        seg["z"] = z
                continue
        if family is not None and n_layers:
            ev = _label_event(family, line)
            if ev is not None:
                close(i)
                seg = None
                key = ev[1] if ev[0] == "start" else None
        mv = s.feed(raw)
        if not n_layers or (family is not None and key is None):
            continue
        if seg is None:
            seg = {"key": key, "z": z, "box": None, "end": None, "end_state": None}
            segments.append(seg)
        if (mv is not None and mv["extruding"] and mv["moved"]
                and s.feature in _WALL_ROLES and None not in (mv["x0"], mv["y0"], mv["x1"], mv["y1"])):
            b = seg["box"]
            lo_x, hi_x = min(mv["x0"], mv["x1"]), max(mv["x0"], mv["x1"])
            lo_y, hi_y = min(mv["y0"], mv["y1"]), max(mv["y0"], mv["y1"])
            seg["box"] = (lo_x, lo_y, hi_x, hi_y) if b is None else (
                min(b[0], lo_x), min(b[1], lo_y), max(b[2], hi_x), max(b[3], hi_y))
    return segments, {"bbl": bbl, "family": family, "layers": n_layers}


def _instances(segments):
    """Group segments by instance key and layer: {key: [layer, ...]} in file order, where a
    layer merges every segment of that instance at that z (box union; ends at the last one)."""
    by_key = {}
    for seg in segments:
        if seg["z"] is None:
            continue
        layers = by_key.setdefault(seg["key"], {})
        zk = _zkey(seg["z"])
        cur = layers.get(zk)
        if cur is None:
            layers[zk] = dict(seg)
            continue
        a, b = cur["box"], seg["box"]
        if b is not None:
            cur["box"] = b if a is None else (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
        if seg["end"] is None or (cur["end"] is not None and seg["end"] > cur["end"]):
            cur["end"], cur["end_state"] = seg["end"], seg["end_state"]
    return {key: list(layers.values()) for key, layers in by_key.items()}


def _label_object_index(key):
    """Object index (position in the print's object list) named by a label, if it names one."""
    if key is None:
        return None
    m = re.search(r"(?:_id_|\bid:)(\d+)(?:_copy_| copy )\d+$", key)
    return int(m.group(1)) if m else None


def _match_object(captures, layers, key):
    """Pick the capture whose layers match this instance's G-code layers.

    Every capture is calibrated against the instance; among those that pass, the best
    covers most layers on both sides, then has the smallest size mismatch and spread.
    Returns (object id or None, calibration, number of passing candidates).
    """
    best = None
    passing = []
    for oid, cap in captures.items():
        cal = _calibrate(cap["layers"], layers)
        if not cal["ok"]:
            if best is None or (best[1].get("samples", 0) < cal.get("samples", 0)):
                best = (None, cal)
            continue
        cover = cal["matched"] / max(len(layers), 1) + cal["matched"] / max(len(cap["layers"]), 1)
        passing.append((-round(cover, 3), round(cal["size_mismatch"], 2), round(cal["spread_p90"], 2), oid, cal))
    if not passing:
        return None, (best[1] if best else {"ok": False, "why": "no object captured"}), 0
    passing.sort(key=lambda t: t[:3])
    top = [t for t in passing if t[:3] == passing[0][:3]]
    pick = top[0]
    want = _label_object_index(key)
    if len(top) > 1 and want is not None:
        for t in top:
            if captures[t[3]].get("index") == want:
                pick = t
                break
    return pick[3], pick[4], len(top)


def _calibrate(captured, gcode_layers):
    """Measure the object->G-code XY offset from matching layer bounding boxes."""
    dxs, dys, dws, dhs = [], [], [], []
    matched = 0
    for gl in gcode_layers:
        if gl["z"] is None:
            continue
        cl = captured.get(_zkey(gl["z"]))
        if cl is None:
            continue
        matched += 1
        ref, box = cl["ref"], gl["box"]
        if ref is None or box is None:
            continue
        dxs.append(0.5 * (box[0] + box[2]) - 0.5 * (ref[0] + ref[2]))
        dys.append(0.5 * (box[1] + box[3]) - 0.5 * (ref[1] + ref[3]))
        dws.append((box[2] - box[0]) - (ref[2] - ref[0]))
        dhs.append((box[3] - box[1]) - (ref[3] - ref[1]))
    cal = {"matched": matched, "samples": len(dxs), "ok": False}
    if not dxs:
        cal["why"] = "no layer had both a captured reference box and wall moves in the G-code"
        return cal
    dx, dy = _median(dxs), _median(dys)
    devs = sorted(max(abs(a - dx), abs(b - dy)) for a, b in zip(dxs, dys))
    p90 = devs[min(len(devs) - 1, int(0.9 * len(devs)))]
    size = max(abs(_median(dws)), abs(_median(dhs)))
    cal.update({"dx": dx, "dy": dy, "spread_p90": p90, "spread_max": devs[-1], "size_mismatch": size})
    if p90 > 0.5:
        cal["why"] = "offset differs between layers (several objects/copies printed without object labels?)"
    elif size > 1.5:
        cal["why"] = "G-code walls and captured geometry differ in size (wrong object or stale capture?)"
    else:
        cal["ok"] = True
    return cal


def _wave_block(layer_cap, state, cal, bbl, cfg, totals):
    """G-code for the waves (then the kept walls) of one layer, leaving the stream state as
    found."""
    h = layer_cap["height"]
    filament_area = math.pi * (cfg["filament_diameter"] * 0.5) ** 2
    retract = cfg["retract"]
    f_travel = cfg["travel_speed"] * 60.0
    hop_limit = cfg["connector_limit"]

    # (points, mm of filament per mm, feedrate, feature, line width, kind)
    jobs = [(path, cfg["flow_mm3_per_mm"] / filament_area, cfg["print_speed"] * 60.0,
             "Overhang wall", cfg["line_width"], "wave") for path in layer_cap["paths"]]
    jobs += [(wall["pts"], wall["mm3_per_mm"] / filament_area, cfg["wall_speed"] * 60.0,
              wall["role"], wall["width"], "wall") for wall in layer_cap.get("walls", [])]

    out = [_TAG + "_START z=" + _fmt(layer_cap["z"]) + " paths=" + str(len(layer_cap["paths"]))
           + (" walls=%d" % len(layer_cap["walls"]) if layer_cap.get("walls") else "") + "\n"]

    def tags(feature, width):
        if bbl:
            return ["; FEATURE: " + feature + "\n", "; LINE_WIDTH: " + _fmt(width) + "\n",
                    "; LAYER_HEIGHT: " + _fmt(h) + "\n"]
        return [";TYPE:" + feature + "\n", ";WIDTH:" + _fmt(width) + "\n", ";HEIGHT:" + _fmt(h) + "\n"]

    current_tags = None
    if jobs:
        out += tags(jobs[0][3], jobs[0][4])
        current_tags = (jobs[0][3], jobs[0][4])
    if not state["e_relative"]:
        out.append("M83\n")
    fan_p = " P1" if bbl else ""
    out.append("M106%s S%d\n" % (fan_p, int(round(cfg["fan_speed"] * 2.55))))

    def pull(kind, amount):
        if kind == "fw":
            return ["G10\n"]
        return ["G1 E-%.5f F1800\n" % amount] if amount > 0.0 else []

    def push(kind, amount):
        if kind == "fw":
            return ["G11\n"]
        return ["G1 E%.5f F1800\n" % amount] if amount > 0.0 else []

    if state.get("fw_retracted"):
        entry_kind, entry_amount = "fw", 0.0
    elif state.get("retracted", 0.0) > 1e-4:
        entry_kind, entry_amount = "g1", state["retracted"]
    else:
        entry_kind, entry_amount = None, 0.0
    kind = entry_kind or "g1"

    pos = None
    retracted = entry_kind is not None
    for path, e_per_mm, f_print, feature, width, what in jobs:
        if (feature, width) != current_tags:
            out += tags(feature, width)
            current_tags = (feature, width)
        pts = [(px + cal["dx"], py + cal["dy"]) for px, py in path]
        start = pts[0]
        short_hop = pos is not None and math.dist(pos, start) <= hop_limit
        if short_hop and not retracted:
            out.append("G1 X%s Y%s F%s\n" % (_fmt(start[0]), _fmt(start[1]), _fmt(f_travel)))
        else:
            amount = entry_amount if (retracted and entry_kind is not None and pos is None) else retract
            if not retracted:
                out += pull(kind, amount)
            out.append("G1 X%s Y%s F%s\n" % (_fmt(start[0]), _fmt(start[1]), _fmt(f_travel)))
            out += push(kind, amount)
            retracted = False
        if pos is not None:
            totals["travel_mm"] += math.dist(pos, start)
        out.append("G1 F%s\n" % _fmt(f_print))
        prev = start
        for pt in pts[1:]:
            d = math.hypot(pt[0] - prev[0], pt[1] - prev[1])
            if d < 1e-4:
                continue
            out.append("G1 X%s Y%s E%.5f\n" % (_fmt(pt[0]), _fmt(pt[1]), d * e_per_mm))
            totals["filament_mm"] += d * e_per_mm
            if what == "wave":
                totals["length_mm"] += d
            else:
                totals["wall_mm"] = totals.get("wall_mm", 0.0) + d
                totals["wall_s"] = totals.get("wall_s", 0.0) + d / max(f_print / 60.0, 0.1)
            prev = pt
        pos = prev
    if entry_kind is not None:
        out += pull(entry_kind, entry_amount)  # hand the nozzle back as retracted as it was
    fan = state.get("fan")
    out.append("M106%s S%d\n" % (fan_p, int(round(fan))) if fan else ("M107\n" if not bbl else "M106 P1 S0\n"))
    if not state["e_relative"]:
        out += ["M82\n", "G92 E%.5f\n" % state["e"]]
    if state["f"] is not None:
        out.append("G1 F%s\n" % _fmt(state["f"]))
    for tag_line in (state["feature_line"], state["width_line"], state["height_line"]):
        if tag_line is not None:
            out.append(tag_line if tag_line.endswith("\n") else tag_line + "\n")
    out.append(_TAG + "_END\n")
    return out


# ============================================================================
# preview data (for the preview panel)
# ============================================================================

# The preview panel's page: a WebGL view of the data from extract_preview(). It asks the plugin
# for its data once loaded ({"command": "preview_ready"}) and gets {"command": "preview", "layers",
# "bbox", "stats", "title"} back. #mode=layer&to=57&from=0&yaw=-50&pitch=35&show=wk in the URL
# sets the initial view (used by tests/preview_page_test.py).
PREVIEW_PAGE = r"""
<!doctype html>
<meta charset="utf-8">
<title>Wave preview</title>
<style>
  html, body { margin: 0; height: 100%; overflow: hidden; background: var(--orca-bg, #1e1e1e);
               color: var(--orca-fg, #ddd); font: 12px sans-serif; }
  body { display: flex; flex-direction: column; }
  #bar { padding: 6px 8px 4px; display: flex; flex-direction: column; gap: 5px; }
  .row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .row label { display: flex; align-items: center; gap: 3px; cursor: pointer; }
  .row input[type=range] { flex: 1; min-width: 120px; }
  button { padding: 2px 8px; cursor: pointer; }
  button.on { outline: 2px solid #1fb89a; }
  #wrap { flex: 1; position: relative; min-height: 120px; }
  canvas { position: absolute; left: 0; top: 0; width: 100%; height: 100%; display: block; outline: none; }
  #info, #status { padding: 2px 8px 6px; color: var(--orca-muted, #999); font-size: 11px; }
  .bad { color: #e5534b; }
  .sw { display: inline-block; width: 10px; height: 3px; margin: 0 3px 2px 0; vertical-align: middle; }
</style>
<div id="bar">
  <div class="row">
    <button type="button" id="m3d" class="on">3D</button>
    <button type="button" id="mlayer">Layer</button>
    <button type="button" id="fit" title="Fit the view to the waves (double-click the view)">Fit</button>
    <label><input type="checkbox" id="sw" checked><span class="sw" style="background:#1ad9b3"></span>waves</label>
    <label><input type="checkbox" id="sk" checked><span class="sw" style="background:#ff8c1a"></span>kept walls</label>
    <label><input type="checkbox" id="sc" checked><span class="sw" style="background:#9e9ea8"></span>outer walls</label>
  </div>
  <div class="row"><span id="lt" style="min-width:90px">top layer</span><input type="range" id="to" min="0" max="0" value="0">
    <button type="button" id="pw" title="Previous layer with waves">&#9664; waves</button>
    <button type="button" id="nw" title="Next layer with waves">waves &#9654;</button></div>
  <div class="row"><span id="lf" style="min-width:90px">from layer</span><input type="range" id="from" min="0" max="0" value="0"></div>
</div>
<div id="wrap"><canvas id="c" tabindex="0"></canvas></div>
<div id="info"></div>
<div id="status">waiting for the plugin...</div>
<script>
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var statusEl = $("status"), infoEl = $("info"), canvas = $("c");
  function fail(msg) { statusEl.textContent = "error: " + msg; statusEl.className = "bad"; }
  window.onerror = function (m) { fail(m); };

  var gl = null, prog = null, loc = {};
  var data = null, bufs = {}, off = {}, lens = {}, wavedLayers = [];
  var KINDS = ["c", "k", "w"];
  var COLORS = { c: [0.62, 0.62, 0.66, 0.5], k: [1.0, 0.55, 0.1, 1.0], w: [0.1, 0.85, 0.7, 1.0] };
  var S = { mode: "3d", from: 0, to: 0, show: { c: true, k: true, w: true } };
  var cam = { tx: 0, ty: 0, tz: 0, dist: 100, yaw: -1.0, pitch: 0.6 };
  var saved3d = null;
  var FOV = 45 * Math.PI / 180;

  // ---- small matrix helpers (column-major) -------------------------------------------------
  function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function norm(a) { var l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
  function perspective(aspect, near, far) {
    var f = 1 / Math.tan(FOV / 2);
    return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, 2 * far * near / (near - far), 0];
  }
  function lookAt(eye, c, up) {
    var z = norm(sub(eye, c)), x = norm(cross(up, z)), y = cross(z, x);
    return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
  }
  function mul(a, b) {
    var o = new Array(16), c, r, k, s;
    for (c = 0; c < 4; c++) for (r = 0; r < 4; r++) {
      s = 0; for (k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
      o[c * 4 + r] = s;
    }
    return o;
  }
  function eyeOf() {
    var cp = Math.cos(cam.pitch);
    return [cam.tx + cam.dist * cp * Math.cos(cam.yaw), cam.ty + cam.dist * cp * Math.sin(cam.yaw),
            cam.tz + cam.dist * Math.sin(cam.pitch)];
  }
  function basis() {
    var e = eyeOf(), f = norm(sub([cam.tx, cam.ty, cam.tz], e));
    var right = norm(cross(f, [0, 0, 1]));
    return { f: f, right: right, up: cross(right, f) };
  }

  // ---- WebGL --------------------------------------------------------------------------------
  function initGL() {
    gl = canvas.getContext("webgl", { antialias: true });
    if (!gl) { fail("WebGL is not available in this panel"); return false; }
    function sh(type, src) {
      var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    }
    prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, "attribute vec3 p; uniform mat4 m; void main(){gl_Position=m*vec4(p,1.0);}"));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, "precision mediump float; uniform vec4 col; void main(){gl_FragColor=col;}"));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    loc.p = gl.getAttribLocation(prog, "p");
    loc.m = gl.getUniformLocation(prog, "m");
    loc.col = gl.getUniformLocation(prog, "col");
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.enable(gl.DEPTH_TEST);
    return true;
  }

  function load(msg) {
    var L = msg.layers || [];
    if (!L.length) { statusEl.textContent = "the plugin sent no layers"; return; }
    data = msg;
    KINDS.forEach(function (k) {
      var n = 0, i, j, t, pls, f;
      off[k] = new Int32Array(L.length + 1);
      lens[k] = new Float64Array(L.length);
      for (i = 0; i < L.length; i++) {
        off[k][i] = n; pls = L[i][k];
        for (j = 0; j < pls.length; j++) n += (pls[j].length / 2 - 1) * 2;
      }
      off[k][L.length] = n;
      var arr = new Float32Array(n * 3), q = 0;
      for (i = 0; i < L.length; i++) {
        pls = L[i][k];
        for (j = 0; j < pls.length; j++) {
          f = pls[j];
          for (t = 2; t < f.length; t += 2) {
            arr[q++] = f[t - 2]; arr[q++] = f[t - 1]; arr[q++] = L[i].z;
            arr[q++] = f[t]; arr[q++] = f[t + 1]; arr[q++] = L[i].z;
            lens[k][i] += Math.hypot(f[t] - f[t - 2], f[t + 1] - f[t - 1]);
          }
        }
      }
      if (!bufs[k]) bufs[k] = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, bufs[k]);
      gl.bufferData(gl.ARRAY_BUFFER, arr, gl.STATIC_DRAW);
    });
    wavedLayers = [];
    for (var i = 0; i < L.length; i++) if (lens.w[i] > 0 || lens.k[i] > 0) wavedLayers.push(i);
    $("to").max = $("from").max = L.length - 1;
    S.to = L.length - 1; S.from = 0;
    applyHash();
    fit();
    syncUI();
    var st = msg.stats || {};
    statusEl.textContent = L.length + " layers, " + (st.numbers || 0) + " numbers" +
      (msg.title ? " - " + msg.title : "");
    statusEl.className = "";
    draw();
  }

  // ---- view ---------------------------------------------------------------------------------
  function range() { return S.mode === "layer" ? [S.to, S.to] : [S.from, S.to]; }

  function fit() {
    if (!data) return;
    var r = range(), lo = [1e18, 1e18, 1e18], hi = [-1e18, -1e18, -1e18], any = false;
    function scan(kinds) {
      kinds.forEach(function (k) {
        for (var i = r[0]; i <= r[1]; i++) {
          var pls = data.layers[i][k];
          for (var j = 0; j < pls.length; j++) for (var t = 0; t < pls[j].length; t += 2) {
            any = true;
            lo[0] = Math.min(lo[0], pls[j][t]); hi[0] = Math.max(hi[0], pls[j][t]);
            lo[1] = Math.min(lo[1], pls[j][t + 1]); hi[1] = Math.max(hi[1], pls[j][t + 1]);
          }
          lo[2] = Math.min(lo[2], data.layers[i].z); hi[2] = Math.max(hi[2], data.layers[i].z);
        }
      });
    }
    scan(["w", "k"]);
    if (!any) { lo = [1e18, 1e18, 1e18]; hi = [-1e18, -1e18, -1e18]; scan(KINDS); }
    if (!any) return;
    cam.tx = (lo[0] + hi[0]) / 2; cam.ty = (lo[1] + hi[1]) / 2; cam.tz = (lo[2] + hi[2]) / 2;
    var diag = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], S.mode === "layer" ? 0 : hi[2] - lo[2]) || 10;
    cam.dist = diag * 0.75 / Math.tan(FOV / 2) + 2;
  }

  function resize() {
    var dpr = window.devicePixelRatio || 1;
    var w = Math.max(1, Math.round(canvas.clientWidth * dpr)), h = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  }

  function bgColor() {
    var m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(getComputedStyle(document.body).backgroundColor);
    return m ? [m[1] / 255, m[2] / 255, m[3] / 255] : [0.12, 0.12, 0.12];
  }

  function draw() {
    if (!gl || !data) return;
    resize();
    var bg = bgColor();
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(bg[0], bg[1], bg[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    var e = eyeOf();
    var up = S.mode === "layer" ? [0, 1, 0] : [0, 0, 1];
    var m = mul(perspective(canvas.width / canvas.height, cam.dist * 0.01, cam.dist * 20),
                lookAt(e, [cam.tx, cam.ty, cam.tz], up));
    gl.useProgram(prog);
    gl.uniformMatrix4fv(loc.m, false, new Float32Array(m));
    var r = range();
    KINDS.forEach(function (k) {
      if (!S.show[k]) return;
      var first = off[k][r[0]], count = off[k][r[1] + 1] - first;
      if (count <= 0) return;
      gl.bindBuffer(gl.ARRAY_BUFFER, bufs[k]);
      gl.enableVertexAttribArray(loc.p);
      gl.vertexAttribPointer(loc.p, 3, gl.FLOAT, false, 0, 0);
      gl.uniform4fv(loc.col, new Float32Array(COLORS[k]));
      gl.drawArrays(gl.LINES, first, count);
    });
  }

  function syncUI() {
    var L = data.layers, r = range(), sum = { w: 0, k: 0 }, i;
    for (i = r[0]; i <= r[1]; i++) { sum.w += lens.w[i]; sum.k += lens.k[i]; }
    $("to").value = S.to; $("from").value = S.from;
    $("lt").textContent = (S.mode === "layer" ? "layer " : "top layer ") + (S.to + 1) + " / " + L.length;
    $("lf").textContent = "from layer " + (S.from + 1);
    $("from").disabled = S.mode === "layer";
    $("m3d").className = S.mode === "3d" ? "on" : "";
    $("mlayer").className = S.mode === "layer" ? "on" : "";
    infoEl.textContent = "z " + L[S.to].z + " mm   shown: waves " + Math.round(sum.w) + " mm, kept walls " +
      Math.round(sum.k) + " mm   (whole print: waves " + Math.round((data.stats || {}).wave_mm || 0) + " mm on " +
      wavedLayers.length + " layers)";
  }

  function refresh() { syncUI(); draw(); }

  function setMode(mode) {
    if (mode === S.mode) return;
    if (mode === "layer") {
      saved3d = { yaw: cam.yaw, pitch: cam.pitch };
      cam.yaw = -Math.PI / 2; cam.pitch = Math.PI / 2 - 0.0005;
    } else if (saved3d) { cam.yaw = saved3d.yaw; cam.pitch = saved3d.pitch; }
    S.mode = mode;
    fit();
    refresh();
  }

  function applyHash() {     // #mode=layer&to=57&from=0&yaw=-50&pitch=35&show=wk  (for tests and bookmarks)
    var h = {};
    location.hash.replace(/^#/, "").split("&").forEach(function (kv) {
      var p = kv.split("="); if (p[0]) h[p[0]] = p[1];
    });
    var n = data.layers.length;
    if (h.yaw !== undefined) cam.yaw = parseFloat(h.yaw) * Math.PI / 180;
    if (h.pitch !== undefined) cam.pitch = parseFloat(h.pitch) * Math.PI / 180;
    if (h.from !== undefined) S.from = Math.min(n - 1, Math.max(0, parseInt(h.from, 10)));
    if (h.to !== undefined) S.to = Math.min(n - 1, Math.max(0, parseInt(h.to, 10)));
    if (h.show !== undefined) KINDS.forEach(function (k) { S.show[k] = h.show.indexOf(k) >= 0; });
    KINDS.forEach(function (k) { $({ w: "sw", k: "sk", c: "sc" }[k]).checked = S.show[k]; });
    if (h.mode === "layer") { saved3d = { yaw: cam.yaw, pitch: cam.pitch }; cam.yaw = -Math.PI / 2; cam.pitch = Math.PI / 2 - 0.0005; S.mode = "layer"; }
    if (S.from > S.to) S.from = S.to;
  }

  // ---- controls -----------------------------------------------------------------------------
  $("to").addEventListener("input", function () { S.to = +this.value; if (S.from > S.to) S.from = S.to; refresh(); });
  $("from").addEventListener("input", function () { S.from = +this.value; if (S.from > S.to) S.to = S.from; refresh(); });
  $("m3d").addEventListener("click", function () { setMode("3d"); });
  $("mlayer").addEventListener("click", function () { setMode("layer"); });
  $("fit").addEventListener("click", function () { fit(); draw(); });
  [["sw", "w"], ["sk", "k"], ["sc", "c"]].forEach(function (p) {
    $(p[0]).addEventListener("change", function () { S.show[p[1]] = this.checked; draw(); });
  });
  function jump(dir) {
    var i, cur = S.to;
    if (dir > 0) { for (i = 0; i < wavedLayers.length; i++) if (wavedLayers[i] > cur) { S.to = wavedLayers[i]; break; } }
    else { for (i = wavedLayers.length - 1; i >= 0; i--) if (wavedLayers[i] < cur) { S.to = wavedLayers[i]; break; } }
    if (S.from > S.to) S.from = S.to;
    refresh();
  }
  $("pw").addEventListener("click", function () { jump(-1); });
  $("nw").addEventListener("click", function () { jump(1); });
  canvas.addEventListener("keydown", function (ev) {
    if (!data) return;
    if (ev.key === "ArrowUp") { S.to = Math.min(data.layers.length - 1, S.to + 1); refresh(); ev.preventDefault(); }
    if (ev.key === "ArrowDown") { S.to = Math.max(0, S.to - 1); if (S.from > S.to) S.from = S.to; refresh(); ev.preventDefault(); }
  });

  var drag = null;
  canvas.addEventListener("mousedown", function (ev) {
    canvas.focus();
    drag = { x: ev.clientX, y: ev.clientY, pan: ev.button !== 0 || ev.shiftKey || S.mode === "layer" };
    ev.preventDefault();
  });
  window.addEventListener("mouseup", function () { drag = null; });
  window.addEventListener("mousemove", function (ev) {
    if (!drag || !data) return;
    var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    drag.x = ev.clientX; drag.y = ev.clientY;
    if (drag.pan) {
      var b = basis(), k = cam.dist * 2 * Math.tan(FOV / 2) / Math.max(1, canvas.clientHeight);
      cam.tx += (-dx * b.right[0] + dy * b.up[0]) * k;
      cam.ty += (-dx * b.right[1] + dy * b.up[1]) * k;
      cam.tz += (-dx * b.right[2] + dy * b.up[2]) * k;
    } else {
      cam.yaw -= dx * 0.01;
      cam.pitch = Math.max(-1.5, Math.min(1.5, cam.pitch + dy * 0.01));
    }
    draw();
  });
  canvas.addEventListener("wheel", function (ev) {
    cam.dist = Math.max(1, cam.dist * Math.exp(ev.deltaY * 0.0012));
    draw(); ev.preventDefault();
  }, { passive: false });
  canvas.addEventListener("dblclick", function () { fit(); draw(); });
  window.addEventListener("resize", draw);

  // ---- start --------------------------------------------------------------------------------
  try { if (initGL()) {
    orca.onMessage(function (msg) { if (msg && msg.command === "preview") { try { load(msg); } catch (e) { fail(e.message || e); } } });
    orca.postMessage({ command: "preview_ready" });
  } } catch (e) { fail(e.message || e); }
})();
</script>
"""


def extract_preview(lines, e_relative, tol_mm=0.02):
    """Per-layer toolpaths of the final G-code for the preview panel.

    Returns {"layers": [{"z", "w", "k", "c"}], "bbox", "stats"}. In each layer "w" are the wave
    lines, "k" the walls kept along wide overhangs (both between the WAVE_OVERHANGS_START/END
    markers) and "c" the slicer's own outer and overhang walls as context. Every entry is a list
    of polylines, each a flat [x0, y0, x1, y1, ...] in mm, in G-code (plate) coordinates.
    Context and kept walls are simplified with Douglas-Peucker at `tol_mm`.
    """
    bbl = any(l.startswith("; CHANGE_LAYER") for l in lines[:20000])
    stream = _Stream(e_relative, bbl)
    layers = []
    cur = None
    want_z = False
    in_wave = False
    kind = None
    pts = []
    stats = {"wave_mm": 0.0, "wall_mm": 0.0, "numbers": 0}
    lo = [1e18, 1e18]
    hi = [-1e18, -1e18]

    def flush():
        nonlocal kind, pts
        if kind is not None and cur is not None and len(pts) >= 2:
            line = pts if kind == "w" else douglas_peucker(pts, tol_mm)
            length = polyline_length(line)
            if kind == "w":
                stats["wave_mm"] += length
            elif kind == "k":
                stats["wall_mm"] += length
            flat = [round(v, 2) for point in line for v in point]
            cur[kind].append(flat)
            stats["numbers"] += len(flat)
            for x, y in line:
                lo[0], lo[1] = min(lo[0], x), min(lo[1], y)
                hi[0], hi[1] = max(hi[0], x), max(hi[1], y)
        kind, pts = None, []

    for raw in lines:
        line = raw.strip()
        if line.startswith(";"):
            body = line[1:]
            if _is_layer_tag(line):
                flush()
                cur = None
                want_z = True
            elif want_z and (body.startswith(" Z_HEIGHT: ") or body.startswith("Z:")):
                try:
                    cur = {"z": float(body.split(":", 1)[1]), "w": [], "k": [], "c": []}
                    layers.append(cur)
                except ValueError:
                    cur = None
                want_z = False
            elif line.startswith(_TAG + "_START"):
                flush()
                in_wave = True
            elif line.startswith(_TAG + "_END"):
                flush()
                in_wave = False
        mv = stream.feed(raw)
        if mv is None or cur is None or not mv["moved"]:
            continue
        if not mv["extruding"] or None in (mv["x0"], mv["y0"], mv["x1"], mv["y1"]):
            flush()     # a travel ends the polyline
            continue
        feature = stream.feature
        if in_wave:
            k = "w" if feature == "Overhang wall" else ("k" if feature in ("Outer wall", "Inner wall") else None)
        else:
            k = "c" if feature in ("Outer wall", "Overhang wall") else None
        if k is None:
            flush()
            continue
        start = (mv["x0"], mv["y0"])
        if k != kind or not pts or math.dist(pts[-1], start) > 1e-3:
            flush()
            kind, pts = k, [start]
        pts.append((mv["x1"], mv["y1"]))
    flush()
    return {"layers": layers, "bbox": [lo[0], lo[1], hi[0], hi[1]] if layers and lo[0] < 1e17 else None,
            "stats": stats}


def _wall_speed(ctx, p):
    """mm/s for the kept walls: the plugin setting, else the slicer's speed for fully
    overhanging walls (overhang_4_4_speed, mm/s or % of the outer wall speed), else its
    bridge speed."""
    if p["wall_speed_mm_s"] > 0:
        return p["wall_speed_mm_s"]
    outer = _first_float(ctx.config_value("outer_wall_speed"), 60.0)
    raw = ctx.config_value("overhang_4_4_speed")
    speed = 0.0
    if raw is not None and _truthy(str(ctx.config_value("enable_overhang_speed")).split(",")[0]):
        text = str(raw).replace(";", ",").split(",")[0].strip()
        speed = _first_float(text, 0.0) * (outer / 100.0 if text.endswith("%") else 1.0)
    if speed <= 0:
        speed = _first_float(ctx.config_value("bridge_speed"), 25.0)
    return max(speed, 1.0)


def _post_process(lines, captures, survived, cfg, notes=None):
    """Returns (new_lines, summary). `captures` is {object id: capture}. Lines for the user's
    notification are appended to `notes` as (text, level)."""
    notes = [] if notes is None else notes
    report = ["version %s; state hand-over: module global %s, capability instance %s"
              % (_VERSION, "kept" if survived[0] else "LOST", "kept" if survived[1] else "LOST")]
    if any(line.startswith(_TAG) for line in lines[:200]):
        return lines, "already processed, left untouched"

    if not captures:
        report.append("nothing captured at slice time (re-slice with the plugin selected): nothing injected")
    else:
        _inject_all(lines, captures, cfg, report, notes)

    stamp = [_TAG + " " + text + "\n" for text in report]
    lines[1 if lines else 0:1 if lines else 0] = stamp
    return lines, "; ".join(report)


def _object_name(cap, oid):
    name = cap.get("name") or "object"
    return "%s (#%s)" % (name, cap["index"]) if cap.get("index") is not None else "%s (id %s)" % (name, oid)


def _inject_all(lines, captures, cfg, report, notes):
    for oid, cap in captures.items():
        if cap.get("skipped"):
            report.append("%s: %s, left alone" % (_object_name(cap, oid), cap["skipped"]))
            continue
        st = cap["stats"]
        report.append("%s generated at %s: %d wave path(s) in %d region(s) on %d layer(s), %s mm; "
                      "slices cut on %d layer(s)%s"
                      % (_object_name(cap, oid), cap["generated_at"], st["paths"], st["regions"],
                         st["wave_layers"], _fmt(st["length_mm"]), cap["cut_layers"],
                         ("; gentle slopes left to the slicer on %d island-layer(s)" % st["narrow_kept"]
                          if st.get("narrow_kept") else "")
                         + ("; %s mm of kept wall" % _fmt(st["wall_mm"]) if st.get("wall_mm") else "")))
        if st["wave_layers"] and not cap["cut_layers"]:
            report.append("WARNING: %s: the slices were not cut, so the slicer's own walls/infill are still "
                          "there; re-slice from scratch with the plugin enabled" % _object_name(cap, oid))
            notes.append(("Wave Overhangs, %s: no waves added, the slicer reused an earlier slice. Change the "
                          "layer height and back to slice it again." % cap.get("name", "object"), "warning"))

    segments, info = _scan_gcode(lines, cfg["e_relative"])
    bbl = info["bbl"]
    instances = _instances(segments)
    labels = dict(_LABEL_FAMILIES).get(info["family"], "none")
    report.append("G-code: %s tags, %d layers, object labels: %s, %d instance(s)"
                  % ("Bambu" if bbl else "standard", info["layers"], labels, len(instances)))
    if info["family"] is None and len(captures) > 1:
        report.append("WARNING: %d objects but the G-code has no object labels; turn on 'Exclude objects' "
                      "or 'Label objects' (process settings, Others) so each copy can be placed"
                      % len(captures))
        notes.append(("Wave Overhangs: no waves added. With several objects, turn on Exclude objects or "
                      "Label objects (Others > G-code output).", "warning"))

    plan = []  # (end line index, layer capture, calibration, end state)
    used = set()
    for key, layers in instances.items():
        name = key if key is not None else "whole plate"
        oid, cal, candidates = _match_object(captures, layers, key)
        if oid is None:
            report.append("%s: no captured object matches (%s); skipped"
                          % (name, cal.get("why", "calibration failed")))
            if info["family"] is not None or len(captures) == 1:
                notes.append(("Wave Overhangs: couldn't place %s, so it got no waves. Slice again; if it "
                              "persists, see %s." % (name, _LOG_NAME), "warning"))
            continue
        used.add(oid)
        cap = captures[oid]
        waves = 0
        no_end = 0
        for gl in layers:
            cl = cap["layers"].get(_zkey(gl["z"]))
            if cl is None or not cl["paths"]:
                continue
            if gl["end"] is None:
                no_end += 1
                continue
            plan.append((gl["end"], cl, cal, gl["end_state"], cap["line_width"]))
            waves += 1
        report.append("%s: %s%s, offset X %s Y %s mm (deviation from it: 90th percentile %s mm, worst layer "
                      "%s mm; size mismatch %s mm; %d of %d layers compared), waves on %d layer(s)%s"
                      % (name, _object_name(cap, oid),
                         " (1 of %d identical matches)" % candidates if candidates > 1 else "",
                         _fmt(cal["dx"]), _fmt(cal["dy"]), _fmt(cal["spread_p90"]), _fmt(cal["spread_max"]),
                         _fmt(cal["size_mismatch"]), cal["samples"], len(layers), waves,
                         " (%d top layer(s) skipped)" % no_end if no_end else ""))
    for oid, cap in captures.items():
        if oid not in used and cap["stats"]["wave_layers"]:
            report.append("%s: has waves but was not placed (%s)"
                          % (_object_name(cap, oid), "no object labels" if info["family"] is None
                             else "not found in the G-code; another plate?"))

    if not cfg["enabled"]:
        report.append("enabled = 0: report only")
        return
    totals = {"length_mm": 0.0, "filament_mm": 0.0, "travel_mm": 0.0, "wall_mm": 0.0, "wall_s": 0.0}
    # Insert from the bottom of the file up so the earlier indices stay valid.
    plan.sort(key=lambda t: t[0], reverse=True)
    for end, cl, cal, state, line_width in plan:
        lines[end:end] = _wave_block(cl, state, cal, bbl, dict(cfg, line_width=line_width), totals)
    minutes = (totals["length_mm"] / max(cfg["print_speed"], 0.1)
               + totals["travel_mm"] / max(cfg["travel_speed"], 0.1) + totals["wall_s"]) / 60.0
    report.append("injected %d wave block(s): %s mm of wave line, %s mm of kept wall at %s mm/s, "
                  "%s mm of filament, about %s min"
                  % (len(plan), _fmt(totals["length_mm"]), _fmt(totals["wall_mm"]), _fmt(cfg.get("wall_speed", 0)),
                     _fmt(totals["filament_mm"]), _fmt(minutes)))
    if plan:
        notes.append(("Wave Overhangs: waves added to the exported G-code, %s of printing."
                      % _minutes_text(minutes), "info"))


_VERSION = "0.7.0"


# ============================================================================
# the capability
# ============================================================================

class WaveOverhangs(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Wave Overhangs"

    def get_default_config(self):
        return _DEFAULTS

    def execute(self, ctx):
        step = ctx.step
        if ctx.print is not None and step != orca.slicing.Step.psGCodePostProcess:
            self._refresh(ctx)
        if step == orca.slicing.Step.posSlice and ctx.object is not None:
            return self._slice(ctx)
        if step == orca.slicing.Step.posPerimeters and ctx.object is not None:
            return self._perimeters(ctx)
        if step == orca.slicing.Step.psGCodePostProcess and ctx.gcode_path:
            return self._inject(ctx)
        return orca.ExecutionResult.success()

    def _stores(self):
        store = getattr(self, "_wave_state", None)
        if store is None:
            store = {}
            self._wave_state = store
        return (_STATE, store)

    def _remember(self, ctx, cap):
        try:
            cap["name"] = ctx.object.model_object().name
        except Exception:
            pass
        for target in self._stores():
            target[ctx.object.id()] = cap
        self._refresh(ctx)

    def _refresh(self, ctx):
        """Drop captures of objects no longer in the print and renumber the rest.

        The index is the object's position in the print's object list, which is the object
        id in "_id_<n>_copy_" labels (GCode::assign_object_label_ids). It changes when other
        objects are added or removed while this one's steps stay cached, so it is refreshed
        on every slicing step. Only a tie-breaker; objects are matched by shape.
        """
        try:
            order = [o.id() for o in ctx.print.objects()]
        except AttributeError:
            return
        position = {oid: n for n, oid in enumerate(order)}
        for target in self._stores():
            for oid in list(target):
                if oid not in position:
                    del target[oid]
                else:
                    target[oid]["index"] = position[oid]

    @staticmethod
    def _skip_reason(ctx, p):
        """Why this object is left alone, or None."""
        if p["apply_to"] == "unsupported":
            try:
                if _truthy(ctx.object.config_value("enable_support")):
                    return "Orca supports are on"
            except Exception:
                pass
        return None

    def _slice(self, ctx):
        p = _params(self)
        started = time.time()
        skip = self._skip_reason(ctx, p)
        cap, cuts = _generate_object(ctx, p, waves=skip is None)
        if ctx.cancelled():
            return orca.ExecutionResult.success()
        if skip:
            cap["skipped"] = skip
        elif p["enabled"]:
            cap["cut_layers"] = _cut_slices(ctx, cuts)
        self._remember(ctx, cap)
        st = cap["stats"]
        name = cap.get("name") or "object"
        _write_log({"event": "slice", "object": name, "skipped": skip, "stats": st,
                    "cut_layers": cap["cut_layers"], "seconds": round(time.time() - started, 2), "settings": p})
        if skip:
            return orca.ExecutionResult.success("Wave Overhangs: %s left alone (%s)" % (name, skip))
        if st["wave_layers"]:
            text = ("Wave Overhangs, %s: waves on %d layer(s), %s"
                    % (name, st["wave_layers"],
                       _minutes_text(st["length_mm"] / max(p["print_speed_mm_s"], 0.1) / 60.0)))
            if st.get("wall_mm"):
                text += ", with a wall along the wide overhangs"
            if st.get("narrow_kept"):
                text += "; gentle slopes keep normal walls"
            _note(text, "slice")
        return orca.ExecutionResult.success(
            "Wave Overhangs: %d wave path(s) on %d layer(s); slices cut on %d layer(s)"
            % (st["paths"], st["wave_layers"], cap["cut_layers"]))

    def _perimeters(self, ctx):
        oid = ctx.object.id()
        cap = None
        for target in self._stores():
            cap = target.get(oid)
            if cap is not None:
                break
        if cap is None:
            # posSlice was cached from before the plugin was active: the slices can no
            # longer be cut, so generate for the report only and inject nothing.
            p = _params(self)
            skip = self._skip_reason(ctx, p)
            cap, _ = _generate_object(ctx, p, waves=skip is None)
            cap["generated_at"] = "posPerimeters (posSlice was cached)"
            if skip:
                cap["skipped"] = skip
            for entry in cap["layers"].values():
                entry["paths"] = []
        if ctx.cancelled():
            return orca.ExecutionResult.success()
        _capture_refs(ctx, cap)
        self._remember(ctx, cap)
        return orca.ExecutionResult.success("Wave Overhangs: reference boxes captured")

    def _inject(self, ctx):
        p = _params(self)
        instance_state = getattr(self, "_wave_state", None)
        survived = (bool(_STATE), bool(instance_state))
        captures = _STATE or instance_state or {}
        nozzle = _first_float(ctx.config_value("nozzle_diameter"), 0.4)
        cfg = {
            "enabled":           p["enabled"] != 0.0,
            "e_relative":        _truthy(ctx.config_value("use_relative_e_distances")),
            "filament_diameter": _first_float(ctx.config_value("filament_diameter"), 1.75),
            "retract":           min(_first_float(ctx.config_value("retraction_length"), 0.8), 2.0),
            "flow_mm3_per_mm":   max(p["flow_mm3_per_mm"], 0.0),
            "print_speed":       max(p["print_speed_mm_s"], 0.1),
            "travel_speed":      max(p["travel_speed_mm_s"], 1.0),
            "fan_speed":         min(max(p["fan_speed_percent"], 0.0), 100.0),
            "wall_speed":        _wall_speed(ctx, p),
            "connector_limit":   max(p["line_spacing_mm"], nozzle) + max(p["perimeter_overlap_mm"], 0.0),
            "line_width":        nozzle,
        }
        with open(ctx.gcode_path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            lines = f.readlines()
        before = list(lines)
        notes = []
        started = time.time()
        lines, summary = _post_process(lines, captures, survived, cfg, notes)
        if lines != before:
            with open(ctx.gcode_path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
                f.writelines(lines)
        report = [l[len(_TAG) + 1:].rstrip("\n") for l in lines[1:40] if l.startswith(_TAG + " ")]
        _write_log({"event": "gcode", "file": ctx.output_name or ctx.gcode_path, "report": report,
                    "seconds": round(time.time() - started, 2)})
        self._store_preview(ctx, p, lines, lines != before, cfg["e_relative"])
        for text, level in notes:
            _note(text, level)
        return orca.ExecutionResult.success("Wave Overhangs: " + summary)

    # --- preview panel -------------------------------------------------------------------------
    panel = None

    def _store_preview(self, ctx, p, lines, changed, e_relative):
        """Keep the data for the preview panel from this export (worker thread: no UI here)."""
        global _PREVIEW
        data = None
        if not changed and any(l.startswith(_TAG) for l in lines[:200]):
            return      # the file already had its waves (an export of the same file): keep what we have
        if p["preview"] != 0 and changed:
            started = time.time()
            try:
                pv = extract_preview(lines, e_relative)
                if pv["stats"]["wave_mm"] > 0:
                    data = {"data": pv, "fresh": True,
                            "title": os.path.basename(str(ctx.output_name or ctx.gcode_path))}
                    _write_log({"event": "preview_data", "numbers": pv["stats"]["numbers"],
                                "layers": len(pv["layers"]), "seconds": round(time.time() - started, 2)})
            except Exception as e:
                _write_log({"event": "preview_data", "error": "%s: %s" % (type(e).__name__, e)})
        _PREVIEW = data
        self._preview_state = data

    def _preview(self):
        return _PREVIEW or getattr(self, "_preview_state", None)

    def _preview_payload(self):
        pv = self._preview()
        return None if pv is None else dict(pv["data"], command="preview", title=pv["title"])

    def show_preview(self):
        """Open the preview panel, or bring the open one to the front and send it the latest data.
        UI thread (the notification link, the lifecycle event)."""
        payload = self._preview_payload()
        if payload is None:
            return "no preview data"
        if self.panel is not None and self.panel.is_open():
            self.panel.show()
            self.panel.post(payload)
            return "preview panel refreshed"
        self.panel = orca.host.ui.create_dock_panel(
            html=PREVIEW_PAGE, title="Wave preview", width=480, height=640,
            on_message=self._panel_message, on_close=self._panel_closed, dock="right")
        return "preview panel opened"

    def _panel_message(self, message):
        if (message or {}).get("command") == "preview_ready":
            payload = self._preview_payload()
            if payload is not None and self.panel is not None:
                self.panel.post(payload)

    def _panel_closed(self):
        self.panel = None

    def _link_clicked(self):
        try:
            _write_log({"event": "show_waves", "result": self.show_preview()})
        except Exception as e:
            _write_log({"event": "show_waves", "error": "%s: %s" % (type(e).__name__, e)})
        return True

    def on_lifecycle_event(self, event, ctx):
        """Show the queued results when the slicing job (slice, or slice + export) completes.
        Orca fires SlicingJobComplete on the UI thread; a cancelled job shows nothing and a
        failed one only its warnings. After an export that added waves the notification offers
        "Show waves", and an open preview panel is refreshed."""
        try:
            ev = getattr(orca, "LifecycleEvent", None)
            if ev is None or event != ev.SlicingJobComplete:
                return
            notes = _take_notes()
            code = getattr(orca, "LifecycleEvtCode", None)
            completed = code is None or getattr(ctx, "code", None) == code.Ok
            if not completed:
                notes = [n for n in notes if n[1] == "warning"] if ctx.code == code.Error else []
            pv = self._preview() if completed else None
            fresh = pv is not None and pv.get("fresh")
            if fresh:
                pv["fresh"] = False
                if self.panel is not None and self.panel.is_open():
                    self.show_preview()
            if not notes:
                return
            if any(level == "slice" for _, level in notes) and not any("exported G-code" in t for t, _ in notes):
                notes.append(("The waves are added to the G-code when it is exported; except on Bambu printers "
                              "the preview doesn't show them.", "info"))
            ui = orca.host.ui
            level = getattr(ui.NotificationLevel, notification_level(notes))
            if fresh:
                ui.push_notification(level, notification_text(notes), "Show waves", self._link_clicked)
            else:
                ui.push_notification(level, notification_text(notes))
        except Exception as e:  # never break Orca over a notification
            _write_log({"event": "notification", "error": "%s: %s" % (type(e).__name__, e)})


@orca.plugin
class WaveOverhangsPackage(orca.base):
    def register_capabilities(self):
        orca.register_capability(WaveOverhangs)
