# Proposal: show G-code plugin changes in the preview on all printers

Draft for an issue on [OrcaSlicer/OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer).

## The problem

Slicing-pipeline plugins can change the G-code in their final post-processing step. On Bambu
printers that step runs right after slicing, and the preview shows the result. On every other
printer it only runs when the file is exported, so the preview never shows what the plugin did.

For plugins that add or change moves, users on non-Bambu printers have nothing to check before
printing:

- the preview and layer slider show toolpaths that will not be printed, and leave out ones
  that will;
- the print time and filament estimates don't include the plugin's changes;
- the only way to see the real result is to export, then open the exported file in the G-code
  viewer.

The motivating case is [Wave Overhangs](https://github.com/micatlkw/WaveOverHangsPlugin), a
plugin port of the [OrcaSlicer-WaveOverhangs](https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs)
fork. It adds wave-shaped overhang paths and can add more than an hour of printing, none of
which appears in the preview on non-Bambu printers.

## The proposal

Run plugins' G-code step right after slicing for all printers, as is already done for Bambu
printers, so the preview shows the G-code that will actually be printed. Post-processing
scripts can keep running at export as they do today.

## Why this approach

- **It already works.** The Bambu path runs the step after slicing and refreshes the preview
  from the edited file. Using it for every printer adds a printer type to an existing,
  tested mechanism; it doesn't add a new one.
- **The preview matches what gets printed**, so users can check plugin output before printing,
  like any other slicer feature.
- **Plugins behave the same on every printer.** Today the same plugin shows up in the preview
  on one printer and not on another, which is confusing for users and plugin authors.
- **Scripts are not affected.** Keeping scripts at export avoids changing behaviour that
  existing user scripts rely on, such as renaming the output file.

## Things to consider

- Plugins would run once per slice instead of once per export, and slicing would take as long
  as the plugin does (time that export takes today).
- Plugins would run before the post-processing scripts rather than after, and would no longer
  see the final export file name. If that matters for some plugins, the change could be an
  option, either a process setting or a flag a plugin declares.
- The print time and filament estimates should also be refreshed from the edited file. That
  doesn't happen on Bambu printers today either, so it is worth fixing at the same time.

We are happy to help test, or to contribute a PR if the maintainers agree with the approach.
