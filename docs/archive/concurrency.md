# Concurrency for the AI agent: batch proposals + concurrent solving

> A learning guide to concurrency plus a plan for letting the AI agent propose and
> simulate several designs per round. Status: **Phases 1 and 2 implemented** (2026-09-14):
> `CANDIDATES_PER_ROUND` in `agent/config.py`, prepare/launch/collect in `agent/femm_agent/services/optimize.py`,
> tests in `backend/agent/femm_agent/tests/test_agent_optimize.py`. Phase 3 is not built.
> Supersedes `backend/agent/docs/concurrency_plan.md`, which describes the
> pre-JobRunner `orchestrator.py` loop that called `simulate()` directly.

---

## Context: why we're doing this

Right now the agent works like a scientist who runs **one experiment, waits,
thinks, runs one more**:

```
Round 1: AI thinks (77 s) -> FEMM solves 1 design (~8 s) -> AI sees 1 result
Round 2: AI thinks (77 s) -> FEMM solves 1 design (~8 s) -> AI sees 1 result
...
```

The goal: let the AI propose **several designs at once**, run them all,
and send back **all the results together**. Then each round shows several
input→output pairs side by side ("radius +1 mm did X, turns +1 did Y, trace
width +0.1 did Z"), so the AI learns more per round and the whole search
takes less time.

---

## Part A: Concurrency basics (plain-language guide)

### A1. Three words people mix up

| Word | Kitchen analogy | In this project |
|---|---|---|
| **Sequential** | One cook makes dish 1, then dish 2, then dish 3. | Today's candidate loop in `optimize.py`. |
| **Concurrent** | Several dishes are *in progress* at once. The cook starts the soup, and while it simmers, chops for the salad. Nothing has to happen at the exact same instant. It's about not sitting idle while waiting. | The agent thread starts a solve and moves on instead of standing there waiting. |
| **Parallel** | Several cooks work at the *same instant*, one per stove. | Four `femmcli` processes on your 4 CPU cores. |

Concurrency is the **structure** ("start many things, collect results later").
Parallelism is the **hardware** actually running them at the same moment.
You need concurrent code to benefit from parallel hardware.

### A2. Processes, threads, and the GIL (just what you need)

- A **process** is a running program with its own memory. Each `femmcli`
  solve is a separate process (`engine/services/xfemm_backend.py`,
  `subprocess.run(...)`), so the operating system can put each one on a
  different CPU core.
- A **thread** is a worker *inside* one Python program. Threads share memory.
- Python's **GIL** (Global Interpreter Lock) lets only one thread run Python
  code at a time. That sounds bad, but a thread that is *waiting* (on a
  subprocess, a network reply, or a file) releases the GIL. Our threads spend
  almost all their time waiting on `femmcli` or on the LLM server, so threads
  work fine here. The heavy maths happens in `femmcli`, outside Python.

### A3. Thread pool, jobs, and futures

A **thread pool** is a fixed team of workers plus a queue of tasks:

```
                 queue                     workers (4 = your CPU cores)
submit(job) -> [ j5 j6 j7 j8 ... ]  ->  [W1: j1] [W2: j2] [W3: j3] [W4: j4]
```

- `submit()` returns **immediately** with a *ticket* for the job (a
  **Future**, or in this codebase a `Job` object whose `.status` changes
  from `queued` to `running` to `done`/`failed`).
- Later you "cash in" the ticket: you wait until its result is ready.
- If there are more jobs than workers, the extras wait in the queue.

This already exists: `frontend/src/frontend/services/job_runner.py` has
`EXECUTOR = ThreadPoolExecutor(max_workers=os.cpu_count())`, which is **4 on
this machine**.

### A4. The key rule: *start everything first, then wait*

```python
# SLOW: start one, wait, start the next (sequential)
for c in candidates:
    job = submit(c)
    wait(job)

# FAST: start all, then wait for all (concurrent)
jobs = [submit(c) for c in candidates]   # returns instantly
for job in jobs:
    wait(job)                              # the pool runs them side by side
```

### A5. Things that go wrong with concurrency, and how this codebase avoids them

| Hazard | What it means | Status here |
|---|---|---|
| **Race condition** | Two threads change the same data at once and corrupt it. | Each solve has its own temp directory (`run_femmcli`), so there is no shared file. Agent state is written by one thread only and *read* by the GUI thread (see `agent/femm_agent/models/state.py` docstring). |
| **Deadlock** | A waits for B, and B waits for A, so both wait forever. | `agent/services/runner.py` deliberately runs the agent loop on its **own** thread, not inside the solve pool it waits on (its docstring explains why). Keep it that way. |
| **Oversubscription** | More jobs than cores. They don't finish faster, they just queue. | The pool is capped at `cpu_count()`. Extra jobs wait in line, which is safe. |
| **One failure kills the batch** | One bad design throws an error and the other results are lost. | We will wait for and score *each* candidate inside its own `try/except`. |
| **Mixed-up answers** | With 4 results, which one belongs to which proposal? | Tag each result with its `tool_call_id` (see C3). |

---

## Part B: What your data says (this matters most)

I read the timing stats saved in `ai_run_messages` (77 rounds of past runs):

| | Median time |
|---|---|
| LLM "thinking" per round | **~77 s** |
| FEMM for a 1-candidate round (4 distances, already parallel) | **~6–11 s** (≈0 s on a cache hit) |
| FEMM for a 4-candidate round (candidates one after another) | **~20–28 s** |
| Candidates per round so far | **Just 1 candidate in 62 of 77 rounds.** The prompt tells the model "one candidate per turn". |

**Where the time goes today (1 candidate per round):**
```
|====================== LLM 77s ======================|== FEMM 8s ==|
```
About **90 % of every round is waiting on the AI**, not on FEMM.

**Lesson 1: batching is the big win.** One 77 s LLM call that produces 4
candidates is worth about 4× more than a call that produces 1:

| Mode | Time per round | Candidates per hour |
|---|---|---|
| Today: 1 per round | ~85 s | ~42 |
| Batch of 4, solved one after another | ~100 s LLM + ~24 s FEMM | ~115 |
| Batch of 4, solved concurrently, **on this 4-core PC** | ~100 s + ~24 s (same!) | ~115 |
| Batch of 4, solved concurrently, on a 16-core PC | ~100 s + ~6 s | ~135 |

**Lesson 2: concurrent solving barely helps *on this machine*.** One candidate
already runs its 4 distances on all 4 cores at once (`scorer.evaluate_async`
submits all distances before waiting). 4 candidates × 4 distances = 16 solves
through 4 cores takes about 4 "waves" either way. Starting all 16 at once
still pays off, for three reasons:
- it scales automatically on a machine with more cores,
- Cancel drops all of a batch's queued solves at once,
- it's the correct structure, and it costs almost nothing to write.

We'll **measure** it rather than assume (see Verification).

**Lesson 3: the trade-off.** Designs inside one batch can't learn from each
other: design #2 is chosen before design #1's result exists. So the prompt
must ask for batches that are *deliberately different* (below), not four
near-copies.

---

## Part C: Implementation (recommended approach)

### Phase 1: Let the AI propose a batch (the main win)

**C1. New setting**
- `backend/agent/config.py`: add `CANDIDATES_PER_ROUND = 4`.
- `backend/agent/input_chat/models/spec.py`: add `candidates_per_round: int` to
  `AgentSpec`, defaulting to it (same pattern as `max_tool_calls`).

**C2. Prompt changes** in `backend/agent/femm_agent/services/prompts.py` (`system_prompt`)
- Replace "one candidate per turn" with: "Propose up to N candidates per turn
  by calling propose_coilset up to N times in one reply. They are all
  simulated at the same time, and you get every result back together next
  turn."
- Add batch strategy guidance, so the batch teaches the model something:
  - **Probe**: from the current best, change *one* lever per candidate
    (radius, turns, width, thickness...) to see which lever moves which
    objective. That is a mini sensitivity study.
  - **Bracket**: try the same lever in both directions (+ and −).
  - **Exploit**: once one lever clearly works, try 2–3 step sizes of it.
  - Never propose two identical candidates.
- `build_tools` in `schema.py`: the tool description should say it may be
  called several times per reply.

**C3. Match each result to its proposal** in `backend/agent/femm_agent/services/optimize.py`
- The tool message today is `{"role": "tool", "content": ...}` with **no
  `tool_call_id`**. With several calls in one reply, the OpenAI format uses
  `tool_call_id` to pair each result with its call. Add
  `"tool_call_id": tc.get("id")` so results can't get mixed up.
- Enforce the cap: tool calls beyond `spec.candidates_per_round` get a
  rejected result ("over the per-round limit") and no solve.

**C4. Context size**
- Each round now carries up to 4 tool results. With
  `KEEP_RECENT_ROUNDS = 10`, the prompt grows about 4× faster, and a longer
  prompt makes the LLM slower. Start with `KEEP_RECENT_ROUNDS = 5` when
  batching, and check that `llm_seconds` doesn't climb in later rounds.

### Phase 2: Solve the whole batch concurrently

Restructure the candidate loop in `optimize.py` (`for tc in tool_calls:`)
into three passes:

```
1. PREPARE  (instant, no FEMM)   for each tool call:
             pop rationale -> coerce_delta -> dataclasses.replace -> audit
             -> "prepared" or "rejected" (same errors as today)
2. LAUNCH   (non-blocking)       for each prepared candidate:
             jobs[i] = evaluate_async(candidate, spec.distances, job_runner)
             -> all (candidate x distance) solves are now queued in the pool
3. COLLECT  (blocking, in order) for each candidate, in tool-call order:
             try: metrics = wait_for_jobs(jobs[i]); scored = score(...)
             except: rejected result (this doesn't touch the others)
             -> append CandidateResult, update best, append tool message
```

Reuse what already exists:
- `scorer.evaluate_async` / `scorer.wait_for_jobs`: unchanged, already the
  right shape.
- `scorer.evaluate`: split into `score(coilset, metrics, baseline,
  baseline_metrics, spec)` (the pure scoring half) plus a thin `evaluate()`
  that calls `wait_for_jobs` then `score()`. Other callers keep working.
- `runner._RunJobs`: already records every submitted job, so **Cancel keeps
  working** and now drops a whole batch's queued solves.
- The cancellation checkpoint `if state.cancelled: return` stays before
  LAUNCH and between COLLECT steps.
- GUI (`frontend/src/frontend/screen_agent/agent_panel.py`), persistence (`runner._persist`)
  and `followup.run_context` already loop over `round.candidates`, so no
  GUI changes are needed.

Optional, only if measurement shows it's slow: `JobRunner.find_cached`
re-reads every result file on each call, which means 16 disk scans per
batch. If that shows up in timings, build the signature→row index once per
round.

### Phase 3 (later, experimental): parallel AI calls
Run 2–3 independent LLM calls at once (e.g. "explorer" and "refiner" threads).
This only helps if the office LLM server can serve requests in parallel.
llama.cpp-style servers have `--parallel` slots, but on one GPU the slots
*share* its speed, so each call gets slower. Measure before building. Not
part of this plan.

---

## Files to change

Files a future implementation would touch:

| File | Change |
|---|---|
| `backend/agent/config.py` | `CANDIDATES_PER_ROUND`, maybe lower `KEEP_RECENT_ROUNDS` |
| `backend/agent/input_chat/models/spec.py` | `candidates_per_round` field |
| `backend/agent/femm_agent/services/prompts.py` | Batch wording + probe/bracket/exploit guidance |
| `backend/agent/femm_agent/models/schema.py` | Tool description allows repeated calls |
| `backend/agent/femm_agent/services/optimize.py` | Cap, `tool_call_id`, prepare/launch/collect |
| `backend/agent/femm_agent/services/scorer.py` | Split `evaluate` into `score` + wrapper |
| `backend/agent/femm_agent/tests/test_agent_optimize.py` (new) | See below |

Noticed but out of scope: `runner.py` saves `best_round` as the *last* round,
not the round where the best was found. Batching doesn't change this, but it's
worth a separate fix.

---

## Verification

1. **Unit test** (`backend/agent/femm_agent/tests/test_agent_optimize.py`, new): patch
   `agent.optimize.call_llm` to return one reply with 3 tool calls (one with
   an invalid field), and use a fake job runner whose `submit` records calls
   and returns `Job`-like objects already `done`. Check that:
   - all valid candidates' solves are **submitted before any is waited on**
     (check the fake's call order),
   - the invalid one is rejected while the others are still scored,
   - tool messages carry the matching `tool_call_id`, in order,
   - calls beyond `candidates_per_round` are rejected without submitting.
   Run with `uv run pytest backend frontend/tests`.
2. **Same numbers**: solving the same candidates concurrently must give
   identical M/ESR values to a sequential run, because each solve is a pure
   function of (coilset, distance). Cache hits in `find_cached` make this
   easy to confirm.
3. **Measure, don't assume**: run the agent from the GUI (or
   `orchestrator.py`) on the default Gen2_coil objective with batch size 1,
   then 4. Compare `llm_seconds` / `femm_seconds` per round (already saved
   in `ai_run_messages.payload_json`), plus total candidates and total time
   until `goal_met`. Expected: FEMM time per round about the same on 4 cores,
   and many more candidates per hour.
4. **Cancel mid-batch** in the GUI: queued solves are dropped and nothing is
   persisted.

---

## Part D: The search's memory (implemented 2026-09-15)

### What went wrong
A batched run got stuck on round 5 and failed with "timed out". Its prompt was
**~129 KB**, because each round resent every earlier round raw:

| Piece of the round-5 prompt | Size |
|---|---|
| System prompt + tool definition | ~13 KB |
| The AI's own earlier chain of thought | ~36 KB |
| **16 full results** (12 objectives x 4 distances each, ~4 KB apiece) | **~63 KB** |

A big prompt is slow for the server to read in, and the server was also busy
with other work.

### What each request carries now
The memory is rebuilt every round (`search_memory()` in `agent/femm_agent/services/optimize.py`)
instead of piling up:

1. **System prompt + task**: unchanged.
2. **Best `KEEP_BEST_CANDIDATES` (10) candidates so far**, ranked by the search's
   own scoring (`rank_key`), one line each:
   `#1 round 3: {'tank_trace_thickness': 0.14} -> goal_met=False: M12 -0.2% ok, ..., tank_esr -4.3% MISS`
3. **Every other candidate already tried**, on a shorter line that only lists
   what it missed, so the AI doesn't propose it again:
   `round 1: {...} -> misses: tank_esr -4.3%, L_rc +0.2%`
4. **The latest round, raw**: its tool calls and every full result, but **no
   chain of thought**.

See `docs/agent_prompt_example.md` for a complete example request.

The old `KEEP_RECENT_ROUNDS` setting is gone. The same round 5 is now about
**38 KB** (system prompt and tool definition included), and the size stays roughly flat as the search goes on. Only the
short "already tried" lines keep growing, at about 100 chars per candidate.

### How many candidates per round
`CANDIDATES_PER_ROUND = 5` is the most the AI may propose in one reply. The
prompt asks it to use all 5 unless it's confident fewer would help more (for
example, one or two careful refinements close to the goal). Anything past 5 is
rejected without being solved. It isn't remembered as tried either, so the AI
can propose it again next round.

History in the database still saves **every** candidate. Only what's sent back
to the AI is trimmed.
