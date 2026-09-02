# Dear ImGui desktop app: what was built, and what's concerning

**Status: working, verified against the real engine and real data — not yet
battle-tested by real day-to-day use.** `desktop_app/` is a native Python +
[imgui_bundle](https://github.com/pthom/imgui_bundle) replacement for
`webapp/` (now at `legacy/webapp/`), built on top of `coil_simulation`
without changing a line of it.

## Background

The web app (FastAPI + Jinja2 + htmx, talking to `coil_simulation` over
HTTP) was harder to debug than it needed to be: two processes, a browser
devtools/Python debugger split, HTTP round trips for state that's really
just local function calls. `coil_simulation` was already refactored
(`docs/REFACTOR_NOTES.md`) into `models/`/`repositories/`/`services/`
specifically so a UI could sit on top of it without owning any business
logic - so this migration only replaces the view layer. Nothing in
`coil_simulation/` was touched.

Explicit build order (per instruction): wire the new app to the real engine
and prove it works *before* touching `webapp/` at all, and never delete
`webapp/` - relocate it once the new app is proven.

## What was built

`desktop_app/` - one Python process, one window, a 3-tab layout mirroring
the webapp's 3 nav sections:

- **Config Library** - a config list (built-in + saved) on the left; a form
  on the right, built entirely from `CoilSet`'s own field metadata (no
  hand-typed field list). Save / Clone / Delete, with the same
  built-ins-can't-be-deleted rule and the same `check_valid_coilset`
  validation the webapp uses.
- **New Run** - pick a config + RC separation distance, submit. Runs solve
  on a background thread pool so the UI never freezes.
- **Run History** - every past run (read from
  `coil_simulation/data/results/`) merged live with anything still solving,
  selectable to see its 12 result values or its error.

### Files

| File | What it does |
|---|---|
| `pyproject.toml` | Just `imgui-bundle` + `numpy` (the one third-party import the live xfemm solve path actually needs - checked directly, not assumed) |
| `src/desktop_app/_sim_path.py` | Copy of `webapp`'s trick: puts `coil_simulation/` on `sys.path` so its bare `from repositories import ...` imports resolve |
| `src/desktop_app/field_layout.py` | `FIELD_GROUPS` (6 sections, presentation-only) + `field_types()`/`field_labels()` read straight from `CoilSet`'s `dataclasses.field(metadata={'label': ...})` - a drift-guard raises loudly if `CoilSet` ever gains/loses/renames a field without this file being updated |
| `src/desktop_app/job_runner.py` | `Job`/`JobStatus` + a `ThreadPoolExecutor(max_workers=os.cpu_count())` - **not** single-worker; matches the concurrency model `webapp/src/webapp/job_runner.py` was upgraded to once xfemm made concurrent solves safe. No asyncio - the GUI's per-frame loop just checks each job's status directly |
| `src/desktop_app/panels/configs_panel.py` | Config Library tab: list + grouped-form + Save/Clone/Delete, calling `coilset_repository`/`femm_service` directly |
| `src/desktop_app/panels/runs_panel.py` | New Run + Run History tabs, sharing one `JobRunner` |
| `src/desktop_app/main.py` | Entry point - `immapp.run()` with a 3-tab `imgui.begin_tab_bar` |
| `tests/*.py` | 13 tests, all against real configs/real `engine.simulate()` calls, not mocks |

### Verification actually performed

- `engine.simulate()` called directly through `desktop_app`'s own import
  path *before any GUI code existed* - returned a real `SimResult` in
  ~4.4s.
- 13 pytest tests, all exercising real code paths (real `CoilSet`s, a real
  `femmcli` solve, real file writes) rather than mocks: field-metadata
  drift guard, config load/save/clone/delete/validation-rejection, job
  submission → completion → persistence, two concurrent runs.
- The app was actually launched (twice - once cold, once pre-loaded with a
  config selected) and screenshotted: confirmed the window, all 3 tabs, the
  16 real configs with correct Built-in tags, and the full 7-section form
  (6 field groups + Advanced) all render.
- After moving `webapp/` → `legacy/webapp/`, its `_sim_path.py` needed a
  path-depth fix (`parents[3]` → `parents[4]`, since it's now nested one
  level deeper) - caught by actually booting it from the new location and
  hitting `/configs`, not by inspection.

## Concerns

Roughly in order of how much they'd bite:

- **No automated test ever clicked a real button.** The 13 tests call
  `ConfigsPanelState`/`RunsPanelState` methods directly (`state.save()`,
  `state.submit_run()`, ...) - the same methods the GUI's `if
  imgui.button(...):` blocks call, but the click→handler wiring itself was
  only checked by reading the code and by screenshots of static frames, not
  by an automated click. There's no `xdotool` (or equivalent) in this
  environment to drive the native window, so a typo in a button's `if`
  block wouldn't be caught by anything automated right now.
- **`coil_simulation` is being actively edited outside this session, right
  now, uncommitted.** This is the biggest real risk: the xfemm/femmcli
  backend migration (`docs/xfemm.md`) is not committed, and more changes
  landed on disk *during this session* between planning and building. If
  `engine.simulate()`'s or `check_valid_coilset()`'s signature changes
  again, or `CoilSet` gains a field, `desktop_app` breaks - loudly for a
  `CoilSet` field change (the drift-guard test), but as a plain
  `TypeError` for anything else. Nothing here is pinned to a commit.
- **The xfemm mutual-inductance fix lives only in the locally-patched
  vendored `femmcli` binary**, not upstream (`docs/xfemm.md`'s own
  concerns section already flags this). Not something `desktop_app`
  introduced, but it inherits the exposure: if `coil_simulation/vendor/` is
  ever re-vendored from a fresh xfemm release without reapplying
  `xfemm_flux_linkage_fix.patch`, `M12/M13/M23/k12/k13/k23` go silently
  wrong again, in both UIs.
- **No delete confirmation.** The webapp's Delete button has a JS
  `confirm()` before submitting; `desktop_app`'s Delete acts immediately on
  click. Deliberately left out for v1 simplicity, but it's a real
  regression for an accidental double-click - worth a one-line
  `imgui.open_popup`/`begin_popup_modal` fix if you want it back.
- **A brand-new config starts as all-zeros, not empty.** The web form
  starts with genuinely empty inputs and fails with "missing required
  field: X" if you submit incomplete; ImGui numeric widgets can't represent
  "empty", so `New` fills every field with `0`/`0.0`, which passes
  construction but reliably fails `check_valid_coilset` with a geometry
  message instead. Functionally equivalent (you can't save garbage either
  way) but the error text you'll see differs from the webapp's.
- **Thread-safety of `Job` status is informal, not proven.** The worker
  thread mutates `Job.status`/`.result`; the render thread reads them every
  frame with no lock. This works today because of the GIL and because
  nothing does cross-thread read-modify-write, but it hasn't been
  stress-tested under, say, rapid-fire submission of many runs at once.
- **Linux-only as shipped.** `coil_simulation/vendor/xfemm-4.0-linux64/femmcli`
  is a Linux ELF binary - `desktop_app` (like `webapp`) only actually solves
  on Linux; elsewhere every run will fail cleanly rather than crash, but
  won't produce results.
- **No packaging.** Runs via `uv run` from this exact repo checkout only -
  no installer, no frozen executable. Wasn't asked for; flagging so it's a
  deliberate choice, not an oversight.
- **The Stitch mockup requested during planning never actually finished
  generating** (polled it ~8 times over several minutes; it never returned
  a screen). The Config Library layout you're using was validated by
  running the real app and looking at it, not by that mockup - the Stitch
  project (`3814206053826164709`) may have finished asynchronously since;
  worth checking directly if you want it, but nothing here depended on it
  landing.
- **Result files accumulate with no pruning**, same as the webapp always
  did - `desktop_app` just makes it faster to generate them (no page
  navigation between runs).
