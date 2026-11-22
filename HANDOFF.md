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
- Then: models with several overhang layers (sloped/stacked overhangs) and holes.
