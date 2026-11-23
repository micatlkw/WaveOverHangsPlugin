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

w = gi.w
orca = w.orca

# --- fake host bits -----------------------------------------------------------------------
orca.LifecycleEvent = types.SimpleNamespace(SlicingJobComplete="SlicingJobComplete", SliceStarted="SliceStarted")
orca.LifecycleEvtCode = types.SimpleNamespace(Ok="Ok", Warn="Warn", Error="Error")
shown = []
orca.host = types.SimpleNamespace(ui=types.SimpleNamespace(
    NotificationLevel=types.SimpleNamespace(ImportantNotificationLevel="Important",
                                            WarningNotificationLevel="Warning"),
    push_notification=lambda level, text: shown.append((level, text))))
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
    check(len(notes) == 1 and notes[0][1] == "info" and "of printing" in notes[0][0], "summary note: %s" % notes)
    notes = []
    w._post_process(gi.build(None, gi.INSTANCES), gi.captures(), (True, True), dict(gi.CFG), notes)
    check(any(level == "warning" and "Exclude objects" in text for text, level in notes),
          "warning when several objects have no labels")

    # showing them
    w._take_notes()
    shown.clear()
    w._note("Wave Overhangs, part2: waves on 3 layer(s), about 4 min", "slice")
    cap.on_lifecycle_event("SlicingJobComplete", types.SimpleNamespace(code="Ok"))
    check(len(shown) == 1 and shown[0][0] == "Important" and "part2" in shown[0][1]
          and "preview doesn't show them" in shown[0][1], "slice result shown once, with the preview hint")
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
