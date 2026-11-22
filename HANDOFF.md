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

## 8. Next

Wipe tower, by-object printing; outer wall kept over the overhang (setting); README for users;
upstream API proposal for writable extrusions.
