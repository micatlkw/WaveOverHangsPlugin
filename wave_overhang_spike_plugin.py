# /// script
# requires-python = ">=3.12"
#
# [tool.orcaslicer.plugin]
# name = "Wave Overhang Spike"
# description = "Spike: captures overhang areas at slice time and draws a marker loop around each one in the exported G-code."
# author = "WaveOverHangsPlugin"
# version = "0.01"
# type = "slicing-pipeline"
# ///
"""Wave Overhang Spike -- proves the two halves of a G-code-level wave plugin can be joined.

NOT a wave-overhang implementation. It answers three questions before any of
the real algorithm is ported:

1. Does plugin state survive from the slicing hooks to the G-code post-process
   call? (There is no live Print at that step, so everything must be carried.)
2. Do the coordinates line up? Slice geometry is in object space; the G-code is
   in plate space.
3. Does an injected path show up in the preview / print where expected?

How it works
------------
* Step.posPerimeters (per object): for every layer, overhang = this layer's
  islands minus the layer below, with thin slivers removed. The outline of each
  overhang area (inset by half a line width) is copied out as plain mm tuples,
  keyed by print Z. A reference bounding box of the layer's wall toolpaths is
  copied too (falls back to the slice outline when numpy is unavailable).
* Step.psGCodePostProcess: the G-code is parsed per layer; the bounding box of
  its wall extrusions is compared with the captured reference box to MEASURE
  the object-to-plate offset. With that offset, one "Custom" marker loop per
  overhang area is appended to the end of its layer.

A report is written as "; WAVE_SPIKE ..." comment lines right after the first
line of the G-code file. Read that to get the answers.

Limits (it is a spike): one object with one instance on the plate, no
sequential (by-object) printing, no wipe tower. If the measured offset is not
consistent the plugin writes the report and injects nothing.

The marker loops extrude at marker_flow_ratio (default 5% of a normal line) so
an accidental print is harmless; raise it to 1.0 to print real lines.
"""
import json
import math

import orca

_DEFAULTS = {
    "min_overhang_width_mm": 0.5,   # overhang strips narrower than this are ignored (e.g. gently sloped walls)
    "min_overhang_area_mm2": 1.0,   # overhang areas smaller than this are ignored
    "marker_flow_ratio":     0.05,  # extrusion amount of the marker loop relative to a normal line
    "marker_speed_mm_s":     20.0,  # print speed of the marker loop
    "inject":                1.0,   # 0: measure and report only, leave the toolpaths untouched
}

_WALL_ROLES = ("Outer wall", "Inner wall", "Overhang wall")
_TAG = "; WAVE_SPIKE"

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
    out = {}
    for key, default in _DEFAULTS.items():
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


def _capture_object(ctx, p):
    obj = ctx.object
    k = orca.slicing.unscale(1)  # mm per scaled unit, read live
    line_w = _first_float(ctx.config_value("nozzle_diameter"), 0.4)
    open_r = int(round(p["min_overhang_width_mm"] * 0.5 / k))
    half_w = int(round(line_w * 0.5 / k))
    min_area = p["min_overhang_area_mm2"] / (k * k)

    use_toolpaths = True
    layers = {}
    overhang_layers = 0
    regions = 0
    collapsed = 0
    for layer in obj.layers():
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

        loops = []
        for area in _overhang_areas(layer, open_r, min_area):
            inset = area.offset(-half_w)
            if not inset:
                collapsed += 1
                continue
            for ex in inset:
                pts = [(pt.x * k, pt.y * k) for pt in ex.contour.points]
                if len(pts) >= 3:
                    loops.append(pts)
        if loops:
            overhang_layers += 1
            regions += len(loops)
        layers[_zkey(layer.print_z)] = {
            "z": float(layer.print_z),
            "height": float(layer.height),
            "ref": ref,
            "ref_kind": kind,
            "loops": loops,
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
    }


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


def _scan_gcode(lines, e_relative):
    """Walk the file once. Returns (layers, bbl_flavor).

    Each layer: {"z", "marker" (line index of its layer-change tag), "box" (wall
    extrusion bbox or None), "end" (line index where the layer ends, or None for the
    last layer), "end_state" (feature/width/height tag lines, F, E, E mode at "end")}.
    """
    layers = []
    cur = None
    bbl = False
    want_z = False
    x = y = None
    last_e = 0.0
    last_f = None
    feature = None
    feature_line = width_line = height_line = None

    def snapshot():
        return {
            "feature_line": feature_line, "width_line": width_line, "height_line": height_line,
            "f": last_f, "e": last_e, "e_relative": e_relative,
        }

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if line[0] == ";":
            body = line[1:]
            if body.startswith(" CHANGE_LAYER") or body.startswith("LAYER_CHANGE"):
                bbl = bbl or body.startswith(" CHANGE_LAYER")
                if cur is not None:
                    cur["end"] = i
                    cur["end_state"] = snapshot()
                cur = {"z": None, "marker": i, "box": None, "end": None, "end_state": None}
                layers.append(cur)
                want_z = True
            elif body.startswith(" Z_HEIGHT: ") or body.startswith("Z:"):
                if want_z and cur is not None:
                    try:
                        cur["z"] = float(body.split(":", 1)[1])
                    except ValueError:
                        pass
                    want_z = False
            elif body.startswith(" FEATURE: ") or body.startswith("TYPE:"):
                feature = body.split(":", 1)[1].strip()
                feature_line = raw
            elif body.startswith(" LINE_WIDTH: ") or body.startswith("WIDTH:"):
                width_line = raw
            elif body.startswith(" LAYER_HEIGHT: ") or body.startswith("HEIGHT:"):
                height_line = raw
            continue

        cmd, w = _parse_words(line.split(";", 1)[0])
        if cmd == "M82":
            e_relative = False
        elif cmd == "M83":
            e_relative = True
        elif cmd == "G92":
            if "E" in w:
                last_e = w["E"]
        elif cmd in ("G0", "G1", "G2", "G3"):
            if "F" in w:
                last_f = w["F"]
            nx = w.get("X", x)
            ny = w.get("Y", y)
            extruding = False
            if "E" in w:
                extruding = (w["E"] > 0.0) if e_relative else (w["E"] > last_e)
                if not e_relative:
                    last_e = w["E"]
            moved = ("X" in w) or ("Y" in w)
            if (extruding and moved and cur is not None and feature in _WALL_ROLES
                    and x is not None and y is not None and nx is not None and ny is not None):
                b = cur["box"]
                lo_x, hi_x = min(x, nx), max(x, nx)
                lo_y, hi_y = min(y, ny), max(y, ny)
                cur["box"] = (lo_x, lo_y, hi_x, hi_y) if b is None else (
                    min(b[0], lo_x), min(b[1], lo_y), max(b[2], hi_x), max(b[3], hi_y))
            x, y = nx, ny
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


def _fmt(v):
    s = "%.3f" % v
    return s.rstrip("0").rstrip(".") if "." in s else s


def _marker_block(layer_cap, state, cal, bbl, cfg):
    """G-code lines drawing every marker loop of one layer, leaving the stream state as found."""
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
    for loop in layer_cap["loops"]:
        pts = [(px + cal["dx"], py + cal["dy"]) for px, py in loop]
        if retract > 0.0:
            out.append("G1 E-" + _fmt(retract) + " F1800\n")
        out.append("G1 X" + _fmt(pts[0][0]) + " Y" + _fmt(pts[0][1]) + " F" + _fmt(f_travel) + "\n")
        if retract > 0.0:
            out.append("G1 E" + _fmt(retract) + " F1800\n")
        out.append("G1 F" + _fmt(f_print) + "\n")
        prev = pts[0]
        for pt in pts[1:] + [pts[0]]:
            d = math.hypot(pt[0] - prev[0], pt[1] - prev[1])
            if d < 1e-4:
                continue
            out.append("G1 X" + _fmt(pt[0]) + " Y" + _fmt(pt[1]) + " E" + ("%.5f" % (d * e_per_mm)) + "\n")
            prev = pt
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

    if any(line.startswith(_TAG + "_START") for line in lines):
        return lines, "already processed, left untouched"

    if not captures:
        report.append("nothing captured at slice time: cannot calibrate or inject")
    elif len(captures) > 1:
        report.append("captured %d objects; the spike supports exactly one, nothing injected" % len(captures))
    else:
        cap = next(iter(captures.values()))
        kinds = sorted({l["ref_kind"] for l in cap["layers"].values()})
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
        else:
            cfg = dict(cfg, line_width=cap["line_width"])
            no_end = 0
            # Bottom-up scan order, inserted top-down so earlier line indices stay valid.
            for gl in reversed(gcode_layers):
                if gl["z"] is None:
                    continue
                cl = cap["layers"].get(_zkey(gl["z"]))
                if cl is None or not cl["loops"]:
                    continue
                if gl["end"] is None:
                    no_end += 1  # last layer: no following layer-change tag to anchor on
                    continue
                lines[gl["end"]:gl["end"]] = _marker_block(cl, gl["end_state"], cal, bbl, cfg)
                injected += len(cl["loops"])
            report.append("injected %d marker loop(s)" % injected
                          + (" (%d layer(s) skipped: top layer has no anchor)" % no_end if no_end else ""))

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
        if ctx.step == orca.slicing.Step.posPerimeters and ctx.object is not None:
            return self._capture(ctx)
        if ctx.step == orca.slicing.Step.psGCodePostProcess and ctx.gcode_path:
            return self._inject(ctx)
        return orca.ExecutionResult.success()

    def _capture(self, ctx):
        cap = _capture_object(ctx, _params(self))
        if ctx.cancelled():
            return orca.ExecutionResult.success()
        # Forget objects that are no longer part of this print, then store this one in both places.
        live = {o.id() for o in ctx.print.objects()}
        store = getattr(self, "_spike_state", None)
        if store is None:
            store = {}
            self._spike_state = store
        for target in (_STATE, store):
            for stale in [oid for oid in target if oid not in live]:
                del target[stale]
            target[ctx.object.id()] = cap
        return orca.ExecutionResult.success(
            "Wave Overhang Spike: captured %d layers, %d overhang areas on %d layers"
            % (len(cap["layers"]), cap["regions"], cap["overhang_layers"]))

    def _inject(self, ctx):
        p = _params(self)
        instance_state = getattr(self, "_spike_state", None)
        survived = (bool(_STATE), bool(instance_state))
        captures = _STATE or instance_state or {}

        rel = str(ctx.config_value("use_relative_e_distances")).strip().lower()
        cfg = {
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
        before = len(lines)
        lines, summary = _post_process(lines, captures, survived, cfg)
        if len(lines) != before:
            with open(ctx.gcode_path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
                f.writelines(lines)
        return orca.ExecutionResult.success("Wave Overhang Spike: " + summary)


@orca.plugin
class WaveOverhangSpikePackage(orca.base):
    def register_capabilities(self):
        orca.register_capability(WaveOverhangSpike)
