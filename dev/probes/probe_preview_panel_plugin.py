# /// script
# requires-python = ">=3.12"
#
# [tool.orcaslicer.plugin]
# name = "Preview Panel Probe"
# description = "Test for the wave preview panel: can a slicing-pipeline plugin open a dock panel, how big a message can it send, does WebGL work."
# author = "WaveOverHangsPlugin"
# version = "0.0.1"
# type = "slicing-pipeline"
# ///
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Preview Panel Probe -- step 0 of the wave preview panel (see docs/development-notes.md).

Install it, pick it as the slicing pipeline plugin (Others > Slicing Pipeline Plugin), slice
anything. When slicing finishes it opens a docked panel on the right (from the lifecycle event, on
the UI thread) and shows a notification with a link that opens the panel again (from the
notification callback). The panel runs the checks below and shows the results as text; copy them
back. The plugin also appends them to preview_panel_probe_log.jsonl next to this file.

Checks, all done by the page:
  1. what the page is (user agent, WebGL 1/2, GPU name, whether window.orca is there);
  2. a ping to the plugin and back (round-trip time);
  3. the plugin sending messages of 10 thousand ... 2 million numbers (time, intact?);
  4. a WebGL draw of 100 thousand line segments (does it render, how long);
  5. whether a panel can be opened again by the notification link and after being closed.
"""
import json
import os
import time

import orca

PAGE = r"""
<style>
  body { margin: 0; padding: 10px; font: 13px sans-serif; color: var(--orca-fg, #222);
         background: var(--orca-bg, #fff); }
  h3 { margin: 0 0 6px; font-size: 14px; }
  button { margin: 0 6px 8px 0; }
  pre { white-space: pre-wrap; word-break: break-word; font-size: 12px; margin: 6px 0;
        padding: 6px; border: 1px solid var(--orca-border, #aaa); }
  .ok { color: #1a7f37; } .bad { color: #c62828; }
  canvas { display: none; }
</style>
<h3>Preview panel probe</h3>
<button type="button" id="run">Run again</button>
<button type="button" id="copy">Select results</button>
<pre id="out">starting...</pre>
<canvas id="gl" width="400" height="300"></canvas>
<script>
(function () {
  var results = {};
  var out = document.getElementById("out");
  var pending = {};

  function show() { out.textContent = JSON.stringify(results, null, 2); }
  function set(key, value) { results[key] = value; show(); }
  function send(data) { orca.postMessage(data); }

  orca.onMessage(function (msg) {
    if (!msg || !msg.command) return;
    if (msg.command === "pong") {
      set("ping_round_trip_ms", Math.round((performance.now() - msg.t) * 100) / 100);
    } else if (msg.command === "bulk" && pending[msg.id]) {
      var p = pending[msg.id];
      delete pending[msg.id];
      clearTimeout(p.timer);
      var sum = 0;
      for (var i = 0; i < msg.data.length; i++) sum += msg.data[i];
      set("bulk_" + msg.n, {
        ms: Math.round(performance.now() - p.t0),
        length_ok: msg.data.length === msg.n,
        checksum_ok: sum === msg.n * (msg.n - 1) / 2
      });
      next();
    } else if (msg.command === "info") {
      set("plugin", msg.info);
    }
  });

  var sizes = [10000, 100000, 500000, 2000000];
  var queue = [];
  function next() {
    if (!queue.length) { finish(); return; }
    var n = queue.shift(), id = "b" + n;
    var t0 = performance.now();
    var timer = setTimeout(function () {
      delete pending[id];
      set("bulk_" + n, "NO REPLY in 15 s");
      queue = [];            // bigger sizes will not work either
      finish();
    }, 15000);
    pending[id] = { t0: t0, timer: timer };
    send({ command: "bulk", n: n, id: id });
  }

  function glTest() {
    var c = document.getElementById("gl");
    var r = {};
    var gl2 = c.getContext("webgl2");
    var gl = gl2 || c.getContext("webgl");
    r.webgl2 = !!gl2;
    r.webgl = !!gl;
    if (!gl) { return r; }
    var ext = gl.getExtension("WEBGL_debug_renderer_info");
    r.gpu = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : "unknown";
    r.max_texture = gl.getParameter(gl.MAX_TEXTURE_SIZE);
    function shader(type, src) {
      var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s;
    }
    var prog = gl.createProgram();
    gl.attachShader(prog, shader(gl.VERTEX_SHADER, "attribute vec2 p; void main(){gl_Position=vec4(p,0.,1.);}"));
    gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, "precision mediump float; void main(){gl_FragColor=vec4(1.,0.,0.,1.);}"));
    gl.linkProgram(prog);
    r.shader_ok = !!gl.getProgramParameter(prog, gl.LINK_STATUS);
    var n = 100000, pts = new Float32Array(n * 4);
    for (var i = 0; i < pts.length; i++) pts[i] = Math.random() * 2 - 1;
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, pts, gl.STATIC_DRAW);
    gl.useProgram(prog);
    var loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.viewport(0, 0, 400, 300);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    var t0 = performance.now();
    gl.drawArrays(gl.LINES, 0, n * 2);
    var px = new Uint8Array(400 * 300 * 4);
    gl.readPixels(0, 0, 400, 300, gl.RGBA, gl.UNSIGNED_BYTE, px);   // forces the draw to finish
    r.draw_100k_lines_ms = Math.round(performance.now() - t0);
    var red = 0;
    for (var j = 0; j < px.length; j += 4) if (px[j] > 0) red++;
    r.draw_rendered_pixels = red;
    return r;
  }

  function finish() {
    results.finished = new Date().toISOString();
    show();
    send({ command: "report", results: results });
  }

  function run() {
    results = {};
    pending = {};
    results.window_orca = typeof window.orca === "object";
    results.user_agent = navigator.userAgent;
    results.pixel_ratio = window.devicePixelRatio;
    results.panel_size = window.innerWidth + "x" + window.innerHeight;
    try { results.webgl = glTest(); } catch (e) { results.webgl = "error: " + e; }
    show();
    send({ command: "ping_info" });
    send({ command: "ping", t: performance.now() });
    queue = sizes.slice();
    setTimeout(next, 300);
  }

  document.getElementById("run").addEventListener("click", run);
  document.getElementById("copy").addEventListener("click", function () {
    var r = document.createRange(); r.selectNodeContents(out);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  });
  run();
})();
</script>
"""

_LOG = "preview_panel_probe_log.jsonl"


def _log(entry):
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _LOG)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(entry, time=time.strftime("%Y-%m-%d %H:%M:%S")), default=str) + "\n")
    except Exception:
        pass


class PreviewPanelProbe(orca.slicing.SlicingPipelineCapabilityBase):
    panel = None
    opened = 0
    first_open_done = False

    def get_name(self):
        return "Preview Panel Probe"

    def get_default_config(self):
        return {}

    def execute(self, ctx):
        return orca.ExecutionResult.success()

    # --- panel -------------------------------------------------------------------------------
    def open_panel(self, how):
        """Open the panel, or show it again. Returns a text for the log."""
        try:
            if self.panel is not None and self.panel.is_open():
                self.panel.show()
                return "%s: panel already open, shown again" % how
            self.panel = orca.host.ui.create_dock_panel(
                html=PAGE, title="Preview panel probe", width=420, height=520,
                on_message=self.on_message, on_close=self.on_close, dock="right")
            self.opened += 1
            return "%s: panel opened (%d time(s) this session)" % (how, self.opened)
        except Exception as e:
            _log({"event": "open_failed", "how": how, "error": "%s: %s" % (type(e).__name__, e)})
            return "%s: FAILED %s: %s" % (how, type(e).__name__, e)

    def on_close(self):
        self.panel = None
        _log({"event": "panel_closed"})

    def on_message(self, message):
        command = (message or {}).get("command")
        try:
            if command == "ping":
                self.panel.post({"command": "pong", "t": message["t"]})
            elif command == "ping_info":
                self.panel.post({"command": "info", "info": {
                    "python": __import__("sys").version.split()[0],
                    "opened_count": self.opened}})
            elif command == "bulk":
                n = int(message["n"])
                self.panel.post({"command": "bulk", "n": n, "id": message["id"], "data": list(range(n))})
            elif command == "report":
                _log({"event": "report", "results": message.get("results")})
        except Exception as e:
            _log({"event": "message_failed", "command": command, "error": "%s: %s" % (type(e).__name__, e)})

    # --- lifecycle -----------------------------------------------------------------------------
    def on_lifecycle_event(self, event, ctx):
        ev = getattr(orca, "LifecycleEvent", None)
        if ev is None or event != ev.SlicingJobComplete:
            return
        code = getattr(orca, "LifecycleEvtCode", None)
        if code is not None and getattr(ctx, "code", None) != code.Ok:
            return
        text = "Preview panel probe: slicing finished."
        if not self.first_open_done:
            # first time: open from the lifecycle event itself (UI thread)
            self.first_open_done = True
            result = self.open_panel("lifecycle event")
            _log({"event": "lifecycle_open", "result": result})
            text += " " + result
        ui = orca.host.ui
        ui.push_notification(ui.NotificationLevel.ImportantNotificationLevel, text, "Open the probe panel",
                             self._link_clicked)

    def _link_clicked(self):
        result = self.open_panel("notification link")
        _log({"event": "link_open", "result": result})
        return True


@orca.plugin
class PreviewPanelProbePackage(orca.base):
    def register_capabilities(self):
        orca.register_capability(PreviewPanelProbe)
