---
owns: backend/agent/**
last-verified: 2026-10-08
---

# Agent: the AI search agent

**Owns:** `backend/agent/` — builds on top of `backend/engine/` (one-way: agent → engine)
and has no idea a GUI exists. Two phases, `input_chat/` (the interview) and `femm_agent/`
(the search), plus shared root files. For the doc index, see `docs/README.md`; for the
physics/engine it drives, see `Engine.md`; for how its output is rendered, see `GUI.md`.

---

## 1. What it does, in one paragraph

You describe a goal in your own words ("keep mutual inductance within 5% across these
distances, cut tank ESR by at least 20%"). A chat turns that into a precise, checkable
objective. Then a language model proposes coil-design changes, one to several per round;
plain code validates each one, runs it through the real physics engine, scores it, and
feeds the result back. The search does not stop at the first design that passes: it
keeps looking for a better one until code ends it — a budget limit (25 rounds, 90 minutes
or 300 simulations by default), or no real improvement for a few rounds.

## 2. Goals this serves

From `docs/goals.md` (the project's original brief): *"Setup a local AI agent with the
objective of finding a certain coilset geometry that achieves a target mutual inductance
while minimizing self-resistance. Organize code to enable it to cleanly propose and test
geometries."* Objective #1 (organize the simulate-coils code) and Objective #2 (this
agent) are both built. The two bonus objectives — simulating toroid inductors, and
including metal/ferrite in the optimization search — are only partly true: metal/ferrite
fields exist on `CoilSet` and can be opted into a search (§4), but nothing here simulates
toroid inductors; that's still open.

The concrete brief this design was actually built against (recorded in
`docs/archive/ai_agent_design.md` — the design doc this file supersedes for current
facts, kept there for its reasoning and rejected alternatives):

> Compared to the Gen2_coil set: match mutual inductance (within ~5%) across a range of
> distances; reduce ESR of each coil (especially the TC Tank); reduce/maintain outermost
> diameter and self-inductance; no ferrite/shield; same PCB thicknesses; trace thickness
> ∈ {1oz, 2oz, 4oz}.

The key reframing behind the whole design: the objective is never fixed — it's supplied
per session, in the user's own words. So this isn't "one optimizer with one scoring
function," it's "a system that turns a freeform request into a scoring function, safely,
every time."

## 3. Design principles (non-negotiable, not stylistic preferences)

1. **The objective comes from the user, per session, in natural language** — not
   hardcoded, not a fixed config.
2. **Fully offline** — any OpenAI-compatible local/LAN server, no external API calls.
3. **Hard safety/validity logic never lives in the LLM's judgment.** Geometric validity,
   which fields may change, and scoring are plain code the model cannot talk its way
   around. The only alternative considered — trusting the model to respect constraints
   because they're in its prompt — was rejected outright: *"prompted constraints are
   soft, silently droppable under context pressure, and unauditable"* — unacceptable for
   anything headed toward physical hardware.
4. **A human reviews before anything becomes final** — the parsed objective (before any
   solving starts) and the winning candidate (before it's promoted into the saved config
   library).
5. **Fits the existing single-process app** — no second service to run and debug.

## 4. Phase 1 — the interview: chat → `AgentSpec`

`input_chat/services/interview.py::run_interview_turn()`. The model is given exactly one
tool, `finalize_spec`, and a system prompt (`settings/prompts/interview_system.txt`) that
scopes it to gathering a FEMM-simulation objective and nothing else. It asks **one short
question at a time**, stating the default it would otherwise use right in the question
("Any particular distances, or default to [3, 5, 8, 12] mm?") — never a list of questions
at once, and never a silently-guessed default.

Once it has enough, it calls `finalize_spec` with:

```
objective_text          -- the user's request, verbatim, for the record
distances                -- mm, the RC-separation sweep to evaluate at (capped, MAX_DISTANCES; above 0, at most 200 mm, duplicates dropped)
excluded_fields          -- specific fields to pin even though they'd otherwise be free
objectives               -- list of {metric, kind, tolerance_pct, target_pct, required}
favourite_metric         -- optional: the one required reduce/increase objective to push
                            as far as possible (asked only when there are two or more)
```

`_build_spec()` (plain code, not the model) validates this into a real
`input_chat/models/spec.py::AgentSpec` — at least one required objective, no duplicate
metrics, at least one field left free to search over — and rejects a bad spec back to the
model as an error to fix, never silently accepting something broken. These constraints
are also stated proactively in the prompt itself (`## Constraints your finished plan must
satisfy`), so the common ones get avoided rather than discovered by failing.
`favourite_metric` must name a required `reduce`/`increase` objective of the same plan.
Whatever shape the tool arguments have (a list where an object belongs, no arguments at
all), the result is a rejection the user is asked about — never a turn that dies silently.

**A rejection triggers exactly one extra, toolless call before control returns to the
user.** The raw validation error is not shown to the user directly — `run_interview_turn()`
makes one more call with the *same* transcript (now including the error) but **no tools
offered at all**. Omitting the tool isn't a prompt request the model could ignore; the
API request itself carries no `finalize_spec` definition for that one call, so the reply
is structurally guaranteed to be plain text — the model cannot retry `finalize_spec`
itself, silently or otherwise. Its only job on that call is to turn the error into an
actual question for the user (`## If finalize_spec is rejected` in the prompt). Every
subsequent turn works exactly as before — nothing here changes how a normal
model-initiated clarifying question or a successful finalize_spec is handled.

### Relevancy guardrail

A second tool, `flag_off_topic`, is offered alongside `finalize_spec` on every interview
turn. The prompt's `## Scope` section tells the model to call it — instead of replying
normally, instead of `finalize_spec` — when the latest message isn't about this
coil-design task. If called, the user is shown a **fixed, code-authored redirect
message**, never the model's own words: the check for this happens before anything else
in the display logic, specifically so a model that writes real text in its reply
*alongside* the tool call (which does happen) can't leak that text to the user. No
`_build_spec()` attempt is made, but — same as a rejection — one more toolless call
follows immediately: the model still has the whole conversation in view (including
whatever it had already asked before the off-topic message arrived), so it naturally
continues from there instead of a fixed, context-blind line making the conversation
look like it just restarted. That follow-up reply is shown as a normal second message
right after the fixed decline.

This is a prompted rule with a code-enforced *consequence*, not a code-enforced
*trigger* — nothing stops the model from ignoring the instruction and answering an
off-topic message directly instead of calling the tool. What's guaranteed is narrower
but real: *if* the tool is called, what the user sees is always identical and never
model-authored, regardless of what else was in that reply. (An earlier design
considered a fully independent classifier call to guarantee the trigger too, using a
smaller/faster model on the same office server — rejected after live testing showed the
server keeps only one model loaded at a time, so a second model would mean swapping the
main model out and back in on every single turn, not a cheap side-check.)

**A real false positive was found and fixed live** (2026-09-23): a short, direct answer
to the model's own just-asked clarifying question ("at least 10% below the baseline
please") got flagged as off-topic. Cause: the first prompt wording asked the model to
judge "the user's latest message" without explicitly telling it to weigh that message
against its own preceding question — a short, keyword-free reply reads as unrelated if
judged in isolation. Fixed by making the prompt explicitly require judging the message
against the whole conversation (a terse reply to a pending question is on-topic even
with no coil/FEMM wording in it), raising the bar to "clearly and entirely unrelated,"
and adding an explicit "if unsure, treat it as on-topic" default. Verified live,
reproducing the exact failing conversation and two genuinely-unrelated cases (mid-chat
and as an opening message) — the fix resolved the false positive without weakening real
detection.

**A second, separate issue was found and fixed the same day:** on a genuinely off-topic
message arriving mid-conversation, the (at the time) single fixed redirect line asked a
generic opening-style question ("what would you like the search to achieve?"), even
when the model had already asked something specific (e.g. distances) right before the
interruption — reading as if the conversation had reset. Fixed by adding the toolless
follow-up call described above, plus a matching prompt section
(`## After flagging something off-topic`) telling the model to simply continue - re-ask
a pending question, don't apologize or explain the redirect itself. Verified live
through the real interview flow, reproducing the exact scenario turn by turn: after the
off-topic interruption, the model correctly re-asked the same pending distances question
instead of a generic restart.

### Which fields the model may touch

`input_chat/models/variables.py`:

Permission is one table the user owns: **Settings → Search fields**
(`AgentSettings.search_fields`), one row per numeric `CoilSet` field with an enabled flag,
an optional minimum and an optional maximum. Only rows that differ from the shipped
defaults are stored; the table is copied into the run with the rest of the settings when
Start Search is clicked, and the search reads that copy.

- **Shipped defaults (`DEFAULT_SEARCH_FIELDS`)** — the base winding geometry
  (`FREE_VARIABLES`: radius, turns, layers, spacing, trace width, trace thickness, for all
  three coils) is enabled. Ferrite (including `rc_ferrite_y_offset`), shields, backplanes,
  PCB thickness and the loss angles are disabled. The three layer fields have a maximum
  of 8, which the user can clear.
- **A field is offered to the search** when it is enabled, not in the interview's
  `excluded_fields`, and not pinned by `fixed_values` (`free_fields_for()`). What the
  baseline has fitted makes no difference.
- **Code floors, whatever the table says** — a dimension is ≥ 0, a count is ≥ 1, a flag is
  0 or 1, a copper thickness is one of the allowed weights (a thickness row's min/max
  filter that list). They apply to proposed and pinned values, not to the baseline.
- **The interview model cannot change the table.** It may call
  `propose_search_fields_change`; code validates the proposal and the chat shows it with
  **Apply** / **Dismiss**. Only the user's click writes
  (`settings.apply_search_field_changes()`); `finalize_spec` no longer has an
  `enabled_groups` argument.
- **A plan that does not fit the table is refused** by `_build_spec()` with the field
  named: a pinned value outside a limit, an open field whose baseline value is outside
  its limit, an objective on a closed design variable. The same checks run again at
  Start Search (`plan_problems()`), since the table may have been edited meanwhile.
- **Runs stored before the table existed** keep `enabled_groups` in their spec and are
  audited by the old rule (`stored_allowed_fields()` → `legacy_allowed_fields()`).
- `FIELD_GROUPS` only names sets of related fields (a group name in `excluded_fields`, the
  search prompt's ferrite / metal notes). A group grants nothing.

The `finalize_spec` tool, the objective vocabulary (`match`/`reduce`/`increase`, per the
metric's kind), and the interview's system prompt live in `backend/agent/input_chat/` and
`backend/agent/settings/prompts/interview_system.txt`.

## 5. Phase 2 — the search loop

`femm_agent/services/optimize.py::optimize_loop()`. The handoff from Phase 1 is the
`AgentSpec` object alone — the search never sees the chat transcript, which is what lets
it also run headless from the CLI with no interview at all (§10).

```
baseline_metrics = solve the baseline at every spec.distance   -- everything compares to this
loop:
  0. stop before the round if the user stopped the search or a budget limit is reached
  1. build the round's message: system prompt + goal + short memory (§6) + last round's
     full raw results
  2. ask the model (retried on transport failure, §8), with at most the time the search
     has left — it may reply with up to spec.candidates_per_round `propose_coilset`
     calls, or with plain text (once = could just be it thinking out loud -> nudged)
  3. PREPARE each tool call (instant, no FEMM): reject a call that isn't `propose_coilset`,
     whose arguments aren't a JSON object, or that changes nothing from the baseline; fix
     argument types (coerce_delta); reject anything outside the allowed field set; build
     "baseline + pins + the base design's changes + this call's values" (`base` is the
     label of an earlier simulated design, omitted = the baseline; an unknown label, or a
     step that leaves its base unchanged, is rejected). The allowed-field audit always
     compares the built design with the ORIGINAL baseline, so nothing enters through a
     base. A malformed call is only ever rejected on its own (its reason
     goes back to the model) - it never raises, so it can't cost the round's other
     candidates or the run. (An empty delta is allowed when the user pinned values: the
     pinned start point is then a different design from the baseline.)
  4. LAUNCH every valid candidate's solves at once, across every distance, in proposal
     order while the simulation budget allows -- this is what lets several candidates
     solve in parallel (Engine.md §6); an identical candidate proposed twice in one
     round is solved only once
  5. COLLECT + score each candidate in its own try/except -- one bad candidate never
     costs the others their results
  6. give each simulated design the next label (C1, C2, ... - run-wide, rejected
     proposals get none) and feed every result back as that call's tool result
  7. judge progress (code, below) and apply the stop rules
```

**The search keeps going after a design passes**, and code decides when it ends — the
model can only stop by not proposing. Stop rules, checked after every round in this
order; the first that applies is recorded as the run's `stop_reason`:

| # | Rule | `stop_reason` |
|---|---|---|
| 1 | The user stopped it (Stop and keep the best) | `user_stopped` |
| 2 | A budget limit is reached | `rounds`, `time`, `simulations` |
| 3 | A design passes and the goal has only `match` objectives (nothing to push further) | `nothing_left` |
| 4 | After a pass: `plateau_rounds` (2) rounds in a row without a better design | `plateau` |
| 5 | Before any pass: `stall_rounds` (4) rounds without getting closer — code tells the model once, listing the fields it hasn't tried; still nothing 2 rounds later | `stalled` |
| 6 | Two text-only replies in a row | `model_stopped` |

plus `llm_failed` (a round's AI request failed every try, `search_attempts` times) and
`internal_error`. The run's **status** follows from it (`state.status_for()`): `goal_met`
whenever the run has a winner (below), whichever rule ended the search; otherwise
`budget_exhausted` (rules 2), `stopped_by_model` (6), `stalled` (5), `failed`, or
`cancelled` (1).

**Meeting the goal is not yet winning: the fragility check**
(`femm_agent/services/fragility.py`). Fabrication and the solver's mesh both shift a
result by a point or two, so when a stop rule fires (any but a stop by the user), code
re-simulates the best passing design as four copies with every continuous dimension
(every `float` field except permeability, loss angle and copper thickness; counts and
flags are never touched) multiplied together by 0.98, 0.99, 1.01 and 1.02. All four must
meet every required objective at every distance. The design that survives is the
**winner** (`state.winner`), and only a winner makes a run `goal_met`.

- A design that fails is never the winner; the next-ranked passing design is checked,
  up to 5 failures in one go. A design is never checked twice, and a design ranked below
  a winner that already survived is not checked at all.
- If designs passed but none survived, and the rule that fired was not a budget limit,
  the search carries on instead of stopping: the model is told which designs failed and
  why, they are listed in its memory as "not winners", they stop counting as passes for
  the progress verdict, and the no-improvement count starts again.
- The check's solves count against the budget. So that one check can still be afforded,
  the search stops short of the limits by a reserve: 4 x distances simulations always
  (at most half the budget), and 240 s of the time limit once a design has passed (at
  most a quarter of it). The limits shown and stored are the user's own.
- If a passing design still cannot be checked (the budget ran out, or the user stopped
  the search), the run ends without a winner and the report says so and invites
  "continue", which starts with that check.
- A copy models fabrication error, not a proposal: the Search fields limits do not
  apply to it. A copy with invalid geometry, or whose solve fails, is a failed check.
- The check needs no model: a search that loses the AI server after a pass still runs
  it (`AgentRunner._run`) and can end `goal_met`.
- Ending without a winner reads "Target not achieved" with the rule that ended the
  search; one line is added when designs met the goal but none survived. A design that
  never passed is never called fragile. Every check is stored in `ai_runs.fragility_json`
  and shown on the design's card.

The search prompt tells the model the same thing (`## After a candidate meets the goal`):
keep proposing after a pass, how passing designs are compared (the score below, or the
favourite), that a change under `noise_points` is not an improvement, and that the app
ends the search. Without it the model answers in plain text at the first pass.

**Budget** (`femm_agent/models/state.py::Budget`) — rounds, wall clock, and fresh
simulations, counted for the whole search across resumes. A text-only reply uses a round.
One simulation = one fresh solve of one candidate at one distance
(`scorer.evaluate_async()` charges it); cache hits and the baseline's own solves are
free. A candidate is all or nothing: if it needs more fresh solves than are left, it and
every later proposal of that round are turned away and the search stops. No AI request
and no solve starts after the time limit: each request gets
`min(llm_time_limit_s, time left)`, a request cut short by that limit ends the search
(it is not a failed try), and solves still queued when the limit passes are dropped —
the worst overshoot is one solve already running.

**Progress** (`femm_agent/services/progress.py`) is judged by code from stored results.
One number is tracked: before any pass, how far the closest design still is (the most
points any required objective is short); after one, the best score (below). A round
improves when that number beats the last reference by more than `noise_points` (1.0 —
about the solver's own mesh noise). The verdict is `improving` while fewer than 2 rounds
have passed without an improvement, otherwise `plateau` (after a pass) or `stalled`
(before). Code also counts repeated and near-copy proposals (same fields, every value
within 2%) and lists allowed fields never changed. Neither code nor the model ever
declares a target impossible.

**Continue.** At a limit with the verdict still `improving`, the report invites the user
to reply "continue". `AgentRunner.continue_run()` starts a new *segment* of the same run:
a fresh budget (the run's own settings), the same memory, rounds appended after the
existing ones, candidates still compared with the original baseline. Each segment's
limits and spend are one entry in `ai_runs.budget_json`.

`propose_coilset`'s schema (`femm_agent/models/schema.py::build_tools()`) is built fresh
per run from that run's actual free-field set, with `additionalProperties: false` so the
server itself rejects any field outside it — a second, structural layer under the
`_prepare()` check. Each field's entry carries its limits from the run's Search fields
table (`minimum` / `maximum` / `enum`), and `coerce_delta()` enforces them: the schema is
advice to the model, the code check is the one that counts.

`_prepare()` turns one tool call into a candidate or a rejection, before any solve is
queued, in this order: over the per-round limit, tool name, argument shape, each field
(open in this run, right type, inside its limit), the build, the audit against the
baseline, something changed, and the geometry (`engine.core.validate_coilset()`, the
same check `simulate()` runs). A rejection — from `_prepare()` or from a solve that
failed later — is sent to the model in one fixed format,
`{"valid": false, "error": <code>, "field", "allowed", "detail"}`
(`schema.Rejection`). The codes are in `schema.REJECTION_DETAIL`: `over_candidate_limit`,
`unknown_tool`, `bad_arguments`, `unknown_field`, `not_enabled`, `wrong_type`,
`out_of_range`, `not_allowed_value`, `unknown_base`, `no_change`, `invalid_geometry`, `solver_failed`, `solver_timeout`; and
`simulation_limit`, `time_limit` and `search_stopped` for a proposal a limit turned away
unseen (not remembered as tried). `detail` is a fixed sentence per code (for `invalid_geometry`, the validity
check's own sentence naming the coil); the exception's text and any path go to the run
log only. The already-tried list shows a rejection as its code, field and allowed
values. A required `rationale` argument (1–2 sentences: what the last result
showed and why this change should help) is the only reasoning a human watching the search
actually reads; it was made required after the model sometimes skipped it when it was
optional. The optional `base` argument is a plain string, not an enum: the schema is built
once per run and labels are added every round, so `_prepare()` is what checks it. A stored
candidate keeps `delta` (every value changed from the baseline), `step` (only what that
call gave), `label` and `base_label`.

**Run lifecycle.** `ai_runs.status` changes only through
`agent/repositories/ai_run_repository.py::transition()`, which rejects any move not in
this table:

| From | To | Trigger |
|---|---|---|
| (none) | `interviewing` | the user's first message (opening the panel saves nothing) |
| `interviewing` | `plan_ready` | `finalize_spec` accepted by code |
| `plan_ready` | `plan_ready` | a revised plan |
| `plan_ready` | `running` | Start Search |
| `interviewing`, `plan_ready` | `cancelled` | `InterviewRunner.cancel_conversation()`; the app no longer calls it (a new chat leaves a planning chat as it is, to be reopened) |
| `running` | `goal_met`, `budget_exhausted`, `stopped_by_model`, `stalled`, `failed`, `cancelled` | the search ends (rules above) |
| `running` | `interrupted` | the app starts and finds the row still `running` (`reap_running()`) |
| any ending | `running` | "continue" |
| any ending | `plan_ready` | a revised plan after a search (not built yet) |

A stopped or cancelled run is kept with its plan and every finished round; deleting is a
separate, explicit user action. The app assumes **one instance per database**: the
startup reaper would mark another instance's live search interrupted.

**Saving a run.** Interview messages are saved as each turn lands; search rounds are saved
as each one finishes (`AgentRunner._save_rounds`, called through `optimize_loop`'s
`on_round_done` hook wherever a round ends), so a crash mid-search loses at most the round
in flight. `AgentRunState.rounds_persisted` keeps each round to one write; a database error
in the hook is logged as a `warning` and the end-of-run `_persist()` writes whatever was
missed, then the final status, `stop_reason`, the budget entries and the best candidate.
Each round's payload also carries its segment and code's verdict after it. The baseline
is copied into the run at Start Search (`baseline_coilset_json`), since a config can be
edited later. `femm_agent/services/restore.py::restore_run()` rebuilds a stored run's
live state (rounds, memory, best design, verdict) by re-requesting each candidate's
solves — cache hits — and re-scoring them. Design labels come back with it, so a
continued search can build on any earlier design; a run stored before labels existed is
labelled in stored order. The app calls it when the user asks about or
continues a chat reopened from the sidebar, then hands the state to
`AgentRunner.adopt()` so `continue_run()` accepts it. `restore_interview()` rebuilds the
chat side (transcript, plan, settings) without touching the solver, and
`InterviewRunner.attach()` points a runner at the stored row so new messages are saved
after the stored ones. `list_messages()` with no phase returns both phases in the order
they were written; `list_chat_rows()` is the light per-run query the sidebar lists from.

**Schema version.** `PRAGMA user_version` is 2. An older database is migrated the first
time it is opened (`_ensure_schema()`; the app also calls `migrate_if_needed()` at
startup): the file is copied to `backend/data/backups/`, then, from version 0, `ai_runs` is
rebuilt in one transaction with every row carried over unchanged; from version 1 the one
new column (`imported_at`) is added. A failure rolls back and stops the app
with a message naming the backup. `python -m agent.cleanup_runs` (dry run by default) is
the one-time, human-gated cleanup of rows left by the old lifecycle.

**A chat as a file.** `agent/services/chat_export.py` writes one stored chat as JSON
(`{"format": "femm-agent-chat", "version": 1, "content_sha256", "run", "messages",
"baseline_coilset"}`: the run row without its id, every message, and the baseline copy) and
as a PDF report (plan, ending, best design against the baseline, one row per candidate;
drawn with matplotlib from the stored values, nothing re-simulated). A running chat is not
exported. A chat still being planned carries its config as it is at export time; a run
that started before baselines were copied carries none. `import_chat()` stores a file as a
**new** run (`insert_imported_run()`: fresh id, `imported_at` set, one transaction) after
`validate()` has passed every check, in this order: `size`, `json`, `format`, `version`,
`checksum`, `run`, `coilset`, `messages`, `payload`. The first failure rejects the whole
file and names the check. Statuses, stop reasons, phases and roles must be in the
repository's lists; every design must have exactly `CoilSet`'s fields with finite numbers;
the keys this version reads from a round, a candidate or a result must hold the right kind
of value (keys it does not know are kept as data). The checksum catches damage, not a
deliberate edit. An import adds no config and no solve to the cache and touches no
setting; text in the file is stored and shown, never acted on. The export refuses to write
a file its own `validate()` would reject.

**An imported chat on this machine.** Opening it simulates nothing; its stored results are
shown as imported. `restore.stored_spec()` keeps its goal, distances, objectives, pins and
exclusions but replaces its settings with the local ones in full (prompts, server, budgets,
the Search fields table), so the follow-up chat and a continued search never use a prompt
or a field list from a file. `restore_run()` re-simulates and re-scores every stored design
before either; `AgentRunner.continue_run()` first runs `plan_problems()` against the local
table and refuses a plan that does not fit. `stored_baseline()` uses only the baseline the
file carried, never a local config with the same id. A chat imported before its search
started can only be read (`AgentRunner.submit()` refuses it too): planning reads the
baseline from the local config library.

### Scoring

`femm_agent/services/scorer.py`. Each `Objective` has a **kind** —
`match` (stay within ±tolerance of a target % change), `reduce`, or `increase` (change by
at least tolerance%) — and is **required** or a **preference** (only breaks ties). A
simulated quantity is checked at **every distance in the spec; the worst distance
decides.** `goal_met` is true only when every required objective passes at once — a large
win on one never compensates for missing another.

The comparable number across every objective is the **margin**:

```
match:            (tolerance_pct - |change_pct - target_pct|) / max(tolerance_pct, MIN_SCALE_PCT)
reduce:           (-change_pct - tolerance_pct) / max(tolerance_pct, MIN_SCALE_PCT)
increase:         (change_pct - tolerance_pct) / max(tolerance_pct, MIN_SCALE_PCT)
```

≥ 0 passes; more negative is worse, and — because every margin is divided by its own
tolerance — margins from *different* objectives are directly comparable (missing a 5%
band by 0.5pp is worse than missing a 50%-reduction target by 0.5pp). The model is told
to fix its most negative margin first.

A design that meets the goal is measured in plain **points past the bar**
(`objective.points()`: the margin before it is divided by the tolerance, so a 0% target
can't swamp an average). Its **score** over the required `reduce`/`increase` objectives is
`0.5 × the smallest + 0.5 × the average` (`blended_score()`) — or, when the interview
recorded a `favourite_metric`, that objective's points alone.

`rank_key()` (used to pick the best candidate so far) has three tiers: designs that meet
the goal, highest score first, preferences breaking ties (a goal with only `match`
requirements has no score, so preferences alone decide); then designs holding every
required `match` gate, ordered by their weakest `reduce`/`increase` margin, then
preferences; then designs that broke a gate, by how far outside the band they landed — so
there's always a "best attempt" even when nothing fully passed.

## 6. What the model remembers

A chat model has no memory of its own — every request re-sends everything it should
know. Early on this was done by simply appending every round, raw, forever; a batched
run's prompt reached **~129 KB by round 5** (13 KB system prompt, ~36 KB of the model's
own accumulated chain-of-thought, ~63 KB of full results) and the request timed out.
`search_memory()` rebuilds the memory from scratch each round instead of accumulating:

1. System prompt + task — unchanged.
2. The best `KEEP_BEST_CANDIDATES` (10) candidates so far, ranked by `rank_key()`, one
   line each.
3. Every other candidate already tried, on a shorter line listing only what it missed —
   so the model doesn't propose it again.
4. The lever notebook (`femm_agent/services/notebook.py`), written by code from the stored
   results: a `single` line where a design differs in one value from its starting design
   (or, failing that, from the earliest tried design it is one value away from), a
   `combined` line for a step of 2–3 values from its starting design, nothing for larger
   steps. Each line names both designs and the move in every objective's worst-distance
   result, in points. Newest `NOTEBOOK_MAX_LINES` (40) only; identical steps merge.
5. Only when the search is not improving: the free fields with no notebook line. It never
   comments on how many values a proposal changed. "Not improving" is code's progress
   verdict (`progress.py`, §5) being `stalled` or `plateau`. The one-off stalled notice
   (§5) keeps its own list: the fields no proposal has changed at all.
6. The latest round, raw — its tool calls and every full result — but **no chain of
   thought** (the model's own reasoning was ~28% of that 129 KB and isn't something a
   later round can act on directly).

Lines 2–4 name each simulated design by its label, e.g. `C7 (round 3, from C3)`.

So the model learns from *outcomes*, not from its own past reasoning about them, and the
prompt stays roughly flat in size as a search goes on (only the short "already tried"
lines keep growing, ~100 chars per candidate). A full example request is in
`docs/archive/agent_prompt_example.md`.

The tried list lives on the run state (`AgentRunState.tried`), not in the loop, so it
survives a resume after a failed round and a "continue": the first request afterwards has
no raw latest round, and every candidate tried so far is in the one-line memory instead.
When the search stalls before any pass, code adds one message after that round's results:
"No improvement in N rounds. Fields not tried yet: ...".

## 7. Batch proposals: why several candidates per round

`CANDIDATES_PER_ROUND = 5` (`agent/config.py`, overridable in `AgentSettings`). Real
timing data from this project's own run history is the reason this exists:

| | Median |
|---|---|
| LLM "thinking" per round | ~77–141 s (varies by server load and model) |
| FEMM solve, one candidate, all its distances (already parallel) | ~6–20 s |

**~90% of a round is spent waiting on the model, not on physics.** One 77–140 s model
call that yields 5 candidates is worth roughly 5× a call that yields 1 — batching, not
faster solving, is the actual lever on wall-clock search time. The trade-off: candidates
proposed in the same round can't learn from each other (design #2 is chosen before design
#1's result exists), so the prompt asks each batch to be deliberately different, not near
copies:

- **Probe** — set `base` to the best design's label and change *one* lever per candidate,
  to learn which lever moves which objective (a mini sensitivity study; each such step
  becomes a `single` line in the notebook, §6).
- **Bracket** — try the same lever in both directions.
- **Exploit** — once a lever clearly helps, try 2–3 step sizes of it.

The cap is a ceiling, not a target: the prompt says to propose only as many candidates as
there are distinct, useful ideas — several probes early, fewer once the levers are known or
a candidate is close to the goal — because every extra candidate costs a full simulation
and stays in the model's context. (It used to say "use all N unless confident fewer would
help", which made the cap the default.)

Each result is matched back to its proposal via `tool_call_id`, the same mechanism the
OpenAI tool-calling format uses to pair several results with several calls in one reply.
Concurrent solving itself (Engine.md §6) barely speeds up any *one* round further on a
small machine — 5 candidates × 4 distances still takes ~4–5 "waves" through 4 cores
either way — but it costs almost nothing to write, scales automatically on more cores,
and lets Cancel drop a whole batch's queued solves at once.

## 8. LLM request handling

`agent/services/llm_client.py::call_llm()` — plain HTTP to any OpenAI-compatible
`/v1/chat/completions` server; nothing here knows what a coil is. Every request is
**streamed**, for two reasons found in practice: a hard time limit can only be enforced
by *closing the connection* (a non-streaming request keeps running on the server after
the client gives up, slowing whatever runs next — one request once ran ~63 minutes
against a 600 s socket timeout before this was fixed), and streaming lets a caller show
the model's live chain-of-thought while it "thinks" for minutes before any tool call, so
a stalled-looking round is distinguishable from a genuine hang.

| Limit | Setting |
|---|---|
| Wall-clock time per request (queue + read + generate), enforced even if the server sends nothing | `llm_time_limit_s` (default 900 s) |
| Reply length, thinking included — a runaway reply that hits this counts as a failed try | `llm_max_tokens` (default 32,000) |

**Sampling is sent explicitly** with every request — `temperature`, `top_p`, `seed`
(settings of the same names; defaults 1.0 / 0.95 / -1, the office server's own defaults,
where seed -1 means a new random seed per request). A run therefore doesn't change when
the server's defaults do. The request also asks for a usage block
(`stream_options.include_usage`), which is where the prompt and reply token counts come
from; with llama.cpp's `timings` as the fallback, and none at all if the server sends
neither. `build_payload()` is the one place the request body is assembled.

**Run log** (`agent/services/run_log.py`) — a developer tool; the app writes it and
never reads it. One JSON-lines file per run, `backend/data/agent_logs/<ai_run_id>.jsonl`
(git-ignored), written through the `log` argument of `call_llm()` by all three phases:

| `event` | What it holds |
|---|---|
| `run_start` | git commit, the full settings snapshot, the spec, the baseline coilset, the search tool schema |
| `llm_call` | phase (`interview`/`search`/`followup`), round / search attempt / try, server, model, sampling, limits, SHA-256 of the system prompt and of the tool schema, the **full messages sent**, the **full reply** (reasoning included) or the error (kind, detail, and what had arrived), sizes, token counts, timings |
| `warning` | where, a message, and the full exception text — the only place a raw exception is written (a round that couldn't be saved, a solve that failed, an internal error) |
| `run_end` | one per search segment: status, `stop_reason`, segment, rounds, fresh simulations and cache hits (both include the baseline's own solves), the segment's budget entry (limits and spend, baseline free), failed AI tries, seconds |

A log write that fails never fails the request: it is reported once on stderr and the run
carries on. `python -m agent.report` (from `backend/`) summarises runs from the database
(opened read-only) and these logs — per-run and cross-run tables, `--run ID` for one run
round by round, and `--run ID --request N` to print round N's exact request body rebuilt
from the log, ready to re-send.

`LLMError.kind` — `no_response` (time limit hit, server sent nothing — busy or still
reading the prompt), `too_slow` (tokens were still arriving), `length_limit` (hit the
token cap), `unreachable`, `http_error`, `bad_reply` (unparseable stream/tool arguments),
`aborted` (the user stopped the search or restarted).

**Retry policy (search only):** a round's request is retried once after a 5 s pause
(`round_llm_attempts`, default 2 tries total). If both fail, the round has failed and the
search **resumes with its memory kept** — everything tried so far is still sent, the
budget keeps counting — until `search_attempts` (default 2) rounds have failed that way;
then the search ends with `llm_failed`. A design that already met the goal is still the
result. The interview and follow-up chats are not retried — they show the reason and the
user can just send again.

**End-of-run report** (`femm_agent/services/outcome.py::outcome_report()`) — written by
code for every ending. With a passing design: which rule ended the search and the
design's score. Without one: "Target not achieved", the limit or rule that ended it, and
the closest candidate with exactly what it still missed; for `stalled`, also the repeat
and near-copy counts and the fields never changed; for `llm_failed`, which rounds and
tries failed and why. At a budget limit with the verdict still `improving`, it invites
"continue". It never says a target is impossible. Stored in `ai_runs.error`, shown in the
chat, the search summary, and a reopened chat, and handed to the follow-up chat (§9).

## 9. The follow-up chat

Once a search starts, the same chat keeps going — "how's it going?" while running, "why
did tank_esr drop?" after. It rebuilds a fresh snapshot of the live run (plan, baseline
numbers, every round, best candidate) each turn — a deliberately different, much smaller
context than the search loop's own message history, which is trimmed to its latest round
plus the best few candidates. **It cannot change anything, structurally, not just by
prompt instruction:** it's called with no tools at all, so it has no `finalize_spec` to
re-plan with and no `propose_coilset` to try a design with — the prompt just tells it to
point the user at a new chat instead. Its snapshot includes the budget used, code's progress
verdict and the end-of-run report, so "why did it stop?" is answered from what code
recorded, with no extra model call when a search ends.

**"continue" is not a model decision.** When a finished search's chat receives a message
that is exactly the word "continue" (any case), the panel records it and calls
`AgentRunner.continue_run()` (§5); the Continue button calls the same function. Any other
message goes to the follow-up model.

## 10. Settings — what's editable without touching code

`agent/settings/settings.py::AgentSettings` — the LLM connection (URL, model, time
limit, token cap, and the sampling values `temperature` / `top_p` / `seed` of §8), the search budget (max rounds, total time and simulation limits,
plateau and stall lengths, the noise threshold `noise_points`, candidates per round,
best-kept count, retry counts, max distances), and the three system prompts, each **one** template
of plain text with `{{placeholder}}` fills and `{{#name}}...{{/name}}` conditional
blocks (a batch-size-dependent paragraph, ferrite/metal notes that only appear if those
fields are open, and so on) — `agent/settings/templating.py`. It also holds the Search
fields table of §4 (`search_fields`: which fields a search may change, and their limits). Only values that differ
from the code defaults (`agent/config.py`) are stored, in the `agent_settings` table. A
run copies the settings in force when it starts, so editing them mid-run never changes
that run, and History records which prompt text a given run actually used.

**Deliberately not configurable:** the tool schemas and their validation, the code
floors under the Search fields table (dimension ≥ 0, count ≥ 1, the allowed
copper-thickness enum), the solver's geometry-validity checks, and the structural
field-allowlist audit — the plain-code safety layer from §3's third principle.

## 11. Where the actual deployment differs from the original design

`docs/archive/ai_agent_design.md` (§5.6) chose **Ollama** for the model server. The
code was written against a generic "any OpenAI-compatible endpoint" interface precisely
so this could change without a rewrite — and it has: the actual current default
(`agent/config.py`) is a **llama.cpp** server on an office GPU box
(`http://192.168.1.74:8854`), serving `Qwen3.8-Flash-Next-UD-Q4_K_XL`, launched with a
262,144-token context window. Everyone shares that one server. Both are settable via
`LLM_API_BASE`/`AGENT_MODEL` environment variables or the Settings screen.

## 12. Design decisions worth knowing (condensed; full trade-off tables in
`docs/archive/ai_agent_design.md` §5)

- **The LLM proposes candidates directly** (tool calls), not a classical numeric
  optimizer (Bayesian optimization/genetic algorithm) with the LLM bolted on the side.
  Traded weaker sample-efficiency (LLMs aren't principled numeric optimizers) for
  inspectability (every proposal comes with a stated English reason) and much lower
  implementation complexity. A hybrid — a cheap surrogate regressor as an *additional*
  tool once enough run history exists — was deferred, not rejected (`§6, Phase C` there).
- **Freeform prompt → LLM-parsed spec**, not a structured form, because that's how the
  objective is actually described in practice; the reliability gap this trades in is
  closed by the confirmation step before anything solves (§4).
- **Batched, concurrent proposals**, not strict one-at-a-time — a direct consequence of
  the engine's concurrency already existing (§7); the cost is candidates within one batch
  can't react to each other, mitigated by the probe/bracket/exploit prompt guidance.
- **Lexicographic ranking** (required gate, then priority-ordered secondary objectives),
  not a weighted sum or a Pareto front — the most literal reading of how objectives are
  usually phrased ("especially the tank"), cheapest to implement and explain; flagged for
  revisiting once real ranked output existed to look at.
- **A subpackage of the existing app**, not a separate workspace member — keeps a single
  process, at the cost of the app's dependency footprint growing to include an LLM
  client.
- **MCP (Model Context Protocol) — considered, deferred, not rejected.** MCP standardizes
  the *interface* to a tool, not the reasoning loop around it; it's a second way to
  expose the same underlying functions (`check_valid_coilset`, `simulate`, `get_coilset`,
  `save_config`) to a different client (e.g. Claude Code driving a search from a
  terminal), not a replacement for this loop. Nothing here is built in a way that would
  need to change to add an MCP wrapper later if a concrete second client shows up.

## 13. Known concerns / open questions

From ad hoc stress testing (full report: `docs/archive/agent_stress_test_report.md`) and
this project's own running notes — worth checking current status against the code before
relying on any of these:

- Sample-efficiency of LLM-driven search in practice is still mostly judged by watching
  real runs, not measured systematically.
- Whether the search loses useful signal by dropping chain-of-thought from memory (§6) —
  plausible in principle, not directly measured.
- Worker/LLM-concurrency limits are tuned informally (see Engine.md §6's "Known limits").
- The bonus goal of simulating toroid inductors (§2) is not built.

## 14. Quick file reference

| Path | Role |
|---|---|
| `agent/config.py` | Default value of every tunable number — what `AgentSettings` starts from |
| `agent/orchestrator.py`, `__main__.py` | CLI entry (`python -m agent`, from `backend/`) — hardcoded objective, no interview, same `optimize_loop()`/`run_interview_turn()` underneath as the GUI; imports `frontend.services.job_runner.JobRunner` as its throwaway solver pool |
| `agent/report.py` | Command-line run report from the database and the run logs (§8) |
| `agent/benchmark.py` | Fixed goals × repeats against the real LLM server; results in `backend/data/benchmark/<label>/`, summary in `docs/summarize_result.md` |
| `agent/settings/settings.py` | `AgentSettings`, validation, load/save |
| `agent/settings/prompt_defs.py`, `prompts/*.txt` | The three editable system prompts and their placeholders |
| `agent/settings/templating.py` | The `{{placeholder}}`/`{{#name}}...{{/name}}` engine |
| `agent/repositories/agent_settings_repository.py` | The `agent_settings` table |
| `agent/input_chat/models/objective.py` | `Objective`, `margin()` — the pass/fail vocabulary |
| `agent/input_chat/models/spec.py` | `AgentSpec` — the one object describing a whole run |
| `agent/input_chat/models/variables.py` | Which `CoilSet` fields the model may touch (§4) |
| `agent/input_chat/services/interview.py` | Chat → `AgentSpec` |
| `agent/femm_agent/models/schema.py` | Tool JSON-schema + delta validation |
| `agent/femm_agent/models/state.py` | `AgentRunState`/`RoundState` — the search's live, polled state; `Budget`; `status_for()` |
| `agent/femm_agent/services/progress.py` | Code's verdict on a search: improving / stalled / plateau (§5) |
| `agent/femm_agent/services/restore.py` | Rebuilds a stored run's live state from the database (§5) |
| `agent/cleanup_runs.py` | One-time, human-gated cleanup of rows from the old run lifecycle (§5) |
| `agent/femm_agent/services/optimize.py` | The search loop body (§5) |
| `agent/femm_agent/services/scorer.py` | Pass/fail + ranking (§5) |
| `agent/femm_agent/services/notebook.py` | The lever notebook and the untried-fields list (§6) |
| `agent/femm_agent/services/prompts.py` | Fills the search system prompt each run |
| `agent/femm_agent/services/outcome.py` | End-of-run report (§8) |
| `agent/femm_agent/services/followup.py` | Read-only Q&A about a running/finished search (§9) |
| `agent/services/llm_client.py` | Raw HTTP to the LLM server (§8) |
| `agent/services/run_log.py` | `RunLog` — the per-run JSON-lines log of every AI request and warning (§8) |
| `agent/input_chat/services/runner.py` | `InterviewRunner` — background-thread wrapper around the interview, for the GUI |
| `agent/femm_agent/services/runner.py` | `AgentRunner` — background-thread wrapper around the search loop, for the GUI; runs on its own thread, never the solve pool (Engine.md §6) |
| `agent/repositories/ai_run_repository.py` | CRUD for `ai_runs`/`ai_run_messages` — the agent's own two tables, referencing the engine's `configs` table; `transition()`, the schema version and its migrations, `insert_imported_run()` (§5) |
| `agent/services/chat_export.py` | A chat as a file: JSON export, the import checks, import as a new run, the PDF report (§5) |
| `agent/*/tests/` | Each phase's own tests; `agent/tests/` is cross-phase (LLM client, run store, live reasoning) |

**Import rule:** `agent/`'s core imports `engine/`, never `frontend/` (the sole
exceptions are `orchestrator.py`'s and `benchmark.py`'s command-line-only `JobRunner` borrow, above). Inside `agent/`,
`femm_agent/` imports `input_chat/`'s models (`AgentSpec`, `Objective`, `VariableSpec` are
the contract the interview hands the search) — the two exceptions running the other way
are `input_chat/models/variables.py` reusing `femm_agent/models/schema.py`'s field-schema
helpers, and `input_chat/services/interview.py` borrowing `design_values` from
`femm_agent/services/scorer.py`.
