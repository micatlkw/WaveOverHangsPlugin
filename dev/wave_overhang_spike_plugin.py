# /// script
# requires-python = ">=3.12"
#
# [tool.orcaslicer.plugin]
# name = "Wave Overhang Spike"
# description = "Spike: captures overhang areas at slice time, optionally removes the slicer's own toolpaths there, and draws a marker loop around each one in the exported G-code."
# author = "WaveOverHangsPlugin"
# version = "0.02"
# type = "slicing-pipeline"
# ///
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 WaveOverHangsPlugin contributors
"""Wave Overhang Spike -- proves the two halves of a G-code-level wave plugin can be joined.

NOT a wave-overhang implementation. It answers, before any of the real
algorithm is ported:

1. Does plugin state survive from the slicing hooks to the G-code post-process
   call? (There is no live Print at that step, so everything must be carried.)
2. Do the coordinates line up? Slice geometry is in object space; the G-code is
   in plate space.
3. Does an injected path show up in the preview / print where expected?
4. How should the slicer's own walls, bridges and infill in an overhang area be
   removed so waves can replace them? (the "mode" option, below)

How it works
------------
* Step.posSlice (per object): for every layer, overhang = this layer's islands
  minus the layer below, with thin slivers removed. Each overhang area
  (contour and holes) and its outline (inset by half a line width) is copied
  out as plain mm tuples, keyed by print Z. Every layer is captured before
  anything is cut, so "shrink" never makes the capture see a cut layer as the
  support of the layer above.
* Step.posPerimeters (per object): a reference bounding box of each layer's wall
  toolpaths is added (falls back to the slice outline when numpy is unavailable).
* Step.psGCodePostProcess: the G-code is parsed per layer; the bounding box of
  its wall extrusions is compared with the captured reference box to MEASURE
  the object-to-plate offset. With that offset, one "Custom" marker loop per
  overhang area is appended to the end of its layer.

Modes ("mode" in the plugin config)
-----------------------------------
* "marker": marker loops only; the slicer's toolpaths are left alone.
* "shrink": at posSlice the overhang areas are cut out of the layer's slices, so
  the slicer never generates walls or infill there. The layer above then hangs
  over air where the cut was, which changes how the slicer classifies it.
* "delete": the slices are left alone; at post-process the overhang layer's
  extrusion moves are clipped against the overhang areas and the parts inside
  become travel moves.

The report ends with a census, for each overhang layer and the layer above it,
of what is extruded inside the overhang area, by feature. Compare it between
modes.

A report is written as "; WAVE_SPIKE ..." comment lines right after the first
line of the G-code file. Read that to get the answers.

Limits (it is a spike): one object with one instance on the plate, no
sequential (by-object) printing, no wipe tower. If the measured offset is not
consistent the plugin writes the report and changes nothing else.

The marker loops extrude at marker_flow_ratio (default 5% of a normal line) so
an accidental print is harmless; raise it to 1.0 to print real lines.
"""
import json
import math

import orca

_DEFAULTS = {
    "mode":                  "marker",  # "marker", "shrink" or "delete" (see the module docstring)
    "min_overhang_width_mm": 0.5,   # overhang strips narrower than this are ignored (e.g. gently sloped walls)
    "min_overhang_area_mm2": 1.0,   # overhang areas smaller than this are ignored
    "marker_flow_ratio":     0.05,  # extrusion amount of the marker loop relative to a normal line
    "marker_speed_mm_s":     20.0,  # print speed of the marker loop
    "inject":                1.0,   # 0: measure and report only, leave the G-code untouched
}
_MODES = ("marker", "shrink", "delete")

_WALL_ROLES = ("Outer wall", "Inner wall", "Overhang wall")
_TAG = "; WAVE_SPIKE"
_CENSUS_LAYERS = 8  # overhang layers listed individually in the census

# Module-level hand-over store: object id -> capture dict. The same data is also
# kept on the capability instance, so the report can say which one survived.
_STATE = {}


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

def _params(self):
    try:
        src = json.loads(self.get_config())
    except (AttributeError, TypeError, ValueError):
        src = {}
    if not isinstance(src, dict):
        src = {}
    out = {}
    for key, default in _DEFAULTS.items():
        if isinstance(default, str):  # only "mode"
            value = str(src.get(key, default)).strip().lower()
            out[key] = value if value in _MODES else default
            continue
        try:
            out[key] = float(src[key])
        except (KeyError, TypeError, ValueError):
            out[key] = default
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


def _zkey(z):
    return round(float(z), 3)


def _median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _fmt(v):
    s = "%.3f" % v
    return s.rstrip("0").rstrip(".") if "." in s else s


def _mm_points(poly, k):
    return [(pt.x * k, pt.y * k) for pt in poly.points]


# ----------------------------------------------------------------------------
# half 1: capture at slice time
# ----------------------------------------------------------------------------

def _overhang_areas(layer, open_r, min_area_scaled):
    """[ExPolygon] of this layer's islands not covered by the layer below (copies)."""
    lower = layer.lower_layer
    if lower is None:
        return []
    lows = lower.lslices()
    out = []
    for island in layer.lslices():
        pieces = [island]
        for low in lows:
            nxt = []
            for piece in pieces:
                nxt.extend(piece.diff_ex(low))
            pieces = nxt
            if not pieces:
                break
        for piece in pieces:
            # Morphological opening: drops strips narrower than 2 * open_r.
            for core in piece.offset(-open_r):
                for opened in core.offset(open_r):
                    if opened.area() >= min_area_scaled:
                        out.append(opened)
    return out


def _toolpath_bbox(layer, k):
    """(min_x, min_y, max_x, max_y) in mm of the layer's wall toolpaths. Needs numpy."""
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
    if box is None:
        return None
    return tuple(v * k for v in box)


def _outline_bbox(layer, k):
    """(min_x, min_y, max_x, max_y) in mm of the layer's slice outline. No numpy needed."""
    xs = []
    ys = []
    for island in layer.lslices():
        for pt in island.contour.points:
            xs.append(pt.x)
            ys.append(pt.y)
    if not xs:
        return None
    return (min(xs) * k, min(ys) * k, max(xs) * k, max(ys) * k)


def _capture_areas(ctx, p):
    """Overhang areas and marker outlines of every layer, from the slices as they are now.

    Returns (capture, found). `capture` holds plain values only. `found` is
    [(layer, [ExPolygon])] for "shrink"; it is valid only during this execute().
    """
    obj = ctx.object
    k = orca.slicing.unscale(1)  # mm per scaled unit, read live
    line_w = _first_float(ctx.config_value("nozzle_diameter"), 0.4)
    open_r = int(round(p["min_overhang_width_mm"] * 0.5 / k))
    half_w = int(round(line_w * 0.5 / k))
    min_area = p["min_overhang_area_mm2"] / (k * k)

    layers = {}
    found = []
    overhang_layers = 0
    regions = 0
    collapsed = 0
    for layer in obj.layers():
        if ctx.cancelled():
            break
        areas = _overhang_areas(layer, open_r, min_area)
        loops = []
        areas_mm = []
        for area in areas:
            areas_mm.append((_mm_points(area.contour, k), [_mm_points(h, k) for h in area.holes]))
            inset = area.offset(-half_w)
            if not inset:
                collapsed += 1
                continue
            for ex in inset:
                pts = _mm_points(ex.contour, k)
                if len(pts) >= 3:
                    loops.append(pts)
        if loops:
            overhang_layers += 1
            regions += len(loops)
        if areas:
            found.append((layer, areas))
        layers[_zkey(layer.print_z)] = {
            "z": float(layer.print_z),
            "height": float(layer.height),
            "ref": None,
            "ref_kind": None,
            "loops": loops,
            "areas": areas_mm,
        }

    instance_offset = None
    try:
        instances = obj.model_object().instances()
        instance_offset = [tuple(inst.offset()) for inst in instances]
    except Exception:
        pass

    return {
        "layers": layers,
        "line_width": line_w,
        "overhang_layers": overhang_layers,
        "regions": regions,
        "collapsed": collapsed,
        "instance_offset": instance_offset,
        "areas_step": None,
        "mode": p["mode"],
        "cut_layers": 0,
    }, found


def _cut_overhangs(found):
    """"shrink": cut the overhang areas out of every region's slices. Returns layers cut."""
    cut = 0
    for layer, areas in found:
        for region in layer.regions():
            by_type = {}
            for surface in region.slices.surfaces:
                pieces = [surface.expolygon]
                for area in areas:
                    pieces = [q for piece in pieces for q in piece.diff_ex(area)]
                    if not pieces:
                        break
                if pieces:
                    by_type.setdefault(surface.surface_type, []).extend(pieces)
            region.slices.clear()
            for surface_type, expolys in by_type.items():
                region.slices.append(expolys, surface_type)
        # Re-derive the merged islands, so perimeters and the classification of
        # the layer above see the cut geometry.
        layer.make_slices()
        cut += 1
    return cut


def _capture_refs(ctx, cap):
    """Add each layer's reference bounding box (for the offset calibration) to `cap`."""
    k = orca.slicing.unscale(1)
    use_toolpaths = True
    for layer in ctx.object.layers():
        if ctx.cancelled():
            break
        ref = None
        if use_toolpaths:
            try:
                ref = _toolpath_bbox(layer, k)
            except Exception:
                use_toolpaths = False  # numpy missing: use the slice outline from here on
        kind = "toolpath"
        if ref is None:
            ref = _outline_bbox(layer, k)
            kind = "outline"
        entry = cap["layers"].get(_zkey(layer.print_z))
        if entry is not None:
            entry["ref"] = ref
            entry["ref_kind"] = kind


# ----------------------------------------------------------------------------
# half 2: G-code parsing, calibration and injection
# ----------------------------------------------------------------------------

def _parse_words(code):
    """'G1 X1.2 Y3 E.5' -> ('G1', {'X': 1.2, 'Y': 3.0, 'E': 0.5})"""
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

    "retracted" is the filament length (mm) pulled back by G1 E moves (wipes
    included) and not yet pushed back; "fw_retracted" is True between a firmware
    G10 and G11. "e" is the E axis position (meaningful in absolute E mode).
    """
    _FIELDS = ("x", "y", "e", "f", "e_relative", "retracted", "fw_retracted",
               "feature", "feature_line", "width_line", "height_line")

    def __init__(self, e_relative, state=None):
        self.x = self.y = None
        self.e = 0.0
        self.f = None
        self.e_relative = e_relative
        self.retracted = 0.0
        self.fw_retracted = False
        self.feature = None
        self.feature_line = self.width_line = self.height_line = None
        if state:
            for name in self._FIELDS:
                if name in state:
                    setattr(self, name, state[name])

    def snapshot(self):
        return {name: getattr(self, name) for name in self._FIELDS}

    def feed(self, raw):
        """Advance over one line. Returns a move dict for G0-G3, else None."""
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
                    # Push back what was retracted first; only the rest is extrusion.
                    extruding = de > self.retracted + 1e-6
                    self.retracted = max(0.0, self.retracted - de)
            self.x = w.get("X", self.x)
            self.y = w.get("Y", self.y)
            return {"cmd": cmd, "w": w, "x0": x0, "y0": y0, "x1": self.x, "y1": self.y,
                    "de": de, "extruding": extruding,
                    "moved": ("X" in w) or ("Y" in w)}
        return None


def _scan_gcode(lines, e_relative):
    """Walk the file once. Returns (layers, bbl_flavor).

    Each layer: {"z", "marker" (line index of its layer-change tag), "box" (wall
    extrusion bbox or None), "end" (line index where the layer ends, or None for the
    last layer), "start_state" / "end_state" (_Stream snapshots at "marker" / "end")}.
    """
    layers = []
    cur = None
    bbl = False
    want_z = False
    s = _Stream(e_relative)

    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith(";"):
            body = line[1:]
            if body.startswith(" CHANGE_LAYER") or body.startswith("LAYER_CHANGE"):
                bbl = bbl or body.startswith(" CHANGE_LAYER")
                if cur is not None:
                    cur["end"] = i
                    cur["end_state"] = s.snapshot()
                cur = {"z": None, "marker": i, "box": None, "end": None,
                       "start_state": s.snapshot(), "end_state": None}
                layers.append(cur)
                want_z = True
                continue
            if body.startswith(" Z_HEIGHT: ") or body.startswith("Z:"):
                if want_z and cur is not None:
                    try:
                        cur["z"] = float(body.split(":", 1)[1])
                    except ValueError:
                        pass
                    want_z = False
                continue
        mv = s.feed(raw)
        if (mv is not None and mv["extruding"] and mv["moved"] and cur is not None
                and s.feature in _WALL_ROLES and None not in (mv["x0"], mv["y0"], mv["x1"], mv["y1"])):
            b = cur["box"]
            lo_x, hi_x = min(mv["x0"], mv["x1"]), max(mv["x0"], mv["x1"])
            lo_y, hi_y = min(mv["y0"], mv["y1"]), max(mv["y0"], mv["y1"])
            cur["box"] = (lo_x, lo_y, hi_x, hi_y) if b is None else (
                min(b[0], lo_x), min(b[1], lo_y), max(b[2], hi_x), max(b[3], hi_y))
    return layers, bbl


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
    spread = max(max(abs(v - dx) for v in dxs), max(abs(v - dy) for v in dys))
    # Judge consistency on the better 90% of layers: a few odd layers (thin walls,
    # a brim counted as a wall, ...) must not veto an otherwise clean measurement.
    devs = sorted(max(abs(a - dx), abs(b - dy)) for a, b in zip(dxs, dys))
    p90 = devs[min(len(devs) - 1, int(0.9 * len(devs)))]
    size = max(abs(_median(dws)), abs(_median(dhs)))
    cal.update({"dx": dx, "dy": dy, "spread_max": spread, "spread_p90": p90, "size_mismatch": size})
    if p90 > 0.5:
        cal["why"] = "offset differs between layers (more than one object/instance on the plate?)"
    elif size > 1.5:
        cal["why"] = "G-code walls and captured geometry differ in size (wrong object or stale capture?)"
    else:
        cal["ok"] = True
    return cal


# --- planar geometry on mm tuples (plate space) -------------------------------

def _plate_areas(areas_mm, cal):
    """Captured areas shifted to plate space, as [(contour, holes, bbox)]."""
    out = []
    for contour, holes in areas_mm:
        c = [(x + cal["dx"], y + cal["dy"]) for x, y in contour]
        hs = [[(x + cal["dx"], y + cal["dy"]) for x, y in h] for h in holes]
        xs = [pt[0] for pt in c]
        ys = [pt[1] for pt in c]
        out.append((c, hs, (min(xs), min(ys), max(xs), max(ys))))
    return out


def _point_in_poly(x, y, poly):
    inside = False
    xj, yj = poly[-1]
    for xi, yi in poly:
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        xj, yj = xi, yi
    return inside


def _in_areas(x, y, areas):
    for contour, holes, box in areas:
        if (box[0] <= x <= box[2] and box[1] <= y <= box[3] and _point_in_poly(x, y, contour)
                and not any(_point_in_poly(x, y, h) for h in holes)):
            return True
    return False


def _split_segment(x0, y0, x1, y1, areas):
    """[(t0, t1, inside)] covering the segment from t=0 to t=1, alternating inside/outside."""
    dx, dy = x1 - x0, y1 - y0
    lo_x, hi_x = min(x0, x1), max(x0, x1)
    lo_y, hi_y = min(y0, y1), max(y0, y1)
    ts = {0.0, 1.0}
    for contour, holes, box in areas:
        if box[0] > hi_x or box[2] < lo_x or box[1] > hi_y or box[3] < lo_y:
            continue
        for poly in [contour] + holes:
            qx0, qy0 = poly[-1]
            for qx1, qy1 in poly:
                sx, sy = qx1 - qx0, qy1 - qy0
                den = dx * sy - dy * sx
                if abs(den) > 1e-12:
                    px, py = qx0 - x0, qy0 - y0
                    t = (px * sy - py * sx) / den
                    u = (px * dy - py * dx) / den
                    if 0.0 < t < 1.0 and 0.0 <= u <= 1.0:
                        ts.add(t)
                qx0, qy0 = qx1, qy1
    ts = sorted(ts)
    pieces = []
    for a, b in zip(ts, ts[1:]):
        if b - a < 1e-9:
            continue
        m = 0.5 * (a + b)
        inside = _in_areas(x0 + m * dx, y0 + m * dy, areas)
        if pieces and pieces[-1][2] == inside:
            pieces[-1] = (pieces[-1][0], b, inside)
        else:
            pieces.append((a, b, inside))
    return pieces or [(0.0, 1.0, False)]


# --- "delete" mode and the census ---------------------------------------------

def _new_delete_stats():
    return {"layers": 0, "moves": 0, "removed": 0, "split": 0, "filament": 0.0,
            "by_feature": {}, "skipped": 0}


def _delete_in_layer(lines, gl, areas, stats):
    """Replacement for lines[marker:end] of one G-code layer in which nothing is
    extruded inside `areas`: the inside parts of extrusion moves become travel."""
    s = _Stream(None, gl["start_state"])
    out = []
    touched = False
    for raw in lines[gl["marker"]:gl["end"]]:
        retracted_before = s.retracted
        e_before = s.e
        mv = s.feed(raw)
        if (mv is None or not mv["extruding"] or not mv["moved"]
                or None in (mv["x0"], mv["y0"], mv["x1"], mv["y1"])):
            out.append(raw)
            continue
        x0, y0, x1, y1 = mv["x0"], mv["y0"], mv["x1"], mv["y1"]
        pieces = _split_segment(x0, y0, x1, y1, areas)
        if not any(inside for _, _, inside in pieces):
            out.append(raw)
            continue
        w = mv["w"]
        if mv["cmd"] != "G1" or "Z" in w or retracted_before > 1e-6:
            # Arcs, Z moves and moves that also un-retract are left alone in the spike.
            stats["skipped"] += 1
            out.append(raw)
            continue

        de = mv["de"]
        e_run = e_before
        removed = 0.0
        for n, (a, b, inside) in enumerate(pieces):
            text = "G1 X" + _fmt(x0 + b * (x1 - x0)) + " Y" + _fmt(y0 + b * (y1 - y0))
            part = de * (b - a)
            if inside:
                removed += part
            elif s.e_relative:
                text += " E%.5f" % part
            else:
                e_run += part
                text += " E%.5f" % e_run
            if n == 0 and "F" in w:
                text += " F" + _fmt(w["F"])
            out.append(text + "\n")
        if not s.e_relative:
            out.append("G92 E%.5f\n" % s.e)  # resync the absolute E counter

        touched = True
        stats["moves"] += 1
        stats["removed" if len(pieces) == 1 else "split"] += 1
        stats["filament"] += removed
        feature = s.feature or "?"
        stats["by_feature"][feature] = stats["by_feature"].get(feature, 0) + 1
    if touched:
        stats["layers"] += 1
    return out


def _census(lines, gl, areas):
    """{feature: mm of extrusion path inside `areas`} for one G-code layer."""
    s = _Stream(None, gl["start_state"])
    end = gl["end"] if gl["end"] is not None else len(lines)
    lengths = {}
    for raw in lines[gl["marker"]:end]:
        mv = s.feed(raw)
        if (mv is None or not mv["extruding"] or not mv["moved"]
                or None in (mv["x0"], mv["y0"], mv["x1"], mv["y1"])):
            continue
        length = math.hypot(mv["x1"] - mv["x0"], mv["y1"] - mv["y0"])
        for a, b, inside in _split_segment(mv["x0"], mv["y0"], mv["x1"], mv["y1"], areas):
            if inside:
                feature = s.feature or "?"
                lengths[feature] = lengths.get(feature, 0.0) + (b - a) * length
    return lengths


def _fmt_lengths(lengths):
    items = sorted(((v, k) for k, v in lengths.items() if v >= 0.05), reverse=True)
    return ", ".join("%s %s" % (k, _fmt(v)) for v, k in items) or "nothing"


def _census_report(lines, cap, cal, e_relative):
    gcode_layers, _ = _scan_gcode(lines, e_relative)
    rows = []
    for idx, gl in enumerate(gcode_layers):
        if gl["z"] is None:
            continue
        cl = cap["layers"].get(_zkey(gl["z"]))
        if cl is None or not cl["areas"]:
            continue
        if len(rows) >= _CENSUS_LAYERS:
            rows.append("census: further overhang layers not listed")
            break
        areas = _plate_areas(cl["areas"], cal)
        here = _fmt_lengths(_census(lines, gl, areas))
        above = "none (top layer)"
        if idx + 1 < len(gcode_layers):
            nxt = gcode_layers[idx + 1]
            above = "z=%s: %s" % (_fmt(nxt["z"]) if nxt["z"] is not None else "?",
                                  _fmt_lengths(_census(lines, nxt, areas)))
        rows.append("census z=%s (mm extruded inside the overhang area): this layer: %s; layer above %s"
                    % (_fmt(gl["z"]), here, above))
    return rows


# --- marker loops ---------------------------------------------------------------

def _marker_block(layer_cap, state, cal, bbl, cfg):
    """G-code lines drawing every marker loop of one layer, leaving the stream state as found.

    Retraction is balanced against the state the layer ended in: if the nozzle is
    already retracted (G1 E or firmware G10) the block does not retract again before
    its first travel, and it restores that retraction at the end, so the slicer's own
    un-retract that follows still pushes back exactly what it pulled.
    """
    w = cfg["line_width"]
    h = layer_cap["height"]
    # Rounded-rectangle cross-section of a normal line, scaled by the marker flow ratio.
    area = max((w - h) * h + math.pi * (h * 0.5) ** 2, 0.0) * cfg["flow_ratio"]
    e_per_mm = area / (math.pi * (cfg["filament_diameter"] * 0.5) ** 2)
    retract = cfg["retract"]
    f_print = cfg["speed"] * 60.0
    f_travel = cfg["travel_speed"] * 60.0

    out = [_TAG + "_START z=" + _fmt(layer_cap["z"]) + " loops=" + str(len(layer_cap["loops"])) + "\n"]
    if bbl:
        out += ["; FEATURE: Custom\n", "; LINE_WIDTH: " + _fmt(w) + "\n", "; LAYER_HEIGHT: " + _fmt(h) + "\n"]
    else:
        out += [";TYPE:Custom\n", ";WIDTH:" + _fmt(w) + "\n", ";HEIGHT:" + _fmt(h) + "\n"]
    if not state["e_relative"]:
        out.append("M83\n")

    def pull(kind, amount):
        if kind == "fw":
            return ["G10\n"]
        return ["G1 E-" + ("%.5f" % amount) + " F1800\n"] if amount > 0.0 else []

    def push(kind, amount):
        if kind == "fw":
            return ["G11\n"]
        return ["G1 E" + ("%.5f" % amount) + " F1800\n"] if amount > 0.0 else []

    # How the nozzle is retracted when the block starts: the block keeps using the
    # same mechanism, and re-applies it at the end.
    if state.get("fw_retracted"):
        entry_kind, entry_amount = "fw", 0.0
    elif state.get("retracted", 0.0) > 1e-4:
        entry_kind, entry_amount = "g1", state["retracted"]
    else:
        entry_kind, entry_amount = None, 0.0
    kind = entry_kind or "g1"

    for n, loop in enumerate(layer_cap["loops"]):
        pts = [(px + cal["dx"], py + cal["dy"]) for px, py in loop]
        if n == 0 and entry_kind is not None:
            amount = entry_amount  # already retracted: travel as is
        else:
            amount = retract
            out += pull(kind, amount)
        out.append("G1 X" + _fmt(pts[0][0]) + " Y" + _fmt(pts[0][1]) + " F" + _fmt(f_travel) + "\n")
        out += push(kind, amount)
        out.append("G1 F" + _fmt(f_print) + "\n")
        prev = pts[0]
        for pt in pts[1:] + [pts[0]]:
            d = math.hypot(pt[0] - prev[0], pt[1] - prev[1])
            if d < 1e-4:
                continue
            out.append("G1 X" + _fmt(pt[0]) + " Y" + _fmt(pt[1]) + " E" + ("%.5f" % (d * e_per_mm)) + "\n")
            prev = pt
    if entry_kind is not None:
        out += pull(entry_kind, entry_amount)  # hand the nozzle back as retracted as it was
    if not state["e_relative"]:
        out += ["M82\n", "G92 E" + ("%.5f" % state["e"]) + "\n"]
    if state["f"] is not None:
        out.append("G1 F" + _fmt(state["f"]) + "\n")
    # Re-announce the tags that were active, so the next moves are not shown as "Custom".
    for tag_line in (state["feature_line"], state["width_line"], state["height_line"]):
        if tag_line is not None:
            out.append(tag_line if tag_line.endswith("\n") else tag_line + "\n")
    out.append(_TAG + "_END\n")
    return out


def _post_process(lines, captures, survived, cfg):
    """Returns (new_lines, summary). `captures` is {object id: capture}."""
    report = ["state hand-over: module global %s, capability instance %s"
              % ("SURVIVED" if survived[0] else "LOST", "SURVIVED" if survived[1] else "LOST")]
    injected = 0

    if any(line.startswith(_TAG + "_START") or line.startswith(_TAG + " ") for line in lines):
        return lines, "already processed, left untouched"

    if not captures:
        report.append("nothing captured at slice time: cannot calibrate or inject")
    elif len(captures) > 1:
        report.append("captured %d objects; the spike supports exactly one, nothing injected" % len(captures))
    else:
        cap = next(iter(captures.values()))
        mode = cfg["mode"]
        report.append("mode: %s (at slice time: %s); overhang areas captured at %s; slices cut on %d layer(s)"
                      % (mode, cap["mode"], cap["areas_step"], cap["cut_layers"]))
        if mode == "shrink" and cap["cut_layers"] == 0 and cap["overhang_layers"] > 0:
            report.append("WARNING: shrink is selected but no slices were cut (posSlice did not run "
                          "with shrink selected); re-slice from scratch")
        elif mode != "shrink" and cap["cut_layers"] > 0:
            report.append("WARNING: the slices were cut by shrink although the mode is now %s" % mode)
        kinds = sorted({l["ref_kind"] or "none" for l in cap["layers"].values()})
        report.append("captured %d layers, %d with overhangs, %d overhang areas (%d too thin to outline)"
                      % (len(cap["layers"]), cap["overhang_layers"], cap["regions"], cap["collapsed"]))
        report.append("reference geometry: " + "/".join(kinds))
        if cap["instance_offset"] is not None:
            report.append("model instance offset(s): " + "; ".join(
                "(" + ", ".join(_fmt(v) for v in off) + ")" for off in cap["instance_offset"]))

        gcode_layers, bbl = _scan_gcode(lines, cfg["e_relative"])
        cal = _calibrate(cap["layers"], gcode_layers)
        report.append("G-code flavor: %s; layers in G-code %d, matched by Z %d, usable for calibration %d"
                      % ("Bambu tags" if bbl else "standard tags", len(gcode_layers), cal["matched"], cal["samples"]))
        if "dx" in cal:
            report.append("measured object->G-code offset: X %s Y %s mm" % (_fmt(cal["dx"]), _fmt(cal["dy"])))
            report.append("offset consistency: 90%% of layers within %s mm, worst layer %s mm; size mismatch %s mm"
                          % (_fmt(cal["spread_p90"]), _fmt(cal["spread_max"]), _fmt(cal["size_mismatch"])))
        if not cal["ok"]:
            report.append("calibration FAILED: " + cal["why"] + "; nothing injected")
        elif not cfg["inject"]:
            report.append("inject = 0: report only")
            report += _census_report(lines, cap, cal, cfg["e_relative"])
        else:
            cfg = dict(cfg, line_width=cap["line_width"])
            delete = mode == "delete"
            dstats = _new_delete_stats()
            no_end = 0
            # Bottom-up scan order, edited top-down so earlier line indices stay valid.
            for gl in reversed(gcode_layers):
                if gl["z"] is None:
                    continue
                cl = cap["layers"].get(_zkey(gl["z"]))
                if cl is None or not (cl["loops"] or (delete and cl["areas"])):
                    continue
                if gl["end"] is None:
                    no_end += 1  # last layer: no following layer-change tag to anchor on
                    continue
                end = gl["end"]
                if delete and cl["areas"]:
                    new = _delete_in_layer(lines, gl, _plate_areas(cl["areas"], cal), dstats)
                    lines[gl["marker"]:end] = new
                    end = gl["marker"] + len(new)
                if cl["loops"]:
                    lines[end:end] = _marker_block(cl, gl["end_state"], cal, bbl, cfg)
                    injected += len(cl["loops"])
            report.append("injected %d marker loop(s)" % injected
                          + (" (%d layer(s) skipped: top layer has no anchor)" % no_end if no_end else ""))
            if delete:
                report.append("delete: %d extrusion move(s) clipped on %d layer(s) (%d removed whole, %d split), "
                              "%s mm of filament removed; by feature: %s; %d move(s) left alone (arc, Z or un-retract)"
                              % (dstats["moves"], dstats["layers"], dstats["removed"], dstats["split"],
                                 _fmt(dstats["filament"]),
                                 ", ".join("%s %d" % kv for kv in sorted(dstats["by_feature"].items())) or "none",
                                 dstats["skipped"]))
            report += _census_report(lines, cap, cal, cfg["e_relative"])

    stamp = [_TAG + " " + text + "\n" for text in report]
    lines[1 if lines else 0:1 if lines else 0] = stamp
    return lines, "; ".join(report)


# ----------------------------------------------------------------------------
# the capability
# ----------------------------------------------------------------------------

class WaveOverhangSpike(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Wave Overhang Spike"

    def get_default_config(self):
        return _DEFAULTS

    def execute(self, ctx):
        step = ctx.step
        if step == orca.slicing.Step.posSlice and ctx.object is not None:
            return self._capture_slice(ctx)
        if step == orca.slicing.Step.posPerimeters and ctx.object is not None:
            return self._capture_perimeters(ctx)
        if step == orca.slicing.Step.psGCodePostProcess and ctx.gcode_path:
            return self._inject(ctx)
        return orca.ExecutionResult.success()

    def _stores(self):
        store = getattr(self, "_spike_state", None)
        if store is None:
            store = {}
            self._spike_state = store
        return (_STATE, store)

    def _remember(self, ctx, cap):
        """Store `cap` for this object in both hand-over stores, dropping objects no longer printed."""
        live = None
        try:
            live = {o.id() for o in ctx.print.objects()}
        except AttributeError:
            pass
        for target in self._stores():
            if live is not None:
                for stale in [oid for oid in target if oid not in live]:
                    del target[stale]
            target[ctx.object.id()] = cap

    def _capture_slice(self, ctx):
        p = _params(self)
        cap, found = _capture_areas(ctx, p)
        if ctx.cancelled():
            return orca.ExecutionResult.success()
        cap["areas_step"] = "posSlice"
        if p["mode"] == "shrink":
            cap["cut_layers"] = _cut_overhangs(found)
        self._remember(ctx, cap)
        return orca.ExecutionResult.success(
            "Wave Overhang Spike: %d overhang areas on %d layers; cut from the slices on %d layers"
            % (cap["regions"], cap["overhang_layers"], cap["cut_layers"]))

    def _capture_perimeters(self, ctx):
        oid = ctx.object.id()
        cap = None
        for target in self._stores():
            cap = target.get(oid)
            if cap is not None:
                break
        if cap is None:
            # posSlice did not fire (its result was cached from before this plugin
            # was active): capture the areas from the slices as they are now.
            cap, _ = _capture_areas(ctx, _params(self))
            cap["areas_step"] = "posPerimeters"
        if ctx.cancelled():
            return orca.ExecutionResult.success()
        _capture_refs(ctx, cap)
        self._remember(ctx, cap)
        return orca.ExecutionResult.success(
            "Wave Overhang Spike: reference boxes captured for %d layers" % len(cap["layers"]))

    def _inject(self, ctx):
        p = _params(self)
        instance_state = getattr(self, "_spike_state", None)
        survived = (bool(_STATE), bool(instance_state))
        captures = _STATE or instance_state or {}

        rel = str(ctx.config_value("use_relative_e_distances")).strip().lower()
        cfg = {
            "mode":              p["mode"],
            "e_relative":        rel in ("1", "true"),
            "filament_diameter": _first_float(ctx.config_value("filament_diameter"), 1.75),
            "retract":           min(_first_float(ctx.config_value("retraction_length"), 0.8), 2.0),
            "travel_speed":      _first_float(ctx.config_value("travel_speed"), 120.0),
            "speed":             max(p["marker_speed_mm_s"], 1.0),
            "flow_ratio":        max(p["marker_flow_ratio"], 0.0),
            "inject":            p["inject"] != 0.0,
            "line_width":        0.4,
        }

        # surrogateescape keeps any non-UTF-8 bytes intact through the round trip.
        with open(ctx.gcode_path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            lines = f.readlines()
        before = list(lines)
        lines, summary = _post_process(lines, captures, survived, cfg)
        if lines != before:
            with open(ctx.gcode_path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
                f.writelines(lines)
        return orca.ExecutionResult.success("Wave Overhang Spike: " + summary)


@orca.plugin
class WaveOverhangSpikePackage(orca.base):
    def register_capabilities(self):
        orca.register_capability(WaveOverhangSpike)
