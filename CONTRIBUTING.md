# Contributing

Thanks for helping. Bug reports with a test print or an exported G-code are the most useful thing.

## Reporting a problem

Open an issue and include: the plugin version (the first lines of the exported G-code start with
`; WAVE_OVERHANGS version ...`), the OrcaSlicer version, your printer, what you expected and what
happened, and the report lines (`; WAVE_OVERHANGS ...`) from the top of the G-code. The log
`wave_overhangs_log.jsonl` next to the plugin file helps too. **Remove anything private first**; do not
post whole G-code files that carry your settings if you do not want them public.

For security problems, see [SECURITY.md](SECURITY.md), not a public issue.

## Changing the code

- The plugin is deliberately one file: `wave_overhangs_plugin.py`. `CLAUDE.md` describes how it is
  built, and `docs/development-notes.md` records how each part was checked in OrcaSlicer.
- Run the tests before and after a change (they need Python 3.12, `pyclipper==1.4.0`, `numpy`, and
  `matplotlib` plus `google-chrome` for the page tests):

  ```
  python3 -m venv .venv && .venv/bin/pip install pyclipper==1.4.0 numpy matplotlib
  for t in generator_smoke min_overhang_test outer_wall_test gcode_instances_test notifications_test \
           preview_slice_test preview_extract_test print_time_test preview_page_test config_page_test; do
    .venv/bin/python tests/$t.py | tail -1
  done
  ```

  Each should end with `all OK`. Set `WAVE_TEST_GCODE_DIR` to a folder of your own exported `.gcode`
  files to also run the checks against real files.
- A new setting needs an entry in `_DEFAULTS`, in `_CONFIG_FIELDS` (the settings page) and in the
  README table; `tests/config_page_test.py` fails if a setting has no control.
- Do not add dependencies, network access or anything that starts another program without a good
  reason and a note in `SECURITY.md`.
- Do not commit G-code files, logs, local paths or personal details.

## Licence

The project is under the **GNU AGPL-3.0-or-later** (see `LICENSE`), because it ports AGPL code. By
contributing you agree that your contribution is licensed the same way.
