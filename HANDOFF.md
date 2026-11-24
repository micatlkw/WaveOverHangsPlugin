# Handoff: next steps for route 1

Written by another session after the first real run of the spike (see "Current state" in
CLAUDE.md). Delete this file once the steps are done or folded into CLAUDE.md.

## 1. Close out the spike — done

Position, retraction and numpy are checked; results are in CLAUDE.md.

## 2. Second spike: how to remove the slicer's own toolpaths in the overhang area — done

All three modes were run in OrcaSlicer; results and the recommendation (`shrink`) are in CLAUDE.md.

## 3. Port the generator — done

`wave_overhangs_plugin.py` v0.1.0 (pyclipper; decisions: AGPL-3.0, no outer wall over the
overhang, every overhang waved). Tested outside OrcaSlicer: `tests/generator_smoke.py` (ring,
cantilever, T with a neck) and a fake-host run on the first test model's G-code (cut only the
overhang layer, 25 rings r 15.6–24.0 mm, retraction/fan/E state restored, idempotent).

## 4. Next

- Done: first real run in OrcaSlicer (see "Current state" in CLAUDE.md); waves, cut and bridge
  floor are where they should be.
- Done: printed the test model with the fork's defaults (2 mm/s waves, 0.15 mm³/mm, fan
  100 %; the wave layer adds about 16 min). The user reported it printed perfectly.
- Done: second test model (`OverhangWaveTest`: cross on a square post, cone, stepped square,
  plate with two holes over a small post; 258 paths on 12 layers z 9.8–12.8). Waves follow
  the slope layer by layer, stop at hole edges, every island waved separately, bridge floor
  above each waved layer. The user reported the print looks good.
- Report wording: the offset line now gives the 90th-percentile and the worst-layer deviation
  (the old "90% of layers within 0 mm" read as if 10% disagreed; it never said that).

## 5. Several objects / instances on the plate — done

v0.2.0: G-code split per object copy by its labels, each copy matched to its object by shape
and placed at its own offset; waves inserted at the end of each copy's part of the layer.
Tested outside OrcaSlicer: `tests/gcode_instances_test.py` (2 objects, 3 copies, all four
label families, no-label cases) and a regression on both real G-code files (waves rebuilt
from the file: same insertion points, every other line identical to v0.1.0's output).

Real run: OverhangWaveTest twice (copy/paste, so a second object sharing the first one's slicing)
plus Assembly, Klipper labels: 3 instances placed at the EXCLUDE_OBJECT_DEFINE centres (deviation
≤ 0.001 mm), 25 wave blocks each inside its own object's block. Labels off: warning, nothing
injected. Not printed.
Real second copy ("+" button, `Test_Object_1.stl_id_2_copy_1`): both copies share one capture,
copy_1 placed at its EXCLUDE_OBJECT_DEFINE centre (shift 36.849, 44.622 from copy_0); its 17
blocks have the same moves and extrusion as copy_0's, shifted (≤ 0.001 mm). Its blocks go in
after the slicer's retract + wipe (before EXCLUDE_OBJECT_END) and before the z-hop of the next
travel, so the nozzle is at layer height and retracted; the block un-retracts and re-retracts.
Second real run (OverhangWaveTest, Assembly, the fork's "Test Object 1"; 110 layers): all three
placed exactly, 30 blocks. The report showed a stale object number for Assembly (its steps were
cached from the previous plate, where it was #2); indices are now refreshed on every slicing
step. Test Object's curved side is waved from where its edge grows ≥ ~0.2 mm per layer
(z 17.4–20.0, 3–5 lines per layer), as the fork would: candidate for a minimum-overhang setting.

## 6. Preview on non-Bambu printers — proposal drafted

`docs/upstream-proposal-preview-postprocess.md`: run plugins (not scripts) in place after
slicing for all printers, as the Bambu path does, then `reload_gcode_moves()`. Not filed yet.

## 7. Minimum overhang — done

Plugin setting `min_overhang_mm` (default 0 = fork behaviour). Tested outside OrcaSlicer with
`tests/min_overhang_test.py`. To check: slice the plate with Test Object 1 with
`min_overhang_mm` = 0.5; its curved side (z 17.4–20.0, edge growing 0.2–1 mm per layer) should
keep normal walls on the layers that grow less than 0.5 mm, and the z=20.2 shelf stay waved.

Real run (same 4-copy plate, `min_overhang_mm` = 0.5): Test Object 1's curved side keeps the
slicer's walls on z 17.4–19.4 (edge growing 0.21–0.48 mm per layer) and is waved from z=19.6
(growth > 0.5) up to the shelf at z=20.2, on both copies; z=5.2 and z=10.2 unchanged. Its
waves went from 17 layers / 183 paths to 6 layers / 150 paths. OverhangWaveTest unchanged: its
cone grows 0.55 mm per layer, just above the threshold (0.6 would leave it to the slicer).
Printed (4 copies, 3 objects): the user reported it printed OK.

## 8. Wipe tower and by-object printing — checked (sliced, not printed)

- Wipe tower (2 filaments, `enable_prime_tower` = 1, Test Object 1 alone): the tower and the
  tool change come at the start of each layer, outside the object's exclude block (the tower
  uses Bambu-style `; FEATURE: Prime tower` tags even in a standard file; harmless). All 6 wave
  blocks sit inside the object's block after its own paths, so they print with the filament
  the object used on that layer. Offset exact.
- By object (`print_sequence` = by object, 4 copies, 320 layer tags): each copy's waves are
  inserted inside its own block, on its own pass, with the nozzle at that layer's Z (checked
  against the last G1 Z): 25 blocks, 0 problems.

Known limit: when one object uses two filaments on the same layer, the waves print with the
filament active at the end of that object's part of the layer, not necessarily the one the
overhang region is painted with (the region's extruder is not captured).

## 9. Default and README — done

v0.3.0: `min_overhang_mm` defaults to 0.5 (chosen after the multi-object print). README rewritten
for users: install, every setting, the report, troubleshooting, known limits.

## 10. Kept outer wall — done

v0.4.0: `outer_walls` (default 1, the fork's default) and `wall_speed_mm_s` (0 = the slicer's
overhang 75–100% speed, else bridge speed). Tested outside OrcaSlicer:
`tests/outer_wall_test.py` (walls on the edge, only where cut, waves inside them; 0 walls
gives the old waves exactly), `tests/gcode_instances_test.py` (wall after the waves, its tag,
speed and flow, retraction balanced), regression on the real files (no walls: identical).

To check in OrcaSlicer: re-slice the 4-copy plate; the report's "generated" lines give mm of
kept wall and the "injected" line the wall speed. On the G-code viewer each waved overhang
should have a red outer wall along its edge right after the waves.

Real run (4-copy plate laid out by object, v0.4.0, `overhang_4_4_speed` = 10): 16 wave blocks,
848 mm of kept wall at 10 mm/s; walls follow every waved edge (cross arms, shelves, the arch at
z=10.2, both plate holes at z=12.8), outer wall flow 0.0754 mm³/mm = 0.0314 mm E per mm (0.42 ×
0.2 mm) as computed. With one wall kept, the wave area shrinks by one wall spacing (~0.42 mm),
so overhangs growing less than that plus half a nozzle get no waves: OverhangWaveTest's cone
(0.55 mm per layer) and Test Object's z=19.6 are now left to the slicer (5 instead of 12 waved
layers on OverhangWaveTest), as the fork does with `wave_overhang_outer_perimeters` = 1. Not printed.

## 11. Wall or waves chosen per overhang — done

v0.5.0: walls are kept only where the overhang reaches past them plus two wave lines (~1.1 mm
per layer, ~80° at 0.2 mm layers); narrower overhangs are waved to the edge, so the cone and
Test Object's z=19.6 should be waved again (as in v0.3.0) while shelves keep their wall.
`tests/outer_wall_test.py` covers a narrow band (no wall, same waves as `outer_walls` = 0) and a
band joined to a shelf (wall only along the shelf and its root).

Real run (OverhangWaveTest + Test Object 1, v0.5.0): 18 wave blocks, 540 mm of kept wall at
10 mm/s. Walls on the wide overhangs only: stepped square (z 10.8, 11.8, 12.4), cross arms
(9.8), plates with holes (12.8), Test Object's shelves (5.2, 10.2, 20.2). Waved to the edge
with no wall: the cone (z 11.0–12.6, 4 lines per layer, as in v0.3.0) and Test Object's
z 19.6–20.0. Both kinds on one layer at z=10.8 and z=12.8. Not printed.

## 12. Notification, per-object toggle, log — done in code, not yet run in OrcaSlicer

v0.6.0 (ideas from the Support Fins plugin): results shown as an OrcaSlicer notification when
slicing / export completes; objects with Orca supports on are left alone (`apply_to`); a JSON-lines
log next to the plugin. `tests/notifications_test.py` covers them against a fake host. To check
in OrcaSlicer: the notification appears after slicing and after export; an object with Enable
support on gets no waves and the report says so; `wave_overhangs_log.jsonl` appears next to the
installed plugin.

## 13. Wave preview panel — plan, step 0 (the probe)

Plan: an HTML/WebGL panel beside the 3D view showing the waves (a docked `create_dock_panel`),
fed by the exported G-code. Steps: 0 probe in OrcaSlicer; 1 extract per-layer moves from the final
G-code (test: wave length equals the report); 2 flat layer view (also the WebGL fallback);
3 3D view (WebGL lines, orbit/zoom, layer-range slider); 4 "Show waves" link in the export
notification, reopen/refresh, setting to turn it off; 5 docs, v0.7.0.

Step 0: `probes/probe_preview_panel_plugin.py` (a separate slicing-pipeline plugin). Install it,
pick it under Others > Slicing Pipeline Plugin, slice. It opens a panel from the lifecycle event
and shows a notification whose link reopens it; the panel reports WebGL support, a ping round
trip, plugin-to-page messages of 10 thousand to 2 million numbers, and a 100 thousand line draw.
Results are also in `preview_panel_probe_log.jsonl` next to the plugin. Unknowns this answers:
can a pipeline plugin open a panel; does the notification link work; message size limit; WebGL.
Fallback if a pipeline plugin can't: a second script-plugin capability in the same file owns the
panel.

Step 0 result (Windows, OrcaSlicer's WebView2): a pipeline plugin CAN open a dock panel from
`on_lifecycle_event(SlicingJobComplete)`; the page is Chromium 107 (Edge) with WebGL2 (Intel Arc,
100k lines drawn in 24 ms); ping round trip 4 ms; plugin-to-page messages arrive intact up to
2 million numbers (0.7 s), so no chunking is needed. Not tested: the notification link reopening a
panel, and a second slice (the user did not report them); step 4 will exercise both.

Step 1 done: `extract_preview(lines, e_relative)` in the plugin returns per layer the wave lines
("w"), kept walls ("k") and the slicer's outer/overhang walls as context ("c"), flat x,y lists in
mm, plate coordinates, context and walls simplified at 0.02 mm. `tests/preview_extract_test.py`:
wave and kept-wall lengths equal the report on all 9 real files (within 0.05 mm of waves);
data is small (20k-46k numbers, 0.3 MB for a whole plate).

Steps 2-3 done: `PREVIEW_PAGE` in the plugin (WebGL 1, no libraries). 3D view (left-drag orbit,
right/shift-drag pan, wheel zoom, double-click or Fit to refit to the waves) and a top-down Layer
view; top-layer and from-layer sliders, previous/next layer with waves, arrow keys, toggles for
waves / kept walls / outer walls; shows lengths for the shown layers. It asks the plugin for data
with `{command: "preview_ready"}` and expects `{command: "preview", layers, bbox, stats, title}`;
`#mode=layer&to=57&from=0&yaw=-50&pitch=35&show=wk` sets the initial view.
`tests/preview_page_test.py` renders four views of a real file in headless Chrome and checks the
colours are where they should be and that waves can be hidden (all pass; also on synthetic data).
Not covered by the test: mouse orbit/pan/zoom, the sliders and buttons (wired up, not clicked).

## 14. Preview panel wired into the plugin — done in code, not yet run in OrcaSlicer

v0.7.0: step 4 done. After an export that added waves, the notification has a "Show waves" link;
the panel opens docked on the right, asks for its data and draws it; a later export refreshes an
open panel; `preview` = 0 turns it off. `tests/notifications_test.py` covers the link, opening,
reuse, refresh, reopening after close and the cases that drop the data. README and `CLAUDE.md`
updated (step 5).

To check in OrcaSlicer: slice and export a plate with waves; the second notification should have
"Show waves"; click it; the panel should show the plate (try Layer view, the sliders, the
"waves" jump buttons, mouse orbit/pan/zoom, which no test has clicked); export again with the
panel open and it should refresh. Also the v0.6.0 checks (notifications, Enable support toggle,
log file).

Real run of 0.7.0/0.7.1 (user: no notification, no preview): read from the user's OrcaSlicer data
folder (`%APPDATA%/OrcaSlicer/orca_plugins/wave_overhangs_plugin.py/wave_overhangs_log.jsonl`,
readable from WSL as `%APPDATA%/OrcaSlicer/`). Cause: after reinstalling
the plugin OrcaSlicer reused the cached slice (SliceStarted -> SliceGeometryFinished in the same
second, no `slice` entry), so the plugin's posSlice never ran, the reloaded module had no captures,
the export said "nothing captured" in the file only, so no notes, no preview data. The lifecycle
event and `ctx.code` do reach the plugin. 0.7.2 adds a warning notification for this case (remove
Wave Overhangs from Slicing Pipeline Plugin, slice, add it back, slice, export). Possible
improvement: persist the captures on disk so a reloaded plugin can still inject into a cached slice
(needs a staleness check).

## 15. Preview before the export — done in code, not yet run in OrcaSlicer

v0.8.0: the "Show waves" link now comes right after slicing, with the panel drawn from what the
slice captured (waves, kept walls, layer outlines placed by each instance's bounding-box centre);
the export refreshes it with the exact data from the file and logs the position error in the report
("slice-time position guess off by X mm"). `tests/preview_slice_test.py`: on the synthetic plate the
slice-time route and the exported G-code give identical waves, kept walls and outlines.

To check in OrcaSlicer (after reinstalling: remove Wave Overhangs from Slicing Pipeline Plugin, slice,
add it back, slice): the notification after slicing should have "Show waves"; the panel should show
the plate with waves; export, then the panel refreshes, and the exported file's report lines should say
the position guess was off by ~0 mm for every copy. If the numbers are not ~0, send the report lines.

## 16. Notification and reopening (user feedback on 0.8.0) — done in code, not yet run

0.8.0 works in OrcaSlicer (user confirmed). Feedback: (1) the "Show waves" link was only visible after
expanding the notification; (2) once the panel was closed there was no way to open it again. 0.8.1: one
short notification line (OrcaSlicer collapses past two lines and puts the link inline), and a Plugins
dialog action "Show Wave Preview" (a second, script capability in the same file) that reopens the panel
with the latest data. To check: reinstall, slice (the link must be visible without expanding), close the
panel, then Plugins > Show Wave Preview. Open question: whether the Plugins dialog lists the action next
to the pipeline capability.

Bigger idea, not built: a plugin *page* (`PagesPluginCapability`, `get_ui()`) is a permanent tab next to
Prepare/Preview in the main window (`PluginPages` in MainFrame), so the preview could live in a tab that can
not be closed and gets the whole window. Same HTML; messages go through `on_message`/`post_message`.

Run of 0.8.1 (read from the user's data folder): both capabilities loaded ("Collected 2 registered
capability class(es)"), the slice notification was pushed with its link at 18:31:40 (log
`notification_pushed`), but the user did not see it (Important level fades after 20 s) and expected a
tab (the tab was only an idea in the reply, not built). 0.8.2: the notification with the link uses
`HintNotificationLevel` (stays 300 s); the Plugins dialog has a Run button per capability, so
"Show Wave Preview" is there; README says how to find it.

## 17. The Wave Preview tab — done in code, not yet run in OrcaSlicer

The user found the Plugins-dialog button and asked for a tab. 0.9.0 adds `WavePreviewTab` (a Pages
capability): a "Wave Preview" tab next to Prepare/Preview with the same page as the side panel,
refreshed after every slice/export. `tests/notifications_test.py` covers its messages, updates and the
registration of the three capabilities; `tests/preview_page_test.py` the empty state.

To check: reinstall 0.9.0; a "Wave Preview" tab should appear next to Prepare/Preview (maybe only after
the plugin is toggled off and on, or after restarting OrcaSlicer, as `PluginPages` creates pages when a
capability registers); open it (before any slice it says there are no waves yet); slice, and it should
fill in; export, and it should refresh. Unknowns: whether the tab appears without a restart, the tab
icon (none), the tab's size/behaviour of WebGL when hidden then shown.

## 18. Side panel from the tab, and automatically — done in code, not yet run in OrcaSlicer

User asked whether the tab could be a button for the side panel. A tab cannot (it always shows its page,
no tab-switching API, the dock only shows in Prepare/Preview), so 0.10.0 adds (A) `open_panel` (default 0):
the side panel opens by itself after a slice, once per slice; (B) an "Open side panel" button in the tab.
Tests: `tests/notifications_test.py` (auto-open on/off, not reopened by the export, the button's replies),
`tests/preview_page_test.py` (button only in the tab's page).

To check: set `open_panel` to 1 in the plugin configuration, slice: the panel should be docked in Preview;
close it, export: it should stay closed. In the Wave Preview tab, click Open side panel, then Preview.

## 19. Checkbox instead of a button (user feedback on 0.10.0) — done in code, not yet run

0.10.0 worked, but clicking "Open side panel" gave no sign it had done anything, and a tab cannot jump to
Preview (no tab-switching API). 0.10.1: the button is a "show side panel" checkbox that mirrors whether the
side panel is open (bold text "side panel open: see the Prepare or Preview tab"), unticking closes it,
and it follows the panel when it is opened/closed any other way. Tests in `tests/notifications_test.py`;
layout checked in Chrome (wide and narrow).

## 20. Next

Check 0.10.1 in OrcaSlicer, then push and tag (v0.8.x to v0.10.x are untagged). Also: add the "second
window" sentence to the preview proposal; README note for macOS.
