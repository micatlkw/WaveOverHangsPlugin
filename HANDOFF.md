# Handoff: next steps for route 1

Written by another session after the first real run of the spike (see "Current state" in
CLAUDE.md). Delete this file once the steps are done or folded into CLAUDE.md.

## 1. Close out the spike — done

Position, retraction and numpy are checked; results are in CLAUDE.md.

## 2. Second spike: how to remove the slicer's own toolpaths in the overhang area — done

All three modes were run in OrcaSlicer; results and the recommendation (`shrink`) are in CLAUDE.md.

## 3. Port the generator — done, awaiting a real run

`wave_overhangs_plugin.py` v0.1.0 (pyclipper; decisions: AGPL-3.0, no outer wall over the
overhang, every overhang waved). Tested outside OrcaSlicer: `tests/generator_smoke.py` (ring,
cantilever, T with a neck) and a fake-host run on the first test model's G-code (cut only the
overhang layer, 25 rings r 15.6–24.0 mm, retraction/fan/E state restored, idempotent).

## 4. Next

- Install `wave_overhangs_plugin.py` in OrcaSlicer, slice the test models, export, and check the
  `; WAVE_OVERHANGS` report and the waves in a G-code viewer (non-Bambu preview won't show them).
- Check what the slicer prints on the cut layer and on the layer above (bridge floor).
- Print one. Defaults are the fork's: 2 mm/s waves, so a large overhang layer takes long
  (the ring test model: ~26 min for one layer).
