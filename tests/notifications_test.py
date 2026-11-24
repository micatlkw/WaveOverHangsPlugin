"""Test of the slice notification, the per-object support toggle and the log, outside OrcaSlicer.

    python3 tests/notifications_test.py

Needs pyclipper and numpy. Fakes the bits of the orca host these use (lifecycle events,
orca.host.ui.push_notification, PrintObject config) and checks:
  - an object with Orca's "Enable support" on is left alone (no waves, nothing cut) unless
    apply_to = "all", and the report says so;
  - the G-code step queues a summary, and a warning when objects can't be placed;
  - SlicingJobComplete shows the queued lines once, as a warning if any line is one;
    a cancelled job shows nothing, a failed one only its warnings, and stale lines are dropped;
  - log lines are written as JSON.
"""
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcode_instances_test as gi  # noqa: E402  (stubs orca, loads the plugin, builds G-code)
import preview_slice_test as ps  # noqa: E402  (synthetic captures with instance positions and outlines)

w = gi.w
orca = w.orca

# --- fake host bits -----------------------------------------------------------------------
orca.LifecycleEvent = types.SimpleNamespace(SlicingJobComplete="SlicingJobComplete", SliceStarted="SliceStarted")
orca.LifecycleEvtCode = types.SimpleNamespace(Ok="Ok", Warn="Warn", Error="Error")
shown = []
links = []
panels = []


class FakePanel:
    def __init__(self, **kw):
        self.kw, self.open, self.posted, self.shows = kw, True, [], 0

    def post(self, data):
        self.posted.append(data)

    def is_open(self):
        return self.open

    def show(self):
        self.shows += 1


def create_dock_panel(**kw):
    panels.append(FakePanel(**kw))
    return panels[-1]


messages = []


def push_notification(level, text, hyper="", on_click=None):
    shown.append((level, text))
    links.append((hyper, on_click))


orca.host = types.SimpleNamespace(ui=types.SimpleNamespace(
    NotificationLevel=types.SimpleNamespace(ImportantNotificationLevel="Important",
                                            WarningNotificationLevel="Warning"),
    push_notification=push_notification, create_dock_panel=create_dock_panel,
    message=lambda text, title="": messages.append((title, text))))
orca.slicing.Step = types.SimpleNamespace(posSlice="posSlice", posPerimeters="posPerimeters",
                                          psGCodePostProcess="psGCodePostProcess")
orca.ExecutionResult = types.SimpleNamespace(success=lambda msg="": ("ok", msg))
LOG = os.path.join(tempfile.mkdtemp(), "log.jsonl")
w._LOG_NAME = LOG  # absolute: os.path.join keeps it


class FakeLayer:
    def __init__(self, z):
        self.print_z, self.height = z, 0.2

    def lslices(self):
        return []


class FakeObject:
    def __init__(self, oid, support):
        self._id, self.support = oid, support

    def id(self):
        return self._id

    def layers(self):
        return [FakeLayer(z) for z in gi.ZS]

    def config_value(self, key):
        return "1" if key == "enable_support" and self.support else None

    def model_object(self):
        return types.SimpleNamespace(name="part%d" % self._id, instances=lambda: [])


def slice_ctx(obj, others):
    return types.SimpleNamespace(step="posSlice", object=obj, print=types.SimpleNamespace(objects=lambda: others),
                                 cancelled=lambda: False, config_value=lambda key: None)


def capability(config):
    cap = w.WaveOverhangs()
    cap.get_config = lambda: json.dumps(config)
    return cap


def main():
    ok = True

    def check(cond, what):
        nonlocal ok
        ok &= bool(cond)
        print("%s %s" % ("OK  " if cond else "FAIL", what))

    # per-object support toggle
    w._STATE.clear()
    supported, plain = FakeObject(1, True), FakeObject(2, False)
    cap = capability({})
    res = cap.execute(slice_ctx(supported, [supported, plain]))
    capt = w._STATE[1]
    check(capt.get("skipped") == "Orca supports are on" and capt["cut_layers"] == 0
          and not any(e["paths"] for e in capt["layers"].values()),
          "object with supports on is left alone: %s" % res[1])
    cap.execute(slice_ctx(plain, [supported, plain]))
    check(not w._STATE[2].get("skipped"), "object without supports is processed")
    capability({"apply_to": "all"}).execute(slice_ctx(supported, [supported, plain]))
    check(not w._STATE[1].get("skipped"), "apply_to = all processes it too")
    report = []
    w._STATE[1]["skipped"] = "Orca supports are on"
    w._inject_all(gi.build("klipper", gi.INSTANCES), {1: w._STATE[1]}, dict(gi.CFG), report, [])
    check(any("Orca supports are on, left alone" in r for r in report), "report names the skipped object")

    # notes from the G-code step
    notes = []
    w._post_process(gi.build("klipper", gi.INSTANCES), gi.captures(), (True, True), dict(gi.CFG), notes)
    check(len(notes) == 1 and notes[0][1] == "info" and "waves added" in notes[0][0], "summary note: %s" % notes)
    notes = []
    w._post_process(gi.build(None, gi.INSTANCES), gi.captures(), (True, True), dict(gi.CFG), notes)
    check(any(level == "warning" and "Exclude objects" in text for text, level in notes),
          "warning when several objects have no labels")

    notes = []
    w._post_process(gi.build("klipper", gi.INSTANCES), {}, (False, False), dict(gi.CFG), notes)
    check(len(notes) == 1 and notes[0][1] == "warning" and "reused an earlier slice" in notes[0][0],
          "warning when the plugin captured nothing (OrcaSlicer reused a slice)")

    # showing them
    w._take_notes()
    shown.clear()
    w._note("Wave Overhangs, part2: waves on 3 layer(s), about 4 min", "slice")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(len(shown) == 1 and shown[0][0] == "Important" and "part2" in shown[0][1]
          and "\n" not in shown[0][1], "slice result shown once, as a single line")
    shown.clear()
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(not shown, "nothing shown twice")
    w._note("Wave Overhangs: waves added to the exported G-code, about 4 min of printing.", "info")
    w._note("Wave Overhangs: couldn't place X, so it got no waves.", "warning")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(len(shown) == 1 and shown[0][0] == "Warning" and "preview" not in shown[0][1],
          "a warning makes the notification a warning")
    shown.clear()
    w._note("slice line", "slice")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Warn"))
    check(not shown, "cancelled job shows nothing")
    w._note("slice line", "slice")
    w._note("a warning", "warning")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Error"))
    check(len(shown) == 1 and shown[0][1] == "a warning", "failed job shows only its warnings")
    shown.clear()
    with w._NOTES_LOCK:
        w._NOTES.append(("old line", "info", 0.0))
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(not shown, "stale lines are dropped")
    cap.on_lifecycle_event("SomethingElse", types.SimpleNamespace(code="Ok"))

    # preview panel
    exported, _ = w._post_process(gi.build("klipper", gi.INSTANCES), gi.captures(), (True, True), dict(gi.CFG))
    gctx = types.SimpleNamespace(output_name="/x/plate.gcode", gcode_path="/tmp/y.pp")
    w._take_notes()
    shown.clear(); links.clear(); panels.clear()
    w._PANEL = None
    cap = capability({})
    cap._store_preview(gctx, {"preview": 1}, exported, True, True)
    pv = w._PREVIEW
    check(pv is not None and pv["fresh"] and pv["title"] == "plate.gcode"
          and abs(pv["data"]["stats"]["wave_mm"] - 26.0) < 0.1, "export stores preview data (26 mm of waves)")
    w._note("Wave Overhangs: waves added to the exported G-code, under a minute of printing.", "info")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(len(shown) == 1 and links[-1][0] == "Show waves" and callable(links[-1][1]) and not panels
          and not w._PREVIEW["fresh"], "the notification offers Show waves; no panel opened by itself")
    links[-1][1]()
    check(len(panels) == 1 and panels[0].kw["html"] == w.PREVIEW_PAGE and panels[0].kw["dock"] == "right",
          "the link opens the preview panel, docked on the right")
    check(not panels[0].posted, "nothing is sent before the page asks")
    cap.panel.kw["on_message"]({"command": "preview_ready"})
    msg = panels[0].posted[-1]
    check(msg["command"] == "preview" and msg["title"] == "plate.gcode" and len(msg["layers"]) == len(gi.ZS)
          and msg["stats"]["wave_mm"] > 25, "preview_ready is answered with the layers and stats")
    cap.panel.kw["on_message"]({"command": "something else"})
    check(len(panels[0].posted) == 1, "other messages are ignored")
    cap._store_preview(gctx, {"preview": 1}, exported, True, True)     # a second export
    w._note("Wave Overhangs: waves added to the exported G-code, under a minute of printing.", "info")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(len(panels) == 1 and len(panels[0].posted) == 2 and panels[0].shows == 1,
          "an open panel is refreshed after the next export, not opened twice")
    links[-1][1]()
    check(len(panels) == 1 and len(panels[0].posted) == 3, "the link reuses the open panel")
    panels[0].open = False
    cap.panel.kw["on_close"]()
    links[-1][1]()
    check(len(panels) == 2, "after closing, the link opens a new panel")
    # the preview data is kept, replaced or dropped as the export says
    shown.clear(); links.clear()
    w._note("a note", "info")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Warn"))
    check(not shown, "a cancelled job shows nothing and offers no link")
    cap._store_preview(gctx, {"preview": 1}, exported, False, True)    # same file exported again
    check(w._PREVIEW is not None, "a file that already had its waves keeps the data")
    cap._store_preview(gctx, {"preview": 1}, gi.build("klipper", gi.INSTANCES), False, True)
    check(w._PREVIEW is None, "an export with no waves drops it")
    cap._store_preview(gctx, {"preview": 0}, exported, True, True)
    check(w._PREVIEW is None, "preview = 0 stores nothing")
    w._note("Wave Overhangs: waves added to the exported G-code, under a minute of printing.", "info")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(shown and links[-1][0] == "", "and the notification has no link")

    # the preview before any export, from the slice
    w._STATE.clear()
    w._STATE.update(ps.synthetic_captures())
    w._PREVIEW = None
    w._SLICE_DIRTY = True
    w._take_notes()
    shown.clear(); links.clear(); panels.clear()
    w._PANEL = None
    cap = capability({})
    w._note("Wave Overhangs, part: waves on 1 layer(s), under a minute", "slice")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(len(shown) == 1 and links[-1][0] == "Show waves" and w._PREVIEW["source"] == "slice"
          and not w._SLICE_DIRTY and not panels, "right after slicing, before any export, the link is offered")
    check("\n" not in shown[0][1] and len(shown[0][1]) + len("Show waves") + 1 <= 60,
          "the notification is one short line, so the link shows without expanding: %r" % shown[0][1])
    links[-1][1]()
    cap.panel.kw["on_message"]({"command": "preview_ready"})
    msg = panels[0].posted[-1]
    check(msg["title"].startswith("from the slice") and abs(msg["stats"]["wave_mm"] - 26.0) < 0.1
          and any(l["c"] for l in msg["layers"]), "the panel shows the waves and outlines from the slice")
    cap._store_preview(gctx, {"preview": 1}, exported, True, True)
    check(w._PREVIEW["source"] == "gcode" and not w._SLICE_DIRTY, "the export replaces the slice preview")
    w._note("Wave Overhangs: waves added to the exported G-code, under a minute of printing.", "info")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(len(panels) == 1 and panels[0].posted[-1]["title"] == "plate.gcode" and links[-1][0] == "Show waves",
          "the open panel is refreshed with the exported data, and the link is offered again")
    # Plugins dialog > Show Wave Preview: opens the panel again, also after it was closed
    action = w.ShowWavePreview()
    panels[0].open = False
    w._panel_closed()
    n = len(panels)
    result = action.execute()
    check(len(panels) == n + 1 and "opened" in result[1], "the Show Wave Preview action reopens the closed panel")
    action.execute()
    check(len(panels) == n + 1 and panels[-1].shows >= 1, "and brings the open one forward instead of opening another")
    w._PREVIEW = None
    action.execute()
    check(messages and "no wave preview yet" in messages[-1][1], "with nothing to show it says how to get something")
    w._PREVIEW = None
    w._SLICE_DIRTY = True
    shown.clear(); links.clear()
    w._note("a slice note", "slice")
    capability({"preview": 0}).on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(w._PREVIEW is None and links[-1][0] == "" and not w._SLICE_DIRTY, "preview = 0: nothing is built, no link")
    w._SLICE_DIRTY = True
    w._STATE.clear()
    w._note("a slice note", "slice")
    capability({}).on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(w._PREVIEW is None and not w._SLICE_DIRTY, "no captured waves: no preview")

    # log
    with open(LOG, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f]
    check(entries and all("version" in e and "time" in e for e in entries)
          and any(e.get("event") == "slice" and e.get("skipped") for e in entries),
          "log has %d JSON line(s), with the skipped object" % len(entries))

    print("all OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
