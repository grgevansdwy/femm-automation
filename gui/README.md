# gui

A native [Dear ImGui](https://github.com/ocornut/imgui) (via
[imgui_bundle](https://github.com/pthom/imgui_bundle)) desktop app for
`coil_simulation`: browse and edit the config library, run a single config
at a given RC separation distance, and see its self-inductance, mutual
inductance, and ESR - one local process, no browser, no HTTP.

Replaces `legacy/webapp/` (kept for reference/rollback, not the primary UI
going forward). See `docs/gui.md` for what was built and its known
concerns.

## Prerequisites

- Python >=3.13 and [`uv`](https://docs.astral.sh/uv/)
- Linux, with a display (the app opens a real window - no headless mode).
  `coil_simulation`'s solve backend (`vendor/xfemm-4.0-linux64/femmcli`) is
  a Linux binary, so runs will fail cleanly (not crash) on macOS/Windows.

This package is a member of the repo's uv workspace (see the root
`pyproject.toml`), alongside `coil_simulation`, so dependencies are
resolved together and share one `uv.lock`. Run all commands from the repo
root.

## Run it

```bash
uv sync
uv run --package gui python gui/src/gui/main.py
```

A window titled "FEMM Coil Automation" opens as one fixed dashboard - no
tabs, everything visible at once:

- **Left: config list** - **+ New** to start blank, **Import** to load a
  config from a `.json` file (see Export below), or select one to edit it.
- **Center: the selected config's form** - 53 fields grouped into
  collapsible sections. **Save** validates geometry the same way the webapp
  did, and rejects a name that collides with another config. **Clone**
  duplicates it as `"<name> (copy)"`. **Export** writes the selected
  config to a `.json` file you pick (native save dialog) - the easiest way
  to hand a config to someone else; they use **Import** on their end to
  load it (a name collision auto-suffixes as `"<name> (imported)"`, never
  silently overwriting). **Delete removes any config, including
  built-ins, permanently** - a confirmation popup stands between the button
  and the actual deletion, but there's still no undo once confirmed (see
  `docs/gui.md`'s Concerns for the two real accidental deletions that led
  to that popup existing).
- **Right, top: Coil Geometry** - a live schematic of the selected config's
  loop/tank/RC coils (and ferrite/shield/backplane when present), redrawn
  every frame as you edit the form - proportional rings, not a literal
  turn-by-turn render (see `docs/gui.md` for the v1 scope).
- **Right, middle: Run** - pick a config and an RC separation distance
  (mm), then **Run**. If that exact geometry + distance was already solved
  before, the existing result is shown instantly instead of re-running
  femmcli, and that result's entry rises back to the top of Run History.
  Solves happen on a background thread pool (`os.cpu_count()` workers) so
  the window stays responsive; you can submit more than one run before the
  first finishes.
- **Right, bottom: Run History** - every past run plus anything still
  solving, most-recently-used first. Select one to see its result
  (`L_loop/L_tank/L_rc`, `M12/M13/M23`, 3 ESRs, `k12/k13/k23`) or its error.

Configs are written to `coil_simulation/data/configs/*.json`; an exported
`.json` file is that same record shape, so any `.json` this app exported is
also what it expects back on Import. Run output lands as
`coil_simulation/data/results/<run_id>.json` - the same schema
`legacy/webapp` and `coil_simulation/main.py` write, so results are shared
across all three.

## Config

`SIM_DIR` env var overrides where the app looks for `coil_simulation/`
(default: the `coil_simulation/` directory at the repo root) - see
`src/gui/_sim_path.py`.

## Tests

```bash
uv run --package gui --group test pytest gui/tests/
```

All 38 tests run against real `CoilSet`s and real `engine.simulate()`
calls (no mocks) - expect this to take ~30-40s. Test runs are isolated from
the real `coil_simulation/data/results/` (see `tests/conftest.py`), but not
from `coil_simulation/data/configs/` - config-touching tests clean up
whatever they create instead.
