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

- `wave_overhangs_plugin.py` — the plugin (v0.12.0: several objects and copies per plate;
  gentle slopes keep the slicer's walls, `min_overhang_mm` = 0.5; one wall kept along the
  edge of wide overhangs, `outer_walls` = 1). Ports the fork's generator with pyclipper
  and prints the waves through the G-code route. First real run inside OrcaSlicer (non-Bambu,
  test model: 32 mm square column under a ~45 mm disc at z=30) worked: 71 wave paths in one
  region on z=30, slices cut on that layer only, exact calibration; the slicer printed the
  shrunk square's walls inside the waves, bridge + overhang wall over the whole disc ring at
  z=30.2 (the floor) and solid infill at z=30.4. Printed with the fork's defaults: the user
  reported it printed perfectly. A second model (cross, cone, stepped square, plate with
  holes; 12 waved layers) also sliced correctly and printed well. v0.2.0 placed the waves of
  three objects on one plate (Klipper exclude-object labels); with labels off it refused, as
  designed. A 4-copy plate (3 objects, one "+" copy) with `min_overhang_mm` = 0.5 printed OK.
- `dev/wave_overhang_spike_plugin.py` — the spike that proved the route (hand-over, offset
  calibration, retraction, `shrink` vs `delete`). Kept as reference; see "Spike" below.
- `docs/upstream-proposal-preview-postprocess.md` — draft upstream proposal: run G-code
  plugins after slicing for all printers so the preview shows their edits.
- `dev/probes/` — dependency probes; pyclipper and shapely both install inside OrcaSlicer.

## Commands

There is no build, lint or test setup. A plugin is a single `.py` file loaded by OrcaSlicer's
embedded Python (>= 3.12); it imports `orca`, which only exists inside the app.

- Syntax check: `python3 -m py_compile wave_overhangs_plugin.py`
- Generator smoke test (needs pyclipper and numpy; matplotlib for the picture):
  `python3 tests/generator_smoke.py [out.png]`
- G-code half with several objects/copies and every label family:
  `python3 tests/gcode_instances_test.py`
- Minimum-overhang setting (slope / shelf / slope joined to a shelf):
  `python3 tests/min_overhang_test.py [out.png]`
- Every test file runs on its own and ends with `all OK` (see `CONTRIBUTING.md` for the list). None needs a local
  file: real exported G-code is optional (`WAVE_TEST_GCODE_DIR=<folder of .gcode>`). The page tests need
  `google-chrome`. `dev/make_screenshots.py PLATE.gcode DISC.gcode` redraws `docs/images/` from real exports.
- Notification, support toggle and log (fake host): `python3 tests/notifications_test.py`
- Preview panel data (lengths equal the report on real files): `python3 tests/preview_extract_test.py [file.gcode ...]`
- Preview panel page, rendered headlessly in Chrome (needs `google-chrome`; PNGs go to the given
  folder): `python3 tests/preview_page_test.py [file.gcode] [out_dir]`
- Kept walls (cantilever / ring / plate with a hole / narrow band / band + shelf, 0–2 walls):
  `python3 tests/outer_wall_test.py [out.png]`
- A local venv with the test dependencies: `python3 -m venv .venv && .venv/bin/pip install
  pyclipper numpy matplotlib` (`.venv/` is excluded in `.git/info/exclude`).
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
- Object labels (`GCode.cpp`, `assign_object_label_ids`): objects are numbered by their position
  in `print.objects()` (not `PrintObject.id()`), copies within an object from 0; names are
  `<name>_id_<n>_copy_<c>`. The exclude-object start/end strings are emitted lazily by the
  writer (at the next travel).
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
  Captures are keyed by `PrintObject.id()` and also record the object's position in
  `print.objects()` and its name.
- **`psGCodePostProcess`**: `_scan_gcode` splits every layer into one segment per object copy,
  using the object labels in the file (preference: `; printing object` comments
  (`gcode_label_objects`), then `EXCLUDE_OBJECT_START/END` (Klipper), `M486 S<n>` (Marlin/RRF),
  Bambu `unique label id` comments; the last three need `exclude_object`). A segment ends at its
  end label, the next start label or the next layer-change tag, whichever is first. The
  exclude-object labels are written lazily, so the last copy's end label comes after the next
  `;LAYER_CHANGE`; the layer tag ends it then. Without labels each layer is one segment, which
  only works with one object and one copy (a warning says to turn labels on).
  Each copy is matched to a capture by calibrating against every capture (`_match_object`:
  passing ones ranked by layer coverage on both sides, then size mismatch and spread; ties
  broken by the object index in `_id_<n>_copy_` / `id:<n>` labels), which also gives its
  offset. Copies of one object are one `PrintObject` (same shape, different shift), so they
  share a capture. Separate but identical objects (copy/paste) are separate `PrintObject`s, but
  Orca slices only the first and copies its layers to the others (`set_shared_object`,
  `copy_layers_from_shared_object`): `execute()` never sees the others, they have no capture
  of their own, and they get the first one's cut slices and match its capture. Verified in a
  real run (two OverhangWaveTest objects + Assembly: 3 labelled instances, 2 captures, all
  placed exactly). The waves are inserted at the end of the copy's segment (inside its
  exclude-object block, so cancelling the object also cancels its waves)
  as `;TYPE:Overhang wall`, at the configured flow (mm³/mm), speed and fan, with retraction,
  fan, E mode, feedrate and tags restored afterwards. Short hops between fronts (≤ the fork's
  connector limit) travel without retracting.
  Paths keep the generator's order (front level by front level, so separate sides alternate),
  as the fork prints them; this lets each front cool before the next is laid against it, at the
  cost of a retracted travel per path when the overhang has several sides.

`min_overhang_mm` (plugin setting, default 0.5 since v0.3.0; 0 = wave everything, as the fork): `island_support()` adds the gentle-slope
part of an island's overhang to its lower layer before `generate()`, so the slicer keeps its
own walls there. Gentle = within `min_overhang_mm` of the lower edge and not within
`min_overhang_mm` of overhang that reaches further (that strip is a shelf's root, which its
waves need as anchor band). Test: `python3 tests/min_overhang_test.py [out.png]`.

`outer_walls` (default 1, capped at `wall_loops`), as the fork's `wave_overhang_outer_perimeters`:
`wall_side()` picks the overhang that reaches past `outer_walls` perimeter spacings plus two wave
spacings (with its root, like `narrow_overhangs()`); only there are walls kept. `island_waves()`
then grows the waves in the island minus the walls' band along that part (the fork's
`wave_infill`, restricted) with `perimeter_count = wall_loops - outer_walls`, or, when no part
is wide enough or nothing is waved that way, in the whole island with no walls (narrow, steep
overhangs are waved to the edge: with a wall they would get no waves at all). The cut zone is
unchanged (it already reaches the edge). `kept_walls()` draws the wall centre lines (outer at
`outer_wall_line_width / 2` from the edge, then Orca's ext/perimeter spacings), clipped to the
island's cut zone, so they run exactly where the slicer's walls were removed. The plugin prints
them in the same block right after the waves (innermost first, outer last), tagged Outer/Inner
wall, with Orca's flow for that width and layer height, at `wall_speed_mm_s` (0 = the slicer's
`overhang_4_4_speed`, or `bridge_speed` if unset). Where a wall ends next to an overhang waved to
the edge, those waves pass the wall's end. The fork prints the waves before the
island's walls too, so the walls are laid against them in both.

User feedback (v0.6.0, borrowed from the Support Fins plugin in `../support-fins/plugins/orca`):

- **Notification**: `execute()` runs on the slicing worker, where UI calls can deadlock, so
  `_note()` queues lines (posSlice: one per object that got waves; G-code step: the added time
  and any problems) and `on_lifecycle_event` shows them with `orca.host.ui.push_notification`
  when `LifecycleEvent.SlicingJobComplete` fires on the UI thread (it fires after slicing, and
  again after an export, which runs in the same background job). Warning level if any line is a
  warning (stays until closed), else Important (20 s). A cancelled job shows nothing, a failed
  one only its warnings; lines older than 10 min are dropped (post-processing outside a job,
  e.g. saving a 3MF with G-code, would otherwise surface on the next slice).
- **Per-object toggle**: `apply_to` = `unsupported` (default) leaves objects with
  `enable_support` on alone (`PrintObject.config_value`); they still get a capture (layers and
  wall boxes, no waves) so their copies are matched and reported, not flagged as unplaceable.
- **Log**: `_write_log()` appends JSON lines to `wave_overhangs_log.jsonl` next to the plugin
  file (slice: object, stats, settings, time; G-code: report lines). Best effort.

Preview panel (v0.7.0, before-export in v0.8.0; the plugin's workaround for OrcaSlicer's preview not showing G-code edits on
non-Bambu printers, see `docs/upstream-proposal-preview-postprocess.md`):

- **Data**: `extract_preview(lines, e_relative)` runs at the end of `_inject()` (worker thread, ~0.3 s)
  on the final G-code and keeps `_PREVIEW` ({"data", "title", "fresh"}; module global and instance):
  per layer the wave lines, kept walls (between the WAVE_OVERHANGS markers) and the slicer's outer /
  overhang walls as context, flat x,y lists in plate mm; context and walls are simplified at 0.02 mm.
  A file that already has its waves keeps the old data; an export without waves, or `preview` = 0,
  drops it. 20-46 thousand numbers (0.3 MB) per plate, sent in one message (the probe showed 2 million
  numbers arrive intact in 0.7 s).
- **Before the export (v0.8.0)**: the waves and kept walls exist after posSlice, so
  `build_slice_preview(captures)` (UI thread, when SlicingJobComplete fires with `_SLICE_DIRTY` set by
  `_slice`) builds the same data from the captures. Each capture keeps `instances` (plate centres) and
  per-layer `ctx` outlines (the uncut layer islands, simplified 0.02 mm, ~4 ms per ring). Plate position of an
  instance = centre of `ModelInstance.bounding_box()`: OrcaSlicer centres each object before slicing
  (`PrintObject.cpp:159`) and G-code = slice + `shift` (instance position + centre offset, `PrintObject.cpp:179`,
  `PrintApply.cpp:211`); for a later plate the G-code is relative to the plate origin, which plugins cannot
  read, so only absolute positions differ there. `_object_instances()` picks the model instances whose
  matrix equals `PrintObject.trafo()` (translation aside), as one model object can make several PrintObjects.
  The export replaces the data (source "gcode") and refreshes an open panel; the export's report line says
  how far the slice-time guess was ("slice-time position guess off by X mm").
- **Notification layout (v0.8.1)**: OrcaSlicer collapses a notification to two lines (`m_lines_count > 2`
  shows "More") and draws the hyperlink inline after the last text line (adding a line if it does not fit),
  so a three-line text hid "Show waves" until expanded. The slice notification is now one short summary
  (`_slice_summary()`: "Wave Overhangs: 18 wave layers, about 45 min."), the export one "waves added, about
  N min."; per-object details are in the log. Keep text + link under ~60 characters.
- **Tab (v0.9.0)**: a third capability, `WavePreviewTab(orca.pages.PagesPluginCapabilityBase)`, is a tab
  next to Prepare/Preview (`PluginPages`, `MainFrame.cpp`): label = the capability name, created when the plugin is
  enabled, its page built lazily the first time the tab is shown. `get_ui()` returns `PREVIEW_PAGE` (the same page
  as the side panel; it asks `preview_ready`, and gets the data or `{command: "empty"}` -> "No waves to show
  yet"). `post_message(obj)` (any thread, delivered with CallAfter; a no-op until the page exists) is how the
  lifecycle handler (`_push_to_tab()`) refreshes it after each slice/export. `on_message(str)` gets the page's
  JSON as a string. The notification link / Plugins action still open the side panel (plugins cannot select a
  tab).
- **Side panel from the tab and automatically (v0.10.0)**: a tab cannot be a one-click action: selecting it
  shows its page, a plugin has no API to switch tabs (`PluginHostApp.cpp` Plater binding: model, dirty
  flags, project path), and a dock panel only shows in Prepare/Preview. So the tab's page (the same
  `PREVIEW_PAGE`, told `window.__WAVE_TAB__` by `get_ui()`) has a "show side panel" checkbox (v0.10.1;
  it was a button that gave no sign it had worked). It posts `{command: "side_panel", open}`;
  `WavePreviewTab.on_message` calls `show_preview()` / `close_side_panel()`, and `_notify_panel_state()`
  (called from `show_preview()`, `_panel_closed()` and the replies) posts `{command: "panel_state", open}` so
  the checkbox mirrors the panel however it was opened or closed (link, Plugins button, auto-open, its own
  x). With nothing to show the page gets an `info` line and the box goes back to unticked. `open_panel` = 1 opens the side panel by itself: `_slice` sets `_AUTO_OPEN`,
  the first fresh preview after it (`_take_auto_open()`) opens the panel, later exports only refresh an
  open one (a panel the user closed stays closed).
- **Settings page (v0.11.0)**: OrcaSlicer's Plugins dialog shows only the *stored* configuration
  (`PresetPluginConfigService::get_effective_config`: the preset's override, else `orca_plugins/config.json`),
  and nothing was ever stored for this plugin, so its settings did not show at all. `has_config_ui()` /
  `get_config_ui()` give a page (`_CONFIG_FIELDS` + `CONFIG_PAGE`, built by `config_page_html()` with the defaults from
  `_DEFAULTS`) that lists every setting, fills in defaults for the unset ones, marks changes, validates
  numbers, keeps keys it has no control for, and uses the bridge `window.orca.getConfig/saveConfig/restoreDefaults/
  onConfig` (`resources/web/dialog/js/plugin-config-ui.js`). A new setting needs an entry in `_DEFAULTS`,
  `_CONFIG_FIELDS` and the README table (`tests/config_page_test.py` fails when a default has no control).
- **Print time (v0.11.0)**: the slicer's estimate (the `M73 P<percent> R<minutes>` lines, the footer
  `; estimated printing time (normal mode)`, in Bambu files `total estimated time` / `model printing time`)
  leaves out the waves. Each wave block's start marker carries its time (`min=`; length / wave speed + travel +
  kept-wall time, no acceleration). `_adjust_time()` (setting `adjust_print_time`) adds to every M73 line's R the
  blocks still ahead of it, recomputes P from the new total, and rewrites the footer. The file name keeps the
  slicer's time (OrcaSlicer names the file before post-processing). The preview data carries
  `time: {slicer_s, waves_s}` (`_EXPORT_TIME` from the export; slice-time: waves only, `slicer_s` None) and the page shows
  "slicer + waves = total".
- **Public-release state (v0.12.0)**: `open_panel` defaults to on; `pyclipper==1.4.0` and `numpy>=2.0,<3` are
  pinned in the PEP 723 header (supply chain: no newer or hijacked release is installed by itself; update the pin
  deliberately and re-run the tests); every `.py` has an SPDX licence identifier (no copyright line, by the maintainer's choice); the research-era files live
  in `dev/`; `SECURITY.md` states what the plugin does (no network, no subprocess, no dynamic code: re-check with
  a grep before each release); a `[hidden]` rule in the page CSS keeps tab-only elements hidden in the side panel.
- **Notification duration (v0.8.2)**: `Important` fades after 20 s, so a notification with the link uses
  `HintNotificationLevel` (300 s; `NotificationManager.hpp` `get_standard_duration`; levels only change the
  duration for a custom notification); warnings never fade. Each capability of a plugin gets a ▶ Run button
  in the Plugins dialog (`CapabilityCanRun`, `run_script_plugin_capability`).
- **Reopening (v0.8.1)**: the panel state is module-level (`_PANEL`, `show_preview()`), and a second capability,
  `ShowWavePreview` (a script capability: Plugins dialog > Show Wave Preview), opens/refreshes it from the latest
  data at any time, with a message box when there is nothing yet. The header `type` is display-only, so one
  file can hold a pipeline and a script capability.
- **Panel**: no UI calls from `execute()`. `on_lifecycle_event(SlicingJobComplete)` (UI thread) pushes
  the notification with the hyper text "Show waves" when the data is `fresh`, and refreshes an open
  panel; the link callback (`show_preview()`) opens `PREVIEW_PAGE` with
  `orca.host.ui.create_dock_panel(dock="right")` or reuses the open one. The page asks for its data
  with `{command: "preview_ready"}` (a message posted before the page loads can be dropped) and gets
  `{command: "preview", layers, bbox, stats, title}`.
- **Page**: `PREVIEW_PAGE` is hand-written WebGL 1 (Edge WebView2 on Windows is Chromium with WebGL2;
  verified with `dev/probes/probe_preview_panel_plugin.py`), no libraries. `tests/preview_page_test.py`
  renders it headlessly in Chrome and checks the colours (mouse and slider handlers are not clicked
  by it).

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

Differences from the fork (v0.5.0): the kept walls (`outer_walls`, default 1) are drawn and
printed by the plugin, not the slicer's own wall paths, and only along wide overhangs; every overhang is waved (bridge detector not available
to plugins), except gentle slopes (`min_overhang_mm`, default 0.5); waves printed at the end of their copy's part of the layer; no floor-layer, Hilbert, temperature or
min-time features; top-layer overhangs skipped; with two filaments on one layer of one object
the waves use the filament active at the end of that object's part of the layer.

## Spike architecture (`dev/wave_overhang_spike_plugin.py`)

`dev/wave_overhang_spike_plugin.py` has two halves joined by a hand-over store:

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
