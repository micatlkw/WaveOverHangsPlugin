# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 WaveOverHangsPlugin contributors
"""Test of the print-time correction (M73 lines, the estimate line, the preview's time), outside OrcaSlicer.

    python3 tests/print_time_test.py

Needs pyclipper and numpy. The synthetic file has OrcaSlicer's own time information (M73 P/R lines and
the "estimated printing time" line, 10 minutes) and waves that, at a very slow wave speed, add a known
time. Checks that every M73 line gets the time of the blocks still ahead of it, that the percentages
stay monotonic and end at 100, that the estimate line becomes slicer + waves, that switching the
setting off leaves the slicer's numbers alone, and that the real files' estimates parse.
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcode_instances_test as gi  # noqa: E402  (stubs orca, loads the plugin, builds G-code)

w = gi.w
ok = True


def check(cond, what):
    global ok
    ok &= bool(cond)
    print("%s %s" % ("OK  " if cond else "FAIL", what))


def with_time_info(lines, total_min=10):
    """The synthetic G-code with M73 lines after every layer and the footer estimate."""
    out = []
    layers = [i for i, l in enumerate(lines) if l.startswith(";LAYER_CHANGE")]
    n = len(layers)
    for i, line in enumerate(lines):
        if line.startswith(";LAYER_CHANGE"):
            k = layers.index(i)
            out.append("M73 P%d R%d\n" % (round(100.0 * k / n), round(total_min * (1 - k / n))))
        out.append(line)
    out.append("M73 P100 R0\n")
    out.append("; total layers count = %d\n" % n)
    out.append("; estimated printing time (normal mode) = %dm 0s\n" % total_min)
    return out


def durations():
    check(w._parse_duration("30m 58s") == 1858 and w._parse_duration("1h 1m 9s") == 3669
          and w._parse_duration("1d 2h 3m 4s") == 93784, "durations parse in OrcaSlicer's form")
    check(w._fmt_duration(1858) == "30m 58s" and w._fmt_duration(3612) == "1h 0m 12s"
          and w._fmt_duration(93784) == "1d 2h 3m 4s" and w._fmt_duration(42) == "42s",
          "and format the same way")


def synthetic():
    base = with_time_info(gi.build("klipper", gi.INSTANCES))
    cfg = dict(gi.CFG, print_speed=0.1, adjust_time=True)        # 26 mm of waves at 0.1 mm/s (the slowest the plugin counts): ~4.3 min
    out, _ = w._post_process(list(base), gi.captures(), (True, True), cfg)
    blocks = [float(re.search(r" min=([0-9.]+)", l).group(1)) for l in out if l.startswith(w._TAG + "_START")]
    waves_min = sum(blocks)
    check(len(blocks) == 3 and 4.0 < waves_min < 5.0, "3 wave blocks carry their time: %.2f min" % waves_min)
    m73 = [(int(m.group(1)), int(m.group(2))) for m in (re.match(r"M73 P(\d+) R(\d+)", l) for l in out) if m]
    check(m73[0][1] == round(10 + waves_min), "the first M73 line says %d min remain (10 + %.1f)" % (m73[0][1], waves_min))
    check(m73[-1] == (100, 0), "the last M73 line is still P100 R0")
    check(all(a[1] >= b[1] for a, b in zip(m73, m73[1:])), "the remaining time never goes up")
    check(all(a[0] <= b[0] for a, b in zip(m73, m73[1:])), "the percentage never goes down")
    # M73 lines on either side of the blocks: remaining time drops by the block's time
    pos = [i for i, l in enumerate(out) if l.startswith(w._TAG + "_START")]
    first_block = pos[0]
    before = [re.match(r"M73 P(\d+) R(\d+)", l) for l in out[:first_block]]
    after = [re.match(r"M73 P(\d+) R(\d+)", l) for l in out[first_block:]]
    r_before = [int(m.group(2)) for m in before if m][-1]
    r_after = [int(m.group(2)) for m in after if m][0]
    check(r_before - r_after >= 2, "the M73 lines before and after a block differ by its time (%d -> %d)" % (r_before, r_after))
    est = next(l for l in out if l.startswith("; estimated printing time"))
    sec = w._parse_duration(est.split("=")[1])
    check(abs(sec - (600 + waves_min * 60)) < 2, "the estimate line is slicer + waves: %s" % est.strip())
    check(w._EXPORT_TIME and w._EXPORT_TIME["slicer_s"] == 600 and abs(w._EXPORT_TIME["waves_s"] - waves_min * 60) < 2,
          "the preview gets the slicer's 600 s and the waves' %.0f s: %s" % (waves_min * 60, w._EXPORT_TIME))
    report = [l for l in out[:12] if l.startswith(w._TAG + " print time")]
    check(report and "slicer 10m 0s" in report[0], "the report says what was changed: %s" % (report[0].strip() if report else None))
    plain, _ = w._post_process(list(base), gi.captures(), (True, True), dict(cfg, adjust_time=False))
    check([l for l in plain if l.startswith("M73")] == [l for l in base if l.startswith("M73")]
          and any("10m 0s" in l for l in plain if l.startswith("; estimated printing")),
          "with the setting off the slicer's numbers are left alone")
    check(w._EXPORT_TIME and w._EXPORT_TIME["slicer_s"] == 600, "the preview still gets the times")
    none, _ = w._post_process(list(base), {}, (True, True), dict(cfg))
    check(w._EXPORT_TIME is None and [l for l in none if l.startswith("M73")] == [l for l in base if l.startswith("M73")],
          "an export with no waves changes nothing and leaves no stale time")
    again, _ = w._post_process(list(out), gi.captures(), (True, True), dict(cfg))
    check(again == out, "a file that already has its waves is left untouched, so its time is not added twice")


def real_files():
    folder = os.environ.get("WAVE_TEST_GCODE_DIR")      # optional: a folder of real exports
    files = sorted(glob.glob(os.path.join(folder, "*.gcode"))) if folder else []
    parsed = 0
    for path in files:
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
            lines = f.readlines()
        est = next((l for l in reversed(lines) if l.startswith("; estimated printing time (normal mode)")), None)
        first = next((l for l in lines if l.startswith("M73 P0 R")), None)
        if not est or not first:
            continue
        secs = w._slicer_seconds(lines)
        parsed += 1
        if abs(secs - int(first.split("R")[1]) * 60) > 90:
            check(False, "%s: estimate %d s but first M73 says R%s" % (os.path.basename(path), secs, first.split("R")[1]))
            return
        if w._fmt_duration(secs) != est.split("=")[1].strip():
            check(False, "%s: %r does not round-trip" % (os.path.basename(path), est))
            return
    check(parsed == 0 or True, "%d real file(s): the estimate parses, matches the first M73 line and round-trips" % parsed)


def main():
    durations()
    synthetic()
    real_files()
    print("all OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
