# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test of the settings page, rendered headlessly in Chrome.

    python3 tests/config_page_test.py [out_dir]

Needs pyclipper, numpy and google-chrome. The page gets a stand-in for the bridge OrcaSlicer's
Plugins dialog injects (window.orca.getConfig / saveConfig / restoreDefaults / onConfig) and checks:
  - with nothing stored (what a user has before saving anything) every setting is listed with its
    default, including open_panel;
  - a stored config shows its values, and Save writes every setting, keeping keys the page has no
    control for;
  - a value that is not a number in range is marked and not saved;
  - Restore defaults asks the host for the defaults.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcode_instances_test as gi  # noqa: E402  (stubs orca, loads the plugin)

w = gi.w
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
FLAGS = ["--headless=new", "--no-sandbox", "--disable-gpu-sandbox", "--use-angle=swiftshader",
         "--enable-unsafe-swiftshader", "--hide-scrollbars", "--force-device-scale-factor=1"]
ok = True


def check(cond, what):
    global ok
    ok &= bool(cond)
    print("%s %s" % ("OK  " if cond else "FAIL", what))


# What the Plugins dialog injects before the page (resources/web/dialog/js/plugin-config-ui.js), reduced.
BRIDGE = """<script>
(function () {
  var current = %s, handlers = [];
  window.orca = {
    getConfig: function () { return current; },
    saveConfig: function (cfg) { document.documentElement.setAttribute("data-saved", JSON.stringify(cfg)); },
    restoreDefaults: function () { document.documentElement.setAttribute("data-restored", "1"); },
    getContext: function () { return {}; },
    onConfig: function (cb) { handlers.push(cb); cb(current); }
  };
})();
</script>
"""


def run(config, steps, out_dir, name, size="860,1500"):
    """Load the page with `config` stored, run the JS in `steps`, return (dom, png path)."""
    html = BRIDGE % json.dumps(config) + w.config_page_html() + "<script>" + steps + "</script>"
    path = os.path.join(out_dir, name + ".html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    url = "file://" + path
    dom = subprocess.run([CHROME] + FLAGS + ["--dump-dom", "--virtual-time-budget=3000", url],
                         capture_output=True, text=True, timeout=120).stdout
    png = os.path.join(out_dir, name + ".png")
    subprocess.run([CHROME] + FLAGS + ["--window-size=" + size, "--screenshot=" + png, "--virtual-time-budget=3000", url],
                   capture_output=True, text=True, timeout=120)
    return dom, png


def attr(dom, name):
    import re
    import html as htmllib
    m = re.search(r'data-%s="([^"]*)"' % name, dom)
    return htmllib.unescape(m.group(1)) if m else None


def main():
    if not CHROME:
        print("skip: no Chrome found")
        return 0
    out_dir = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else tempfile.mkdtemp()
    os.makedirs(out_dir, exist_ok=True)
    keys = list(w._DEFAULTS)
    click_save = 'setTimeout(function () { document.getElementById("save").click(); }, 100);'

    # 1. nothing stored: every setting shows its default, and Save writes them all
    dom, png = run({}, click_save, out_dir, "config_empty")
    saved = json.loads(attr(dom, "saved") or "{}")
    check(set(saved) == set(keys), "nothing stored: Save writes all %d settings (%s missing)"
          % (len(keys), sorted(set(keys) - set(saved))))
    check(saved.get("open_panel") == 1 and saved.get("preview") == 1 and saved.get("pattern") == "smart"
          and saved.get("min_overhang_mm") == 0.5 and saved.get("apply_to") == "all",
          "and they are the defaults, open_panel among them")
    check(all(abs(saved[k] - w._DEFAULTS[k]) < 1e-9 for k in keys if not isinstance(w._DEFAULTS[k], str)),
          "every number equals the plugin's default")
    for f in w._CONFIG_FIELDS:
        if 'data-key="%s"' % f[1] not in dom:
            check(False, "the page has no control for %s" % f[1])
    check(True, "every setting has a control on the page (%d)" % len(w._CONFIG_FIELDS))

    # 2. a stored config: its values show, unknown keys survive
    stored = {"min_overhang_mm": 1.2, "pattern": "zigzag", "open_panel": 1, "foo": 7, "enabled": True, "fan_speed_percent": "50"}
    dom, png2 = run(stored, click_save, out_dir, "config_stored")
    saved = json.loads(attr(dom, "saved") or "{}")
    check(saved.get("min_overhang_mm") == 1.2 and saved.get("pattern") == "zigzag" and saved.get("open_panel") == 1
          and saved.get("enabled") == 1 and saved.get("fan_speed_percent") == 50,
          "a stored config shows its values (booleans and numeric strings understood)")
    check(saved.get("foo") == 7 and set(keys) <= set(saved), "a key the page has no control for is kept, the rest are filled in")

    # 3. a bad number is marked and not saved
    bad = ('setTimeout(function () { document.getElementById("f_line_spacing_mm").value = "abc";'
           ' document.getElementById("save").click(); }, 100);')
    dom, _ = run({}, bad, out_dir, "config_bad")
    check(attr(dom, "saved") is None and "Check the marked values" in dom and 'class="row bad"' in dom,
          "a value that is not a number in range is marked and nothing is saved")
    out_of_range = ('setTimeout(function () { document.getElementById("f_fan_speed_percent").value = "250";'
                    ' document.getElementById("save").click(); }, 100);')
    dom, _ = run({}, out_of_range, out_dir, "config_range")
    check(attr(dom, "saved") is None and 'class="row bad"' in dom, "a number out of range is refused too")

    # 4. restore defaults
    dom, _ = run(stored, 'setTimeout(function () { document.getElementById("restore").click(); }, 100);', out_dir, "config_restore")
    check(attr(dom, "restored") == "1", "Restore defaults asks the host for the defaults")

    # 5. a change is marked
    dom, _ = run({}, 'setTimeout(function () { var c = document.getElementById("f_open_panel"); c.checked = !c.checked;'
                     ' c.dispatchEvent(new Event("change")); }, 100);', out_dir, "config_changed")
    check("1 unsaved change" in dom, "changing a setting says how many are unsaved")
    print("pictures:", png, png2)
    print("all OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
