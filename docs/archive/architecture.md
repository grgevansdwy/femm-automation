# Repo architecture: how the pieces connect

**Purpose of this doc: connections, not implementation.** For "what does
this function do," read the code (it's well-commented) or
`backend/agent/docs/gui_agent_plan.md`. This is for "which file
calls which file, and why" — the map you'd want before diving in top-down.
(`docs/CODEBASE_OVERVIEW.md` is an older doc from before this structure
existed — this one supersedes it for anything agent/GUI-related.)

> **Layout note (2026-09-18):** `backend/` used to be a flat mix of
> `engine.py`, `models/`, `services/`, `repositories/` and `agent/`. It is now
> exactly two packages — `engine/` and `agent/` — laid out by layer
> (`models/`, `services/`, `repositories/`, `tests/`), and `agent/` is further
> split into its two phases, `input_chat/` and `femm_agent/`. Older docs and run
> logs still use the old paths; §9 maps old → new.

---

## 1. The three pieces

```
frontend/                        <- the desktop app (Dear ImGui). What a person sees/clicks.
backend/
  engine/                   <- the FEMM engine: a CoilSet in, a SimResult out. Knows nothing
                               about any UI or about the agent.
  agent/                    <- the AI agent. Builds ON TOP of engine/ (one-way: agent -> engine),
                               and has no idea a GUI exists. Two phases:
    input_chat/               the interview - a chat turns a request into an AgentSpec
    femm_agent/               the search - proposes candidates and simulates them against that AgentSpec
    services/                 shared plumbing: llm_client, gui_bridge, runner (the GUI boundary)
    repositories/             shared run store: ai_run_repository (both phases write the same two tables)
```

Every one of these folders is organised the same way, by layer, and owns its own tests:

| Layer | `engine/` | `agent/input_chat/` | `agent/femm_agent/` | `agent/` (shared) |
|---|---|---|---|---|
| **models/** — plain data & pure rules, no I/O | `CoilSet` (a design), `SimResult` (one solve) | `Objective`, `AgentSpec`, `VariableSpec` | tool `schema`, run `state` | — |
| **services/** — behavior | `femm_service`, `xfemm_backend`, `femm_launcher` (talk to FEMM) | `interview` | `optimize`, `scorer`, `prompts`, `outcome`, `followup` | `llm_client`, `gui_bridge`, `runner` |
| **repositories/** — storage (SQLite) | `db`, `coilset_repository`, `run_result_repository`, `constants_repository` | — | — | `ai_run_repository` |
| **tests/** | `engine/tests/` | `input_chat/tests/` | `femm_agent/tests/` | `agent/tests/` (cross-phase) |

The two agent phases have no `repositories/` of their own on purpose: the interview and the
search write rows into the *same* two tables (`ai_runs`, `ai_run_messages` — one run record spans
both phases), so that store is shared and lives one level up.

The dependency rules that keep this tractable:

- `engine/` imports nothing from `agent/` or `frontend/`.
- `agent/`'s core imports `engine/`, never `frontend/`. (One exception: `orchestrator.py`, the
  CLI-only glue, borrows `frontend.job_runner.JobRunner` as its solver pool.)
- Inside `agent/`, the search builds on the interview: `femm_agent/` imports `input_chat/`'s models
  (`AgentSpec`, `Objective`, `VariableSpec` are the contract the interview hands over). Two imports
  go the other way and are the wrinkle to know about: `input_chat/models/variables.py` reuses the
  field-schema helpers in `femm_agent/models/schema.py`, and `input_chat/services/interview.py`
  borrows `design_values` from `femm_agent/services/scorer.py`.
- `frontend/` only ever calls into `agent/services/gui_bridge.py` and `agent/services/runner.py` (see
  §3) and renders whatever they hand back — it never reimplements agent logic.

Both packages are imported as top-level names (`import engine`, `import agent`), which works
because `backend/` is put on `sys.path` (`agent/__init__.py` and
`frontend/src/frontend/_sim_path.py` both do it). Storage is one shared SQLite file, deliberately — the
agent's `ai_runs` table references the engine's `configs` table — but each side creates only its
own tables: `engine/repositories/db.py` owns the connection and the engine's tables,
`agent/repositories/ai_run_repository.py` creates the agent's.

---

## 2. Two ways to launch, same layers underneath

| | Entry point | What it does |
|---|---|---|
| **Desktop GUI** | `frontend/src/frontend/main.py` → `main()` | The real, day-to-day way this gets used. Interview chat + live search progress, or the manual single-run dashboard. |
| **Agent CLI** | `backend/agent/__main__.py` (`python -m agent`, from `backend/`) | A no-GUI, no-chat run: interview phase, optimize phase, then a printed report. Uses the fixed test objective in `agent/config.py` (baseline, distances, tolerances) instead of a real conversation. |
| **Engine CLI** | `python -m engine.main` (from `backend/`) | One solve of a hard-coded config (`TR302_and_225_000056`), saved to the database. An example script more than a tool. |

Both agent entry points end up calling the exact same `agent/services/` functions
(`run_interview_turn()`, `optimize_loop()`) — the CLI just calls them synchronously and prints,
where the GUI polls them from a background thread every frame. **`frontend/` never calls
`orchestrator.py`** (grep-confirmed, zero references) — `orchestrator.py` is CLI-only glue; the
GUI has its own glue (`agent_panel.py`'s `AgentPanelState`, see below).

---

## 3. The main trace: one AI Agent session, top to bottom

This is the one worth internalizing — it's the real path from a click in the GUI down to FEMM
and back.

```
frontend/src/frontend/main.py
  AppState.__init__()
    self.agent_panel = agent_panel.AgentPanelState(job_runner)
                                         │
                                         ▼
frontend/src/frontend/screen_agent/agent_panel.py   <-- THE frontend/ <-> agent/ boundary
  AgentPanelState.__init__()
    from agent.services.gui_bridge import InterviewRunner  <-- first import of agent/
    from agent.services.runner import AgentRunner              in the whole GUI codebase
```

**Phase A — picking a baseline & talking through the objective (the interview):**

```
user picks a config in the dropdown
  agent_panel.AgentPanelState.select_config(config_id)
    self.interview_runner.start(config_id)
      -> agent/services/gui_bridge.py: InterviewRunner.start()
           creates an ai_runs DB row, an empty InterviewState

user types a message, hits Send
  agent_panel.AgentPanelState.send_chat()
    self.interview_runner.send(state, text)
      -> agent/services/gui_bridge.py: InterviewRunner.send() (background thread)
           -> agent/input_chat/services/interview.py: run_interview_turn()
                -> agent/services/llm_client.py: call_llm() with FINALIZE_SPEC_TOOL
                -> once the model calls finalize_spec:
                     agent/input_chat/services/interview.py: _build_spec()
                       -> agent/input_chat/models/objective.py:  Objective(...)   (one per objective)
                       -> agent/input_chat/models/variables.py:  VariableSpec(...), free_fields_for()
                       => a real agent/input_chat/models/spec.py: AgentSpec
```

Every frame, `agent_panel.py` just reads `interview_state.transcript`/`.finalized`/`.spec` and
draws it — it never blocks waiting for the LLM (that's the whole reason for the background
thread + polling shape).

**Phase B — the user clicks "Start Search," and the actual optimization runs:**

```
agent_panel.AgentPanelState.confirm_plan()  -> plan_confirmed = True
  (next frame) AgentPanelState.poll()
    self.agent_runner.submit(spec, ai_run_id)
      -> agent/services/runner.py: AgentRunner.submit()   (background thread)
           -> agent/femm_agent/services/optimize.py: optimize_loop()
                loop, up to spec.max_tool_calls rounds:
                  agent/femm_agent/services/prompts.py:  system_prompt(spec, ...)  <- what the model is told
                  agent/services/llm_client.py: call_llm() with PROPOSE_COILSET_TOOL
                                        (built by agent/femm_agent/models/schema.py: build_tools())
                  model proposes a delta -> agent/femm_agent/models/schema.py: coerce_delta()
                                        (checked against agent/input_chat/models/variables.py: free_fields_for())
                  dataclasses.replace(baseline, **delta)
                                        -> a new engine/models/coilset.py: CoilSet
                  agent/femm_agent/services/scorer.py: evaluate()
                       -> engine/core.py: simulate(coilset, distance)
                            -> engine/services/xfemm_backend.py
                                 -> actually drives FEMM, once per distance
                       -> scores the result against spec.objectives
                            (agent/input_chat/models/objective.py: margin(), is_per_distance())
                  agent/femm_agent/services/scorer.py: tool_result()  <- fed back to the model as the tool's result
                until goal met, or the model stops, or max_tool_calls is hit
                mutates a shared AgentRunState (agent/femm_agent/models/state.py) in place, each round
```

`agent_panel.py` polls `run_state` every frame and renders it live through
`agent_candidate_card.py` (shared by the live view and history replay — see §4). Once the run
ends, `AgentRunner._persist()` writes it to SQLite via
`agent/repositories/ai_run_repository.py`, so it shows up in the History tab forever
after, even after the app restarts. The chat stays open after the search starts: questions about
the running/finished run go to `agent/femm_agent/services/followup.py`, which can answer but (structurally —
it is given no tools) can never change anything.

**One important wrinkle:** when running under the GUI, `evaluate()`/`evaluate_async()` don't call
`simulate()` directly — they are handed the `JobRunner` from `frontend/src/frontend/services/job_runner.py`, which
caches results (same coilset + distance already solved before → instant reuse) and runs solves
concurrently on a thread pool. The agent code only ever sees it as a parameter, never imports it.
The CLI path (`orchestrator.py`) builds a `JobRunner` too, just a throwaway one with nothing
cached yet.

---

## 4. The GUI rendering layer (AI Agent mode)

Three files in `frontend/src/frontend/screen_agent/`, plus the shared coil diagram, each with one job:

- **`agent_panel.py`** — owns `AgentPanelState`, the chat, and picks which of the other two to
  show (live progress / finished summary / history).
- **`agent_candidate_card.py`** — the shared renderer for one round/candidate (geometry + tables +
  reasoning) and the finished-search summary. Used by *both* the live view and history replay, so
  they can never visually drift apart. Deliberately its own file, not part of `agent_panel.py`, so
  `agent_history.py` can import it without a circular import.
- **`agent_history.py`** — reads saved runs straight from `ai_run_repository.py` and replays them
  through `agent_candidate_card.py`'s same rendering functions, from plain dicts (never a live
  `AgentRunState`).
- **`visualization/coil_diagram.py`** — the geometry schematic. Not agent-specific — Manual Runs uses it too —
  but the agent's live view and summary both call it directly (`coil_diagram.draw(...)`).

---

### Settings: what a person can change without touching code

The **Settings** screen, opened from the AI Agent view (`screen_settings/settings_panel.py`), edits
`AgentSettings` (`agent/settings/`): the three system prompts, the LLM connection (URL, model, limits), the
search budget (rounds, candidates per round, retries...). The objective itself is never a setting: baseline, distances and targets are chosen per search in the chat. Defaults live
in code (`agent/config.py`, `agent/settings/prompts/*.txt`); only changes are stored, in the
`agent_settings` table. There are three system prompts - FEMM agent, Input chat, Follow up chat -
each ONE template with `{{placeholders}}` the app fills in (objectives, baseline values, distances...),
so the wording is editable while the facts stay computed. Parts that only apply sometimes (several
candidates vs one, ferrite fields opened, search running vs finished) are `{{#name}}...{{/name}}`
blocks inside the prompt. A saved prompt written for placeholders that no longer exist is ignored
and the default is used.

A conversation or search copies the settings in force when it starts (`InterviewState.settings`,
then `AgentSpec.settings`), so editing them never changes a run in progress, and History keeps
which settings a run used (only the edited prompts, in its `spec_json`). Not configurable on
purpose: the tool schemas and validation, the copper-thickness list, the solver's geometry-validity
checks and the structural audit.

---

## 5. For contrast: Manual Runs (the non-agent path)

Picking a config and hitting "Run" in Manual Runs mode never touches `agent/` at all:

```
frontend/src/frontend/screen_manual/configs_panel.py   (pick/edit a config)
frontend/src/frontend/screen_manual/runs_panel.py      (RunsPanelState.submit_run())
  -> frontend/src/frontend/services/job_runner.py: JobRunner.submit()
       -> engine/core.py: simulate(coilset, distance)                    <- called directly, once
       -> engine/repositories/run_result_repository.py                   <- saved
```

Same `engine/`, `JobRunner` and storage underneath, just no LLM, no interview, no search loop —
one config, one distance, one solve. Useful reference point: it's the same machinery the agent's
`evaluate()` rides on top of, minus everything agent-specific.

---

## 6. The data layer (shared by everything above)

```
backend/engine/repositories/
  db.py                     <- one SQLite connection helper + the engine's tables (configs, runs)
  coilset_repository.py     <- CRUD for coil configs (get_coilset, list_configs)
  run_result_repository.py  <- CRUD for manual-run results
  constants_repository.py   <- reads data/constants.json (the test-rig constants)
backend/agent/repositories/
  ai_run_repository.py      <- CRUD for agent runs + their round-by-round messages;
                               creates its own ai_runs / ai_run_messages tables
```

One local SQLite file (`backend/data/femm_automation.db`), gitignored — a fresh checkout
starts empty. "Backup All"/"Restore All" in the GUI toolbar (`frontend/src/frontend/files_backup/backup_io.py`) is the
only way data moves between machines. `backend/data/` stays at the `backend/`
level because it is runtime data shared by both packages, not code.

---

## 7. Quick reference: what each file/folder is for

| Path | Role |
|---|---|
| `frontend/src/frontend/main.py` | App entry point, top-level layout, owns `AppState` |
| `frontend/src/frontend/screen_agent/agent_panel.py` | AI Agent mode's chat + state machine — **the frontend/↔agent/ boundary** |
| `frontend/src/frontend/screen_agent/agent_candidate_card.py` | Shared round/summary card renderer |
| `frontend/src/frontend/screen_agent/agent_history.py` | Replays a saved run through the same renderer |
| `frontend/src/frontend/screen_settings/settings_panel.py` | Settings screen - edit the system prompts and numbers; the second frontend/↔agent/ touch point (`agent.settings`) |
| `frontend/src/frontend/visualization/coil_diagram.py` | Geometry schematic (used everywhere, not agent-only) |
| `frontend/src/frontend/screen_manual/configs_panel.py`, `runs_panel.py` | Manual Runs mode (no agent involved) |
| `frontend/src/frontend/visualization/result_table.py` | The 12-value result table, shared by both modes |
| `frontend/src/frontend/services/field_layout.py` | How CoilSet fields are grouped into form sections |
| `frontend/src/frontend/files_backup/config_io.py`, `run_io.py` | Export / import of one config or run |
| `frontend/src/frontend/services/job_runner.py` | The FEMM-solve cache + thread pool, used by both modes |
| `frontend/src/frontend/visualization/femm_open.py` | "Open in FEMM" button - opens a geometry directly in the FEMM GUI |
| `frontend/src/frontend/files_backup/backup_io.py` | Backup All / Restore All |
| **`backend/agent/`** | |
| `agent/__main__.py`, `orchestrator.py` | CLI entry point (`python -m agent`) |
| `agent/config.py` | The **defaults** of every number (LLM server, budgets, CLI objective) - what `AgentSettings` starts from |
| `agent/settings/settings.py` | `AgentSettings` - every editable number + prompt override, validation, `load_settings()`/`save_settings()` |
| `agent/settings/prompt_defs.py`, `prompts/*.txt` | The three editable system prompts: their `{{placeholders}}` and default text |
| `agent/settings/templating.py` | The `{{placeholder}}` / `{{#name}}...{{/name}}` syntax and its checks |
| `agent/repositories/agent_settings_repository.py` | The `agent_settings` table - only what differs from the defaults |
| `agent/input_chat/models/objective.py` | `Objective`, `margin()` - the pass/fail vocabulary |
| `agent/input_chat/models/spec.py` | `AgentSpec` - the one object describing a whole run |
| `agent/input_chat/models/variables.py` | Which `CoilSet` fields the model may touch |
| `agent/femm_agent/models/schema.py` | JSON-schema tool definitions + delta validation |
| `agent/femm_agent/models/state.py` | `AgentRunState`/`RoundState` - the search loop's live state |
| `agent/input_chat/services/interview.py` | Chat → `AgentSpec` (the interview phase) |
| `agent/femm_agent/services/optimize.py` | The propose → evaluate → feedback search loop |
| `agent/femm_agent/services/scorer.py` | Turns a solved candidate into pass/fail + rank |
| `agent/femm_agent/services/prompts.py` | Builds what the model is told each optimize round (fills the search system prompt) |
| `agent/femm_agent/services/outcome.py` | End-of-run report: why a search didn't meet its goal |
| `agent/femm_agent/services/followup.py` | Read-only Q&A about a running/finished search |
| `agent/services/llm_client.py` | Raw HTTP to the LLM server, nothing coil-specific |
| `agent/services/gui_bridge.py` | `InterviewRunner` - background-thread wrapper around `interview.py`, for the GUI |
| `agent/services/runner.py` | `AgentRunner` - background-thread wrapper around `optimize.py`, for the GUI |
| `agent/repositories/ai_run_repository.py` | Saves/loads agent runs |
| `agent/input_chat/tests/`, `agent/femm_agent/tests/` | Each phase's own tests |
| `agent/tests/` | Cross-phase tests: the LLM client, the run store, objective scoring, live reasoning |
| `agent/docs/` | Agent design docs and dated run logs |
| **`backend/engine/`** | |
| `engine/core.py` | Builds the FEMM script and calls the backend — the real physics entry point (`simulate()`) |
| `engine/constants.py` | Fixed test-rig values (frequency, currents, ...) |
| `engine/main.py` | Tiny CLI example (`python -m engine.main`) |
| `engine/models/coilset.py`, `result.py` | `CoilSet` (the design) and `SimResult` (one solve's output) |
| `engine/services/femm_service.py` | Geometry / circuit helpers shared by the solve and the launcher |
| `engine/services/xfemm_backend.py` | Actually drives FEMM (via `femmcli`) |
| `engine/services/femm_launcher.py` | Opens a design directly in interactive FEMM |
| `engine/repositories/*.py` | SQLite CRUD for configs / manual-run results / constants |
| `engine/vendor/` | The bundled xfemm binaries |
| `engine/tests/` | The engine's tests |
| **elsewhere** | |
| `backend/conftest.py` | Shared test fixture: every test gets its own empty temp database |
| `backend/data/` | Runtime data (the SQLite file, constants.json, FEMM scratch) — gitignored |

---

## 8. If you only remember one thing

`agent_panel.py`'s two imports —

```python
from agent.services.gui_bridge import InterviewRunner
from agent.services.runner import AgentRunner
```

— are the entire surface area between `frontend/` and `backend/agent/`. Everything on the
`frontend/` side of that line is rendering and clicking; everything past it is the real agent, and it
has no idea a GUI exists. And `agent/` reaches `engine/` through ordinary imports
(`engine.core`, `engine.models`, `engine.repositories`) — never the other way round.

---

## 9. Old path → new path

For reading older docs, run logs and commit history:

| Before | After |
|---|---|
| `backend/engine.py` | `backend/engine/core.py` |
| `backend/constants.py`, `main.py` | `backend/engine/constants.py`, `engine/main.py` |
| `backend/models/` | `backend/engine/models/` |
| `backend/services/` | `backend/engine/services/` |
| `backend/repositories/` (all but `ai_run_repository.py`) | `backend/engine/repositories/` |
| `backend/repositories/ai_run_repository.py` | `backend/agent/repositories/ai_run_repository.py` |
| `backend/vendor/` | `backend/engine/vendor/` |
| `agent/objective.py`, `spec.py`, `variables.py` (a flat file at the agent root, or under `agent/input_chat/` before it was layered) | `agent/input_chat/models/…` |
| `agent/interview.py` | `agent/input_chat/services/interview.py` |
| `agent/schema.py`, `state.py` | `agent/femm_agent/models/…` |
| `agent/optimize.py`, `scorer.py`, `prompts.py`, `outcome.py`, `followup.py` | `agent/femm_agent/services/…` |
| `agent/llm_client.py`, `runner.py`, `gui_bridge.py` | `agent/services/…` (shared plumbing) |
| `backend/experiments/` | removed - `cd backend && python -m agent` replaces its entry-point shim |
| `legacy/` (`webapp/`, `noelle/`, `genki/`, `wine_backend/`) | removed on 2026-09-18 - still in git history (commit `321b8d7`) |
| `backend/tests/` | Split by what they test: `engine/tests/`, `agent/input_chat/tests/`, `agent/femm_agent/tests/`, `agent/tests/` (cross-phase); `conftest.py` → `backend/conftest.py` |
| `from engine import simulate` | `from engine.core import simulate` |
| `from models.coilset import CoilSet` | `from engine.models.coilset import CoilSet` |
| `from repositories import coilset_repository` | `from engine.repositories import coilset_repository` |
| `from agent.scorer import …` | `from agent.femm_agent.services.scorer import …` |
| `from agent.objective import …` | `from agent.input_chat.models.objective import …` |
