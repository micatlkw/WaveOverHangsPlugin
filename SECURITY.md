# Security

## Reporting a vulnerability

Please report security problems **privately**, not in a public issue: use the repository's
**Security** tab, then **Report a vulnerability** (GitHub private vulnerability reporting). Say what
you found, how to reproduce it and which version it affects. You will get an answer as soon as the
maintainer can, and a fix is released before the details are published.

Only the latest release is supported.

## What this plugin does and does not do

Wave Overhangs is a single Python file that runs inside OrcaSlicer's embedded Python. A plugin has
broad access to the machine, so here is what this one does. You can check every line of it: the file
is `wave_overhangs_plugin.py`.

- It reads the slicing data OrcaSlicer hands to plugins, and changes the layer outlines.
- It reads the exported G-code file and writes it back with the waves added.
- It appends diagnostic lines to `wave_overhangs_log.jsonl`, next to the installed plugin file.
  The log holds settings, counts and file names; nothing is sent anywhere.
- It shows OrcaSlicer notifications and web pages (the preview and the settings) inside OrcaSlicer.
  The pages are written into the plugin file itself and load **nothing from the internet**: no
  scripts, styles, fonts or images from other sites.
- It makes **no network connections** (it does not import `socket`, `urllib`, `http` or `requests`),
  starts **no other programs** (no `subprocess` or `os.system`), and runs **no dynamic code** (no
  `eval`, `exec`, `pickle` or `ctypes`).
- The only files it opens are the exported G-code (read, then written back) and its own log.

## Dependencies

The file's header lists what OrcaSlicer installs from PyPI when the plugin is loaded:

| Package | Pinned as | Why |
|---|---|---|
| `pyclipper` | `==1.4.0` (exact) | polygon clipping and offsetting (the library the original code uses) |
| `numpy` | `>=2.0,<3` | array maths for outline simplification and the preview |

`pyclipper` is pinned to the exact version the plugin is tested with, so a newer release (or a
hijacked one) is not installed automatically. Updating it is a deliberate change in this repository,
tested before release.

## Checking what you install

- Take the plugin from a tagged release of this repository, not from a copy someone sent you.
- Each release lists the **SHA-256** of `wave_overhangs_plugin.py`. Compare it with yours
  (`sha256sum wave_overhangs_plugin.py` on Linux/macOS, `certutil -hashfile wave_overhangs_plugin.py SHA256`
  on Windows).
- Read it if you like: it is one file with no obfuscation, and the dependency header is the first
  thing in it.
- OrcaSlicer has its own plugin permission system. If it shows a permission request for this plugin,
  read it: the plugin needs nothing beyond reading and writing the G-code file and its log.
