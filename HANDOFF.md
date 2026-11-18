# Handoff: next steps for route 1

Written by another session after the first real run of the spike (see "Current state" in
CLAUDE.md). Delete this file once the steps are done or folded into CLAUDE.md.

## 1. Close out the spike — done

Position, retraction and numpy are checked; results are in CLAUDE.md.

## 2. Second spike: how to remove the slicer's own toolpaths in the overhang area

Add a `mode` config option with two values, slice the same model both ways and compare,
especially what the layer above the overhang becomes.

- **shrink:** at `posSlice`, capture the overhang first, then cut it out of `region.slices`
  (see `../OrcaSlicer/sandboxes/orca_inset_plugin_any.py` for the write-back idiom and
  `layer.make_slices()`). The capture must keep the unshrunk lower layer, otherwise the next
  layer's overhang is computed wrong.
  Risk: the slicer then sees the layer above as hanging over air. It may be bridged (probably
  fine; the fork forces a bridge floor above waves anyway) or detected as a new overhang by our
  own capture, cascading upward.
- **delete:** leave the slices alone and, at post-process, drop wall/bridge/infill moves whose
  segments fall inside the captured overhang. More fragile G-code surgery, but the slicer's
  classification of other layers is untouched.

The test model has a single overhang layer, which makes it a good first case for either.

## 3. Port the generator

`../OrcaSlicer-WaveOverhangs/src/libslic3r/WaveOverhangs/WaveOverhangs.cpp` (~890 lines) leans
on polygon offsets; the host only offers pairwise booleans. Before porting, try declaring a
dependency such as `pyclipper` or `shapely` in the plugin header (`dependencies = [...]`, as
`../OrcaSlicer/sandboxes/orca_inspector_plugin_any.py` does with numpy). If OrcaSlicer installs
it, the port is much more direct and faster.
