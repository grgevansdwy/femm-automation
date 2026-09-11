# Objective #2 — Design: a prompt-driven local AI agent for coilset optimization

**Status: design only, nothing implemented yet.** This document lays out the
proposed architecture for `docs/goals.md`'s Objective #2 ("setup a local AI
agent... to cleanly propose and test geometries"), the reasoning behind each
major choice, the alternatives considered and rejected (or deferred), and
what's still genuinely open. Written so someone other than the author can
pick this up, agree, disagree, or redirect it — not a record of a decision
already locked in.

---

## 1. Problem statement

Per `docs/goals.md`, Objective #2 is: *"Setup a local AI agent with the
objective of finding a certain coilset geometry that achieves a target
mutual inductance while minimizing self-resistance. Organize code to enable
it to cleanly propose and test geometries."*

In practice, the objective is never that generic. A concrete example from
George:

> Compared to the Gen2_coil set:
> **Primary:** match mutual inductance (within ~5%?) across a range of
> distances; reduce ESR of each coil (especially the TC Tank).
> **Secondary:** reduce/maintain outermost diameter (especially TC Tank and
> RC); reduce/maintain self-inductance (especially TC Tank and RC).
> **Constraints:** no ferrite; no external metal/shield; same PCB
> thicknesses; trace thickness ∈ {1oz, 2oz, 4oz}.

The next coilset will have a different baseline, different tolerances,
different emphasis (maybe RC diameter matters more than tank ESR next
time), maybe different hard constraints. **The objective function is not a
fixed thing to hardcode — it's an input the user supplies per session, in
their own words.** That reframes the problem: this isn't "build one
optimizer with one scoring function," it's "build a system that turns a
freeform engineering request into a scoring function, safely, every time."

That reframing is what drives most of the design below.

## 2. Where this sits in the actual codebase today

The repo has two live packages plus one retired one:

- **`coil_simulation/`** — the simulation engine (models/repositories/
  services layers). Entry point: `engine.simulate(coilset, distance) ->
  SimResult`, one coilset + one RC separation distance in, self/mutual
  inductance + ESR + coupling out. Backed by `femmcli`, a vendored native
  Linux xfemm binary.
- **`gui/`** — the live desktop UI: a native Dear ImGui app (`imgui_bundle`
  / HelloImGui), one process, one window, one fixed 3-column dashboard
  (config list / edit form / diagram+run+history). `gui/src/gui/
  job_runner.py` runs solves on a `ThreadPoolExecutor(max_workers=
  os.cpu_count())` — **solves already run in parallel** — and caches
  results by `(coilset, distance)` hash (`coilset_signature()`/
  `find_cached()`), so re-evaluating a previously-seen geometry+distance is
  free.
- **`legacy/webapp`, `legacy/noelle`** — the prior FastAPI/Jinja2 web app
  and its simulation package, retired in favor of the two above.
  `docs/gui.md` covers why (mainly: two-process debugging pain, HTTP round
  trips for what's really local function calls). Not relevant to this
  design except as a reminder that this is the second UI generation, and
  the agent should be built against the current one.

No optimizer, agent, or LLM-integration code exists anywhere yet.

## 3. Design goals (what any architecture here has to satisfy)

1. **Objective comes from the user, per session, in natural language.**
   Not configurable-but-fixed; genuinely different every time.
2. **Fully offline.** No external API calls — an open-weight model on local
   hardware. Confirmed with George; matches the repo's zero `.env`/API-key
   infrastructure today.
3. **Hard safety/validity logic never lives in the LLM's judgment.**
   Geometric validity, hard constraints (no ferrite, etc.), and scoring are
   plain code the LLM cannot talk its way around — it proposes, code
   decides what's valid and what wins.
4. **A human reviews before anything becomes "the answer."** Both the
   parsed objective (did the system understand the request correctly?) and
   the winning candidate (before it's promoted into the config library or
   heads toward hardware).
5. **Fits the existing app**, which was deliberately rebuilt as a single
   process with a minimal dependency footprint (`docs/gui.md`). A design
   that reintroduces a second process/service undoes that work.

## 4. Chosen architecture

Two-stage, both stages living in a new `gui/src/gui/agent/` subpackage, UI
surfaced as a new panel in the existing dashboard.

```
 ┌───────────────────────────────────────────────────────────────────┐
 │  Ollama (localhost HTTP, offline) — tool-calling-capable model      │
 └───────────────────────────┬─────────────────────────────────────┘
                              │ tool calls / responses
 ┌───────────────────────────▼─────────────────────────────────────┐
 │  gui/src/gui/agent/  (new subpackage)                             │
 │                                                                     │
 │  1) interpreter.py — prompt → structured Spec (baseline, primary/  │
 │     secondary objectives w/ tolerances, hard constraints)          │
 │     -> shown to user in a new agent panel for confirmation         │
 │                                                                     │
 │  2) orchestrator.py — batch agent loop against the confirmed Spec: │
 │     LLM proposes a BATCH of CoilSet deltas → check_valid_coilset() │
 │     (free, per-delta) → simulate() at each required distance,      │
 │     submitted through the EXISTING JobRunner/ThreadPoolExecutor    │
 │     (parallel, cached) → scorer.py ranks the batch vs. baseline →  │
 │     ranked results + reasoning fed back to the LLM for next batch  │
 │                                                                     │
 │  3) scorer.py — new, pure code. Computes candidate-vs-baseline     │
 │     relative deltas (%M error per distance, %ESR change, %diameter,│
 │     %self-L) and hard-constraint pass/fail. Never delegated to the │
 │     LLM's judgment.                                                │
 │                                                                     │
 │  4) schema.py — the ~20-25 geometry fields exposed as the LLM's    │
 │     delta tool schema, minus whatever the Spec's hard constraints  │
 │     fix (e.g. PCB thickness excluded when "keep same" is set)      │
 └───┬─────────────────────────────────────────────┬─────────────────┘
     │ reuses                                        │ reuses
     ▼                                                ▼
 ┌─────────────────────────┐          ┌───────────────────────────────┐
 │ services/femm_service.py │          │ gui/src/gui/job_runner.py      │
 │ check_valid_coilset()    │          │ JobRunner + ThreadPoolExecutor │
 │ (reused as-is)           │          │ + coilset_signature() cache    │
 └─────────────────────────┘          │ (reused as-is)                 │
                                        └───────────────┬─────────────┘
                                                          ▼
                                        engine.simulate(coilset, distance)
```

### 4.1 Interpreter — prompt to structured `Spec`

Takes a freeform prompt (George's Gen2 example) and produces:

```
Spec:
  baseline_config_id: str            # e.g. "Gen2_coil"
  distances: list[float]             # mm, the sweep to evaluate M-matching across
  primary: list[Objective]           # pass/fail gate, e.g. {metric: M, tolerance_pct: 5, direction: match}
  secondary: list[Objective]         # ranking/tie-break, e.g. {metric: ESR, coil: tank, direction: minimize}
  hard_constraints: dict             # structural fields fixed to specific values
```

Rendered back to the user for confirmation before any solving starts —
vague inputs ("maybe 5%?") get a stated, editable default instead of a
silent permanent guess.

### 4.2 Orchestrator — batched propose/evaluate/score loop

This is the **evaluator-optimizer** agent pattern: propose (batch) →
evaluate (parallel, cached) → aggregate/rank → feed results back as the
next input → repeat. In plain terms:

```
loop:
  1. LLM sees the Spec + ranked results so far
  2. LLM proposes a BATCH of candidate geometry deltas (not one at a time)
  3. each delta checked with check_valid_coilset() — free, rejects junk
     before any solve is spent on it
  4. valid candidates built via dataclasses.replace(baseline, **delta),
     evaluated at every required distance through the app's EXISTING
     JobRunner — these all solve concurrently on its thread pool, and
     reuse find_cached() automatically for any repeated geometry+distance
  5. scorer.py ranks the batch against the baseline
  6. ranked results + reasoning go back to the LLM as the next input
stop when:
  - convergence — best candidate(s) inside the primary tolerance, with no
    further improvement in the secondary ranking for N consecutive batches
  - OR budget — a hard cap on total simulate() calls for the session
    (the actual expensive resource), whichever fires first
```

Either stop condition hands control back to the user with whatever was
found — the loop never runs open-ended.

### 4.3 Field mapping — proof the current schema already covers the example

No new `CoilSet` fields are needed for George's example constraints:

| Requirement | Field(s) in `coil_simulation/models/coilset.py` |
|---|---|
| No ferrite | `tank_ferrite_present=0`, `rc_ferrite_present=0` |
| No external metal/shield | `backplane=0`, `rc_backplane=0`, `tc_shield=0`, `rc_shield=0` |
| Keep same PCB thickness | `loop_PCB_thickness`/`tank_PCB_thickness`/`rc_PCB_thickness` fixed to baseline, excluded from the delta schema |
| Trace thickness ∈ {1,2,4oz} | `loop_trace_thickness`/`tank_trace_thickness`/`rc_trace_thickness` (float, mm), restricted to a 3-value enum (1oz≈0.035mm, 2oz≈0.07mm — Gen2_coil.json's own value, 4oz≈0.14mm) |

`Gen2_coil` already exists as a loadable built-in config
(`coil_simulation/data/configs/Gen2_coil.json`), so "compared to Gen2_coil"
resolves directly via `coilset_repository.get_coilset("Gen2_coil")`. The
one real gap: **nothing in the repo today computes a candidate-vs-baseline
relative comparison** — that's genuinely new, but small (a scoring
function, not new physics or new fields).

### 4.4 Human-in-the-loop checkpoints

1. Confirm the parsed Spec before any solving runs.
2. Review the ranked batch — field-level diff vs. baseline, relative
   metrics, and the LLM's stated reasoning — before promoting a candidate
   into the config library via the existing `save_config()` (same path
   `configs_panel` already uses for Clone).

---

## 5. Design decisions and trade-offs

This is the part George asked to see compared honestly, not just justified.

### 5.1 LLM drives the search loop, vs. a classical optimizer

**Chosen: the LLM proposes candidates directly** (via tool calls), not a
classical optimizer with the LLM bolted on the side.

| | LLM-driven (chosen) | Classical optimizer (Bayesian opt / genetic algorithm) | Hybrid (BO core, LLM as interpreter + narrator) |
|---|---|---|---|
| Sample efficiency on a smooth numeric objective | Weaker — LLMs aren't principled numeric optimizers; no guaranteed exploration/exploitation balance | Strong — this is exactly what BO/GA are built for | Strong (inherits BO's efficiency) |
| Handles genuinely different objectives per session | Natural — the LLM reads the Spec and reasons about it directly | Needs a fresh scoring function wired up each time (same problem this whole design exists to avoid) unless paired with an interpreter anyway | Natural, same interpreter either way |
| Inspectability | High — the LLM states *why* it proposed something, in English, every batch | Low — a black-box acquisition function, no narrative | High for the interpretation, low for why BO chose a specific point |
| Implementation complexity | Lower — no new numerical optimization code, just tool-calling plumbing | Higher — needs a real BO/GA implementation (or a dependency), careful handling of mixed discrete/continuous/categorical geometry variables | Highest — both an LLM layer and a BO layer to build and keep in sync |
| Local-model risk | An 8B–30B-class local model's tool-calling reliability and reasoning quality are unproven for this specific task — real risk of noisy or repetitive proposals | None — deterministic, well-understood algorithms | Same local-model risk, but confined to interpretation rather than the search itself |

**Why LLM-driven was chosen anyway:** George was explicit about this —
*"You tell me the LLM structure, I don't ask you to do classical
optimizer."* Beyond honoring that directly, the inspectability point is
real and matters for this use case: an engineer reviewing a proposed coil
geometry before it goes near hardware benefits from "I increased the tank
turn count because ESR was over budget and diameter had headroom" in a way
a bare BO acquisition value never gives them. The efficiency risk is real
and unresolved — noted in §7 as something to watch once real batches run.

**A middle path exists and is worth keeping in view, not building now:**
Phase C (§6) already anticipated a cheap surrogate regressor once enough
run history accumulates — that's the natural place a BO-style
acquisition function could later live *underneath* the LLM (LLM still
proposes and narrates, but a `suggest_promising_region()` tool backed by
the surrogate nudges it), without giving up inspectability. Not needed for
a first version.

### 5.2 Natural-language interpreter, vs. a structured objective form

**Chosen: freeform prompt → LLM-parsed structured Spec**, with a
confirmation step, rather than a form the user fills in directly.

| | Prompt + LLM interpreter (chosen) | Structured form (dropdowns/fields for metric, direction, tolerance, constraints) |
|---|---|---|
| Matches how George actually described the objective | Yes — the Gen2 example is a paragraph, not a filled-out form | No — would require translating prose into form fields by hand every session |
| Reliability | Parsing errors possible (misread tolerance, wrong coil, missed constraint) — mitigated by the confirmation step, not eliminated | No parsing step, no ambiguity |
| Speed for a repeat/simple request | Slower (LLM call + review) | Faster — direct input |
| Extensibility to objectives nobody designed a field for | High — natural language covers cases a form's schema didn't anticipate | Low — a new kind of objective needs a UI change |

**Why the prompt approach was chosen:** this is the specific thing George
asked for ("what if there is an LLM... and the user can just prompt what
they want"), and the reliability gap is exactly what the confirmation
checkpoint (§4.4) exists to close — nothing acts on the parsed Spec until
the user has seen and can correct it. A structured form remains available
as a fallback if interpretation proves unreliable in practice; the Spec's
internal shape doesn't change either way, only how it gets filled in.

### 5.3 Batched, parallel proposals, vs. strict one-candidate-per-turn

**Chosen: the LLM proposes a batch per turn**, all evaluated concurrently.

This is a direct consequence of §2: `gui`'s `JobRunner` already parallelizes
`femmcli` solves across CPU cores. A strictly serial "propose one → wait for
the solve → propose the next" loop (which is what the *old* Wine-based
backend would have forced, back when this design assumed `webapp`) would
leave that parallelism unused and make sessions much slower wall-clock for
no benefit. The trade-off: a serial loop lets the LLM react to one result
before deciding the next move, which is in principle more sample-efficient
reasoning per candidate. Given the LLM-driven approach's efficiency is
already uncertain (§5.1), batching trades a little of that potential
per-candidate reasoning quality for a large, certain wall-clock win. Worth
revisiting if real sessions show the LLM proposing poor batches because it
can't react within a batch — e.g. shrinking batch size, or making a batch
size of 1 configurable per session.

### 5.4 Strict lexicographic ranking, vs. weighted-sum or Pareto front

**Chosen (as a starting default): primary objectives are a hard pass/fail
gate; among survivors, rank by secondary objectives in stated priority
order** (e.g. tank ESR first, per George's "especially the TC Tank").

| | Lexicographic (chosen default) | Weighted sum (single blended score) | Pareto front (show the trade-off curve, user picks) |
|---|---|---|---|
| Matches how George phrased it | Yes — "primary" / "secondary" / "especially X" reads as a priority order, not a blend | Requires inventing weights nobody specified | No — assumes the user wants to browse trade-offs, not get a ranked answer |
| Risk of a good candidate losing to a technicality | Higher — a candidate 0.1% over an ESR "tie-break" loses to one with worse diameter if diameter was checked first | Lower — a bad score in one dimension is smoothed by good scores elsewhere | N/A — nothing is discarded |
| Implementation complexity | Low | Low (once weights exist) | Higher — needs a UI to present a front, not just a ranked list |

**Why lexicographic was chosen as the default, not the final word:** it's
the most literal reading of George's own language and the cheapest to
implement and explain. It's flagged explicitly in §7 as something to
revisit once real ranked output exists to look at — a weighted-sum or even
a small Pareto-front view (the ranked-results panel could plausibly grow
into this later) are both easy to layer in without changing the Spec's
shape, since `primary`/`secondary` objectives are already structured data,
not baked-in logic.

### 5.5 New subpackage inside `gui/`, vs. a separate uv workspace member

**Chosen: `gui/src/gui/agent/`, not a new `optimizer/` workspace member.**
Decided directly with George. Trade-off acknowledged: `docs/gui.md`
explicitly called out `gui`'s dependency set (`imgui-bundle` + `numpy`) as
deliberately minimal; adding an Ollama HTTP client expands that. The
alternative (a separate workspace member, importable by `gui` as a library)
would preserve that minimalism and mirror how `coil_simulation` itself is
kept separate from `gui` — but adds a second package boundary, its own
`pyproject.toml`, and cross-package import wiring for a feature that only
`gui` will ever call. Given the agent has no reason to run headlessly
outside the desktop app (unlike, say, `coil_simulation`, which both `gui`
and future tooling need), the extra boundary wasn't judged worth it.

### 5.6 Ollama, vs. llama.cpp / vLLM

**Chosen: Ollama.** Simplest to stand up (one local HTTP server, model
pulls handled for you), and current Ollama-supported models generally have
solid OpenAI-style tool-calling support, which the whole safety model here
depends on (the LLM must call structured tools, not free-text geometry
values). llama.cpp gives more control over quantization/performance at the
cost of more manual setup; vLLM is oriented at high-throughput serving,
which doesn't matter for a single interactive desktop session. Both remain
viable if Ollama's tool-calling proves unreliable with locally-available
models — the interpreter/orchestrator should be written against a small,
generic "chat with tools" interface so swapping the runtime later isn't a
rewrite.

This also covers the case where the model isn't running on this machine at
all — e.g. an office GPU box already serving deepseek. Ollama itself can
serve deepseek models directly, and most local servers (vLLM, llama.cpp,
Ollama) expose an OpenAI-compatible tool-calling endpoint either way, so
"Ollama on this machine" vs. "deepseek on the office GPU" is a config
change (base URL + model name) against the same generic interface, not a
different design.

### 5.8 MCP — considered, deferred rather than rejected

George's manager separately raised MCP (Model Context Protocol) for this
tool. MCP is a standard for exposing functions as typed "tools" that any
compatible client can call over stdio/HTTP — it standardizes the *interface*
to a tool, not the reasoning loop around it. It's not an alternative to
§4.2's orchestrator; it's a second way to expose the same underlying
functions (`check_valid_coilset`, `simulate`, `get_coilset`, `save_config`):

| | Direct Python calls (chosen for Phase A) | MCP server wrapping the same functions |
|---|---|---|
| Who runs the reasoning loop | `orchestrator.py`, hand-built against Ollama's tool-calling API | Whatever MCP client attaches (Claude Code, Claude Desktop, another local tool-calling client) |
| Setup cost | None beyond the orchestrator itself | An extra thin server process (stdio/HTTP) wrapping the same functions |
| Who can use it | Only this app | Any MCP client, including using Claude Code to drive candidate search directly from a terminal for development/debugging, before `agent_panel.py` exists |
| Offline requirement | Satisfied directly | Not automatic — common MCP hosts (Claude Desktop, Claude Code) default to a cloud model; staying offline requires a local MCP-capable client specifically |

**Decision: deferred, not built in Phase A.** The direct-call orchestrator
is sufficient on its own and is what's actually needed first. An MCP
wrapper is cheap to add later over the same functions if a concrete second
client shows up — nothing in this design is built in a way that would need
to change to add it.

### 5.7 Hard constraints and scoring as plain code, never LLM judgment

Not really a contested trade-off — this is a safety requirement, not a
design preference. Geometric validity (`check_valid_coilset()`) and the
Spec's hard constraints (no ferrite, fixed PCB thickness, etc.) are checked
in code the LLM cannot override by reasoning around them. The only
alternative ("trust the LLM to respect constraints because they're in its
prompt") was rejected outright: prompted constraints are soft, silently
droppable under context pressure, and unauditable — for anything that might
end up as physical hardware, that's not acceptable.

---

## 6. Phasing

1. **Phase A** — `agent/` subpackage: `schema.py`, `scorer.py`, and
   `interpreter.py`/`orchestrator.py` wired against `check_valid_coilset()`
   and `engine.simulate()` directly, exercised from a script/tests, no UI
   yet. Proves the loop and surfaces Ollama tool-calling reliability early.
2. **Phase B** — `agent_panel.py` in the live dashboard: Spec confirmation
   UI, batch progress (via the same per-frame `JobRunner.poll()` pattern
   `runs_panel` already uses), ranked-results review, promote-to-config.
3. **Phase C** — once real session history exists, consider a cheap
   surrogate regressor as an additional tool the LLM can call to pre-screen
   ideas, and revisit ranking (§5.4) with real data to look at.

## 7. Open questions (not guessed)

1. **Which model / which backend** — Ollama locally, or an office GPU
   already serving deepseek (§5.6) — not yet decided, and treated as a
   config value (base URL + model name) rather than a design fork either
   way. Gates whether tool-calling is reliable enough at all.
2. **Batch size and per-session `simulate()` budget** (§4.2's stop
   conditions) — `femmcli` solve time per candidate hasn't been measured
   yet in this environment; worth timing a handful of real solves before
   picking numbers.
3. **Whether LLM-driven search is sample-efficient enough in practice**
   (§5.1's core risk) — only answerable by running Phase A for real and
   looking at how many batches it takes to find something good.
4. **Secondary-objective ranking scheme** (§5.4) — lexicographic is the
   starting default; revisit once real ranked output exists.
5. **MCP wrapper** (§5.8) — deferred for Phase A; revisit if a concrete
   second client (e.g. driving search from Claude Code) becomes useful.

## 8. Verification approach

`femmcli` runs natively on Linux, including this dev environment — unlike
the retired Wine-based backend, this can be exercised for real here, not
just statically. Plan: (1) unit-test `scorer.py`/`schema.py`/delta-building
against synthetic `SimResult`s, no LLM or solve needed; (2) run the
interpreter against Ollama with a few example prompts (including the Gen2
one above) and manually inspect the produced Spec; (3) run a short real
orchestrator session (small batch, few iterations) against `femmcli` and
confirm both the ranked output and the Spec-confirmation UI in the actual
`gui` app.

## 9. Critical files

- `gui/src/gui/main.py` — add the agent panel to the existing `AppState`/
  `_gui()` loop.
- `gui/src/gui/job_runner.py` — `JobRunner`, `ThreadPoolExecutor`,
  `coilset_signature()`/`find_cached()`, reused as-is by the orchestrator.
- `gui/src/gui/panels/runs_panel.py` — existing pattern for a panel that
  polls a `JobRunner` each frame; the agent panel follows the same shape.
- `coil_simulation/services/femm_service.py` — `check_valid_coilset()`,
  reused as-is.
- `coil_simulation/repositories/coilset_repository.py` — `get_coilset()`
  (baseline lookup), `save_config()` (promoting an accepted candidate).
- `coil_simulation/models/coilset.py` — field reference for `schema.py`;
  not modified.
- `coil_simulation/engine.py` — `simulate(coilset, distance)`, the eval
  entry point.
- `gui/pyproject.toml` — new Ollama HTTP client dependency (e.g. `httpx` or
  the `ollama` python package), a deliberate expansion of `gui`'s currently
  minimal dependency set (§5.5).
