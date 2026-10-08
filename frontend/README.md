# frontend

A native [Dear ImGui](https://github.com/ocornut/imgui) (via
[imgui_bundle](https://github.com/pthom/imgui_bundle)) desktop app for
`backend`: browse and edit the config library, run a single config
at a given RC separation distance, and see its self-inductance, mutual
inductance, and ESR - one local process, no browser, no HTTP.

Replaced the old FastAPI web app (`legacy/webapp/`, removed from the tree on
2026-09-18 - still in git history at commit `321b8d7`). See `docs/GUI.md` for what was built and its known
concerns.

## Prerequisites

- Python >=3.13 and [`uv`](https://docs.astral.sh/uv/)
- Linux, with a display (the app opens a real window - no headless mode).
  `backend`'s solve backend (`engine/vendor/xfemm-4.0-linux64/femmcli`) is
  a Linux binary, so runs will fail cleanly (not crash) on macOS/Windows.

This package is a member of the repo's uv workspace (see the root
`pyproject.toml`), alongside `backend`, so dependencies are
resolved together and share one `uv.lock`. Run all commands from the repo
root.

## Run it

```bash
uv sync
uv run femm
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
  `docs/GUI.md`'s Concerns for the two real accidental deletions that led
  to that popup existing).
- **Right, top: Coil Geometry** - two live views of the selected config,
  redrawn every frame as you edit the form or change the Run distance: a
  top-down view (loop/tank/RC sized by radius) on the left, and an axial
  cross-section on the right showing how they're actually arranged in
  space - Loop below Tank on the transmit side, RC drawn `distance` mm
  above them facing down, with ferrite/shield/backplane stacked further out
  on each side when present. Proportional, not a literal turn-by-turn
  render (see `docs/GUI.md` for the scope) - **Open in FEMM** on its title
  row draws the real thing (see below).
- **Right, middle: Run** - pick a config and an RC separation distance
  (mm), then **Run**. If that exact geometry + distance was already solved
  before, the existing result is shown instantly instead of re-running
  femmcli, and that result's entry rises back to the top of Run History.
  Solves happen on a background thread pool (`os.cpu_count()` workers) so
  the window stays responsive; you can submit more than one run before the
  first finishes.
- **Right, bottom: Run History** - every past run plus anything still
  solving, most-recently-used first. Select one to see its result
  (`L_loop/L_tank/L_rc`, `M12/M13/M23`, 3 ESRs, `k12/k13/k23`) or its error,
  and **Open in FEMM** to see the geometry behind those numbers.

Configs and runs are both stored in one SQLite database,
`backend/data/femm_automation.db` (see
`backend/engine/repositories/db.py`) - not JSON files. The database file isn't
checked into git (it's binary, not diff-friendly), so a fresh checkout starts
without one; on launch, if there are no configs, the app loads the 13 reference
designs from `backend/data/starter_configs/*.json`
(`coilset_repository.seed_starter_configs()`). Run history starts empty.

To move your data to another machine, use
**Backup All / Restore All** in the top toolbar - it dumps or restores
every config + every run as one portable `.json` file. Export/Import (the
Config Library's buttons, and Run History's) work the same way for a single
config or run at a time - a literal copy of that one database row as
`.json`, read back in on Import.

## Open in FEMM

Every panel that draws a geometry has an **Open in FEMM** button next to it:
the Coil Geometry panel, a selected run in Run History, the AI Agent's
Baseline box, each candidate card in the search feed, and both the
**Baseline** and **Proposed** halves of a finished search's summary (live
and replayed from AI Run History alike). Clicking one opens real FEMM 4.2
under Wine with that exact design drawn in it, loop circuit energised,
meshed and solved by nobody - ready for you to mesh, solve, probe, or edit
by hand.

The model is not re-drawn for the occasion. `engine/services/femm_launcher.py`
calls `engine.core.build_geometry()` - the same function `engine.core.build_script()`
calls before adding the three solves - so the model that opens is the model
that produced the numbers beside the button, not a second drawing of it that
could drift from it. All the button adds is the loop-driven currents (the
solver's first of three solves), an `mi_saveas()`, and an
`mi_zoomnatural()`.

Requirements: Wine, and FEMM 4.2 installed under it - see
`docs/Engine.md`. Without either, the button says so instead of
failing silently. `FEMM_EXE` overrides where `femm.exe` is looked for
(default `~/.wine/drive_c/femm42/bin/femm.exe`).

Each button writes its `.fem` and the Lua that drew it under
`backend/data/femm_models/<design>/` (git-ignored), overwritten on
every click of that same button. FEMM keeps running after the app is
closed, and a solve done by hand in it drops a multi-MB `.ans` there too.

## Layout

```
frontend/src/frontend/
  main.py          starts the app, lays out both modes
  _sim_path.py     makes engine/ and agent/ importable
  screen_manual/   Manual Runs mode: configs_panel, runs_panel
  screen_agent/    AI Agent mode: agent_sidebar (saved chats), agent_panel (chat + live search), agent_candidate_card
  screen_settings/ Settings screen (opened from AI Agent): settings_panel (edit the agent's system prompts and numbers)
  visualization/   drawing pieces both modes share: coil_diagram, femm_open, result_table
  services/        logic with no drawing: job_runner (solve queue + cache), field_layout
  files_backup/    import / export: config_io, run_io, backup_io
```

`screen_agent/` and `screen_manual/` never import each other; both use `visualization/`, `services/` and `files_backup/`.
`screen_agent/agent_panel.py` runs the agent; `screen_settings/settings_panel.py` edits its settings
(`agent.settings`) - the only two files that talk to the `agent/` package.

## Config

`SIM_DIR` env var overrides where the app looks for `backend/`
(default: the `backend/` directory at the repo root) - see
`src/frontend/_sim_path.py`.

## Tests

```bash
uv run --package frontend --group test pytest frontend/tests/
```

All 54 tests run against real `CoilSet`s and real `engine.core.simulate()`
calls (no mocks) - expect this to take ~30-40s. Every test gets its own
isolated, temporary, empty `femm_automation.db` (see `tests/conftest.py`),
pre-loaded with one real `"base_coil"` config (inserted directly in Python,
not read from a file) so tests that reference it by id and actually solve
it still work - nothing here touches the real database.
