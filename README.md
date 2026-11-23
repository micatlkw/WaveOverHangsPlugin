# Wave Overhangs plugin for OrcaSlicer

Prints overhangs as **waves**: lines grown out from the supported edge, front after front, each
one laid against the one before, so overhangs print without supports. This is a plugin for
upstream [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer). The wave generator is ported
from the [OrcaSlicer-WaveOverhangs](https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs)
fork, so you don't need a separate build of OrcaSlicer.

Status: **experimental**. Tested on a Klipper printer with PLA: single objects, several objects
and copies on one plate, sloped and stepped overhangs, overhangs with holes, a wipe tower and
printing one object at a time.

## How it works

1. **While slicing**, the plugin works out the waves for every overhang. It then cuts the
   overhang area out of that layer, so OrcaSlicer prints nothing there. On the layer above,
   OrcaSlicer sees air underneath and prints a bridge, which becomes the floor on top of the
   waves.
2. **When the G-code is exported**, the plugin adds the waves to each layer at slow speed with
   full fan, then (on wide overhangs) one normal wall along the overhang's edge, laid against
   the waves. It then puts back the slicer's own settings: retraction, fan, feed rate and
   extrusion mode. A report goes at the top of the file.

The waves are added at export, so **on non-Bambu printers the slicer's preview does not show
them** (see [Known limits](#known-limits)). To check a slice, open the exported `.gcode` file in
OrcaSlicer: drag it into the window, or use the File menu. It opens in the G-code viewer, which
shows the waves and the real print time.

## Requirements

- OrcaSlicer 2.5 or later (the first release with the Python plugin system).
- An internet connection the first time the plugin is installed. OrcaSlicer installs the
  plugin's Python packages (`pyclipper`, `numpy`) by itself.

## Installing

1. In OrcaSlicer, open the **Plugins** dialog and install `wave_overhangs_plugin.py`.
2. In the process settings, switch to **Advanced** mode, open **Others** and pick
   **Wave Overhangs** under **Slicing Pipeline Plugin**.
3. If there is more than one object or copy on the plate, turn on **Exclude objects** or
   **Label objects** (Others → G-code output). The plugin needs these labels to tell copies
   apart. Without them it only handles a plate with a single object.
4. Save the process preset.
5. Slice and export. The first lines of the exported G-code start with `; WAVE_OVERHANGS`;
   check them (see [The report](#the-report)).

## Settings

Edit these under **Others → Plugin Configuration** in the process settings, then save the
preset. Distances are in mm.

### Which overhangs get waves

| Setting | Default | What it does |
|---|---|---|
| `min_overhang_mm` | `0.5` | Overhang that reaches no further than this past the layer below keeps the slicer's normal walls. This is meant for gentle slopes: at 0.2 mm layers, 0.5 mm per layer is about 68° from vertical. Where such a slope joins an overhang that reaches further, the strip next to it is still waved, because the waves need it as their anchor. `0` waves every overhang, as the fork does. |
| `min_length_mm` | `0` | Skip overhangs whose outline is shorter than this. |
| `outer_walls` | `1` | Normal walls kept along the edge of wide overhangs, so their side looks like the rest of the object. They are printed right after the waves, innermost first, and the waves fill the area inside them. Chosen automatically per overhang: a wall is kept only where the overhang reaches past it plus two wave lines (about 1.1 mm per layer with the defaults, roughly 80° from vertical at 0.2 mm layers), so the waves inside can carry it. Steeper, narrower overhangs are waved right to the edge, and where one joins a wide overhang only the wide part gets a wall. `0` never keeps a wall. At most the process's wall count. |
| `enabled` | `1` | `0` only measures and writes the report. Nothing is cut and no waves are added. |

### Wave shape

| Setting | Default | What it does |
|---|---|---|
| `line_spacing_mm` | `0.35` | Distance between wave fronts. |
| `perimeter_overlap_mm` | `0.1` | Grow the waved area by this much, so the waves overlap the walls and the edge they start from. |
| `minimum_wave_width_mm` | `0.7` | Split the wave area at necks narrower than this, so each side fills on its own. |
| `pattern` | `smart` | Order of the fronts: `smart`, `monotonic` or `zigzag`. |
| `min_new_area_mm2` | `0.01` | Stop growing fronts when one adds less area than this. |
| `max_iterations` | `0` | Maximum number of fronts per area (`0` = no limit). |
| `corner_taper_enable` | `0` | `1` places fronts closer together near sharp corners. |
| `line_spacing_corner_mm` | `0.18` | Front spacing at those corners. |
| `corner_taper_distance_mm` | `3.0` | How far from the corner the closer spacing reaches. |
| `corner_angle_threshold` | `90` | Corners sharper than this angle (degrees) count as sharp. |

### Printing the waves

| Setting | Default | What it does |
|---|---|---|
| `flow_mm3_per_mm` | `0.15` | Plastic per mm of wave line. It doesn't depend on the layer height. |
| `print_speed_mm_s` | `2` | Wave printing speed. Waves are slow on purpose: each line has to cool against the one before it. |
| `travel_speed_mm_s` | `40` | Travel speed between wave lines. |
| `fan_speed_percent` | `100` | Part-cooling fan while printing waves and kept walls. The slicer's fan setting is restored afterwards. |
| `wall_speed_mm_s` | `0` | Speed of the kept walls. `0` uses the slicer's speed for fully overhanging walls (Speed → Overhang speed, the 75–100% value), or its bridge speed if that isn't set. |

These values come from the printer and filament profiles: nozzle diameter (used as the wave
line width), retraction length (capped at 2 mm), filament diameter, relative/absolute E, wall
and bridge line widths, and resolution.

**Print time:** waves are slow. A ~45 mm disc on a 32 mm square column takes about 16
minutes of waves on its one overhang layer. The report gives the added time for each export.

## The report

Example from a plate with three objects:

```
; WAVE_OVERHANGS version 0.5.0; state hand-over: module global kept, capability instance kept
; WAVE_OVERHANGS OverhangWaveTest.stl (#0) generated at posSlice: 258 wave path(s) in 17 region(s) on 12 layer(s), 4210.381 mm; slices cut on 12 layer(s)
; WAVE_OVERHANGS G-code: standard tags, 110 layers, object labels: EXCLUDE_OBJECT_START/END, 4 instance(s)
; WAVE_OVERHANGS OverhangWaveTest.stl_id_0_copy_0: OverhangWaveTest.stl (#0), offset X 134.85 Y 186.214 mm (deviation from it: 90th percentile 0 mm, worst layer 0 mm; size mismatch 0 mm; 73 of 73 layers compared), waves on 12 layer(s)
; WAVE_OVERHANGS injected 25 wave block(s): 9015.531 mm of wave line, 562.233 mm of filament, about 77.761 min
```

- **generated** (one line per object): how many waves were computed and on how many layers. It
  also says on how many layers a gentle slope was left to the slicer.
- **G-code**: how many object copies were found in the file, and from which labels.
- **One line per copy**: which object it was matched to and where it sits on the plate.
  The deviation and size mismatch should be close to 0.
- **injected**: the total wave line, filament and added time.

Inside the file, each layer's waves sit between `; WAVE_OVERHANGS_START` and
`; WAVE_OVERHANGS_END`. They are typed as `Overhang wall`.

### When something goes wrong

| Report says | Meaning and fix |
|---|---|
| `nothing captured at slice time` | The plugin wasn't selected when the plate was sliced. Select it and slice again. |
| `WARNING: ... the slices were not cut` | OrcaSlicer reused slicing from before the plugin was selected, so the slicer's own walls are still in the overhang. Make it slice the object again from the start, for example by changing the layer height and changing it back. |
| `WARNING: N objects but the G-code has no object labels` | Turn on **Exclude objects** or **Label objects** (Others → G-code output). |
| `no captured object matches (...)` | The plugin couldn't place that copy, so it gets no waves. Slice again. If it persists, please report it with the G-code. |
| `has waves but was not placed` | That object is not in this G-code, for example because it is on another plate. |
| `already processed, left untouched` | The file already had waves. The plugin never adds them twice. |

## Known limits

- **Preview:** on non-Bambu printers the slicer's preview, print time and filament estimate
  don't include the waves. Open the exported file in the G-code viewer to see them. A
  [proposal to OrcaSlicer](docs/upstream-proposal-preview-postprocess.md) would fix this.
- **Plugins can't add toolpaths** to OrcaSlicer yet, so the waves are added to the G-code text
  instead. As a result:
  - waves are printed at the end of each object's part of the layer, not before that island's
    walls as in the fork;
  - the slicer's supports don't know about the waves.
- **Every overhang that isn't a gentle slope gets waves**, including ones the slicer could
  bridge. The fork can leave bridgeable overhangs as bridges, but plugins can't call the
  slicer's bridge detection.
- **Not ported from the fork:** floor layers, Hilbert-curve floors, a separate wave
  temperature, and minimum wave or layer times. The slicer's bridge on the layer above is the
  floor.
- **The top layer** of an object gets no waves.
- **Two filaments on one layer of one object** (for example painted colours): its waves use the
  filament that was active last on that layer.
- **Bambu printers** are untested.

## Credits and license

Wave overhangs algorithm by Janis A. Andersons (andersonsjanis), building on the arc-overhang
algorithm by Steven McCulloch (stmcculloch), who also wrote the PrusaSlicer integration. Port to
OrcaSlicer by Dennis Klappe (dennisklappe). This plugin ports that generator to Python with
[pyclipper](https://github.com/fonttools/pyclipper).

Licensed under the AGPL-3.0, like the code it is ported from (see `LICENSE`).
