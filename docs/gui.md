# Dear ImGui desktop app: what was built, and what's concerning

**Status: working, verified against the real engine and real data — not yet
battle-tested by real day-to-day use.** `gui/` is a native Python +
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

`gui/` - one Python process, one window, a 3-tab layout mirroring
the webapp's 3 nav sections:

- **Config Library** - a config list on the left (no built-in/saved
  distinction shown - flat list, no tags); a form on the right, built
  entirely from `CoilSet`'s own field metadata (no hand-typed field list).
  Save / Clone / Delete, using the same `check_valid_coilset` validation the
  webapp uses - but **every config is deletable, including built-ins**, by
  explicit request. `coilset_repository.delete_config()` (which the webapp
  uses and which refuses built-ins) is deliberately *not* called here;
  delete goes straight to the file. No confirmation dialog either - see
  Concerns.
- **New Run** - pick a config + RC separation distance, submit. If an
  identical (coilset, distance) pair was already solved, the existing result
  is reused instead of re-running femmcli - see `coilset_signature()` in
  `job_runner.py`. Runs solve on a background thread pool so the UI never
  freezes.
- **Run History** - every past run (read from
  `coil_simulation/data/results/`) merged live with anything still solving,
  selectable to see its 12 result values or its error.

### Files

| File | What it does |
|---|---|
| `pyproject.toml` | Just `imgui-bundle` + `numpy` (the one third-party import the live xfemm solve path actually needs - checked directly, not assumed) |
| `src/gui/_sim_path.py` | Copy of `webapp`'s trick: puts `coil_simulation/` on `sys.path` so its bare `from repositories import ...` imports resolve |
| `src/gui/field_layout.py` | `FIELD_GROUPS` (6 sections, presentation-only) + `field_types()`/`field_labels()` read straight from `CoilSet`'s `dataclasses.field(metadata={'label': ...})` - a drift-guard raises loudly if `CoilSet` ever gains/loses/renames a field without this file being updated |
| `src/gui/job_runner.py` | `Job`/`JobStatus` + a `ThreadPoolExecutor(max_workers=os.cpu_count())` - **not** single-worker; matches the concurrency model `webapp/src/webapp/job_runner.py` was upgraded to once xfemm made concurrent solves safe. No asyncio - the GUI's per-frame loop just checks each job's status directly. Also owns `coilset_signature()`/`find_cached()` - the result-reuse cache, keyed on a sha256 of the exact coilset + distance |
| `src/gui/panels/configs_panel.py` | Config Library tab: list + grouped-form + Save/Clone/Delete, calling `coilset_repository`/`femm_service` directly. `delete()` bypasses `delete_config()`'s built-in guard on purpose (see above) |
| `src/gui/panels/runs_panel.py` | New Run + Run History tabs, sharing one `JobRunner`; `submit_run()` checks the cache before submitting a job |
| `src/gui/main.py` | Entry point - `immapp.run()` with a 3-tab `imgui.begin_tab_bar` |
| `tests/*.py` | 16 tests, all against real configs/real `engine.simulate()` calls, not mocks |

### Verification actually performed

- `engine.simulate()` called directly through `gui`'s own import
  path *before any GUI code existed* - returned a real `SimResult` in
  ~4.4s.
- 16 pytest tests, all exercising real code paths (real `CoilSet`s, a real
  `femmcli` solve, real file writes) rather than mocks: field-metadata
  drift guard, config load/save/clone/delete/validation-rejection, job
  submission → completion → persistence, two concurrent runs, cache-hit
  reuse.
- The app was actually launched (twice - once cold, once pre-loaded with a
  config selected) and screenshotted: confirmed the window, all 3 tabs, the
  16 real configs, and the full 7-section form (6 field groups + Advanced)
  all render.
- After moving `webapp/` → `legacy/webapp/`, its `_sim_path.py` needed a
  path-depth fix (`parents[3]` → `parents[4]`, since it's now nested one
  level deeper) - caught by actually booting it from the new location and
  hitting `/configs`, not by inspection.
- The original delete-crash (below) was reproduced live by monkeypatching
  `imgui.button` to force a real click from inside a real render call, on a
  disposable cloned config, then confirmed fixed the same way.

## Concerns

Roughly in order of how much they'd bite:

- **Delete is permanent, unrestricted, and has no confirmation - this
  already caused a real near-loss.** By explicit request, every config is
  deletable (including built-ins), straight to the file, no "are you sure?"
  There was also a real crash here (fixed, see below): clicking Delete while
  that config's form was on screen threw a `KeyError` mid-render, because
  the render code kept going past the button handler and tried to draw
  fields from a buffer `delete()` had just emptied. **While verifying the
  fix, a throwaway test script accidentally deleted the real `Gen3` built-in
  config** (not a webapp/CLI bug - a mistake in my own verification script,
  which called `delete()` on a real config while probing the crash). It was
  recovered from `git show 324d70a:coil_simulation/data/configs/Gen3.json`
  only because that config happened to be unchanged since that commit -
  **a config created or edited only in this app, then deleted, is gone with
  no recovery path.** Worth a confirmation popup
  (`imgui.open_popup`/`begin_popup_modal`) if accidental deletion is a real
  concern day to day.
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
  again, or `CoilSet` gains a field, `gui` breaks - loudly for a
  `CoilSet` field change (the drift-guard test), but as a plain
  `TypeError` for anything else. Nothing here is pinned to a commit.
- **The xfemm mutual-inductance fix lives only in the locally-patched
  vendored `femmcli` binary**, not upstream (`docs/xfemm.md`'s own
  concerns section already flags this). Not something `gui`
  introduced, but it inherits the exposure: if `coil_simulation/vendor/` is
  ever re-vendored from a fresh xfemm release without reapplying
  `xfemm_flux_linkage_fix.patch`, `M12/M13/M23/k12/k13/k23` go silently
  wrong again, in both UIs.
- **A brand-new config starts as all-zeros, not empty.** The web form
  starts with genuinely empty inputs and fails with "missing required
  field: X" if you submit incomplete; ImGui numeric widgets can't represent
  "empty", so `New` fills every field with `0`/`0.0`, which passes
  construction but reliably fails `check_valid_coilset` with a geometry
  message instead. Functionally equivalent (you can't save garbage either
  way) but the error text you'll see differs from the webapp's.
- **The result cache only recognizes runs solved since this feature
  landed.** `coilset_hash` is written into new result files going forward;
  the dozens of pre-existing result files in `coil_simulation/data/results/`
  don't have it, so re-running a config that was already solved once before
  today won't hit the cache the first time - it will after that, once its
  hash is on disk. Also: the cache key is the coilset's *content*, not its
  config id/name, so renaming a config (editing `config_name` and saving)
  changes the hash and is treated as a new geometry even though nothing
  physical changed.
- **Thread-safety of `Job` status is informal, not proven.** The worker
  thread mutates `Job.status`/`.result`; the render thread reads them every
  frame with no lock. This works today because of the GIL and because
  nothing does cross-thread read-modify-write, but it hasn't been
  stress-tested under, say, rapid-fire submission of many runs at once.
- **Linux-only as shipped.** `coil_simulation/vendor/xfemm-4.0-linux64/femmcli`
  is a Linux ELF binary - `gui` (like `webapp`) only actually solves
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
  did - `gui` just makes it faster to generate them (no page
  navigation between runs).
