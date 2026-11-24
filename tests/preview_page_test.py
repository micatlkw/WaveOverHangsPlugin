"""Test of the preview panel page, rendered headlessly in Chrome.

    python3 tests/preview_page_test.py [real.gcode] [out_dir]

Needs pyclipper, numpy and google-chrome (headless, software WebGL). Builds the page from
PREVIEW_PAGE with a stand-in for OrcaSlicer's window.orca that answers "preview_ready" with data
from extract_preview() (a real exported file if given or found, else synthetic G-code), loads it
in several views, and checks:
  - no script error, and the page's status line says how many layers it got;
  - the screenshots are not blank: wave, kept-wall and outer-wall colours are all present where
    they should be (a 3D view, a view of the waved layers, one layer from above, waves hidden).
The PNGs are left in out_dir (default: a temporary folder) so the views can be looked at.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcode_instances_test as gi  # noqa: E402  (stubs orca, loads the plugin, builds G-code)

w = gi.w
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
FLAGS = ["--headless=new", "--no-sandbox", "--disable-gpu-sandbox", "--use-angle=swiftshader",
         "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--hide-scrollbars", "--force-device-scale-factor=1"]
ok = True


def check(cond, what):
    global ok
    ok &= bool(cond)
    print("%s %s" % ("OK  " if cond else "FAIL", what))


def load_data(path):
    if path:
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
            lines = f.readlines()
        e_rel = any(l.startswith("; use_relative_e_distances = 1") for l in lines)
        return w.extract_preview(lines, e_rel), os.path.basename(path)
    out, _ = w._post_process(gi.build("klipper", gi.INSTANCES), gi.captures(), (True, True), dict(gi.CFG))
    return w.extract_preview(out, True), "synthetic"


STUB = """<script>
window.__DATA__ = %s;
(function () {
  var handlers = [];
  window.orca = {
    onMessage: function (cb) { handlers.push(cb); },
    postMessage: function (m) {
      if (m && m.command === "preview_ready")
        setTimeout(function () {
          var d = window.__DATA__ || { command: "empty" }; if (window.__DATA__) d.command = "preview";
          handlers.forEach(function (h) { h(d); });
        }, 20);
    }
  };
})();
</script>
"""


def build_html(data, title, out_dir):
    data = dict(data, title=title) if data is not None else None
    html = w.PREVIEW_PAGE.replace("<div id=\"bar\">", STUB % json.dumps(data) + "<div id=\"bar\">", 1)
    path = os.path.join(out_dir, "page.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def chrome(path, hash_, extra):
    return subprocess.run([CHROME] + FLAGS + extra + ["file://%s#%s" % (path, hash_)],
                          capture_output=True, text=True, timeout=120)


def screenshot(path, hash_, png, size="520,900"):
    chrome(path, hash_, ["--window-size=" + size, "--screenshot=" + png, "--virtual-time-budget=4000"])
    return os.path.exists(png)


def colour_counts(png):
    import numpy as np
    from matplotlib import image
    img = image.imread(png)[:, :, :3]
    img = img[110:-60]      # the drawing area only: not the toolbar (teal outline) or the text below
    px = (img * 255).round().astype(int).reshape(-1, 3)

    def near(rgb, tol):
        return int((np.abs(px - np.array(rgb)).max(axis=1) <= tol).sum())
    return {"waves": near((26, 217, 179), 40), "kept": near((255, 140, 26), 40),
            "outer": near((158, 158, 168), 60) - 0, "pixels": len(px)}


def main():
    if not CHROME:
        print("skip: no Chrome found")
        return 0
    real = next((a for a in sys.argv[1:] if a.endswith(".gcode")), None)
    out_dir = os.path.abspath(next((a for a in sys.argv[1:] if os.path.isdir(a)), None) or tempfile.mkdtemp())
    if not real:
        found = glob.glob("/path/to/your/exports/waveoverhangs-207-wall-loop.gcode")
        real = found[0] if found else None
    data, title = load_data(real)
    layers = data["layers"]
    waved = [i for i, l in enumerate(layers) if l["w"]]
    kept = [i for i, l in enumerate(layers) if l["k"]]
    check(waved, "%s: %d layers, waves on %d of them" % (title, len(layers), len(waved)))
    path = build_html(data, title, out_dir)

    empty = chrome(build_html(None, "", out_dir), "", ["--dump-dom", "--virtual-time-budget=4000"]).stdout
    check("No waves to show yet" in empty and 'id="status" class="bad"' not in empty,
          "with no data the page says there are no waves yet")
    path = build_html(data, title, out_dir)
    dom = chrome(path, "", ["--dump-dom", "--virtual-time-budget=4000"]).stdout
    check('id="status" class="bad"' not in dom, "page loads without a script error")
    check(("%d layers" % len(layers)) in dom, "status line reports %d layers" % len(layers))

    views = {
        "3d_all": "yaw=-55&pitch=35",
        "3d_waved": "yaw=-55&pitch=35&from=%d&to=%d" % (max(0, waved[0] - 3), min(len(layers) - 1, waved[-1] + 2)),
        "layer_kept": "mode=layer&to=%d" % (kept[0] if kept else waved[0]),
        "layer_no_waves": "mode=layer&show=kc&to=%d" % (kept[0] if kept else waved[0]),
    }
    counts = {}
    for name, hash_ in views.items():
        png = os.path.join(out_dir, name + ".png")
        check(screenshot(path, hash_, png), "rendered %s -> %s" % (name, png))
        if os.path.exists(png):
            counts[name] = colour_counts(png)
    for name, c in counts.items():
        print("     %-15s waves %6d px, kept %6d px, outer %6d px" % (name, c["waves"], c["kept"], c["outer"]))
    if counts:
        check(counts["3d_all"]["waves"] > 200 and counts["3d_all"]["outer"] > 200, "3D view shows waves and outer walls")
        check(counts["3d_waved"]["waves"] > 200, "3D view of the waved layers shows waves")
        if kept:
            check(counts["layer_kept"]["kept"] > 50 and counts["layer_kept"]["waves"] > 50,
                  "layer view shows waves and kept walls")
            check(counts["layer_no_waves"]["waves"] == 0 and counts["layer_no_waves"]["kept"] > 50,
                  "waves can be hidden, kept walls stay")
    print("all OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
