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

Current state: only a spike exists (`wave_overhang_spike_plugin.py`). It is not a wave
implementation; it proves the hand-over between slice-time hooks and G-code post-processing.
It has been checked against a fake host only, never run inside OrcaSlicer.

## Commands

There is no build, lint or test setup. A plugin is a single `.py` file loaded by OrcaSlicer's
embedded Python (>= 3.12); it imports `orca`, which only exists inside the app.

- Syntax check: `python3 -m py_compile wave_overhang_spike_plugin.py`
- Testing outside OrcaSlicer requires stubbing the `orca` module in `sys.modules` before loading
  the plugin file (`orca.slicing.SlicingPipelineCapabilityBase`, `Step`, `unscale`,
  `orca.ExecutionResult`, `orca.plugin`, `orca.base`, `orca.register_capability`) and faking
  the geometry objects. The G-code half (`_scan_gcode`, `_calibrate`, `_marker_block`,
  `_post_process`) is pure Python on lists of lines and can be tested directly.
- Real run: install the file through OrcaSlicer's Plugins dialog, select the capability as the
  slicing pipeline plugin in the process settings, slice, export, and read the
  `; WAVE_SPIKE ...` report lines at the top of the exported G-code.

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

## Spike architecture

`wave_overhang_spike_plugin.py` has two halves joined by a hand-over store:

- **Capture** (`posPerimeters`, per object): per layer, overhang = islands minus the layer below,
  opened to drop thin slivers; outlines stored as mm tuples keyed by `print_z` (rounded to 3
  decimals), plus a reference bounding box of the layer's walls.
- **Hand-over**: stored both in the module-level `_STATE` and on the capability instance, so the
  report can say which survives. Whether either survives in a real run is still unverified.
- **Inject** (`psGCodePostProcess`): scan the G-code per layer, measure the object-to-plate
  offset by matching wall bounding boxes, refuse to inject if the offset is inconsistent, then
  insert one "Custom" marker loop per overhang area before the next layer-change tag. The block
  restores E mode, feedrate and the active feature/width/height tags afterwards.

Known limits of the spike: one object with one instance, no by-object printing, no wipe tower,
assumes the nozzle is not retracted at the end of a layer, top-layer overhangs are skipped.

Open design decision for the real plugin: what to do with the walls, bridges and infill the
slicer already generated in the overhang area — delete those G-code lines, or shrink the slices
at `posSlice` so they are never generated (which changes how the layer above is classified).
