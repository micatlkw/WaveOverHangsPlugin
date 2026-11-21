# WaveOverHangsPlugin

Wave overhangs for upstream [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer) as a
slicing-pipeline Python plugin: overhangs are printed as concentric waves grown from the
supported edge, a port of the generator from
[OrcaSlicer-WaveOverhangs](https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs).

Install `wave_overhangs_plugin.py` through OrcaSlicer's Plugins dialog and select it as the
slicing pipeline plugin in the process settings. Status: experimental, see `HANDOFF.md`.

Wave overhangs algorithm by Janis A. Andersons, building on the arc-overhang algorithm by Steven
McCulloch; port to OrcaSlicer by Dennis Klappe. Licensed under the AGPL-3.0 (see `LICENSE`).
