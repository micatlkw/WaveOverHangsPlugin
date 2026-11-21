# Handoff: next steps for route 1

Written by another session after the first real run of the spike (see "Current state" in
CLAUDE.md). Delete this file once the steps are done or folded into CLAUDE.md.

## 1. Close out the spike — done

Position, retraction and numpy are checked; results are in CLAUDE.md.

## 2. Second spike: how to remove the slicer's own toolpaths in the overhang area — done

All three modes were run in OrcaSlicer; results and the recommendation (`shrink`) are in CLAUDE.md.

## 3. Port the generator

`../OrcaSlicer-WaveOverhangs/src/libslic3r/WaveOverhangs/WaveOverhangs.cpp` (~890 lines) leans
on polygon offsets; the host only offers pairwise booleans. Before porting, try declaring a
dependency such as `pyclipper` or `shapely` in the plugin header (`dependencies = [...]`, as
`../OrcaSlicer/sandboxes/orca_inspector_plugin_any.py` does with numpy). If OrcaSlicer installs
it, the port is much more direct and faster.
