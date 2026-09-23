# LLM-Agent Panel for the FEMM GUI

## Context

The standalone CLI prototype at `backend/agent/` (propose → evaluate
→ feed back → repeat against a real FEMM backend and a real OpenAI-compatible
LLM server) has now been validated across many real runs (documented in
`backend/agent/docs/latest_run.md`, runs A-I) - it converges reliably,
self-corrects from real failures, and shows no hallucination across the
hardest goal tested. The next step is turning it into a real GUI feature
inside the existing `frontend/` desktop app: a chat panel that interviews the user
for their objective, a live view of the coil geometry and search progress,
and a persisted, browsable history of every AI run - so this stops being a
script you run from a terminal and becomes a tool anyone using the app can
drive.

This plan was produced after three parallel research passes (GUI panel/layout
patterns, the SQLite persistence + caching layer, and the full 53-field
`CoilSet` structure with its structural presence-flag gating) plus a design
pass, all grounded in real file:line evidence rather than assumption. Two
decisions were confirmed directly with George: the AI-agent UI is a **toggle
between two full layouts** (a "Manual Runs" / "AI Agent" button pair in the
toolbar swaps the whole dashboard, rather than cramming a 4th column into the
existing fixed 3-column layout), and the work is **staged with a check-in
after each milestone** below, not built straight through.

## Package location: keep `backend/agent/`, do not move to root

The whole reason `agent/*.py` can write bare imports like `from engine import
simulate` is that `backend/agent/__init__.py` inserts
`backend/` onto `sys.path` - the exact same trick
`frontend/src/frontend/_sim_path.py` already does one level up, and the two are
idempotent regardless of order. `frontend` has **no real package dependency** on
`backend` at all (not even a uv workspace entry) - the entire
connection is this hand-rolled `sys.path` insertion. That means `frontend` can
already do `from agent.runner import AgentRunner` today with zero new
plumbing, exactly like it already does `from repositories.coilset_repository
import get_coilset`.

Moving `agent/` to a new root-level folder would put it *outside* the
directory that actually gets inserted onto `sys.path`, breaking every
existing bare import inside it for no functional gain - and `agent/` isn't
backend-agnostic code anyway (`schema.py`/`scorer.py`/`orchestrator.py` are
all `backend`-internal by nature: they index `CoilSet` fields
directly and call `engine.simulate`). It's a consumer of `backend`
and, after this plan, a collaborator of `frontend`'s `JobRunner` - not a sibling
of either. **Recommendation: leave it at `backend/agent/`**, add new
modules inside it, and let `frontend/` import it the same way it imports every
other `backend`-internal thing today.

## Design

### 1. Dynamic free-variable computation (`backend/agent/variables.py`, new)

Replaces the hardcoded `FREE_FIELDS` constant in `schema.py` with a function
of the baseline coilset and the user's stated constraints:

```python
WINDING_FIELDS = [...]  # today's 24 hardcoded FREE_FIELDS + "rc_ferrite_y_offset" (see asymmetry note below)

FEATURE_FLAGS = {
    "tank_ferrite": {"flag": "tank_ferrite_present",
                      "fields": ["tank_ferrite_y_offset", "tank_ferrite_thickness", "tank_mu",
                                 "tank_ferrite_outer_r", "tc_loss_angle"]},
    "rc_ferrite":   {"flag": "rc_ferrite_present",
                      "fields": ["rc_ferrite_thickness", "rc_mu", "rc_ferrite_outer_r", "rc_loss_angle"]},
    "tc_shield":    {"flag": "tc_shield", "fields": ["tc_shield_y_distance", "tc_shield_radius", "tc_shield_thickness"]},
    "rc_shield":    {"flag": "rc_shield", "fields": ["rc_shield_y_distance", "rc_shield_radius", "rc_shield_thickness"]},
    "backplane":    {"flag": "backplane", "fields": ["backplane_y_distance"]},
    "rc_backplane": {"flag": "rc_backplane", "fields": ["rc_backplane_y_distance"]},
}
CONVENTIONAL_OPT_IN_FIELDS = ["tank_PCB_thickness", "loop_PCB_thickness", "rc_PCB_thickness"]

def free_fields_for(baseline: CoilSet, spec: VariableSpec) -> dict[str, dict]:
    # 1. Start from WINDING_FIELDS - nothing gates these.
    # 2. For each FEATURE_FLAGS entry: include its fields (+ the flag itself,
    #    as a free 0/1 int) only if the baseline's flag is already 1, OR the
    #    user explicitly enabled that feature - never silently offered otherwise.
    # 3. Include CONVENTIONAL_OPT_IN_FIELDS only if the user asked to make
    #    PCB thickness free (excluded by convention, matching the project's
    #    original "fixed PCB thickness" example objective).
    # 4. Subtract any fields the user explicitly said to keep fixed.
    # 5. Build each surviving field's JSON-schema entry (moved from schema.py);
    #    *_trace_thickness fields keep the existing [0.035, 0.07, 0.14] enum.
```

**`rc_ferrite_y_offset` asymmetry, handled explicitly**: `engine.py` reads
`rc_ferrite_y_offset` *unconditionally* (outside the `if
coilset.rc_ferrite_present:` guard) to position the RC-side stack origin -
unlike `tank_ferrite_y_offset`, which only matters inside its guard. So
`rc_ferrite_y_offset` goes in `WINDING_FIELDS` (always free, independent of
the `rc_ferrite` toggle), with its schema `description` calling this out
explicitly so neither the LLM nor a human reading the log misreads it as
ferrite-only.

Downstream changes this implies: `schema.py`'s `TOOLS` becomes
`build_tools(free_fields, baseline_config_id)` (built per-run, not at import
time); `coerce_delta(delta, free_fields)` takes the free-field map as a
parameter; `llm_client.call_llm(messages, tools)` takes `tools` explicitly
(the interview phase needs a different tool set than the optimize phase);
`prompts.system_prompt(baseline_values, spec, free_fields)` reads from the
new `AgentSpec` (below) instead of `config.py` constants, `PARAM_EFFECTS`
physics guidance is unchanged.

### 2. Two-phase LLM design (`backend/agent/interview.py`, new)

```python
@dataclasses.dataclass
class AgentSpec:
    baseline_config_id: str
    distances: list[float]
    objective_text: str
    primary_metric: str = "M23"
    m_tolerance_pct: float = 5.0
    min_esr_improvement_pct: float = 10.0
    variable_spec: VariableSpec = dataclasses.field(default_factory=VariableSpec)
    notes: str = ""
```

A `finalize_spec` tool (analogous to today's `propose_coilset`) that the
interview LLM calls once - and only once - it has enough: an objective, at
least one distance, and an explicit answer (or silence, defaulting per the
rule above) on which variables must stay fixed.
`interview_system_prompt(baseline_config_id, baseline)` scopes the model to
FEMM-simulation topics (prompt-level constraint - requirement #1 is a soft
guardrail, not a hard filter) and lists which features are already
active/inactive on the baseline so the model knows what it must actively ask
about before ever offering it, per requirement #2/#3's "never assume beyond
the default rule."

`run_interview_turn(state: InterviewState, baseline, user_message)` mutates
`state` in place (transcript, `spec`, `finalized`) - same in-place-mutation
convention as `job_runner.Job`, so a background thread can run it while a
render thread polls. Handoff to the optimize phase is just `AgentSpec` itself
- once `state.finalized`, `AgentRunner.submit(state.spec)` starts the search;
no other module needs to know an interview happened.

### 3. Orchestrator → pollable background loop (`agent/state.py`, `agent/runner.py`, `agent/optimize.py`, new)

`orchestrator.py`'s `main()` today is a single blocking call that only
`print()`s progress - fine for a CLI, useless for a GUI that needs to poll
live round-by-round state the way `frontend/job_runner.py`'s `JobRunner` already
does for plain FEMM solves. Mirror that exact shape:

```python
@dataclass
class AgentRunState:            # the Job-equivalent
    id: str; spec: AgentSpec; baseline_coilset: CoilSet
    status: AgentRunStatus = AgentRunStatus.RUNNING   # RUNNING/GOAL_MET/BUDGET_EXHAUSTED/STOPPED_BY_MODEL/FAILED
    rounds: list[RoundState] = field(default_factory=list)   # each: round_num, status, reasoning_text, candidates
    best: Scored | None = None
    error: str | None = None
    finished_at: datetime | None = None

class AgentRunner:               # mirrors JobRunner 1:1
    def __init__(self, job_runner: JobRunner) -> None: ...   # SAME JobRunner instance the manual-run panel uses
    def submit(self, spec: AgentSpec) -> AgentRunState: ...  # loads baseline, submits self._run to the shared EXECUTOR
    def poll(self) -> None: ...                              # drop completed futures, same as JobRunner.poll()
    def _run(self, run_id) -> None: ...                      # calls optimize.optimize_loop(state, job_runner), catches into state.error/FAILED
```

`optimize_loop(state, job_runner)` is today's orchestrator loop body,
restructured to read `state.spec` instead of `config.py` constants, call
`variables.free_fields_for(...)` instead of the hardcoded `FREE_FIELDS`,
append/mutate a `RoundState` each round instead of printing, and persist
incrementally via `ai_run_repository.append_message(...)` (below) each round
rather than only at the end.

**Caching swap, satisfying the caching requirement (`scorer.py`)**: replace
direct `simulate()` calls with `job_runner.find_cached()`/`.submit()` -
non-blocking per distance, gathered afterward:

```python
def evaluate_async(coilset, distances, job_runner) -> dict[float, Job | dict]:
    # for each distance: find_cached() -> touch() on hit, else submit() - never blocks
def wait_for_jobs(jobs) -> dict[float, dict]:
    # blocks the CALLING thread - the agent's own background worker thread, never the GUI render thread
```

This also naturally gives the concurrency win from the earlier
`concurrency_plan.md` draft for free: a round with N candidates now submits
`N x len(distances)` jobs to the shared `EXECUTOR` all at once, then waits
once - candidates and distances solve concurrently on the existing worker
pool instead of serially. **`concurrency_plan.md`'s standalone-script-only
thread pool proposal is superseded by this** - no need to build a second
pool; the GUI's existing one does the job for both caching and concurrency.
`scorer.py`'s `Scored`/`evaluate()` need their attribute access updated from
`SimResult.field` to `dict["field"]`, since `Job.result` is a plain dict, not
a dataclass.

`orchestrator.main()` becomes a thin CLI wrapper: builds an `AgentSpec` from
today's `config.py` constants (kept as *defaults*, not deleted), constructs a
`JobRunner()`, calls `AgentRunner(job_runner).submit(spec)`, then polls with
a simple sleep loop printing each newly-completed round - preserving the
validated CLI workflow unchanged in spirit.

### 4. GUI panel

New files: `frontend/src/frontend/panels/text_feed.py` (shared chat/progress-feed
widget), `frontend/src/frontend/screen_agent/agent_panel.py` (`AgentPanelState` +
`draw_chat`/`draw_candidate`/`draw_progress`/`draw_comparison`),
`frontend/src/frontend/screen_agent/agent_history.py` (AI-run history browser, analogous to
`runs_panel.draw_history`), `backend/agent/gui_bridge.py` (a tiny
`InterviewRunner` mirroring `JobRunner`'s submit/poll shape for interview
turns, so `frontend/` only ever calls into `agent`'s entry points, never
reimplements orchestration).

`main.py`: `AppState.__init__` adds `self.agent_panel =
agent_panel.AgentPanelState(job_runner=self.runs_panel.job_runner)` - the
**same** `JobRunner` instance, so agent solves share the cache and executor
with manual runs. `_gui()` adds `state.agent_panel.agent_runner.poll()` +
`state.agent_panel.interview_runner.poll()` alongside the existing
`job_runner.poll()`. Per the confirmed toggle design: two toolbar buttons
("Manual Runs" / "AI Agent") switch which of the two full layouts `_gui()`
draws - smallest change to `main.py`'s structure, keeps each mode
uncluttered, still not a tab bar.

**Chat/progress-feed widget**: built on `runs_panel.draw_history()`'s
selectable-row skeleton (which already solves per-row coloring and the
"bare cursor move with nothing drawn after it asserts" gotcha), extended
with real text wrapping and row-height computed from wrapped text size:

```python
def draw_feed(child_id, rows: list[tuple[str, str]], size, auto_scroll=True) -> None:
    # one wrapped (label, text) row per message/round, imgui.set_scroll_here_y(1.0) when auto_scroll
```

One function backs both the interview transcript and the optimize-phase
progress feed (`f"Round {r.round_num} [{r.status}]"` + reasoning + candidate
summary lines per row). `imgui.input_text_multiline` is reserved for the
user's own outgoing message box - not for displaying the immutable
transcript.

**Live diagram + config picker**: `coil_diagram.draw(buffer: dict, distance:
float, size, child_id)` is confirmed a pure function of a plain dict and a
float - reused verbatim for the live candidate view (baseline's buffer
pre-optimize, `dataclasses.asdict(run_state.best.coilset)` once `.best`
exists). The config-picker row-list is factored out of
`configs_panel.draw_list` into a small reusable
`draw_selectable_config_list(configs, selected_id, on_select, size)` used by
both panels, rather than standing up a second full `ConfigsPanelState` (which
carries irrelevant edit-form state).

**Final comparison**: two `coil_diagram.draw()` calls side by side using
distinct `child_id`s (`"agent_baseline_diagram"` / `"agent_best_diagram"` -
exactly why that parameter exists), plus a plain field/baseline/best diff
table below, built the same way `orchestrator.main()`'s final report already
computes its diff dict, rendered via `imgui.begin_table` like
`runs_panel._draw_run_detail`'s existing result table.

### 5. Database schema

Append to `backend/repositories/db.py`'s `_SCHEMA` string (same
convention as the existing `configs`/`runs` tables - no migrations
framework, `CREATE TABLE IF NOT EXISTS`, TEXT primary keys, ISO8601 TEXT
timestamps, JSON blobs as TEXT):

```sql
CREATE TABLE IF NOT EXISTS ai_runs (
    id TEXT PRIMARY KEY,
    baseline_config_id TEXT REFERENCES configs (id),
    spec_json TEXT,               -- NULL while still interviewing
    status TEXT NOT NULL CHECK (status IN
        ('interviewing','running','goal_met','budget_exhausted','stopped_by_model','failed')),
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT,
    best_round INTEGER, best_coilset_json TEXT, best_tool_result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_runs_created_at ON ai_runs (created_at);

CREATE TABLE IF NOT EXISTS ai_run_messages (
    id TEXT PRIMARY KEY,
    ai_run_id TEXT NOT NULL REFERENCES ai_runs (id),
    phase TEXT NOT NULL CHECK (phase IN ('interview', 'optimize')),
    seq INTEGER NOT NULL,         -- monotonic order within (ai_run_id, phase)
    role TEXT NOT NULL,           -- 'user' | 'assistant' | 'tool' | 'system'
    round_num INTEGER,            -- NULL for interview rows
    content TEXT, payload_json TEXT,   -- finalize_spec args, or a round's candidate list
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_run_messages_run ON ai_run_messages (ai_run_id, phase, seq);
```

One combined `ai_run_messages` table for both phases (not two separate
tables): requirement was explicit that both phases live "under one ai_run
for a complete replayable history" - a single ordered `(ai_run_id, phase,
seq)` table makes full replay one query, while `WHERE phase=...` still
cheaply serves each browsing view. `content`/`payload_json` shape differing
per phase is fine - it's opaque to the DB layer, same as `runs.result_json`
already is to `run_result_repository.py`.

New `backend/repositories/ai_run_repository.py` (same
`with db.connection() as conn:` / upsert / uuid / ISO8601 conventions as
`coilset_repository.py`): `create_ai_run(baseline_config_id)`,
`finalize_spec(ai_run_id, spec)`, `append_message(ai_run_id, phase, seq,
role, round_num, content, payload)`, `update_run_status(ai_run_id, status,
error=None, finished_at=None, best_round=None, best_coilset=None,
best_tool_result=None)`, `list_ai_runs()`, `get_ai_run(ai_run_id)`,
`list_messages(ai_run_id, phase=None)`.

## Build order (staged, check in after each milestone)

1. **DB schema + repository** (`db.py` DDL, `ai_run_repository.py`). Verify
   standalone with a script/test against a temp `DB_PATH`, same
   monkeypatch pattern the existing repository tests already use. No LLM, no
   GUI.
2. **Dynamic free-variable computation** (`agent/variables.py`). Pure, no
   network - verify with tests against real saved configs already in the DB
   (`Gen2_coil`: confirms all 22 ferrite/shield/backplane fields excluded but
   `rc_ferrite_y_offset` always included; `Gen3`: confirms an
   already-active-baseline includes them without needing an explicit
   opt-in). The most objectively gradeable milestone - do it early.
3. **Orchestrator → pollable background loop + caching wire-up**, against
   today's hardcoded `config.py` defaults (no interview phase, no GUI yet).
   The highest-risk piece (concurrency correctness), done in isolation on
   purpose. Run against the real LLM server and real FEMM backend, confirm
   convergence still matches `backend/agent/docs/latest_run.md`'s
   prior runs, confirm a second run hits the cache (visible in the `runs`
   table).
4. **Interview phase** (`agent/interview.py`). Verify standalone with a
   small terminal-chat script against the real LLM server - confirm it
   actually asks clarifying questions and eventually calls `finalize_spec`
   with a sane `AgentSpec`, then feed that straight into step 3's
   `AgentRunner.submit()` from the same script - proving the full
   interview→optimize handoff end-to-end before touching the GUI.
5. **GUI panel**: `text_feed.py`, `agent_panel.py`, `agent_history.py`,
   `main.py`'s toolbar toggle + `AppState`/`_gui()` wiring, `gui_bridge.py`.
   Only consumes already-verified pieces from steps 1-4. Verify via a manual
   walkthrough of the running app (the `run` skill) - the integration layer
   has no independent headless test.
6. **Follow-up (non-blocking)**: decide whether `orchestrator.py`'s original
   hardcoded-CLI entry point stays as a fixed-defaults smoke test, or gets
   replaced by the interview-driven CLI from step 4.

`config.py`'s existing constants (`BASELINE_CONFIG_ID`, `DISTANCES`,
`M_TOLERANCE_PCT`, `MIN_ESR_IMPROVEMENT_PCT`, `PRIMARY_M`,
`KEEP_RECENT_ROUNDS`, `MAX_TOOL_CALLS`) become `AgentSpec` field *defaults*
rather than being deleted - keeps step 3's thin CLI wrapper needing zero new
constants, and lets the interview system prompt present them as defaults the
user may override.

## Critical files

- `backend/agent/schema.py`, `orchestrator.py`, `scorer.py`,
  `config.py`, `llm_client.py`, `prompts.py` - existing validated logic being
  extended/refactored, not rewritten.
- `frontend/src/frontend/services/job_runner.py`, `panels/runs_panel.py`, `main.py` - the
  exact patterns (`Job`/`JobStatus`, submit/poll/caching, panel wiring)
  every new piece mirrors.
- `backend/repositories/db.py`, `coilset_repository.py`,
  `run_result_repository.py` - schema/connection/upsert conventions the new
  `ai_run_repository.py` follows exactly.
- `backend/models/coilset.py`, `services/femm_service.py`,
  `engine.py` - full field list and the gate-field logic `variables.py`
  depends on.

## Implementation status (updated as milestones complete)

- **Milestone 1 (DB schema + repository): done.** `ai_runs`/`ai_run_messages`
  added to `db.py`'s `_SCHEMA`; `ai_run_repository.py` built matching
  `coilset_repository.py`'s exact conventions; 6 new tests, all passing.
- **Milestone 2 (dynamic free-variable computation): done.**
  `agent/variables.py` built as designed; 6 new tests, verified against both
  the isolated test DB and the real `Gen2_coil`/`Gen3` configs.
- **Milestone 3 (pollable background loop + caching): done**, with three
  transparent deviations from the design above:
  1. `AgentRunner` runs each agent loop on its own dedicated background
     thread (`threading.Thread`), not the shared `JobRunner` executor pool
     as originally sketched - sharing that pool risked the coordinator
     thread starving/deadlocking the very solve jobs it waits on.
  2. `ai_run_repository` persistence was NOT wired into `optimize_loop` in
     this milestone - deferred to milestones 4/5, where a real consumer can
     validate the call sites, rather than guessing now.
  3. `AgentRunner.poll()` was omitted (nothing to poll - `_run()` mutates
     `state` directly on its own thread, no `Future` to clean up); can be
     added if milestone 5's GUI wiring finds a concrete need.
  Verified with a real 9-round convergence run against the live LLM server
  and FEMM backend (matching prior documented runs' behavior almost
  exactly, including discovering the newly-freed `rc_ferrite_y_offset`
  lever), plus a direct `find_cached()` proof of caching.
- **Milestone 4 (interview phase): done.** `agent/interview.py` built
  (`InterviewState`, `FINALIZE_SPEC_TOOL`, `interview_system_prompt`,
  `run_interview_turn`). Verified standalone against the real LLM server:
  the model opened by correctly asking for objective/distances/constraints
  without assuming anything, named the inactive features it would need
  explicit permission to enable, and finalized a correct `AgentSpec` from
  one detailed user reply. The resulting spec was handed straight into
  `AgentRunner.submit()` from the same script, proving the full
  interview→optimize handoff end-to-end: converged in 9 rounds
  (`goal_met=True`), rediscovering `rc_ferrite_y_offset` as a lever again,
  and cleanly absorbing one real `femmcli` crash (segfault, exit -11) as a
  rejected candidate rather than dying.

  One minor, harmless observation worth a future prompt tweak: the model
  put `enabled_features`-style category names (`"tank_ferrite"`) into
  `excluded_fields`, which expects real `CoilSet` field names - a no-op
  here since those categories were already excluded by the default rule
  (nothing named `"tank_ferrite"` is ever a candidate field), but the
  `excluded_fields` tool description could be more explicit that it wants
  individual field names, not category names.
- **Milestone 5 (GUI panel): done.** `frontend/src/frontend/panels/text_feed.py`
  (shared scrolling feed widget), `agent_panel.py` (`AgentPanelState`,
  chat/config-picker/geometry/comparison/progress drawing), `agent_history.py`
  (AI-run history browser), `backend/agent/gui_bridge.py`
  (`InterviewRunner`, the interview-phase counterpart to `AgentRunner`) all
  built as designed. `main.py` got the confirmed toolbar toggle
  (`_draw_mode_toggle`/`_draw_manual_mode`/`_draw_agent_mode`) and now owns
  `AppState.agent_panel`, sharing `runs_panel`'s `JobRunner` instance so
  agent solves use the same cache/worker pool as manual runs.

  Since Milestone 3 deferred `ai_run_repository` persistence (no consumer
  yet), this milestone finally wired it in: `AgentRunner._persist()` writes
  every round + final status once at the end of a run (not incrementally -
  nothing currently needs to read a still-running agent run's progress from
  disk, since the GUI polls `state` directly in memory), and
  `InterviewRunner._persist_new_messages()` persists each interview turn as
  it lands.

  A real bug was found and fixed during verification, not anticipated in
  the design: `VariableSpec.enabled_features`/`excluded_fields` are Python
  `set`s, which `json.dumps()` cannot serialize - every real
  `finalize_spec()` call crashed on a background thread (silently, since
  nothing awaited that thread) until fixed. Fix: `ai_run_repository.py`
  gained a `_json_default()`/`_dumps()` helper (sets to sorted lists, not
  the project's usual `default=str` fallback, since stringifying a set
  isn't cleanly round-trippable), applied to every `json.dumps()` call in
  the module, plus a new regression test
  (`test_finalize_spec_serializes_a_real_agent_spec_with_sets`) so this
  can't silently regress.

  Verified three ways: (1) a new headless test,
  `frontend/tests/test_agent_panel.py`, exercising `AgentPanelState` exactly the
  way `test_runs_panel.py` exercises `RunsPanelState` - real interview turn
  against the real LLM server, real handoff, real FEMM solves via the
  shared `JobRunner`, then confirms both phases persisted under one shared
  `ai_run_id` and replay correctly via `get_ai_run`/`list_messages` -
  passed twice, independently, back to back. (2) The real app was actually
  launched on a live display and screenshotted (`import -window <id>`
  after root-window capture was blocked by Wayland/GNOME screen-capture
  restrictions) - confirmed the toolbar toggle, chat panel, and coil
  geometry view all render correctly. (3) George interacted with the live
  window directly (switched to AI Agent mode, started a real interview) -
  independent human confirmation alongside the automated proof.
  `draw_comparison`'s side-by-side best-vs-baseline view and
  `agent_history.py`'s browser were exercised by the automated test's
  underlying data path but not visually confirmed in the live window (no
  run had converged yet during the manual session) - worth a look next
  time a search actually finishes in front of a human.

  **A second, real-user-driven UX iteration followed the same day**, once
  George actually used the live window: (1) the chat was rendering the
  model's raw `reasoning_content` (internal scratch-thinking like "Let me
  ask in plain text, no tool call") instead of its actual `content` reply -
  `_message_display_text()`'s priority was backwards for a chat audience
  (right for `agent/optimize.py`'s progress feed, where the raw reasoning
  IS the point - two different audiences, two different fields, now
  correctly split); (2) the opening turn's unprompted multi-question wall
  of text (an LLM call fired with no user message yet) was replaced
  entirely - `InterviewRunner.start()` no longer fires an opening turn at
  all, the chat opens empty with an inviting input placeholder, and
  `interview_system_prompt()` now explicitly tells the model to propose
  promptly with sensible defaults rather than interrogating first; (3) the
  agent was auto-starting the optimize phase the instant the model called
  `finalize_spec`, with no human approval step - a real gap versus this
  project's own original "confirm the spec before solving" design
  principle. Fixed with an explicit `AgentPanelState.plan_confirmed` gate:
  a finalized spec now renders as a plan summary (built from the real
  `AgentSpec`, not trusted free text) with a "Start Search" button, and
  `poll()` only hands off to `AgentRunner` once that's clicked - sending
  another message instead re-arms the gate as a revision. Also styled the
  chat panel itself toward a modern chat-app look (rounded message
  bubbles, a pill input bar, a live elapsed-seconds "Thinking..." indicator
  so a slow real LLM turn doesn't read as hung) - the one deliberate style
  departure from the rest of the app's dense engineering-tool look, scoped
  to `draw_chat()` alone. All changes re-verified: the automated test was
  updated to assert the confirmation gate explicitly (`run_state stays
  None across several poll() calls until confirm_plan()`), passed against
  the real LLM/FEMM backend, and full regression across both suites stayed
  clean.

  **A third UX/bug pass** followed, all from real usage: (1) the message-
  priority bug wasn't fully fixed the first time - on the SAME turn that
  calls finalize_spec, `content` is typically empty, so `content or
  reasoning_content` still fell through to the raw reasoning dump exactly
  when it mattered most; fixed by checking `tool_calls` before falling
  back to `reasoning_content` at all. (2) `text_feed.draw_feed()` and
  `_draw_chat_feed()` called `set_scroll_here_y(1.0)` unconditionally every
  frame, making it impossible to scroll up to read earlier messages -
  fixed with Dear ImGui's own standard log-window idiom (only re-pin to
  the bottom if already there before this frame's content was added).
  (3) User messages had a background tint but spanned the FULL row width
  same as assistant text, which read as ambiguous in a fast back-and-forth
  ("what is the AI asking here, so confusing" - a real report) - fixed
  with an actual layout distinction: user bubbles are now narrower and
  right-aligned, assistant text stays full-width and left, matching every
  mainstream chat app's convention (position, not just color, signals who
  said what). (4) The chat input was single-line - swapped for
  `input_text_multiline` with Dear ImGui's `CtrlEnterForNewLine` flag
  (plain Enter submits, Ctrl+Enter inserts a newline - Dear ImGui's real
  built-in modifier is Ctrl, not the more common Shift+Enter convention,
  and true Shift+Enter would need custom key-callback handling this
  codebase doesn't have elsewhere). (5) The stale "Proposed plan" card
  stayed visible through an entire revision exchange instead of
  disappearing the instant a revision message was sent - fixed by
  resetting `InterviewState.finalized` (not just `plan_confirmed`) in
  `send_chat()`. (6) A real architecture gap, not a UI bug: `AgentSpec`
  only ever supported ONE primary mutual-inductance metric
  (`primary_metric: str` + `m_tolerance_pct: float`) - when a real user
  asked to match BOTH M12 and M23 simultaneously, the model got stuck
  ("the spec field only accepts one label"). Generalized end to end:
  `AgentSpec.primary_metrics: dict[str, float]` (metric -> tolerance, all
  must pass - defaults to `{"M23": 5.0}`, preserving today's behavior
  exactly for anything that doesn't ask for more), `scorer.py`'s
  `evaluate()`/`Scored.m_errors_pct`/`rank_key()`/`tool_result()` all
  updated to gate on every entry, `finalize_spec`'s schema takes a list of
  `{metric, tolerance_pct}` objects, `interview_system_prompt()` now
  explicitly tells the model this capability exists (that's exactly why
  it got confused). Verified with a direct offline check against real
  FEMM solves (a baseline passes both gates trivially against itself; a
  perturbed candidate correctly fails on both), plus the full real
  interview -> handoff -> optimize test passing again end to end. Also
  added a static "What do you want to run today?" welcome line (not
  LLM-generated) before the first message, and a one-line in-UI
  explanation of why a reply can take a while (a reasoning model
  generates a lot of hidden "thinking" before the short visible reply).

  **Real timing data, in response to a direct user question** ("which is
  slower, the AI or xfemm?"): added `RoundState.proposing_started_at`/
  `evaluating_started_at`/`done_at` (`agent/state.py`, stamped in
  `agent/optimize.py`) so every round in the Search Progress feed now
  shows a real `[AI: Xs, FEMM: Ys]` split instead of a guess. A live
  diagnostic run confirmed the AI is consistently the dominant cost
  (rounds ranged 23-67s of LLM time each) while FEMM solves stayed fast
  throughout - and separately confirmed one pytest run that failed on a
  300s timeout was NOT a stall: the same objective, re-run standalone with
  visible per-round timestamps, converged correctly in 281s (4 rounds).
  The test's timeout was bumped to 600s to match observed real-world
  variance rather than masking genuine slowness with a too-tight bound.

  **A real, confirmed scoring-architecture gap, raised directly by the
  user and traced through the actual code, not yet fixed**: today's
  objective is a fixed formula (an M-metric gate AND all-three-coils ESR
  improvement) with only its NUMBERS configurable, not its STRUCTURE.
  `_build_spec()` silently re-imposes the default M23 gate even if a user
  says they don't care about mutual inductance (falls back to the default
  whenever `primary_metrics` is falsy, which can't distinguish "not
  mentioned" from "explicitly none"), and `goal_met`/`rank_key()` in
  `scorer.py` unconditionally require and rank by ESR on all three coils
  with no way to disable that requirement. Confirmed this is unrelated to
  MCP (a transport/exposure protocol, not an objective-function concern) -
  recommended fix is scoping ESR to an optional per-coil
  `esr_requirements: dict[str, float]` and fixing the primary_metrics
  fallback to respect an explicit empty selection, rather than a full
  generic-objective rewrite. Not yet implemented - queued as follow-up
  work. (Full design since written up in `objective_generalization_plan.md`.)

  **Round cards in Search Progress + History** (user request: "the most
  important part is the proposed input changes... I want a geometry image
  ... a table ... concise reasoning ... history should show this too").
  The plain-text feed was replaced by `frontend/src/frontend/panels/
  agent_candidate_card.py`, one shared renderer used by both the live feed
  and `agent_history.py`'s replay (its own module to avoid a circular
  import - `agent_panel` already imports `agent_history`). Each candidate
  gets a compact geometry drawing (`coil_diagram.draw(..., compact=True)`,
  a new mode dropping the title/legend/footer that otherwise ate a small
  card), a "Proposed changes" field/baseline/proposed table, and a
  "Result vs. goal" metric/change/requirement/pass table with the goal-met
  line. Supporting changes: `CandidateResult` gained `candidate_coilset`
  (stored whenever `dataclasses.replace()` succeeds, so even rejected
  candidates can be drawn) and `rationale`; `propose_coilset` gained an
  optional `rationale` argument (popped off in `optimize.py` before
  `coerce_delta()`, which would reject it as an unknown field) that the
  optimize prompt now asks the model to fill with one plain sentence - the
  concise reasoning shown per candidate, instead of truncating raw
  chain-of-thought, which read as noise even when cut short (the raw text
  is still stored, shown as a one-line "Notes" fallback only for older
  runs with no rationale). `AgentRunner._persist()` now also stores each
  round's AI/FEMM seconds so History shows the same timing split.
  Older runs without stored geometry reconstruct it from baseline + delta
  (exactly what `dataclasses.replace()` built). Text passes through an
  ASCII substitution (arrows, dashes, >=) since the default font atlas
  only covers Basic Latin; M-error cells now show the signed worst-
  distance error (an abs() value with a "+" had read as "always went up").

  Verified by rendering a real ImGui window harness side by side (History
  replay of a real persisted run + live-style rounds) and screenshotting
  it, then a real interview + search run: the model filled a clean
  rationale for every candidate ("4oz copper on all three coils cuts ESR
  without touching M12, L, or diameter"; "Open turn spacing and widen
  traces on tank and RC to cut their proximity loss; loop already down"),
  geometry and timing persisted, and History rendered them correctly.

  **Real bug that verification exposed**: the interview prompt never told
  the model which M index is which coil pair, so "match tank-to-RC mutual
  inductance" was finalized as M12 (loop<->tank) instead of M23
  (tank<->RC) - the search then gated on the wrong coupling. Also visible
  in an earlier user screenshot ("M12 (TX-RX mutual inductance)"). Fixed
  by adding the explicit mapping (loop/tank = transmit side, RC = receive;
  M12 = loop<->tank, M13 = loop<->RC, M23 = tank<->RC; "tank-to-RC"/
  "TX-RX"/the power link = M23) to `interview_system_prompt()`, verified
  with a real interview: the same phrasing now finalizes `{"M23": 5.0}`.

  **Two pre-existing issues noticed, not fixed (out of scope)**: (1)
  `frontend/tests/test_job_runner.py::test_submit_runs_a_real_simulation_and_
  persists_result` is intermittently flaky - `JobRunner._run()` sets
  `status = DONE` before its `finally: _persist()` writes the row, so a
  test polling on status can read the DB a moment early (the same race
  fixed in `test_agent_panel.py` by polling for the DB row instead). (2)
  Killing the app mid-interview or mid-search leaves that `ai_runs` row
  stuck at `interviewing`/`running` forever (the daemon thread dies
  before `_persist()`), so History accumulates orphaned rows - worth a
  startup sweep that marks stale non-terminal rows as abandoned.

## Session recap - 2026-09-10

Everything below changed today (file timestamps confirm every file listed
was touched on 2026-09-10). Earlier sections of this doc hold the detail;
this is the one-page summary, plus the late-day changes not written up above.

### What was built

- **Agent core (`backend/agent/`)**
  - `llm_client.call_llm(messages, tools)` returns the message dict directly
    (the `{"message": ...}` wrapper was removed as redundant).
  - `orchestrator.py` is now a thin CLI wrapper over `AgentRunner`.
  - New modules: `variables.py` (free fields computed from the baseline's
    active features), `interview.py` (chat phase + `finalize_spec` tool),
    `state.py` / `runner.py` / `optimize.py` (pollable background search
    loop on its own daemon thread), `gui_bridge.py` (`InterviewRunner`).
  - Every FEMM solve goes through the GUI's shared `JobRunner`, so the agent
    uses the same cache (`find_cached`) and worker pool as manual runs.
  - `AgentSpec.primary_metrics: dict[str, float]` replaces the single
    `primary_metric`: several M-gates at once, all must pass.
  - The interview prompt now spells out the coil mapping (M12 = loop<->tank,
    M13 = loop<->RC, M23 = tank<->RC); "tank-to-RC" had been wrongly
    finalized as M12.
  - Per-round timing (`proposing_started_at` / `evaluating_started_at` /
    `done_at`). Answer to "which is slower?": the AI, by a wide margin.
- **Database**: `ai_runs` + `ai_run_messages` tables and
  `ai_run_repository.py`; `runs` is unchanged. Each round stores its
  candidates, their geometry and rationale, and the AI/FEMM seconds.
- **GUI (`frontend/src/frontend/`)**
  - Toolbar toggle between Manual and AI Agent layouts (`main.py`).
  - Chat (left): bubbles with user messages right-aligned, a multiline
    input (Enter sends, Ctrl+Enter adds a newline), a static welcome line, a
    "Thinking (Ns)" indicator with a note on why it's slow, and scroll
    pinning only when already at the bottom. The model asks short questions
    one at a time, each stating its default, then proposes a plan. Nothing
    runs until **Start Search** is clicked; sending another message hides
    the plan card and treats the message as a revision.
  - Right column: config picker + History toggle, baseline geometry, Search
    Progress while running, AI-run History browser.
  - `agent_candidate_card.py`: one renderer shared by live progress and
    History. Each candidate card shows compact geometry, a "Proposed
    changes" table, a "Result vs. goal" table, and a one-line reasoning.

### Late-day changes (not covered in the sections above)

- **History box** now fills the whole right column; it had been stuck at
  the geometry panel's fixed height.
- **Baseline geometry panel shortened**: 200px in agent mode (`main.py`'s
  `_AGENT_DIAGRAM_HEIGHT`). `coil_diagram.draw()` reserves the measured
  height of its legend/footer instead of a flat 130px, which had left a
  blank gap.
- **Reasoning is never truncated.** The round's raw reasoning is in a
  collapsible "Full reasoning" section, collapsed when the model wrote a
  summary and open when it didn't (older runs).
- **1-2 sentence reasoning summary.** The `rationale` argument of
  `propose_coilset` is now *required* and asks for "your reasoning,
  summarized in 1-2 sentences max: what the previous result told you and
  why this change should move the objective". It is written in the same
  tool call, so it costs no extra LLM round trip. The card label changed
  from "Why:" to "Reasoning:". This supersedes the "optional rationale" and
  "one-line Notes fallback" wording in the round-cards paragraph above.
  - Verified live (run `8b71c46ebec5`, goal met in 2 rounds). Round 2's
    summary cited round 1's numbers: "4oz got loop -19.5% and tank -10.5%
    but RC only -7.4%, so RC is the bottleneck; ..."
  - The summaries are still long (~40-45 words, one sentence). A word cap
    is an option if they need to be shorter.
- **Search Summary replaces Search Progress once a search finishes**
  (`agent_panel._draw_summary()`). When the search has a best candidate,
  one box fills the right column with:
  1. a headline (status, rounds, candidates tried, best step) and the total
     AI/FEMM time;
  2. Baseline and Best geometry side by side
     (`coil_diagram.draw(..., title=...)`);
  3. the best candidate's "Result vs. goal" table
     (`agent_candidate_card.draw_result_table`, made public);
  4. a "What changed" table;
  5. "How the search got there": one row per candidate with step, changes,
     result ("Goal met" / "Missed: ..."), and reasoning, with the winner
     marked "(best)" (`agent_candidate_card.draw_process_table`).

  While a search runs, or if it ends with no valid candidate, the live
  geometry and round-by-round feed show as before. Verified headless: the
  pane was drawn for 3 frames from the rebuilt real run, with no errors.
  It has not been seen on screen yet.

### Docs written today

- This file (the plan plus status notes).
- `objective_generalization_plan.md`: design only, **not implemented**. It
  covers arbitrary objectives over all 12 `SimResult` metrics
  (match/reduce/increase, tolerance, required vs. preferred), normalized
  margin ranking, and a structural audit that no out-of-scope field (e.g.
  ferrite on a no-ferrite baseline) changed.

### Open items

- **The running app is stale.** It was started before the late-day changes,
  so none of them have been seen live. Restart it to see them.
- **Bug: a second search after one finishes never starts.** After a search
  finishes, revising the plan and clicking Start Search does nothing until
  the config is re-picked. `poll()` only submits when `run_state is None`,
  and nothing clears a finished `run_state`.
- **The objective generalization is still to do**: the evaluator still
  requires the M-gates plus ESR reduction on all three coils (see
  `objective_generalization_plan.md`).
- **Pre-existing, not fixed**:
  - `test_job_runner.py` has an intermittent DB-write race.
  - `test_services_do_no_file_io` always fails.
  - Orphaned `interviewing`/`running` `ai_runs` rows are left behind when
    the app is killed; a startup sweep would clean them up.
  - Milestone 6 (the CLI entry-point decision) is still open.
- **Test searches added to History today**: several (headless verification
  runs, including `8b71c46ebec5`).

## Session recap - 2026-09-11

Everything below changed today (file timestamps confirm every file listed
was touched on 2026-09-11). Two other lines of work landed alongside this
session's own (visible via file timestamps, not written up in earlier
sections of this doc): the objective-generalization plan was implemented,
and an "Open in FEMM" button was added. This session's own work is the AI
Agent panel changes in the third section below.

### Objective generalization - now implemented

Yesterday's recap marked `docs/objective_generalization_plan.md` as
"design only, not implemented." It's done now: `agent/objective.py` (new)
defines `Objective`, `SIM_RESULT_METRICS`, `DERIVED_QUANTITIES`, and
`margin()`; `scorer.py`, `spec.py`, `variables.py`, `optimize.py`,
`schema.py`, `orchestrator.py`, `interview.py`, and `prompts.py` were all
reworked around it. `AgentSpec.objectives` replaces the old
`primary_metrics`/`min_esr_improvement_pct` pair with an arbitrary list of
match/reduce/increase objectives, each required or a preference, over any
of the 12 solved quantities. `variables.audit_changed_fields()` gives a
real structural check ("no out-of-scope fields changed") instead of the
old free-fields-filtered diff, which could never have shown a field
changing outside the allowed set. Covered by the new
`backend/tests/test_agent_objective.py` (12 tests, pure Python, no
LLM/FEMM needed) - passing.

### "Open in FEMM" button - new

`frontend/src/frontend/visualization/femm_open.py` (new) plus `coil_diagram.py`'s new optional
`femm_label` parameter put a button on a diagram's title row that opens
that exact geometry, at that exact distance, directly in FEMM. Wired into
Manual Runs' diagram, the AI Agent's Baseline box, and both diagrams in the
Search Summary (Baseline / Proposed). Covered by `frontend/tests/test_femm_open.py`
and `backend/tests/test_femm_launcher.py` - passing.

### AI Agent panel - this session's changes

- **Baseline box redesign.** Picking a baseline now solves it immediately
  (cache-first, the same `JobRunner` manual runs use) instead of waiting
  for a search to start. The config picker, geometry, solved results, and
  read-only inputs now share one "Baseline" box. The History toggle moved
  out of that box onto the top toolbar, next to Manual Runs/AI Agent.
- **Multi-distance baseline solving.** Once a plan is finalized, the
  Baseline box drops any distance the user didn't actually ask for (e.g.
  the 5mm default, if the objective only named 8/12mm) and solves exactly
  `spec.distances`, one result table per distance.
- **Distances capped at 5** - each one is a full extra FEMM solve per
  candidate. Enforced both in the `finalize_spec` tool schema (`maxItems`)
  and as a real server-side check in `_build_spec` (`ValueError`, same
  validation-is-feedback pattern the rest of that function already uses).
  Verified live: asked for 7, the model read the limit and picked 5 on its
  own.
- **Search Summary: a distance dropdown.** All 12 solved quantities are
  genuinely per-distance FEMM results, not one "representative" value - a
  dropdown ("Viewing distance: 5 mm") now lets the Result table and both
  diagrams recompute at any of the search's evaluated distances, straight
  from the shared FEMM cache. An objective's pass/fail is recomputed fresh
  at whichever distance is selected, not read off the search's stored
  worst-case verdict. Verified against a real 3-distance run: M23 differs
  at 5/10/15mm (1.67e-6 / 1.45e-6 / 1.26e-6 H) and the table updates to
  match.
- **Full output/input tables**, replacing the earlier objectives-only
  versions: the Result table now shows all 12 metrics (baseline/proposed/
  change), with only the objective rows highlighted and reasoned about; the
  Inputs table now shows every CoilSet field, with only the changed rows
  highlighted, grouped like the manual-run config form.
- **"Reflected in the diagram" / "Not drawn above" caption**, under the two
  geometry diagrams: names exactly which changed fields the diagram can
  and can't show. It never modeled trace width/thickness/spacing/turn
  count (see `coil_diagram.py`'s own docstring), so a trace-level-only
  search always looked identical baseline-vs-proposed - a real, reported
  confusion ("I don't even see the difference"). Trace-thickness fields are
  classified correctly too: they only move anything if a ferrite/shield/
  backplane is actually present on that side.
- **Round-by-round details** added under the live Search Summary too
  (previously only in History), collapsed by default, reusing the same
  round-card renderer History replays with.
- **History browser overhaul**: readable dates ("Sep 11, 2026 - 17:58
  UTC") instead of raw ISO timestamps; each run's ID shown in the list and
  the detail header; a search box (by run ID or coilset name); a newest/
  oldest sort toggle; "running" runs dropped from the list entirely
  (nothing to replay yet - that's the live view's job); the status tag
  simplified to just "Goal met" / "Rejected" (every non-goal-met terminal
  status collapses into the latter, with the round detail below showing
  why).
- **Proposed plan gets a table.** The plan card now shows all 12 solved
  metrics against the current baseline, with the objective's target row
  highlighted and a "Required: .../Preferred: ..." note, instead of prose
  restating the objectives in words.
- **"Best" renamed to "Proposed"** throughout the summary (diagram title,
  table columns, the step table's tag, the headline sentence).
- **Chat input redesigned**: the Send button is now a round icon (a
  triangle drawn with draw-list primitives, not a text glyph - the font
  atlas is Basic Latin only) sitting inline inside the same rounded input
  box, not a separate button row below it.
- **Real bug found and fixed**: a tool-call turn's chat bubble said
  "Done." unconditionally - even when `finalize_spec` had failed
  validation, or a plan had only been proposed and not yet confirmed as a
  search. Read live as "the simulation finished", prompting a real "wait,
  so when are you running this?". It now reads the paired tool-result
  message and says what actually happened: "I've put together a plan below
  - take a look." on success, "That didn't finalize: `<the real error>`"
  on rejection. "Done" is reserved for an actual FEMM solve completing,
  which only ever happens in Search Progress/Summary - a different part of
  the screen entirely.
- **Also fixed along the way**: the chat placeholder text was being drawn
  underneath the input box's own opaque background (now drawn first, with
  the widget's fill made transparent); and a missing `return distance` in
  `draw_search_summary()` that would have crashed the distance dropdown on
  first real use - caught by a headless smoke test before it shipped.

### How today's AI Agent changes were verified

Every change was checked headlessly (no window opened - the standing rule
this session follows) via scratchpad scripts: real `AgentPanelState`/
`AgentHistoryState` objects, a real `JobRunner` with real FEMM solves and
cache, and real saved runs from the database, rendered for several frames
with `imgui.create_context()` and no backend window. `frontend/tests/
test_agent_panel.py`, `test_runs_panel.py`, and `test_agent_candidate_card.py`
all pass.

### Open items

- **The running app is still stale** - none of today's changes have been
  seen in the actual window. Restart it to see them (ask first, per this
  session's standing rule).
- **Bug from yesterday, not re-checked today**: a second search after one
  finishes didn't restart until the config was re-picked. Unclear whether
  today's `run_state`/`summary_distance` changes affect this - worth
  re-testing before relying on it.
- Pre-existing, still not fixed: `test_job_runner.py`'s intermittent
  DB-write race; `test_services_do_no_file_io` always fails; orphaned
  `interviewing`/`running` `ai_runs` rows are left behind when the app is
  killed (a startup sweep would clean them up); Milestone 6 (the CLI
  entry-point decision) is still open.
- Several test/verification searches were added to real History today,
  across multiple runs (used to verify the History overhaul and the
  distance dropdown against real cached FEMM data).
