# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

An attempt to deliver the wave-overhang feature of the fork
[dennisklappe/OrcaSlicer-WaveOverhangs](https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs)
as a Python plugin for upstream [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer),
instead of as a fork.

Both are cloned next to this repo and are the reference material for all work here:

- `../OrcaSlicer` — upstream (2.6.0-dev), contains the plugin system.
- `../OrcaSlicer-WaveOverhangs` — the fork; the algorithm is `src/libslic3r/WaveOverhangs/WaveOverhangs.cpp`.

Licensed AGPL-3.0 (`LICENSE`), because the generator is a port of the fork's AGPL code.

Current state:

- `wave_overhangs_plugin.py` — the plugin (v0.1.0). Ports the fork's generator with pyclipper
  and prints the waves through the G-code route. First real run inside OrcaSlicer (non-Bambu,
  test model: 32 mm square column under a ~45 mm disc at z=30) worked: 71 wave paths in one
  region on z=30, slices cut on that layer only, exact calibration; the slicer printed the
  shrunk square's walls inside the waves, bridge + overhang wall over the whole disc ring at
  z=30.2 (the floor) and solid infill at z=30.4. Printed with the fork's defaults: the user
  reported it printed perfectly.
- `wave_overhang_spike_plugin.py` — the spike that proved the route (hand-over, offset
  calibration, retraction, `shrink` vs `delete`). Kept as reference; see "Spike" below.
- `probes/` — dependency probes; pyclipper and shapely both install inside OrcaSlicer.

## Commands

There is no build, lint or test setup. A plugin is a single `.py` file loaded by OrcaSlicer's
embedded Python (>= 3.12); it imports `orca`, which only exists inside the app.

- Syntax check: `python3 -m py_compile wave_overhangs_plugin.py`
- Generator smoke test (needs pyclipper and numpy; matplotlib for the picture):
  `python3 tests/generator_smoke.py [out.png]`
- Testing outside OrcaSlicer requires stubbing the `orca` module in `sys.modules` before loading
  the plugin file (`orca.slicing.SlicingPipelineCapabilityBase`, `Step`, `unscale`,
  `orca.ExecutionResult`, `orca.plugin`, `orca.base`, `orca.register_capability`, and
  `orca.host.ExPolygon` for the slice cut) and faking the geometry objects. The generator
  (`generate()` and the Clipper helpers) works on plain scaled-integer tuples and the G-code half
  (`_scan_gcode`, `_calibrate`, `_wave_block`, `_post_process`) on lists of lines; both can be
  tested directly.
- Real run: install the file through OrcaSlicer's Plugins dialog (it installs pyclipper on load),
  select the capability as the slicing pipeline plugin in the process settings, slice, export,
  and read the `; WAVE_OVERHANGS ...` report lines at the top of the exported G-code.

## The upstream plugin API (what constrains the design)

Read these in `../OrcaSlicer` before changing the plugin; the bindings are the only API documentation:

- `src/slic3r/plugin/host/PluginHostSlicing.cpp` — slicing graph bindings (`orca.host`).
- `src/slic3r/plugin/host/PluginHostGeometry.cpp` — `Point` / `Polygon` / `ExPolygon`.
- `src/slic3r/plugin/pluginTypes/slicingPipeline/` — the context object and `Step` enum.
- `src/slic3r/plugin/PluginHooks.cpp`, `src/slic3r/GUI/PostProcessor.cpp` — how capabilities are dispatched.
- `sandboxes/orca_*_plugin_any.py` — working sample plugins (metadata header format, config, idioms).

Facts that shaped the approach:

- A `slicing-pipeline` capability's `execute(ctx)` is called after each slicing step
  (`posSlice`, `posPerimeters`, `posPrepareInfill`, `posInfill`, `posSupportMaterial`, ...) and once more at
  `psGCodePostProcess` on the exported G-code file.
- Slice geometry (`region.slices`, `fill_surfaces`, surface types) is writable. The extrusion
  tree (`perimeters`, `fills`) is **read-only**: a plugin cannot add or clip toolpaths. This is
  why a faithful port of the fork is not possible today.
- At `psGCodePostProcess` there is no live print: `ctx.print` and `ctx.object` are `None`; only
  `ctx.gcode_path`, `ctx.host`, `ctx.output_name` and `ctx.config_value()` are available.
  Anything needed there must be captured earlier and carried in plugin state.
- References into the slicing graph are valid only during `execute()`; copy out plain values.
- Only pairwise polygon booleans exist (`ExPolygon.diff_ex/union_ex/intersection_ex/offset`).
  There is no polygon-set boolean, so set operations are loops.
- `ExtrusionPath.points()` and `Polygon.as_array()` need numpy; `Polygon.points` does not.
- Coordinates are scaled integers; convert with `orca.slicing.unscale(1)` (never hardcode 1e-6).
- Slice geometry is in object space (centred on the object). G-code is in plate space. The
  instance shift is not exposed to plugins, so the spike measures the offset from the G-code.
- Slicing hooks run on the worker thread; do not call `orca.host.ui.*` from them.
- Preview: for Bambu printers post-processing runs in place right after slicing and the preview
  re-reads the edited file (`BackgroundSlicingProcess.cpp`, `Print::reload_gcode_moves`). For
  all other printers it runs only at export on a `.pp` copy, so the preview never shows
  plugin edits to the G-code.
- G-code comment tags differ by flavour: Bambu uses `; CHANGE_LAYER`, `; Z_HEIGHT: `,
  `; FEATURE: `, `; LINE_WIDTH: `, `; LAYER_HEIGHT: `; others use `;LAYER_CHANGE`, `;Z:`,
  `;TYPE:`, `;WIDTH:`, `;HEIGHT:`. Feature names are the role strings in
  `src/libslic3r/ExtrusionEntity.cpp` ("Outer wall", "Custom", ...).

## Routes considered

1. **G-code post-process plugin (current route).** Compute geometry in slicing hooks, splice
   wave paths into the G-code at `psGCodePostProcess`. Works with today's API; loses support
   awareness and per-region settings, and the preview only reflects it on Bambu printers.
2. **Extend the upstream plugin API** (writable extrusions, per-path speed/fan overrides, a hook
   before support generation), then port the generator. Needed for a faithful version.
3. **Stay a fork / upstream as a core feature.** Only route to full parity.

## Plugin architecture (`wave_overhangs_plugin.py`)

- **`posSlice`**: for every layer and island, `generate()` (port of `WaveOverhangs::generate`)
  grows wave fronts from the supported edge, with the lower layer grown by half a nozzle as the
  fork passes it. All layers are generated from the uncut slices first; then each layer's cut
  zone (the fork's wall-clip region + fill carve: overhang zone grown by 1.5 perimeter spacings /
  1.25 widths, plus the painted area) is cut out of `region.slices` and `layer.make_slices()` is
  called. The slicer then prints nothing there, and a bridge on the layer above (the floor).
- **`posPerimeters`**: wall bounding boxes for the offset calibration (as in the spike).
- **`psGCodePostProcess`**: the waves are inserted before the next layer-change tag of their
  layer as `;TYPE:Overhang wall`, at the configured flow (mm³/mm), speed and fan, with retraction,
  fan, E mode, feedrate and tags restored afterwards. Short hops between fronts (≤ the fork's
  connector limit) travel without retracting.
  Paths keep the generator's order (front level by front level, so separate sides alternate),
  as the fork prints them; this lets each front cool before the next is laid against it, at the
  cost of a retracted travel per path when the overhang has several sides.

Porting notes (things that are easy to get wrong):

- Orca's offsets use a patched Clipper that drops input vertices closer than
  `0.005 × |delta|` (`ShortestEdgeLength`). pyclipper lacks it; `_strip_short_edges` reproduces
  it. Without it every round offset at least doubles the vertex count (minutes per layer).
- `jtRound` with limit 0 means Clipper's default arc tolerance, as in Orca.
- Seeds: `Algorithm::wave_seeds` = contour of the anchoring, each contour expanded on its own with
  `jtSquare` (holes by the negated amount), clipped to the wave area. The fork's fallback (the
  area's own boundary inside the expanded anchoring) lies on the clip edge and degenerates to
  a point, so it must stay a fallback.
- `overhang_flow` is approximated from config (`thick_bridges`, `bridge_flow`,
  `inner_wall_line_width`); it only sets anchor/seed distances.

Differences from the fork (v0.1.0): no outer wall kept over the overhang
(`wave_overhang_outer_perimeters` = 0); every overhang is waved (bridge detector not available
to plugins); waves printed at the end of the layer; no floor-layer, Hilbert, temperature or
min-time features; top-layer overhangs skipped; one object, one instance.

## Spike architecture (`wave_overhang_spike_plugin.py`)

`wave_overhang_spike_plugin.py` has two halves joined by a hand-over store:

- **Capture** (`posSlice`, per object): per layer, overhang = islands minus the layer below,
  opened to drop thin slivers; the areas (contour and holes) and their marker outlines are stored
  as mm tuples keyed by `print_z` (rounded to 3 decimals). Every layer is captured before
  anything is cut. At `posPerimeters` a reference bounding box of each layer's walls is added
  (if `posSlice` was cached, the areas are captured there too).
- **Hand-over**: stored both in the module-level `_STATE` and on the capability instance, so the
  report can say which survives. In a real run both survived (verified).
- **Inject** (`psGCodePostProcess`): scan the G-code per layer (`_Stream` tracks position, E mode,
  retraction and feature tags), measure the object-to-plate offset by matching wall bounding
  boxes, refuse to inject if the offset is inconsistent, then insert one "Custom" marker loop
  per overhang area before the next layer-change tag. The block restores E mode, feedrate and
  the active feature/width/height tags afterwards.
- **`mode`** (plugin config) decides what happens to the slicer's own toolpaths in the overhang
  area: `marker` leaves them, `shrink` cuts the areas out of `region.slices` at `posSlice` (then
  `layer.make_slices()`), `delete` clips the overhang layer's extrusion moves against the areas
  at post-process (inside parts become travel; `G92 E` resyncs in absolute E mode).
- **Census** (end of the report): per overhang layer, mm extruded inside the area by feature,
  on that layer and on the layer above. This is how the modes are compared.

Retraction: `_scan_gcode` tracks how far the nozzle is retracted (G1 E moves including wipes,
and G10/G11). `_marker_block` skips its own retract when the nozzle is already retracted and
re-applies the original retraction at the end, so the block's net E is zero. With standard tags,
OrcaSlicer retracts *after* `;LAYER_CHANGE`, so in practice layers end unretracted at the
insertion point.

Known limits of the spike: one object with one instance, no by-object printing, no wipe tower,
top-layer overhangs are skipped. Only the outer contour of each overhang area becomes a loop:
holes are dropped, so for a ring-shaped overhang the supported inner edge (where the fork's
waves start) is not outlined.

What to do with the slicer's own walls, bridges and infill in the overhang area was tested with
all three modes in OrcaSlicer (test model: 4 overhang areas on one layer, z=30):

- `shrink`: z=30 inside the area is empty except for the marker; the slicer moves its bridge and
  overhang wall up exactly one layer (z=30.2, identical amounts) and shifts the solid shells
  above up by one layer. No further cascade; the rest of the file differs only in `M73`
  progress lines. This matches the fork's design (wave layer, then a bridge floor above it).
- `delete`: z=30 inside the area is empty except for the marker, but bridge stubs remain at the
  anchors and z=30.2 stays plain "Internal solid infill" printed over the waves/air with no
  bridge settings.

Recommendation: `shrink` for the real plugin; `delete` only as a fallback.
