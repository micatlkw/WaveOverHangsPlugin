# Proposal: show G-code plugin changes in the preview on all printers

Draft for an issue on [OrcaSlicer/OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer).

## The problem

Slicing-pipeline plugins can change the G-code in their final post-processing step. On Bambu
printers that step runs right after slicing, so the preview, layer slider, print time and
filament estimate all reflect it. On every other printer it runs only when the file is
exported, so none of them do: users see one file in the preview and print another.

## Who this affects

Many useful G-code plugins change the toolpaths themselves, not just comments or start/end
code, so the preview is the only way to check them. Examples of what plugins in this step can
do, many of which exist today as post-processing scripts:

- **Add or replace toolpaths**: overhang strategies such as wave or arc overhangs, extra
  ironing passes, non-planar top surfaces, purge or priming moves.
- **Move existing toolpaths**: shifting alternate walls in Z for interlocking layers ("brick
  layers"), changing Z-hop or travel paths.
- **Rewrite moves**: arc fitting (G2/G3), splitting or merging segments.
- **Change speed, flow, fan or temperature** per feature or per layer: temperature and flow
  calibration towers, adaptive flow or pressure advance, extra cooling on overhangs.
- **Insert pauses and events**: filament or colour changes, timelapse moves, inserts placed at a
  given height.

For all of these, a non-Bambu user today cannot see what will be printed without exporting and
opening the file in the separate G-code viewer.

## Why it matters

- **Safety.** A plugin that adds or moves toolpaths can produce collisions, moves outside the
  object or gaps. Checking the preview before printing is how slicer users catch problems;
  with a plugin, that check currently shows the wrong G-code.
- **Planning.** Print time and filament use decide whether a print fits a time slot or a spool.
  A plugin that slows parts of a print or adds material can change both a lot (one overhang
  plugin adds over an hour on a test model), and the estimate doesn't show it.
- **Consistency.** The same plugin shows its changes on a Bambu printer and hides them on every
  other printer. Users and plugin authors can't tell whether a plugin is broken or the preview
  just doesn't show it.
- **Trust in the plugin system.** Plugins are new. If plugin changes are visible like any other
  slicer feature, users can try plugins confidently and authors can develop and debug them in
  the slicer. If changes only appear after export, plugins feel like a hidden layer users have
  to take on faith.

## The proposal

For all printers, run plugins' G-code step right after slicing and refresh the preview from
the result, as OrcaSlicer already does for Bambu printers. Post-processing scripts can keep
running at export as they do today. The print time and filament estimates should be refreshed
from the edited G-code too; that would also fix the same gap on Bambu printers, where only the
toolpaths are refreshed today.

## Why this approach

- **It reuses what already works.** Running the step after slicing and refreshing the preview
  is already in place and tested for Bambu printers. This extends it to the other printers
  rather than adding a new mechanism.
- **Scripts are not affected.** Existing user scripts, including ones that rename the output
  file or depend on the export target, keep running at export as before.
- **No new work for plugin authors.** Existing plugins don't need changes; their output just
  becomes visible.

## Things to consider

- Plugins would run once per slice instead of once per export. Slicing takes as long as the
  plugin, time that export takes today.
- Plugins would run before post-processing scripts rather than after, and wouldn't see the
  final export file name. If some plugins depend on that, the change could be an option, either
  a process setting or a flag a plugin declares.

We are happy to help test, or to contribute a PR if the maintainers agree with the approach.
