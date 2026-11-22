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

## 5. Several objects / instances on the plate — done in code, not yet run in OrcaSlicer

v0.2.0: G-code split per object copy by its labels, each copy matched to its object by shape
and placed at its own offset; waves inserted at the end of each copy's part of the layer.
Tested outside OrcaSlicer: `tests/gcode_instances_test.py` (2 objects, 3 copies, all four
label families, no-label cases) and a regression on both real G-code files (waves rebuilt
from the file: same insertion points, every other line identical to v0.1.0's output).

To check in OrcaSlicer: a plate with two different objects and a second copy of one of them,
with "Exclude objects" on (default for Klipper) — and once with it off, to see the warning.

## 6. Next

Bambu printer, wipe tower, by-object printing; settings (minimum overhang width, outer wall
kept); README for users; upstream API proposal for writable extrusions (preview).
