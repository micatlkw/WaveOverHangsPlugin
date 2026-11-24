# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 WaveOverHangsPlugin contributors
"""Render the pictures used in README.md (docs/images/) from real exported G-code.

    python3 dev/make_screenshots.py PLATE.gcode DISC.gcode [out_dir]

PLATE.gcode: an export with waves on stepped, sloped and holed overhangs (the pictures show z=12.8);
DISC.gcode: an export with a ring of waves on one layer (z=3.4). The panel pages and the settings page
are drawn headlessly in Chrome exactly as OrcaSlicer's web view shows them. OrcaSlicer's own windows
(notifications, the Plugins dialog, the tab strip) are not reproduced here: those pictures are taken in
OrcaSlicer itself.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import config_page_test as ct  # noqa: E402
import preview_page_test as pt  # noqa: E402

w = pt.w
TICK = ('orca.postMessage({ command: "preview_ready" }); setTimeout(function () { '
        'document.getElementById("side").checked = true; '
        'document.getElementById("sidetext").textContent = "side panel open: see the Prepare or Preview tab"; '
        'document.getElementById("sidewrap").style.fontWeight = "bold"; }, 200);')


def load(path):
    data, _ = pt.load_data(path)
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
        lines = f.readlines()
    head = "".join(l for l in lines[:40] if l.startswith("; WAVE_OVERHANGS"))
    m = re.search(r"about ([0-9.]+) min", head)
    data["time"] = {"slicer_s": w._slicer_seconds(lines), "waves_s": float(m.group(1)) * 60.0 if m else 0.0}
    return data


def index_of(data, z):
    return next(i for i, layer in enumerate(data["layers"]) if abs(layer["z"] - z) < 1e-6)


def shot(data, out_dir, name, hash_, size, tab=False, ticked=False):
    path = pt.build_html(data, "example", out_dir, tab=tab)
    if ticked:
        html = open(path).read().replace('orca.postMessage({ command: "preview_ready" });', TICK)
        open(path, "w").write(html)
    png = os.path.join(out_dir, name)
    pt.screenshot(path, hash_, png, size=size)
    print("wrote", png)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    plate, disc = load(sys.argv[1]), load(sys.argv[2])
    out_dir = os.path.abspath(sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, "..", "docs", "images"))
    os.makedirs(out_dir, exist_ok=True)
    waved = [i for i, l in enumerate(plate["layers"]) if l["w"]]
    shot(plate, out_dir, "preview-3d.png",
         "yaw=-52&pitch=34&from=%d&to=%d" % (max(0, waved[0] - 3), waved[-1] + 2), "1000,720", tab=True, ticked=True)
    shot(plate, out_dir, "preview-layer-plate.png", "mode=layer&to=%d" % index_of(plate, 12.8), "1000,640")
    shot(disc, out_dir, "preview-layer-ring.png", "mode=layer&to=%d" % index_of(disc, 3.4), "1000,640")
    page = os.path.join(out_dir, "settings.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(ct.BRIDGE % "{}" + w.config_page_html())
    png = os.path.join(out_dir, "settings-page.png")
    import subprocess
    subprocess.run([ct.CHROME] + ct.FLAGS + ["--window-size=860,1330", "--screenshot=" + png,
                                             "--virtual-time-budget=3000", "file://" + page],
                   capture_output=True, text=True, timeout=120)
    os.remove(page)
    for leftover in ("page.html",):
        p = os.path.join(out_dir, leftover)
        if os.path.exists(p):
            os.remove(p)
    print("wrote", png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
