# Dear ImGui desktop app: what was built, and what's concerning

**Status: working, verified against the real engine and real data — not yet
battle-tested by real day-to-day use.** `frontend/` is a native Python +
[imgui_bundle](https://github.com/pthom/imgui_bundle) replacement for
`webapp/` (since removed from the tree - in git history at `legacy/webapp/`, commit `321b8d7`), built on top of `backend`
without changing a line of it.

## Background

The web app (FastAPI + Jinja2 + htmx, talking to `backend` over
HTTP) was harder to debug than it needed to be: two processes, a browser
devtools/Python debugger split, HTTP round trips for state that's really
just local function calls. `backend` was already refactored
(`docs/REFACTOR_NOTES.md`) into `models/`/`repositories/`/`services/` (today under `backend/engine/`)
specifically so a UI could sit on top of it without owning any business
logic - so this migration only replaces the view layer. Nothing in
`backend/` was touched.

The first version of `frontend/` shipped as a 3-tab layout (Config Library / New
Run / Run History) mirroring the old webapp's nav. A second pass replaced
that with **one fixed dashboard, no tabs**, added a **live coil-geometry
diagram**, and added **config export/import** - the tabs felt sparse and
required switching back and forth just to see a run's result next to the
config that produced it.

## What was built

One window, one fixed 3-column layout (not docking - a deliberately simpler,
non-rearrangeable arrangement):

- **Left column** - the config list, plus **+ New** and **Import** buttons.
  No built-in/saved distinction shown (flat list, no tags).
- **Center column** - the selected config's edit form, built entirely from
  `CoilSet`'s own field metadata (no hand-typed field list): Save / Clone /
  Export / Delete, `config_name` always visible, then the field groups as
  expanded-by-default sections. Name collisions are handled differently per
  action, on purpose: **Save** rejects a colliding name with an error (you
  typed it, so you pick another); **Clone** never fails - it stacks another
  " (copy)" until free (`X (copy)`, `X (copy) (copy)`, ...) and renames the
  `CoilSet`'s own `config_name` to match, so the list and the form agree;
  **Import** auto-suffixes `(imported)`/`(imported 2)`.
  The list's name and the form's `config_name` are one value: the config
  record's `name` is authoritative and `coilset_repository` forces
  `config_name` to match on every read and write (legacy templates had stored
  a different free-text label there, e.g. `Gen3_4mm_RC_ferrite` vs
  `4mm RC ferrite, FJ7`). `config_name` is also left out of the run-cache
  hash, so renaming a config doesn't invalidate its cached runs.
- **Right column**, stacked top to bottom:
  - **Coil Geometry** - a live schematic redrawn every frame straight from
    the form's current values: concentric rings for loop/tank/rc sized
    proportionally to their actual radius, a translucent disc for ferrite
    and a stroked ring for shield when their presence flag is on, a
    legend, and a compact "quick facts" line. v1 scope is a proportional
    schematic, not literal turn-by-turn winding paths - see Concerns.
  - **Run** - config dropdown, distance field, Run button. If an identical
    (coilset, distance) pair was already solved, the existing result is
    reused instead of re-running femmcli (`coilset_signature()`/
    `find_cached()` in `job_runner.py`); reusing a cached result also bumps
    its `last_used_at` (via `JobRunner.touch()`) so it rises back to the top
    of Run History, without pretending it was re-solved.
  - **Run History** - every past run merged live with anything still
    solving, sorted by `last_used_at`, selectable to see its 12 result
    values or its error.

Every config is deletable, including built-ins, by explicit request -
`coilset_repository.delete_config()`'s built-in guard is deliberately
bypassed. **Delete now requires confirmation**: a popup ("Delete '<name>'?
This can't be undone.") stands between the Delete button and the actual
file removal - see Concerns for why this was added.

### Files

| File | What it does |
|---|---|
| `pyproject.toml` | `imgui-bundle` + `numpy` (the one third-party import the live xfemm solve path needs) |
| `src/frontend/_sim_path.py` | Puts `backend/` on `sys.path` so `import engine` / `import agent` (e.g. `from engine.repositories import ...`) resolve |
| `src/frontend/services/field_layout.py` | `FIELD_GROUPS` (6 sections, presentation-only) + `field_types()`/`field_labels()` read from `CoilSet`'s own field metadata - a drift-guard raises loudly if `CoilSet` ever gains/loses/renames a field without this file being updated |
| `src/frontend/services/job_runner.py` | `Job`/`JobStatus` + a `ThreadPoolExecutor(max_workers=os.cpu_count())`. Owns `coilset_signature()`/`find_cached()` (the result-reuse cache) and `touch()` (bumps `last_used_at` on a cache hit, updating the in-memory `Job` too if still resident - otherwise the next merge in `history_rows()` clobbers the fresh disk write with the stale in-memory copy) |
| `src/frontend/files_backup/config_io.py` | Export/import: pure logic, no `imgui` import. `name_conflict()`/`dedupe_name()` (shared by Save/Clone/Import), `export_record()`, `parse_import()` (only accepts the exact round-trip shape `export_record()` writes, not a bare coilset dict - rejects anything else with a specific reason) |
| `src/frontend/visualization/coil_diagram.py` | `compute_layout()` (pure scaling/geometry, unit-tested) + `draw()` (the `ImDrawList` rendering) |
| `src/frontend/screen_manual/configs_panel.py` | `draw_list()`/`draw_form()` - the left/center columns. Save/Clone/Export/Import/Delete, the delete-confirmation popup, and the two `portable_file_dialogs` handles (export/import) polled once per frame |
| `src/frontend/screen_manual/runs_panel.py` | `draw_controls()`/`draw_history()` - the right column's run block and history list |
| `src/frontend/main.py` | Entry point - computes the 3 column widths from the available content region each frame, no tab bar |
| `tests/*.py` | 38 tests, all against real configs/real `engine.simulate()` calls, not mocks |

### Verification actually performed

- `engine.simulate()` called directly through `frontend`'s own import path before
  any GUI code existed - returned a real `SimResult`.
- 38 pytest tests, all exercising real code paths (real `CoilSet`s, a real
  `femmcli` solve where relevant, real file writes) rather than mocks:
  field-metadata drift guard, config load/save/clone/delete/import/export/
  validation-rejection, name-conflict rejection, job submission →
  completion → persistence, cache-hit reuse and `last_used_at` bumping, the
  diagram's scaling math against real and synthetic buffers.
- `frontend/tests/conftest.py`'s `isolate_results_dir` fixture keeps every test
  out of the real `backend/data/results/` - a test used to write
  real "failed" entries into the real Run History before this existed.
  There's no equivalent isolation for `backend/data/configs/`;
  config-touching tests clean up what they create instead.
- The dashboard, the diagram (against several sample buffers), the
  export/import file-dialog wiring, and the delete-confirmation popup were
  each verified live - the dialog and popup flows by monkeypatching
  `imgui.button` to force real clicks through the actual render call chain
  (calling the real underlying button first and only overriding its
  returned bool, so ImGui's own per-frame bookkeeping stays correct - the
  first version of that trick skipped the real call and corrupted internal
  state instead), not just by reading the code.
- This live verification found two real bugs before they shipped:
  `history_rows()` was clobbering a freshly-`touch()`-ed cache hit's
  `last_used_at` with the stale in-memory `Job`'s copy on the very next
  frame (fixed by having `touch()` update the in-memory `Job` too), and
  `imgui.begin_popup_modal()` returns a `(bool, Optional[bool])` tuple, not
  a plain bool - `if imgui.begin_popup_modal(...):` is Python-truthy on any
  non-empty tuple regardless of the real value, so the delete-confirmation
  popup's `end_popup()` was firing every frame even when never opened. Both
  are exactly the kind of thing that reading the code alone doesn't catch.

## Concerns

Roughly in order of how much they'd bite:

- **Screenshots on this machine capture the whole shared desktop, not just
  the app window.** `gnome-screenshot -w` grabs whichever window currently
  has *focus*, not necessarily a just-launched background process's window
  - twice during this round of work it instead captured the user's own
  browser (once showing an open email/Slack-adjacent session, once showing
  their Claude.ai account and chat history). Neither `xdotool` nor `wmctrl`
  is installed, so there's no reliable way to target a specific window by
  title first. Both screenshots were deleted immediately without acting on
  their content. **Anyone continuing GUI verification in this environment
  should prefer the monkeypatched-real-click-plus-printed-output technique
  above over screenshots**, or at minimum confirm window focus first.
- **Delete confirmation was added** (a `begin_popup_modal` between the
  Delete button and the actual file removal) after two real accidental
  deletions happened in earlier sessions: a built-in `Gen3` config (during
  this project's own crash-fix verification) and later the real `base_coil`
  config (during ordinary use), the latter only recoverable to a stale
  pre-rename git checkpoint - some edits since that commit were lost for
  good. The popup only prevents *future* accidents; it doesn't undo those.
- **The coil diagram is a proportional schematic, not a literal render.**
  Two views share the panel: the original top-down rings (sized by each
  coil's actual radius) and, added later, an axial cross-section - a direct
  port of `backend/engine/core.py`'s real stacking math, showing Loop
  below Tank on the transmit side and RC drawn `distance` mm above them,
  facing down, with ferrite/shield/backplane stacked further outward on
  each side. The cross-section was added specifically because the top-down
  view alone reads as "all three coils on top of each other" - it collapses
  the real axial arrangement onto one plane. Even with both views, turn
  count, trace width, winding path, and the sub-mm per-PCB-layer offset
  inside `draw_coil_traces()` are still not modeled - a reasonable v3 if
  more fidelity is wanted (see the plan file's Deferred section).
- **Import only accepts the exact shape `export_record()` writes**
  (round-trip only) - a hand-built or bare 56-field coilset dict is
  rejected with a specific reason, not silently guessed at. Every failure
  mode was probed directly (non-JSON, valid-JSON-but-not-a-config,
  missing/extra fields, wrong-typed values, impossible geometry, blank
  name): each reports a specific message and writes nothing. The
  wrong-typed-value case was a real crash before that probe - `CoilSet` is
  a plain dataclass and happily accepts `"loop_r": "sixty-five"`, which
  then raised a raw `TypeError` from inside `check_valid_coilset()` and
  would have taken the window down; `parse_import()` now type-checks values
  against `field_layout.field_types()`, not just field names.
- **`backend` is being actively edited outside this session,
  uncommitted, by other concurrent sessions in this same repo.** This
  remains the biggest structural risk: if `engine.simulate()`'s or
  `check_valid_coilset()`'s signature changes, or `CoilSet` gains a field,
  `frontend` breaks - loudly for a `CoilSet` field change (the drift-guard
  test), but as a plain `TypeError` for anything else. Nothing here is
  pinned to a commit.
- **The xfemm mutual-inductance fix lives only in the locally-patched
  vendored `femmcli` binary**, not upstream (`docs/xfemm.md`'s own
  concerns section already flags this). If `backend/engine/vendor/` is
  ever re-vendored from a fresh xfemm release without reapplying
  `xfemm_flux_linkage_fix.patch`, `M12/M13/M23/k12/k13/k23` go silently
  wrong again.
- **A brand-new config starts as all-zeros, not empty**, same as before -
  ImGui numeric widgets can't represent "empty", so `New` reliably fails
  `check_valid_coilset` with a geometry message instead of a "missing
  field" one. Functionally equivalent, different error text than the webapp.
- **The result cache only recognizes runs solved since that feature
  landed** - pre-existing result files lack `coilset_hash` and won't be
  recognized as cache hits the first time they'd otherwise match. The
  cache key is the coilset's *content*, not its config id/name, so
  renaming a config is treated as a new geometry.
- **Thread-safety of `Job` status is informal, not proven** - the worker
  thread mutates `Job.status`/`.result`/`.last_used_at`; the render thread
  reads them every frame with no lock. Works today via the GIL and the
  absence of cross-thread read-modify-write, not stress-tested under
  rapid-fire concurrent submissions.
- **Linux-only as shipped** - `femmcli` is a Linux ELF binary; elsewhere
  every run fails cleanly rather than crashing, but produces no results.
- **No packaging** - runs via `uv run` from this exact repo checkout only.
  Wasn't asked for; a deliberate choice, not an oversight.
- **The Stitch mockup this time actually completed** (project
  `3814206053826164709`, ~6 polls at 60s intervals) - two reference screens
  (a coil-diagram schematic and the full dashboard) were generated, viewed,
  and used to inform the ring styling, the run-history status-color choice,
  and the panel arrangement. The prior attempt from the original migration
  never finished; this one did.
- **Result files accumulate with no pruning**, unchanged from before.
