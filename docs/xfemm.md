# xfemm backend: what was built, the bug, the fix, and what's next

**Status: live.** `engine.py` and the webapp now run on the xfemm backend,
with real concurrent solving. This doc is the full history plus what's worth
fixing next.

## Background

The FEMM simulation backend used to run real `femm.exe` under Wine
(`docs/FEMM_ON_LINUX.md`). That had two problems:

- **Speed/scale.** Sweeps run many coilsets × distances × 3 driven-coil
  solves each. `webapp/src/webapp/job_runner.py` used to cap concurrency at
  `ThreadPoolExecutor(max_workers=1)` because only one Wine/FEMM process is
  safe at a time.
- **Cursor/focus stealing.** `docs/FEMM_ON_LINUX.md` documents this as an
  inherent Wine limitation: the GUI always shows and steals focus while a
  script drives it, and won't exit without an explicit `quit()` call.

[xfemm](https://github.com/crobarcro/xfemm) is an open-source C++ port of
FEMM's magnetics solver core, with a CLI (`femmcli`) that runs Lua scripts
headlessly - no Wine, no GUI, genuinely parallel processes.

## What was done, in order

### 1. Research spike (`xfemm_spike/`, gitignored - throwaway/investigative)

Validated the concept on a minimal single-loop-coil case:

| | Result |
|---|---|
| Accuracy | ~10⁻⁷% relative difference vs. real FEMM under Wine (with and without ferrite) |
| Speed | ~0.16s/solve (femmcli) vs. ~2.2s/solve (Wine) - ~13-14x faster |
| Concurrency | 8 femmcli processes ran simultaneously in 0.37s total, fully independent |
| Cursor stealing | Eliminated - femmcli never opens a window |
| Gap found | `mi_makeABC` (FEMM's asymptotic boundary helper) fails at mesh time under femmcli - worked around with a manual far-field Dirichlet box |

Full writeup: `xfemm_spike/NOTES.md`.

### 2. Production backend built

- **`coil_simulation/vendor/xfemm-4.0-linux64/`** - the prebuilt Linux
  binaries (`femmcli`, `fmesher`, `fsolver`, `init.lua`, material libs), with
  a one-line patch applied to `femmcli` (see below and
  `coil_simulation/vendor/README.md`).
- **`coil_simulation/services/xfemm_backend.py`** - a `LuaScript` class that
  accumulates a self-contained Lua program mirroring `femm_service.py`'s
  `mi_*` calls, plus a Lua port of the flux/voltage summation
  `loop_inductance()`/`loop_ESR()` used to do live in Python (femmcli only
  runs whole scripts start-to-finish, so there's no way to read an
  intermediate result and branch on it in Python - unlike the live
  Wine/COM session). Also contains `run_femmcli()` (subprocess wrapper, one
  temp directory per call so concurrent solves never collide) and a parser
  for femmcli's stringified complex numbers.
- **`coil_simulation/legacy_wine_backend/`** - unmodified copies of the
  original `engine.py`/`femm_service.py`, kept as a rollback path (see
  "Rolling back" below).

### 3. A real correctness bug was found, then fixed

Validating against a real production config (`coil_simulation/data/configs/base_coil.json`
- ferrite, shields, tank + RC coils), the same generated Lua script was run
through both `femmcli` and real FEMM under Wine and diffed.

Self-inductance and ESR (L11/L22/L33, R11/R22/R33) matched to ~10⁻⁷%,
consistent with the spike. **Mutual inductance (M12/M13/M23) did not** - for
any circuit held at a prescribed current of exactly 0 (i.e. every coil except
the one being driven in a given solve), femmcli's flux linkage came back
rotated by **exactly 90°** relative to real FEMM: `xfemm_value = 1j * wine_value`,
confirmed to 12 significant figures. Since the code reads the real part, this
wasn't a small error - it was ~3 orders of magnitude off, and it was invisible
in the spike because that test case only ever had one circuit total.

**Root cause:** xfemm ships its own source (the release tarball includes it,
not just binaries). `cfemm/fpproc/fpproc.cpp`'s `GetFluxLinkage()`, in the
branch that handles a circuit carrying zero prescribed current, computed:

```cpp
FluxLinkage = GetVoltageDrop(circnum) / (2.*PI*Frequency);
```

dividing by the real angular frequency instead of the imaginary `j*omega`
(`I` in this codebase's own complex type) - exactly the missing factor that
produces a 90° rotation. Circuits carrying real current never hit this
branch, which is why self-inductance/ESR were unaffected.

**Fix** (`coil_simulation/vendor/xfemm_flux_linkage_fix.patch`, one line):

```cpp
FluxLinkage = GetVoltageDrop(circnum) / (I*2.*PI*Frequency);
```

Built from source with `cmake` (installed via `uvx cmake`, no system package
needed) + the existing `g++`. No other dependencies required - xfemm bundles
its own Lua and Triangle mesher. Full rebuild instructions in
`coil_simulation/vendor/README.md`.

**Validation:** re-ran the same "same script through both backends"
comparison with the patched `femmcli`, across three structurally different
production configs (ferrite+shields, a config with a metal backplane, and a
higher-turn-count config with neither):

| Config | Worst-case relative difference (any of 12 terms) |
|---|---|
| `base_coil.json` | 2.8×10⁻⁸% |
| `Gen3_backplane.json` | 2.9×10⁻⁸% |
| `seedcoil.json` | 3.7×10⁻⁸% |

Every self- and mutual-inductance/ESR term now matches real FEMM under Wine
to machine precision. The single-turn spike case was re-checked too (no
regression - that path never touched the buggy branch).

### 4. Wired into `engine.py` and the webapp

Once the fix was validated, `engine.py`/`femm_service.py` were switched to
build a Lua script via `xfemm_backend.LuaScript` and run it through the
patched `femmcli`, instead of live `femm.mi_*`/COM calls. Verified with a
real end-to-end run through the actual running webapp (`POST /runs/new` →
queued → solved → rendered result), not just a direct function call - the
result matched a direct `engine.simulate()` call exactly.

### 5. Concurrency unlocked

`webapp/src/webapp/job_runner.py` used to run a single `worker_loop()` task
against a `ThreadPoolExecutor(max_workers=1)`, purely because Wine/FEMM
couldn't run two solves at once. Since each xfemm solve is now an independent
`femmcli` subprocess in its own temp directory, that constraint no longer
applies:

- `WORKER_COUNT = os.cpu_count() or 4` (4 on this machine).
- `EXECUTOR` sized to `WORKER_COUNT`.
- `main.py`'s lifespan now starts `WORKER_COUNT` copies of `worker_loop()`,
  not one - just raising `max_workers` wouldn't have been enough, since a
  single `worker_loop()` awaits each job before pulling the next off the
  queue.

**Proved it**, not just claimed it: submitted 4 runs back-to-back through the
real webapp. 3 finished in ~1s while the heaviest config
(`Gen3_backplane`) kept running independently for several more seconds -
only possible with genuine parallel workers, since it was submitted *before*
two of the jobs that finished first. All 4 completed with correct, distinct
results and no cross-talk between the concurrent temp directories.

## Rolling back

`coil_simulation/legacy_wine_backend/` still holds the original, unmodified
`engine.py`/`femm_service.py`. Restoring the Wine backend is a two-line `cp`:

```
cp coil_simulation/legacy_wine_backend/engine.py coil_simulation/engine.py
cp coil_simulation/legacy_wine_backend/femm_service.py coil_simulation/services/femm_service.py
```

(`job_runner.py`'s worker-pool change is backend-agnostic and doesn't need
reverting - it would just mean multiple Wine processes fighting over one
shared session again, so `WORKER_COUNT` should go back to 1 too if this is
ever rolled back.)

## Concerns / what to fix next

Roughly in order of how much they'd bite:

- **No automated regression tests exist for any of this.** Every accuracy
  and concurrency check above was done with ad-hoc scripts run by hand, not
  committed test files. If `femm_service.py`'s geometry logic changes in the
  future, nothing in CI would catch a regression the way this session's
  manual Wine-vs-xfemm diffing did. Worth turning the "generate script → run
  through both backends → diff" methodology into a real (if slow/opt-in)
  test, plus fast CI-safe unit tests on the Lua generator itself (pure string
  assertions, no binary needed).
- **Only 3 of 16 production configs were individually validated** against
  real FEMM. They cover the main structural variations (ferrite+shields,
  backplane, higher turn count), but the other 13 weren't individually
  diffed - just assumed safe because they share the same `mi_*` call
  patterns.
- **The fix lives only in this repo's vendored, patched `femmcli` binary,
  not upstream.** If xfemm is ever re-vendored from a fresh release without
  reapplying `xfemm_flux_linkage_fix.patch`, the 90°-rotation bug comes back
  silently - no test would catch it without the point above being addressed
  first. Reporting this to https://github.com/crobarcro/xfemm hasn't been
  done yet.
- **Concurrency was validated at small scale**: 4 simultaneous jobs on a
  4-core machine. `WORKER_COUNT = os.cpu_count()` is a reasonable default,
  not a tuned one - it isn't configurable via env var, hasn't been load-tested
  under sustained/bursty traffic, and each `femmcli` call's subprocess
  timeout is a hardcoded 120s in `xfemm_backend.py::run_femmcli()` (untested
  against a genuinely large/slow mesh that might legitimately need longer).
- **No mesh reuse.** Each solve is a fresh process + temp files + full
  remesh from scratch. Fine at today's scale; would need revisiting if
  `docs/goals.md`'s "AI optimization" objective means thousands of
  near-duplicate solves in a loop (e.g. small geometry perturbations during
  an optimizer's search) - that workload would pay full meshing cost on
  every iteration with the current design.
- **Maintenance coupling.** The Lua generator (`xfemm_backend.py`) is a
  hand-maintained mirror of `femm_service.py`'s `mi_*` call surface. Any new
  geometry feature added to `femm_service.py`/`engine.py` needs a matching
  Lua port, or it'll silently do nothing (or crash) under the xfemm path.
  This is exactly the class of bug that bit mutual inductance - a mismatch
  that isn't a syntax error, just wrong physics.
- **femmcli itself is the newest, least battle-tested part of xfemm** -
  single external maintainer, and this session found a real,
  previously-undocumented bug in it on the very first production-scale test.
  Other latent issues of the same kind aren't ruled out.
- **Temp directory cleanup relies on normal process exit.** `run_femmcli()`
  uses `tempfile.TemporaryDirectory()`, which cleans up on a normal return or
  exception - but a hard kill (OOM, `SIGKILL`, container eviction) mid-solve
  would leak a temp directory per killed job. Not observed, but not
  guarded against either.
- **License**: `crobarcro/xfemm` has no GitHub-recognized SPDX license. Not a
  blocker for internal use, but unresolved - check before any external
  distribution.
