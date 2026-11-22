# Proposal: show G-code plugin edits in the preview for all printers

Draft for an issue/PR on [OrcaSlicer/OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer).
Code references are against `main` at `12c9ee8` (2026-11-17).

## Summary

Slicing-pipeline plugins edit the G-code at `psGCodePostProcess`. On Bambu printers that step
runs in place right after slicing and the preview re-reads the edited file, so the preview
shows what the plugin did. On every other printer it runs only at export, on a copy, so the
preview never shows it. This proposal runs **plugins** (not post-processing scripts) the Bambu
way for all printers: in place after slicing, followed by `reload_gcode_moves()`. Scripts keep
running at export as today.

## Problem

Plugins that add or change moves (the motivating case is
[Wave Overhangs](https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs) as a plugin, which
adds wave-shaped overhang paths) cannot be checked in the preview on non-Bambu printers. Users
have to export, then open the exported file in the G-code viewer to see what will be printed.
The preview, the layer slider and the per-feature legend all describe a file that is not the
one being printed.

## How it works today

`src/slic3r/GUI/BackgroundSlicingProcess.cpp`:

- **Bambu printers** (`process_fff()`, lines 291–303): after `export_gcode()` writes the temp
  G-code, `run_post_process_scripts(m_temp_output_path, false /*make_copy*/, "File",
  m_temp_output_path, ...)` runs scripts and plugins on the temp file itself, then
  `Print::reload_gcode_moves(m_gcode_result)` re-reads the moves and line offsets from it. A
  failure invalidates the G-code export so the next attempt re-exports
  (`invalidate_gcode_export_from_worker()`, #17095).
- **Other printers**: nothing runs after slicing. Post-processing runs at each export, on a
  `.pp` copy (`make_copy = true`) because the G-code viewer memory-maps the temp file and
  addresses it by line number:
  - export to file: `finalize_gcode()`, line 871;
  - upload to a print host: `export_gcode()` upload branch, line 1017 (on a dedicated copy);
  - saving a project with G-code: `Plater.cpp`, line 21105.

`src/slic3r/GUI/PostProcessor.cpp`, `run_post_process_scripts_impl()`: scripts run first, then
plugins (`run_post_process_plugins()`), so plugins see an `output_name` a script changed.

The Bambu path already proves the mechanism: editing the temp file in place and then calling
`reload_gcode_moves()` keeps the viewer consistent, because the moves and `lines_ends` are
rebuilt from the edited file.

## Proposal

For non-Bambu printers, when `slicing_pipeline_plugin` is set:

1. **After slicing**, in `process_fff()` right where the Bambu branch is: run only the plugins'
   G-code step in place on `m_temp_output_path`, then `reload_gcode_moves(m_gcode_result)`.
   Use the same failure handling as the Bambu branch.
2. **At export / upload / project save**: run only the scripts (plugins already ran), on the
   `.pp` copy as today.

Bambu printers are unchanged: they keep running scripts and plugins together in place.

Sketch:

```cpp
// PostProcessor.hpp: let callers choose what runs.
enum class PostProcessParts { All, ScriptsOnly, PluginsOnly };
bool run_post_process_scripts(std::string& src_path, bool make_copy, const std::string& host,
                              std::string& output_name, const DynamicPrintConfig& config,
                              PostProcessParts parts = PostProcessParts::All);

// BackgroundSlicingProcess::process_fff(), after export_gcode():
const bool bbl = m_fff_print->is_BBL_printer();
if (bbl || has_slicing_pipeline_plugins(m_fff_print->full_print_config())) {
    try {
        if (run_post_process_scripts(m_temp_output_path, false, "File", m_temp_output_path,
                                     m_fff_print->full_print_config(),
                                     bbl ? PostProcessParts::All : PostProcessParts::PluginsOnly))
            m_fff_print->reload_gcode_moves(m_gcode_result);
    } catch (...) {
        m_fff_print->invalidate_gcode_export_from_worker();
        throw;
    }
}

// finalize_gcode(), the upload branch of export_gcode(), and Plater's project save
// (non-Bambu only, as today):
run_post_process_scripts(..., PostProcessParts::ScriptsOnly);
```

`run_post_process_scripts_impl()` would skip the script loop for `PluginsOnly` and the plugin
call for `ScriptsOnly`; the copy, `.output_name` and error handling stay as they are.

## What changes for users and plugin authors

- **Preview**: shows the plugin's moves, features and layer contents for every printer, as it
  already does on Bambu printers.
- **Plugins run once per slice, not once per export.** Exporting twice writes the same file;
  re-exporting no longer re-runs the plugin. Slicing takes longer by the plugin's run time
  (the same time it costs at export today).
- **Order of scripts and plugins flips** on non-Bambu printers: plugins now run before the
  scripts instead of after. Plugins see `host = "File"` and the temp path as `output_name` (as
  on Bambu printers today), not the export target or a name a script chose. A plugin that needs
  the final file name would no longer get it.
- **Scripts are unaffected**: they still run at export, on a copy, with the real host and
  target name, and can still rename the output.

## Options, if the order change is a concern

- **A. Unconditional** (as above). Simplest; matches the Bambu behaviour.
- **B. Process setting**, e.g. "Apply G-code plugins after slicing (show in preview)", default
  on. Off gives today's behaviour.
- **C. Per plugin**: a capability metadata flag (for example `gcode_post_process_at = "slice"`)
  so plugins that need the export name keep running at export.

A is preferred unless maintainers know of plugins that rely on the export-time `output_name`.

## Related: statistics after the reload

`Print::reload_gcode_moves()` (`src/libslic3r/Print.cpp`, line 5987) replaces only
`result->moves` and `result->lines_ends`. The processor that re-read the file has also
recomputed the time and filament estimates (`processor.result().print_statistics`), but they
are discarded, so the slice summary shows the estimate from before post-processing. This is
already the case on Bambu printers. Copying `print_statistics` (and the per-layer times the
legend shows) in the same place would make the estimate include plugin edits too. It can be
done in the same PR or separately.

## Test plan

- Non-Bambu printer with a plugin that inserts moves: preview shows them after slicing; export
  writes the same file as before this change (apart from script effects, now applied after the
  plugin); exporting twice gives identical files.
- Non-Bambu printer with a script only (no plugin): no change at slice time; export behaves
  exactly as today.
- Script that renames the output via `.output_name`: still works at export.
- Plugin that raises: the slice reports the error and the next slice/export re-exports cleanly
  (same as the Bambu path, #17095).
- Upload to a print host and saving a project with G-code: plugin edits present once, scripts
  applied once.
- Bambu printer: unchanged.
