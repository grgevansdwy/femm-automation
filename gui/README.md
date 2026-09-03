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

A window titled "FEMM Coil Automation" opens with 3 tabs:

- **Config Library** - select a config on the left to edit it (53 fields
  grouped into collapsible sections), or **+ New** to start blank. Save
  validates geometry the same way the webapp did. **Delete removes any
  config, including built-ins, permanently and with no confirmation
  prompt** - there's no undo and no recycle bin, so double-check the
  selection before clicking it (see `docs/gui.md`'s Concerns for why this is
  called out this prominently).
- **New Run** - pick a config and an RC separation distance (mm), then
  **Run**. If that exact geometry + distance was already solved before, the
  existing result is shown instantly instead of re-running femmcli. Solves
  happen on a background thread pool (`os.cpu_count()` workers) so the
  window stays responsive; you can submit more than one run before the
  first finishes.
- **Run History** - every past run plus anything still solving, newest
  first. Select one to see its result (`L_loop/L_tank/L_rc`, `M12/M13/M23`,
  3 ESRs, `k12/k13/k23`) or its error.

Configs are written to `coil_simulation/data/configs/*.json`. Run output
lands as `coil_simulation/data/results/<run_id>.json` - the same schema
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

All 16 tests run against real `CoilSet`s and real `engine.simulate()`
calls (no mocks) - expect this to take ~20s and to write a handful of real
result files into `coil_simulation/data/results/`.
