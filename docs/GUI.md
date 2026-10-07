---
owns: frontend/**
last-verified: 2026-10-07
---

# GUI: the desktop app

**Owns:** `frontend/` — a native Python + [imgui_bundle](https://github.com/pthom/imgui_bundle)
desktop app. One process, one window. Calls into `backend/engine/` (directly) and
`backend/agent/` (only through two functions, see §3) and renders whatever they hand
back — it never reimplements engine or agent logic. For the doc index, see
`docs/README.md`; for what happens *behind* the AI Agent mode, see `Agent.md`.

---

## 1. What it replaced, and why

`frontend/` replaced an earlier FastAPI + Jinja2 + htmx web app (`legacy/webapp/` in git
history, removed 2026-09-18, commit `321b8d7`) that talked to the engine over HTTP. That
split was harder to debug than it needed to be — two processes, a browser-devtools/Python
split, HTTP round trips for what are really local function calls. The engine had already
been layered (`models/`/`repositories/`/`services/`) specifically so a UI could sit on top
without owning business logic, so this migration only replaced the view layer — nothing
in `backend/` changed for it.

The first version mirrored the old webapp's 3-tab nav (Config Library / New Run / Run
History). A second pass replaced that with **one fixed, non-tabbed dashboard** plus a
live coil-geometry diagram and config export/import — tabs felt sparse and required
switching back and forth to see a run's result next to the config that produced it.

## 2. Two modes, toggled by a toolbar button

| Mode | Layout | What it's for |
|---|---|---|
| **Manual Runs** | 3 columns: config list · edit form · diagram + run controls + history | Pick/edit one design, run one solve at one distance, no AI involved |
| **AI Agent** | 2 columns: chat + progress · baseline picker + diagram/history + progress feed | Describe a goal in chat, watch a search run, browse past searches |

`_gui()` (in `main.py`) calls `poll()` on the job runner and the agent panel every frame,
then draws whichever mode is active — nothing here blocks on I/O; all of that happens on
background threads the panels poll.

## 3. The one boundary that matters: `frontend/` ↔ `backend/agent/`

```python
from agent.input_chat.services.runner import InterviewRunner
from agent.femm_agent.services.runner import AgentRunner
```

These two imports, in `screen_agent/agent_panel.py`, are the **entire** surface area
between this package and the AI agent. Everything on this side of the line is rendering
and clicking; everything past it is the real agent, which has no idea a GUI exists (see
`Agent.md`). If you're debugging "the chat isn't updating" or "the search seems stuck,"
the bug is almost certainly on the agent side, not here — this package only polls and
draws state it's handed.

## 4. Manual Runs mode

- **Left column** (`screen_manual/configs_panel.py::draw_list`) — the config list, **+
  New** and **Import** buttons. Flat list, no built-in/saved tagging shown.
- **Center column** (`configs_panel.py::draw_form`) — the selected config's edit form,
  built entirely from `CoilSet`'s own field metadata (`services/field_layout.py`) — no
  hand-typed field list, so a field the engine gains/loses/renames without a matching
  `field_layout.py` update fails loudly via a drift-guard test, not silently.
  - **Save** rejects a colliding name (you typed it — pick another).
  - **Clone** never fails: auto-suffixes `(copy)`, `(copy) (copy)`, ... and renames the
    cloned `CoilSet`'s own `config_name` to match, so the list and the form always agree.
  - **Import** auto-suffixes `(imported)`, `(imported 2)`, ...
  - `config_name` is forced to match the config record's `name` on every read/write, and
    is deliberately excluded from the run-cache hash — renaming a config never
    invalidates its cached solves (see `Engine.md` §6).
  - **Every config is deletable, including built-ins**, by explicit user request
    (`coilset_repository.delete_config()`'s built-in guard is bypassed here on purpose)
    — behind a confirmation popup (§6, why it was added).
- **Right column, top to bottom:**
  - **Coil Geometry** (`visualization/coil_diagram.py`) — a live schematic redrawn every
    frame from the form's current values. Two views: top-down concentric rings sized by
    each coil's real radius, and an axial cross-section (a direct port of
    `engine/core.py`'s real stacking math) showing loop below tank on the transmit side
    and RC drawn `distance` mm above, facing down, with ferrite/shield/backplane stacked
    outward on each side.
  - **Run controls** (`runs_panel.py::draw_controls`) — config dropdown, distance field,
    Run button. An identical `(coilset, distance)` reuses a cached result instead of
    re-solving (`Engine.md` §6); reuse still bumps `last_used_at` without pretending it
    was a fresh solve.
  - **Run History** (`runs_panel.py::draw_history`) — every past run merged live with
    anything still solving, sorted by `last_used_at`, selectable to see its 12 result
    values or its error.

## 5. AI Agent mode (the UI side)

Three files in `screen_agent/`, each with one job — the mechanics they render (interview
turns, search rounds, scoring) belong to `Agent.md`:

- **`agent_panel.py`** — owns `AgentPanelState`: the chat, and which of the other two
  views to show (live progress / finished summary / history). It also gates planning on the
  baseline: if a baseline solve failed (the 5 mm one started on picking a config, or any of
  a finalized plan's distances), `planning_blocked` disables the chat input and Start
  Search and shows a fixed notice with the solver's error and a **Retry solve** button
  (`retry_baseline()` re-queues only the failed solves). No LLM turn is involved. It stops
  applying once the search has started, so follow-up questions stay available.
- **`agent_candidate_card.py`** — the shared renderer for one round/candidate (geometry +
  result tables + reasoning) and the finished-search summary. Used by *both* the live
  view and history replay so they can never visually drift apart — deliberately its own
  file (not part of `agent_panel.py`) so `agent_history.py` can import it without a
  circular import.
- **`agent_history.py`** — reads saved runs straight from the agent's repository and
  replays them through `agent_candidate_card.py`'s same rendering functions, from plain
  dicts, never a live run object.

`visualization/coil_diagram.py` is reused here too (not agent-specific) — both the live
view and the finished summary call `draw()` directly.

### The Settings screen

`screen_settings/settings_panel.py`, opened from the AI Agent view, edits the agent's
`AgentSettings`: the three system prompts, the LLM connection (URL, model, limits, and the sampling
values sent with every request: temperature, top-p, seed), and
the search budget (rounds, candidates per round, retries). This is the second — and
last — `frontend/` ↔ `agent/` touch point (`agent.settings`). Full detail: `Agent.md`.

## 6. Verification performed

38 pytest tests, all against real code paths (real `CoilSet`s, a real `femmcli` solve
where relevant, real file writes), not mocks: field-metadata drift guard, config
load/save/clone/delete/import/export/validation-rejection, name-conflict rejection, job
submission → completion → persistence, cache-hit reuse and `last_used_at` bumping, the
diagram's scaling math. Dialog and popup flows were verified live by monkeypatching
`imgui.button` to force real clicks through the actual render call chain (calling the
real button first, only overriding its returned bool — the naive version of this trick,
skipping the real call, corrupts ImGui's internal per-frame bookkeeping).

This live verification caught two real bugs code review alone hadn't: `history_rows()`
was clobbering a freshly-`touch()`-ed cache hit's `last_used_at` with the stale
in-memory `Job`'s copy on the next frame (fixed by having `touch()` update the in-memory
copy too), and `imgui.begin_popup_modal()` returns a `(bool, Optional[bool])` tuple, not
a plain bool — `if imgui.begin_popup_modal(...):` is Python-truthy on any non-empty
tuple regardless of the real value, so the delete-confirmation popup's `end_popup()` was
firing every frame even when never opened.

## 7. Known concerns

Roughly in order of how much they'd bite:

- **Delete confirmation exists because of two real accidental deletions** in earlier
  sessions — a built-in `Gen3` config and, later, the real `base_coil` config (only
  recoverable to a stale pre-rename git checkpoint; some edits since were lost for good).
  The popup only prevents *future* accidents.
- **The coil diagram is a proportional schematic, not a literal render.** Turn count,
  trace width, winding path, and sub-mm per-layer offsets aren't modeled even with both
  views — a reasonable place to add fidelity later if needed.
- **The AI Agent chat feed renders plain text (`imgui.text_colored`), not markdown** —
  a model reply containing `**bold**`/`_italics_`/backtick spans shows the literal
  formatting marks, not styled text. The three system prompts
  (`backend/agent/settings/prompts/*.txt`) are told to write plain text for exactly
  this reason; a new prompt or a new reply path needs the same instruction, since
  nothing on the rendering side strips or interprets markdown.
- **Import only accepts the exact round-trip shape `export_record()` writes** — a bare or
  hand-built coilset dict is rejected with a specific reason, not silently guessed at.
  Every failure mode (non-JSON, valid-JSON-but-wrong-shape, missing/extra/wrong-typed
  fields, impossible geometry, blank name) was individually probed and writes nothing on
  failure.
- **`backend/` can be edited outside this app, uncommitted, by other concurrent
  sessions in the same repo — this is an active, observed risk, not hypothetical.** If
  `engine.simulate()`'s or `check_valid_coilset()`'s signature changes, or `CoilSet`
  gains a field, this app breaks — loudly for a `CoilSet` field change (the drift-guard
  test), but as a plain `TypeError` for anything else. Nothing here is pinned to a
  commit.
- **The xfemm mutual-inductance fix lives only in the locally patched vendored
  binary** (`Engine.md` §5) — re-vendoring without reapplying the patch breaks
  `M12/M13/M23/k12/k13/k23` silently, with no test to catch it from this side either.
- **A brand-new config starts all-zeros, not empty** (ImGui numeric widgets can't
  represent "empty"), so **New** reliably fails `check_valid_coilset` with a geometry
  message rather than a "missing field" one — functionally fine, just a different error
  text than the old webapp gave.
- **The result cache only recognizes runs solved since caching landed** — older result
  files lack `coilset_hash` and won't be treated as cache hits the first time they'd
  otherwise match.
- **`Job` status thread-safety is informal**, not proven under stress — see `Engine.md`
  §6's hazard table.
- **Linux-only as shipped** — `femmcli` is a Linux ELF binary; elsewhere a run fails
  cleanly, producing no results, rather than crashing.
- **No packaging** — runs via `uv run` from this exact repo checkout only. A deliberate
  choice, not an oversight.
- **Result files accumulate with no pruning.**

## 8. Quick file reference

| Path | Role |
|---|---|
| `main.py` | App entry point, top-level layout, owns `AppState`, no tab bar |
| `_sim_path.py` | Puts `backend/` on `sys.path` so `import engine`/`import agent` resolve |
| `screen_agent/agent_panel.py` | AI Agent mode's chat + state machine — **the frontend/↔agent/ boundary** |
| `screen_agent/agent_candidate_card.py` | Shared round/summary card renderer |
| `screen_agent/agent_history.py` | Replays a saved AI run through the same renderer |
| `screen_settings/settings_panel.py` | Settings screen — edit prompts/numbers (§5) |
| `visualization/coil_diagram.py` | Geometry schematic — used everywhere, not agent-only |
| `visualization/result_table.py` | The 12-value result table, shared by both modes |
| `visualization/femm_open.py` | "Open in FEMM" button (see `Engine.md` §7) |
| `screen_manual/configs_panel.py`, `runs_panel.py` | Manual Runs mode (§4) |
| `services/field_layout.py` | How `CoilSet` fields are grouped into form sections — the drift guard |
| `services/job_runner.py` | The FEMM-solve cache + thread pool — documented in `Engine.md` §6 |
| `files_backup/config_io.py`, `run_io.py` | Export/import of one config or run |
| `files_backup/backup_io.py` | Backup All / Restore All |
| `tests/*.py` | 38 tests against real code paths (§6) |

**Import rule:** `frontend/` only ever calls into `agent/input_chat/services/runner.py` and
`agent/femm_agent/services/runner.py`, plus `agent.settings` for the Settings screen — it never
reimplements engine or agent logic, and `agent/` never imports back from `frontend/`
(the one narrow exception, CLI-only, is noted in `Agent.md`).
