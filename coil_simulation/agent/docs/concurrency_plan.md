# Plan: multithreading the agent's FEMM solves

## The problem

`orchestrator.py`'s round loop is fully sequential today, at two nested
levels:

```python
for tc in tool_calls:                              # candidates this round
    ...
    scored = evaluate(candidate, baseline_metrics, baseline_diam)
```
```python
# scorer.py, inside evaluate()
metrics = {d: simulate(coilset, d) for d in DISTANCES}   # 4 distances
```

Every `simulate(coilset, distance)` call is one real `femmcli` subprocess
solve, taking on the order of seconds. A round with 3 candidates (like Run
I's round 6-10 batches) × 4 distances = 12 solves, run one at a time,
back to back. Wall-clock time for that round is `12 x (one solve)`.

## Why this is safe to fix

Two things already true in this codebase make this a low-risk change, not
a redesign:

1. **`engine.simulate()` is already documented as concurrency-safe**:
   > "Solves via a generated Lua script run through xfemm's femmcli in its
   > own temp directory - safe to call concurrently, unlike the previous
   > single-Wine-process backend." (`engine.py:182-187`)

   Each call gets its own temp directory (`services/xfemm_backend.py`), so
   there's no shared mutable state between concurrent solves to worry
   about.

2. **Invalid geometry fails before any subprocess is spawned.**
   `simulate()` calls `check_valid_coilset()` first (`engine.py:189-197`) -
   pure Python, no `femmcli` involved - and raises immediately if the
   geometry is invalid. So a bad candidate doesn't cost a wasted solve slot
   in the pool; it fails instantly, same as today.

3. **There's already a working precedent in this repo**:
   `gui/src/gui/job_runner.py` runs exactly this kind of work
   (`simulate()` calls) through `ThreadPoolExecutor(max_workers=
   os.cpu_count())` today, for the GUI's own use. That's not something
   this plan proposes to import wholesale (it's a much heavier module -
   `Job`/`JobStatus` dataclasses, on-disk result persistence, a
   `coilset_signature()` cache - all built for the GUI's per-frame polling
   model, none of which the agent script needs). But it's proof the
   pattern works against this exact FEMM backend, sized the same way
   (`os.cpu_count()`), and it's what this plan borrows the *sizing
   convention* from.

## Threads, not processes

`concurrent.futures.ThreadPoolExecutor`, not `ProcessPoolExecutor` or
`multiprocessing`. Reasoning:

- The actual heavy computation happens **outside the Python interpreter**,
  in the `femmcli` subprocess. `subprocess.run(...)` releases the GIL while
  it waits on the child process (`xfemm_backend.py:238`) - so from
  Python's perspective this is I/O-bound waiting, not CPU-bound work.
  Threads are the correct tool for exactly that: no GIL contention, because
  there's barely any Python code running while a solve is in flight.
- Processes would add pickling/IPC overhead moving `CoilSet` objects and
  `SimResult`s across a process boundary, for zero benefit - there's no GIL
  bottleneck here to work around.

## Design choice: one flat pool, not two nested pools

There are two independent axes of parallel work (candidates x distances),
so there are two ways to structure the pool:

**Option A - nested pools** (a pool over candidates, each candidate then
opens its own pool over its 4 distances). Rejected: two pool layers means
guessing a worker-count split between them (e.g. 3 candidate-workers x 4
distance-workers = 12 threads, but that's brittle - it hardcodes the
current `DISTANCES` length into the outer pool's sizing, and a machine with
only 4 cores would end up running distance-pools serially anyway with no
easy way to load-balance across candidates).

**Option B - one flat pool over every `(candidate, distance)` pair**
(chosen). Every solve in the round - regardless of which candidate or
which distance it belongs to - is submitted to the *same*
`ThreadPoolExecutor(max_workers=os.cpu_count())`. The executor's own
scheduler handles load-balancing across all of them; there's one worker
count to reason about, sized once, matching the GUI's existing convention
exactly. Results are grouped back into per-candidate `{distance:
SimResult}` dicts after collection, then scored exactly as today.

## Where this lives in the code

Today, `scorer.evaluate()` takes one `coilset` and returns one `Scored`.
The proposed replacement in `scorer.py`:

```python
_EXECUTOR = ThreadPoolExecutor(max_workers=os.cpu_count() or 4)  # module-level, reused across rounds

def evaluate_batch(candidates: list[CoilSet], baseline_metrics, baseline_diam) -> list[Scored | Exception]:
    """Same scoring as evaluate(), for many candidates at once. Every
    (candidate, distance) solve across the whole batch runs in one shared
    thread pool - not one pool per candidate - so the executor can balance
    load across all of them at once."""
    futures = {
        (i, d): _EXECUTOR.submit(simulate, c, d)
        for i, c in enumerate(candidates)
        for d in DISTANCES
    }
    results: list[Scored | Exception] = []
    for i, c in enumerate(candidates):
        try:
            metrics = {d: futures[(i, d)].result() for d in DISTANCES}  # re-raises per-candidate, same as today
            results.append(_score(c, metrics, baseline_metrics, baseline_diam))  # existing scoring logic, factored out of evaluate()
        except Exception as exc:
            results.append(exc)
    return results
```

`orchestrator.py`'s round loop changes from the current per-`tc`
try/except around a single `evaluate()` call to: build the list of valid
`(delta, candidate)` pairs first (still catching `coerce_delta`/
`dataclasses.replace` errors per-candidate, since those are free/instant
and don't belong in the pool), call `evaluate_batch()` once with all of
them, then iterate the returned list in the same order to print/log/rank -
same output shape as today, just gathered concurrently instead of one at a
time.

`evaluate()` (the current single-candidate function) stays as a thin
wrapper - `evaluate_batch([coilset], ...)[0]` - so any other caller
(offline verification scripts used throughout this investigation, e.g.
`/tmp/verify_10pct.log`'s style of ad-hoc checks) keeps working unchanged.

## What does NOT change

- No result caching (`coilset_signature()`-style dedup). Every round's
  candidates are genuinely new geometries the LLM just proposed - there's
  no repeat-the-same-solve pattern in this workload the way there is in
  the GUI (where a user might re-view an old config), so caching would add
  complexity for no measured benefit here.
- No persistence layer, no `Job`/`JobStatus` tracking, no `RESULTS_ROOT`
  writes. This stays a single stateless script run - the GUI's job runner
  solves a different problem (surviving across GUI frames/panels) that
  doesn't apply to a script that runs start-to-finish in one process.
- `DISTANCES`, `MAX_TOOL_CALLS`, and every other existing constant/behavior
  in `config.py` stays as-is. This is purely a "run the same solves
  concurrently instead of sequentially" change - the search logic, prompt,
  scoring thresholds, and history-pruning are all untouched.

## Verification plan

1. **Correctness**: re-run one of the already-logged rounds from Run I
   (e.g. round 6's three candidates) through `evaluate_batch()` and
   confirm the ESR/M23 numbers match the sequential run's logged values
   exactly - each solve is a pure function of `(coilset, distance)`, so
   concurrent execution must not change any result, only the order/timing
   they complete in.
2. **Error isolation**: feed `evaluate_batch()` a batch where one candidate
   has invalid geometry (e.g. `center_radius < coil_width/2`) alongside
   valid ones, confirm the invalid one comes back as an exception in its
   slot while the others still solve normally - matching today's
   per-candidate `try/except` behavior in `orchestrator.py`.
3. **Speedup, measured not assumed**: time a real 3-candidate round
   (`DISTANCES` has 4 entries, so 12 solves) sequentially vs. through the
   pool on this machine, with `os.cpu_count()` workers. Expect wall time
   to drop from roughly `12 x (one solve)` toward `ceil(12 / cpu_count) x
   (one solve)`, though real speedup will be somewhat less than linear
   (`femmcli` subprocess startup overhead, and shared disk I/O for temp
   files) - worth recording the actual number rather than assuming ideal
   scaling.
4. **Full live run**: run the orchestrator end-to-end once against the
   office server on the same 10%-all-three-coils goal already used for
   Runs G/H/I, confirm it still converges (no expectation the *search*
   behavior changes - only how fast each round's candidates get scored)
   and note the total run time versus Run I's for a real before/after
   comparison.
