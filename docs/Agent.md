---
owns: backend/agent/**
last-verified: 2026-09-23
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
feeds the result back — up to 50 rounds, or until a design passes every required
objective.

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
distances                -- mm, the RC-separation sweep to evaluate at (capped, MAX_DISTANCES)
enabled_groups            -- which optional structural groups the search may touch (below)
excluded_fields          -- specific fields to pin even though they'd otherwise be free
objectives               -- list of {metric, kind, tolerance_pct, target_pct, required}
```

`_build_spec()` (plain code, not the model) validates this into a real
`input_chat/models/spec.py::AgentSpec` — at least one required objective, no duplicate
metrics, at least one field left free to search over — and rejects a bad spec back to the
model as an error to fix, never silently accepting something broken. These constraints
are also stated proactively in the prompt itself (`## Constraints your finished plan must
satisfy`), so the common ones get avoided rather than discovered by failing.

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

- **`FREE_VARIABLES`** — base winding geometry (radius, turns, layers, spacing, trace
  width, trace thickness) for all three coils — always free, no consent needed.
- **`CONSENT_GROUPS`** — ferrite (tank and RC separately), shields (TC and RC),
  backplane (TC and RC), PCB thickness — **fixed unless the user explicitly asks for
  them in the interview.** Whether the baseline already has a group active makes no
  difference: a design that already has ferrite fitted does *not* make ferrite free to
  touch by itself — the search must not change it unless told to.

The `finalize_spec` tool, the objective vocabulary (`match`/`reduce`/`increase`, per the
metric's kind), and the interview's system prompt are all rendered live for a real
baseline design by the devtools app (`devtools/`, if present) — the fastest way to see
the actual current wording rather than reading the `.txt` template.

## 5. Phase 2 — the search loop

`femm_agent/services/optimize.py::optimize_loop()`. The handoff from Phase 1 is the
`AgentSpec` object alone — the search never sees the chat transcript, which is what lets
it also run headless from the CLI with no interview at all (§10).

```
baseline_metrics = solve the baseline at every spec.distance   -- everything compares to this
loop, up to spec.max_tool_calls rounds:
  1. build the round's message: system prompt + goal + short memory (§6) + last round's
     full raw results
  2. ask the model (retried on transport failure, §8) — it may reply with up to
     spec.candidates_per_round `propose_coilset` calls, or with plain text
  3. plain text twice in a row = the model considers itself done -> STOPPED_BY_MODEL
     (once = could just be it thinking out loud -> nudged to continue)
  4. PREPARE each tool call (instant, no FEMM): fix argument types (coerce_delta),
     reject anything outside the allowed field set, build "baseline + this delta"
  5. LAUNCH every valid candidate's solves at once, across every distance -- this is
     what lets several candidates solve in parallel (Engine.md §6); an identical
     candidate proposed twice in one round is solved only once
  6. COLLECT + score each candidate in its own try/except -- one bad candidate never
     costs the others their results
  7. feed every result back to the model as that call's tool result
  8. stop if any candidate passes every REQUIRED objective -> GOAL_MET
stop if the round budget runs out -> BUDGET_EXHAUSTED
```

`propose_coilset`'s schema (`femm_agent/models/schema.py::build_tools()`) is built fresh
per run from that run's actual free-field set, with `additionalProperties: false` so the
server itself rejects any field outside it — a second, structural layer under the
`_prepare()` check. A required `rationale` argument (1–2 sentences: what the last result
showed and why this change should help) is the only reasoning a human watching the search
actually reads; it was made required after the model sometimes skipped it when it was
optional.

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
to fix its most negative margin first. `rank_key()` (used to pick the best candidate so
far) is two-tiered: candidates holding every required `match` gate rank first, ordered by
their weakest `reduce`/`increase` margin, then preferences; candidates that broke a gate
rank by how far outside the band they landed, so there's always a "best attempt" even
when nothing fully passed.

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
4. The latest round, raw — its tool calls and every full result — but **no chain of
   thought** (the model's own reasoning was ~28% of that 129 KB and isn't something a
   later round can act on directly).

So the model learns from *outcomes*, not from its own past reasoning about them, and the
prompt stays roughly flat in size as a search goes on (only the short "already tried"
lines keep growing, ~100 chars per candidate). A full example request is in
`docs/archive/agent_prompt_example.md`.

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

- **Probe** — from the best candidate so far, change *one* lever per candidate, to learn
  which lever moves which objective (a mini sensitivity study).
- **Bracket** — try the same lever in both directions.
- **Exploit** — once a lever clearly helps, try 2–3 step sizes of it.

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

`LLMError.kind` — `no_response` (time limit hit, server sent nothing — busy or still
reading the prompt), `too_slow` (tokens were still arriving), `length_limit` (hit the
token cap), `unreachable`, `http_error`, `bad_reply` (unparseable stream/tool arguments),
`aborted` (the user hit Restart).

**Retry policy (search only):** a round's request is retried once after a 5 s pause
(`round_llm_attempts`, default 2 tries total). If both fail, the whole search restarts
**from zero memory**, up to `search_attempts` (default 2) times total — the model gets
nothing from the earlier attempt, but its rounds stay visible (labeled "restart N") and
saved, and the best candidate found is kept across every attempt. If a round still fails
both tries after the last restart, the search fails. The interview and follow-up chats
are not retried — they show the reason and the user can just send again.

**End-of-run report** (`femm_agent/services/outcome.py::outcome_report()`) — for every
ending except a met goal, a plain-language explanation: which rounds/tries failed and
why (for `FAILED`), that every round was used (`BUDGET_EXHAUSTED`), that the model
stopped on its own (`STOPPED_BY_MODEL`), or that it's an internal bug rather than a
server problem. Every report ends with the closest candidate found and exactly what it
still missed. Stored in `ai_runs.error`, shown in the chat, the search summary, and
History, and handed to the follow-up chat (§9).

## 9. The follow-up chat

Once a search starts, the same chat keeps going — "how's it going?" while running, "why
did tank_esr drop?" after. It rebuilds a fresh snapshot of the live run (plan, baseline
numbers, every round, best candidate) each turn — a deliberately different, much smaller
context than the search loop's own message history, which is trimmed to its latest round
plus the best few candidates. **It cannot change anything, structurally, not just by
prompt instruction:** it's called with no tools at all, so it has no `finalize_spec` to
re-plan with and no `propose_coilset` to try a design with — the prompt just tells it to
point the user at Restart instead.

## 10. Settings — what's editable without touching code

`agent/settings/settings.py::AgentSettings` — the LLM connection (URL, model, time
limit, token cap), the search budget (max rounds, candidates per round, best-kept
count, retry counts, max distances), and the three system prompts, each **one** template
of plain text with `{{placeholder}}` fills and `{{#name}}...{{/name}}` conditional
blocks (a batch-size-dependent paragraph, ferrite/metal notes that only appear if those
fields are open, and so on) — `agent/settings/templating.py`. Only values that differ
from the code defaults (`agent/config.py`) are stored, in the `agent_settings` table. A
run copies the settings in force when it starts, so editing them mid-run never changes
that run, and History records which prompt text a given run actually used.

**Deliberately not configurable:** the tool schemas and their validation, the allowed
copper-thickness enum, the solver's geometry-validity checks, and the structural
field-allowlist audit — the plain-code safety layer from §3's third principle.

## 11. Where the actual deployment differs from the original design

`docs/archive/ai_agent_design.md` (§5.6) chose **Ollama** for the model server. The
code was written against a generic "any OpenAI-compatible endpoint" interface precisely
so this could change without a rewrite — and it has: the actual current default
(`agent/config.py`) is a **llama.cpp** server on an office GPU box
(`http://192.168.1.74:8854`), serving `Qwen3.8-Flash-Next-UD-Q4_K_XL`, launched with a
262,144-token context window. Both are settable via `LLM_API_BASE`/`AGENT_MODEL`
environment variables or the Settings screen.

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
| `agent/orchestrator.py`, `__main__.py` | CLI entry (`python -m agent`, from `backend/`) — hardcoded objective, no interview, same `optimize_loop()`/`run_interview_turn()` underneath as the GUI; the one place agent code imports `frontend.job_runner.JobRunner` as its throwaway solver pool |
| `agent/settings/settings.py` | `AgentSettings`, validation, load/save |
| `agent/settings/prompt_defs.py`, `prompts/*.txt` | The three editable system prompts and their placeholders |
| `agent/settings/templating.py` | The `{{placeholder}}`/`{{#name}}...{{/name}}` engine |
| `agent/repositories/agent_settings_repository.py` | The `agent_settings` table |
| `agent/input_chat/models/objective.py` | `Objective`, `margin()` — the pass/fail vocabulary |
| `agent/input_chat/models/spec.py` | `AgentSpec` — the one object describing a whole run |
| `agent/input_chat/models/variables.py` | Which `CoilSet` fields the model may touch (§4) |
| `agent/input_chat/services/interview.py` | Chat → `AgentSpec` |
| `agent/femm_agent/models/schema.py` | Tool JSON-schema + delta validation |
| `agent/femm_agent/models/state.py` | `AgentRunState`/`RoundState` — the search's live, polled state |
| `agent/femm_agent/services/optimize.py` | The search loop body (§5) |
| `agent/femm_agent/services/scorer.py` | Pass/fail + ranking (§5) |
| `agent/femm_agent/services/prompts.py` | Fills the search system prompt each run |
| `agent/femm_agent/services/outcome.py` | End-of-run report (§8) |
| `agent/femm_agent/services/followup.py` | Read-only Q&A about a running/finished search (§9) |
| `agent/services/llm_client.py` | Raw HTTP to the LLM server (§8) |
| `agent/services/gui_bridge.py` | `InterviewRunner` — background-thread wrapper around the interview, for the GUI |
| `agent/services/runner.py` | `AgentRunner` — background-thread wrapper around the search loop, for the GUI; runs on its own thread, never the solve pool (Engine.md §6) |
| `agent/repositories/ai_run_repository.py` | CRUD for `ai_runs`/`ai_run_messages` — the agent's own two tables, referencing the engine's `configs` table |
| `agent/*/tests/` | Each phase's own tests; `agent/tests/` is cross-phase (LLM client, run store, live reasoning) |

**Import rule:** `agent/`'s core imports `engine/`, never `frontend/` (the sole
exception is `orchestrator.py`'s CLI-only `JobRunner` borrow, above). Inside `agent/`,
`femm_agent/` imports `input_chat/`'s models (`AgentSpec`, `Objective`, `VariableSpec` are
the contract the interview hands the search) — the two exceptions running the other way
are `input_chat/models/variables.py` reusing `femm_agent/models/schema.py`'s field-schema
helpers, and `input_chat/services/interview.py` borrowing `design_values` from
`femm_agent/services/scorer.py`.
