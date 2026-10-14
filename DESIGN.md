# Design

How FEMM Coil Automation is built, at the level of "what are the parts, how do they talk, and why".
For installing and using it, see [`README.md`](README.md).

## 1. What it is

A tool for designing **6.78 MHz wireless-power coils**. A link has three flat spiral coils on a shared
axis: the **Loop** (drive coil), the **Tank** (the resonant transmit coil) and the **RC** (receive coil,
a chosen distance away), plus optional ferrite, metal shields and a PCB stack-up. Given a geometry and a
distance, a finite-element simulation returns twelve numbers: self-inductance of each coil (`L_loop`,
`L_tank`, `L_rc`), mutual inductance of each pair (`M12`, `M13`, `M23`), ESR of each coil, and coupling
`k = M / sqrt(L_a * L_b)`.

On top of that sit two ways to use it:

- **Manual runs**: edit a design, pick a distance, run one simulation.
- **AI agent**: describe a goal in plain English; an LLM interviews you into a checkable objective, then
  searches geometry changes, simulating every one, and shows the best design next to the original.

Everything runs locally and offline. The only network call is to an OpenAI-compatible LLM server you point it at.

## 2. The big picture

```
                              FEMM COIL AUTOMATION
                                        |
     +------------------+---------------+----------------+------------------+
     |                  |                                |                  |
  FRONTEND            AGENT                            ENGINE             DATA / EXTERNAL
  (what you see)      (the AI search)                  (the physics)      (outside the code)
     |                  |                                |                  |
 Manual Runs        1 INTERVIEW                      CoilSet (a design)   SQLite file (local)
  - config list       chat -> plan                    SimResult (12 nums)   - configs, runs
  - edit form         - one question at a time              |               - ai_runs, messages
  - live diagram      - finalize_spec tool            simulate()          13 starter designs
  - run + history     - code validates it              1 validate           (loaded on 1st run)
     |                - you click Start Search          2 build Lua script  Backup All / Restore
 AI Agent                   |                           3 run femmcli          |
  - chat sidebar      2 SEARCH LOOP                     4 read L, M, ESR, k  LLM SERVER
  - chat + plan card    repeat:                            |                (any OpenAI-style)
  - results + baseline   - model proposes up to 5    xfemm (bundled)         - office box by default
  - toasts               - code checks each one       - native Linux           - only network call
     |                   - solve them in parallel     - no Wine                    |
 Settings                - score, rank, remember     - one patch fixes        Optional: Wine + FEMM
  - Field Config         - code decides when to stop    the 90 degree M bug     for "Open in FEMM"
  - prompt, LLM, budget      |                            |
  - saved default      3 FOLLOW-UP CHAT            JobRunner (threads + cache)
                         - answers questions        - parallel solves
                         - stop_search tool          - same design = no re-solve
                         - revised plan
                         - "continue"
```

**A request, start to finish**

```
"cut tank ESR 20%, hold M within 5%"
   -> INTERVIEW turns it into objectives, a table of requirements, distances
   -> you check the table, click Start Search
   -> LOOP: AI proposes 5 designs -> code rejects invalid ones
            -> engine solves the rest in parallel at every distance
            -> scorer: worst distance decides, margins ranked
            -> memory rebuilt (best 10 + what each small step did)
            -> stop rules: budget, no better design for 2 rounds, ...
   -> chat gets a plain summary: best design found, what changed, why it stopped
   -> you ask questions, say "continue", or ask to change the goal
```

**What the user controls, and where**

| You decide | Where |
|---|---|
| The goal, and what must stay put | The chat. A quantity the goal doesn't name has no requirement and is free to change |
| Which geometry fields the AI may touch, and their min/max | Settings > Field Config (the AI can propose a change; only the user's click applies it) |
| Budget, server, prompt | Settings > LLM & Budget / System Prompt |
| Whether a search runs or stops | Start Search; asking the chat to stop |

**The one rule that shapes everything:** plain code decides, the model proposes. Validity, which fields may
change, scoring and when a search ends are ordinary code. Dependencies run one way: frontend calls agent, agent
calls engine, engine knows nothing above it.

## 3. The shape of the code

```
frontend/   desktop app (Dear ImGui)             renders state, never reimplements logic
   |  only calls two agent runners + settings
backend/agent/   the AI search                   interview -> objective -> search loop
   |  imports
backend/engine/  physics                         CoilSet in, SimResult out (xfemm solver)
```

- **One-way dependencies.** `engine/` imports nothing from `agent/` or `frontend/`. `agent/` imports
  `engine/`, never `frontend/`. The frontend touches the agent through its two runners
  (`InterviewRunner`, `AgentRunner`) and the settings module.
- **One process, one window, one SQLite file.** Background work (solves, LLM calls) runs on threads that
  the UI polls every frame; nothing in the UI blocks.
- **Plain code decides, the model proposes.** Which fields may change, whether a geometry is valid, how a
  design is scored, and when a search ends are all ordinary code. The LLM cannot talk its way around them.

```
backend/
  engine/      core.py (simulate), models/ (CoilSet, SimResult), services/ (geometry, xfemm backend),
               repositories/ (SQLite), vendor/ (bundled xfemm)
  agent/       input_chat/ (the interview), femm_agent/ (the search), settings/ (settings + prompts),
               services/ (LLM client, chat export), repositories/ (agent tables), benchmark / report tools
  data/        starter_configs/ (13 example designs), runtime data (database, logs: not in git)
frontend/src/frontend/
  screen_manual/  screen_agent/  screen_settings/  visualization/  services/  files_backup/
.pi/           extension + skill for the `pi` terminal agent (same simulations, no window)
```

## 4. Engine: physics and simulation

### Data model
`CoilSet` is a flat dataclass (~55 fields): per-coil radius, turns, parallel traces, layers, spacing, trace
width and thickness; ferrite (separate for tank and RC); shield and backplane; PCB thickness. It is the one
meaning of "a design" everywhere. Field labels live on the fields themselves, so the edit form can't drift
from the model. `SimResult` is the twelve numbers at one distance.

### How one simulation runs
`engine.core.simulate(coilset, distance)`:

1. **Validate** the geometry without the solver (do traces overlap, does a winding cross the axis).
2. **Build one Lua script**: draw the half cross-section of the round coils (FEMM spins it around the axis,
   turning a 3D problem into a fast 2D one), give every turn its own circuit, then append three solves:
   drive the loop only, then the tank only, then the RC only. Driving one coil and reading the flux induced
   in the others is how both self- and mutual-inductance come out of one script.
3. **Run it** once through `femmcli` (xfemm's headless command line) in its own temp directory.
4. **Extract** the twelve numbers; `k` is computed from `M` and `L`.

### The solver
xfemm is an open-source C++ port of FEMM's solver core. The prebuilt Linux release is bundled in
`backend/engine/vendor/` and **runs natively on Linux: no Wine, no Windows**. It depends only on the standard
C and C++ runtime libraries. Each solve is an independent OS process, so many run at once.

The bundled `femmcli` carries a one-line patch. Stock xfemm divided by the real angular frequency instead of
`j*omega` when computing flux linkage in an undriven circuit, which rotated every mutual-inductance value by
exactly 90 degrees. After the fix, all twelve values match real FEMM 4.2 to machine precision on the configs
tested. The patch and rebuild steps are in `backend/engine/vendor/README.md`. Re-vendoring xfemm without
reapplying it brings the bug back silently.

Real FEMM is only used by the optional **Open in FEMM** button, which draws the exact same geometry in
FEMM.exe under Wine for a person to inspect. It never touches the solve path.

### Concurrency and caching
`JobRunner` (in the frontend's `services/`, but used by manual runs and the agent alike) owns a thread pool
sized to the CPU count and a result cache. `submit()` returns immediately with a job the caller polls. The
rule is *start everything first, then wait*. The cache fingerprints a design's contents (not its name) plus the
distance, so an identical request is never re-solved. The agent's search loop deliberately runs on its own
dedicated thread, not in this pool, because it spends its time waiting on solves from the pool.

### Persistence
One SQLite file, `backend/data/femm_automation.db`, created on first run and never committed. Engine tables
hold saved designs (`configs`) and every solve (`runs`, each row carrying the exact geometry it solved).
The agent owns `ai_runs` and `ai_run_messages`. On first launch the 13 designs in `data/starter_configs/` are
loaded. **Backup All / Restore All** moves everything between machines as one file.

## 5. Agent: from a sentence to a better coil

### Principles
1. The objective comes from the user, per session, in their own words.
2. Offline: any OpenAI-compatible server, no external API.
3. Validity, which fields may change, and scoring never live in a prompt.
4. A human confirms the plan before any search starts.
5. It fits the existing single-process app: no second service.

### Phase 1: the interview (chat to `AgentSpec`)
The model has one tool, `finalize_spec`, and asks one short question at a time, stating the default it would
otherwise use. Plain code validates the result into an `AgentSpec`: baseline, distances, a list of
**objectives**, pinned or excluded fields, and an optional "push this one as far as possible". A bad spec is
sent back as a question, never accepted silently.

An objective is `match` (stay within a band of a target), `reduce` or `increase` (change by at least X%), on
any quantity: a simulated result, an outer diameter, or a design field. It is required or a preference. A
quantity is judged **at every distance and the worst one decides**. **A quantity the goal does not name has no
requirement and may change freely**; naming it is the only way to constrain it.

The user sees the plan as a table (each requirement next to the baseline's own numbers) and must click
**Start Search**. The plan stays in the chat afterwards.

### Which fields the search may touch
The **Field Config** table in Settings is owned by the user: per design field, an open/closed flag and an
optional minimum and maximum. A proposed value outside a limit is rejected before simulation, and the baseline
must itself sit inside the limits. It ships with limits wide enough to open every starter config but narrow
enough to keep a search out of degenerate coils. The model can ask for a change with a tool, which shows an
**Apply** card; only the user's click writes the table.

### Phase 2: the search loop
`optimize_loop()` repeats until code ends it:

1. Build the round's message: system prompt, goal, a short rebuilt memory, last round's full results.
2. Ask the model for up to five `propose_coilset` calls (a delta from the baseline or from an earlier design).
3. **Prepare** each call in plain code: right tool, right argument types, only allowed fields, inside the limits,
   a valid geometry, something actually changed. A bad call is rejected alone, with its reason.
4. **Launch every valid candidate's solves at once**, across all distances.
5. **Score** each candidate and label it (C1, C2, ...), then feed the results back.
6. Judge progress and apply the stop rules.

Why batches: one LLM reply takes minutes while a solve takes seconds, so asking for several candidates per
reply is where the time is saved.

**Scoring.** Every objective gets a *margin* normalised by its own tolerance, so different quantities are
comparable. The goal is met only when every required objective passes at every distance. Designs that meet it
are ranked by a blended score; designs that don't are ordered by how close they are, so there is always a best
attempt. Candidates are compared with margins and `rank_key()`, never an ad hoc number.

**Memory.** The prompt is rebuilt each round instead of growing: the best ten designs on one line each, every
other design tried on a short line of what it missed, a code-written "lever notebook" of what each small step
did, and the latest round in full. Chain-of-thought is dropped. (Appending raw history once produced a 129 KB
prompt by round 5 that timed out.)

**The search keeps going after a pass** and the model cannot end it. Code stops it on, in order: a user stop, a
budget limit (rounds, wall clock, simulations; defaults 25, 90 min, 300), a goal with only `match` objectives
once met, no better design for two rounds after a pass, no progress for four rounds before one, or two
text-only replies. The reason is recorded. A run's status changes only through one function,
`ai_run_repository.transition()`.

### LLM client
Plain streamed HTTP to any `/v1/chat/completions` server. Streaming is deliberate: a hard wall-clock limit can
only be enforced by closing the connection, and live reasoning text shows a slow round from a hung one. Limits,
sampling and retries are explicit settings. A round whose request fails every try is retried at the search
level, then ends as `llm_failed`.

### The chat after a search starts
The same chat carries on. While a search runs, the model answers questions about it and has one tool:
`stop_search`, used only when the user asks to stop and keep the best design. Once a search has ended, the
model can turn "make it 5%" into a **revised plan** that the user starts again, or propose a Field Config
change. Typing exactly `continue` resumes the same run from the budget already used; that is handled by the
app, not the model. When a search ends, the chat gets a plain-words summary written by code from the best
design's numbers (what it achieves, what changed, why it stopped).

Chats can be exported to JSON (complete, checksummed, validated on import) or to a PDF report. An imported chat
opens without simulating anything; if it is continued, its stored designs are re-simulated and re-scored first,
and it runs under *this* machine's settings and Field Config, never the file's. An import made before a search
had started can only be read.

### Settings and prompts
`AgentSettings` holds everything editable without code: the LLM connection and limits, the search budget, stop
rules, retries, the Field Config table, and the three system prompts (interview, search, follow-up) as
templates with `{{placeholders}}`. Only values that differ from the defaults in `agent/config.py` are stored.
A run copies the settings in force when it starts, so editing them never changes a run in progress. The user can
also save the current settings as their own default and reset to it.

## 6. Frontend: the desktop app

Dear ImGui through `imgui-bundle`; one window. `main.py` polls the job runner and the agent each frame, then
draws the active mode.

- **Manual Runs**: config list, an edit form built from `CoilSet`'s field metadata, a live geometry diagram
  (top view and axial cross-section), run controls, run history. Import and export of a single design or run.
- **AI Agent**: a sidebar of every saved chat; the open chat with the plan, the live and final summaries; a right
  column with the baseline (minimizable), progress while running, results when done. Chats survive restarts:
  a search the app was closed on is shown as interrupted and can continue.
- **Settings**: Field Config, the search prompt, LLM and budget.
- **Notifications**: results of import, export and settings actions appear as short toasts in the top right.

The UI only renders state it is handed. The boundary into the agent is deliberately small, so "the chat isn't
updating" is almost always an agent-side question.

## 7. Other entry points

- `cd backend && uv run python -m agent`: run a search headless, without the interview.
- `python -m agent.benchmark`: a fixed set of goals run repeatedly against the real LLM server, to judge a change
  to the prompt or loop by numbers. Works on a copy of the database.
- `python -m agent.report`: summarise runs from the database and per-run request logs.
- `pi` (terminal agent): `.pi/` exposes the same simulations and designs as tools.

## 8. Testing

`uv run --package frontend --group test pytest backend` runs in seconds with no LLM server: every test gets its
own empty database, and the LLM is replaced by a script. Frontend tests are slower and one uses the real server.
Unit tests with a scripted LLM prove the plumbing, not that a prompt or loop change makes the search better; that
needs the benchmark against a real model.

## 9. Design decisions and known limits

**Decisions**

- **A quantity the goal does not name is free to move.** This is intentional. The inputs are guarded first: the
  EE team sets the Field Config limits before any search, so a search can only try geometries inside them. The
  outputs are only checked where the goal names them, so name what matters ("keep every inductance within 10%").
  There is no hidden default requirement.
- **Sleeping does not use up time.** Budgets and request limits are measured on a clock that stops while the
  computer sleeps, so a search that was running when the lid closed has lost nothing on waking. One caveat: a
  request that was in flight during the sleep may have been dropped by the server, and then it waits out its own
  limit (up to 15 minutes by default) before the search retries it.
- **Run-to-run variation is expected and handled by searching on.** The model samples, so two runs of the same
  goal differ. The search does not stop at the first passing design: it keeps going until nothing better turns up
  and reports the best one found, which narrows the spread between runs. It does not remove it.

**Known limits**

- **The xfemm fix lives only in this repo.** The bundled solver has a one-line patch (see "The solver"). A fresh
  download of xfemm does not have it, and without it every mutual inductance comes out wrong without any error.
  It has not been reported upstream, and the xfemm fork declares no licence. Check both before distributing outside
  the team.
- `femmcli` has no automated accuracy regression test here; accuracy was validated by comparison with real FEMM.
- Every solve remeshes from scratch; fine at this scale.
- The Lua script generator mirrors FEMM's API by hand, so a new geometry feature needs a matching port.
- The search is only as good as the model behind it.
