# Changelog

Versions are those in the plugin's header (`# version = ...`) and in the first line of the report in
the exported G-code (`; WAVE_OVERHANGS version ...`).

## 0.12.0 - 2026-11
- The preview side panel now opens by itself after slicing (`open_panel` is on by default; turn it
  off in the settings).
- `pyclipper` is pinned to `1.4.0` and `numpy` to `>=2.0,<3`, so a newer or hijacked release is not
  installed automatically.
- Licence and copyright notices on every source file; fuller credits; `SECURITY.md`,
  `CONTRIBUTING.md`, this changelog and a user guide with pictures.
- The research-era files moved to `dev/`; the tests no longer need any local files.

## 0.11.0
- A settings page that lists every setting with its unit and an explanation, filling in defaults
  (OrcaSlicer's own editor shows only what has been saved, which was nothing).
- Print time: the preview shows "slicer + waves = total", and `adjust_print_time` (on by default)
  corrects the `M73` progress lines and the estimate line in the exported G-code.

## 0.10.x
- `open_panel` and a checkbox in the Wave Preview tab that shows and controls whether the side panel
  is open.

## 0.9.0
- A **Wave Preview** tab next to Prepare and Preview.

## 0.8.x
- The preview is available right after slicing, before any export; the export then refreshes it with
  the exact data. The notification is one short line and stays for 5 minutes. **Show Wave Preview**
  action in the Plugins dialog.

## 0.7.x
- The preview panel (3D and layer views) opened from a **Show waves** link after an export. A warning
  when OrcaSlicer reuses a slice the plugin never saw.

## 0.6.0
- Notifications in OrcaSlicer, a per-object on/off through "Enable support" (`apply_to`), and a log
  file.

## 0.5.0
- Walls and waves are chosen per overhang: a wall is kept only where the overhang is wide enough to
  carry it; steep, narrow overhangs are waved to the edge.

## 0.4.0
- A normal outer wall kept along wide overhangs (`outer_walls`, `wall_speed_mm_s`).

## 0.3.0
- Gentle slopes keep the slicer's own walls (`min_overhang_mm`, 0.5 by default). User README.

## 0.2.0
- Several objects and copies on one plate (found by the slicer's object labels), wipe towers and
  printing one object at a time.

## 0.1.0
- First version: a port of the fork's wave generator to Python, with the waves added to the exported
  G-code.
