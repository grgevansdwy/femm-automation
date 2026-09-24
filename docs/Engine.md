---
owns: backend/engine/**, backend/conftest.py, backend/data/**, frontend/src/frontend/services/job_runner.py
last-verified: 2026-09-24
---

# Engine: physics, the FEMM/xfemm backend, and running solves

**Owns:** `backend/engine/` — a `CoilSet` in, a `SimResult` out. Knows nothing about any
UI or about the AI agent; `backend/agent/` and `frontend/` both build on top of it, never
the other way round. For the doc index, see `docs/README.md`.

---

## 1. The problem, in one paragraph

This project designs **6.78 MHz wireless power transfer (WPT) coils** — the kind that let
a robot charge without a physical connector. The physical system is three flat spiral
coils, stacked on a shared axis with a gap between them:

| Coil | Role | Code names |
|---|---|---|
| **Loop** | Drive/feed coil | `loop`, group `1` |
| **Tank** | The resonant transmit coil the loop excites; reducing *its* ESR is a headline goal | `tank`, group `2` |
| **RC** | Receive coil, sits `distance` mm above the others | `rc`, group `3` |

Plus optional ferrite (flux shaping), metal shields/backplanes (detuning + eddy losses),
and PCB stack-up (trace width, copper weight, layer count).

The whole engine answers one question: **given a coil geometry and a distance, what are
its self-inductance, mutual inductance, coupling, and resistance?**

## 2. The physics, condensed

- **Self-inductance (L)** — how much magnetic field one coil produces per amp of its own
  current. Bigger coil / more turns → bigger L. Barely moves with distance to another coil.
- **Mutual inductance (M)** — how much of *one* coil's field passes through *another*.
  This is the wireless link itself. Falls off with distance; near-field (gap small vs.
  coil radius) it falls off gently, not the steep 1/d³ dipole rule.
- **Coupling (k)** — `k = M / sqrt(L_a · L_b)`, unitless 0–1, so different-sized designs
  are comparable. `k12` loop↔tank, `k13` loop↔RC, `k23` tank↔RC (the power-transfer link).
- **ESR** — energy wasted as heat. At 6.78 MHz, current crowds toward a conductor's
  surface (**skin effect**) and toward neighboring traces (**proximity effect**), so ESR
  depends on trace width/thickness/spacing, not just copper length. Skin depth is
  ~25 µm at 6.78 MHz (roughly, in copper) — trace thickness stops helping ESR much
  past ~2× that.
- **Ferrite** near a coil raises L and M (guides flux) and adds loss (via its loss
  angle) that shows up as ESR.
- **Metal** (shield/backplane) near a coil lowers L and M (eddy currents oppose the
  field) and adds eddy-current loss (raises ESR). Closer/larger → stronger both ways.

For the full zero-to-hero primer (fields, flux, Faraday's law, resonance, Q, FHA
circuit modeling) see `docs/archive/FEMM_AND_WPT_101.md` — accurate physics, though
some of its implementation-specific sections predate the current engine and no longer
match the code (see §9 below).

### Why FEMM, and why axisymmetric

FEMM is a 2D magnetostatic/harmonic solver. Since these coils are round, the whole model
is drawn as a **half cross-section** (radius × height) and FEMM mathematically spins it
around the centre axis — turning a 3D problem into a 2D one, which is why one solve takes
seconds, not minutes. Each *turn* of each coil is its own FEMM circuit (`loop0`, `loop1`,
`tank0`, …) — driving one coil's circuits while the others sit at 0 A and reading the
induced flux on the others is how one solve yields both self- and mutual-inductance.

## 3. The data model

`backend/engine/models/coilset.py` — `CoilSet`, a flat dataclass, ~55 fields: per-coil
radius/turns/parallel-traces/layers/spacing/width/thickness, ferrite
presence/offset/thickness/µ/loss-angle (separate materials for tank vs. RC), shield and
backplane presence/offset/size, PCB thickness. Every script's notion of "a design" is
this object. `table_fields()`/`table_labels()` give the ordered, human-labeled view the
GUI's edit form and this repo's tooling use — labels live on the field itself
(`field(metadata={'label': ...})`), so they can't drift out of order the way a separate
label dict once did.

`backend/engine/models/result.py` — `SimResult`: one `CoilSet`'s L/M/k/ESR at one RC
separation distance. Twelve numbers: `L_loop`/`L_tank`/`L_rc`, `M12`/`M13`/`M23`,
`loop_esr`/`tank_esr`/`rc_esr`, `k12`/`k13`/`k23`.

## 4. The pipeline: what `simulate()` actually does

`backend/engine/core.py` is the physics entry point. Everything else in the repo that
needs a coil's numbers eventually calls `simulate(coilset, distance)`:

```
simulate(coilset, distance)
  1. check_valid_coilset()   -- fast, no FEMM: can this geometry physically exist?
                                 (services/femm_service.py — trace/turn overlap, does the
                                 winding cross the axis)
  2. build_script(coilset, distance)
       a. build_geometry()   -- writes the drawing: problem setup, materials, per-turn
                                 circuits, the three coils' copper traces, then the
                                 ferrite/shield/backplane stack-up (drawn outward from
                                 the loop's underside, and mirrored above the RC)
       b. turn_currents()    -- splits each coil's target test current across its
                                 parallel traces and layers (arbitrary values — L/M/ESR
                                 are ratios, so any non-zero current works)
       c. THREE solves appended: drive loop only, then tank only, then RC only. Driving
          one coil and reading the induced flux on the others is how M is measured;
          M12 vs. M21 should agree by reciprocity (a built-in sanity check).
  3. run_femmcli(script.render())  -- hands the whole script to the xfemm solver
  4. extract_result()        -- reads femmcli's raw {key: complex} results, keeps only
                                 the real part, computes k12/k13/k23 = M/sqrt(L_a·L_b)
```

The engine never holds a live session with the solver — it writes one self-contained
script and runs it once (see §5 for why).

`services/femm_launcher.py` is a separate, parallel path: "Open in FEMM" reuses the exact
same `build_geometry()` (so what a person sees can never drift from what the numbers came
from), appends an `mi_saveas()` + zoom instead of solves, and hands it to real FEMM under
Wine for interactive inspection — it never touches the solve path.

## 5. The xfemm/femmcli backend

**Status: live**, since the migration off Wine. `services/xfemm_backend.py`'s `LuaScript`
class accumulates one Lua program mirroring FEMM's own `mi_*`/`mo_*` API (so
`femm_service.py`'s geometry-drawing functions take either a real `femm` module or a
`LuaScript` interchangeably), plus a Lua port of the flux/voltage summation the old
live-session code used to do in Python (`femmcli` only runs a whole script start-to-finish
— there's no call/response session to read an intermediate result from).

### Why it replaced Wine

FEMM is Windows software; it used to run through Wine (a compatibility layer, not an
emulator — see §7). Two problems: only one Wine/FEMM process could run safely at a time
(so sweeps and concurrent solving were impossible), and the FEMM window always steals
focus while a script drives it. xfemm is an open-source C++ port of FEMM's solver core
with a headless CLI (`femmcli`) that runs Lua scripts with no window and no Wine — ~13×
faster per solve in testing, and, because each solve is an independent OS process, safe
to run many at once.

### A real bug, found and fixed

Validating the new backend against a real multi-coil production config, mutual inductance
(M12/M13/M23) came back rotated by exactly 90° relative to real FEMM under Wine
(`xfemm_value = 1j * wine_value`, confirmed to 12 significant figures) — invisible in
single-coil testing, ~3 orders of magnitude off in practice since only the real part is
kept. **Root cause:** `cfemm/fpproc/fpproc.cpp`'s `GetFluxLinkage()`, for a circuit
carrying zero prescribed current, divided by the real angular frequency instead of the
imaginary `j·omega` — self-inductance/ESR (driven circuits) use a different code path and
were unaffected. **Fix:** a one-line patch,
`backend/engine/vendor/xfemm_flux_linkage_fix.patch`, applied to the vendored binary and
rebuilt (`uvx cmake`, no system install needed). Re-validated across three structurally
different production configs to ~10⁻⁸% (machine precision) against real FEMM.

### Where it lives

`backend/engine/vendor/xfemm-4.0-linux64/` — the prebuilt Linux release from
`crobarcro/xfemm` v4.0, with the patch applied. The original unpatched binary is kept
alongside as `femmcli.orig_unpatched` for rollback/reference. See
`backend/engine/vendor/README.md` for rebuild instructions.

### Validation coverage

`engine/tests/compare_wine_vs_xfemm.py` runs every config in `data/configs/` through
both backends (real FEMM under Wine and xfemm) from the same generated script and writes
a go/no-go + timing report to `docs/archive/wine_vs_xfemm_validation.md` — closing what
was originally a narrower check (3 of 16 configs, by hand). As of the last generated
report, all 16 built-in configs pass at well under the 1% tolerance (worst case ~4×10⁻⁶%)
with a 1.6–5.6× xfemm speedup. Needs real FEMM under Wine installed to run (§7).

### Known concerns

- **The fix lives only in this repo's vendored, patched binary — not reported upstream.**
  Re-vendoring xfemm from a fresh release without reapplying the patch brings the bug
  back silently.
- **No automated regression test** for backend accuracy or for this specific bug; the
  validation above was a one-off manual "diff both backends" check, not committed as a
  test that runs in CI.
- **Maintenance coupling:** `xfemm_backend.py`'s Lua generator is a hand-maintained
  mirror of `femm_service.py`'s `mi_*` surface. Any new geometry feature needs a matching
  Lua port or it silently breaks — the exact class of bug the 90° issue was.
- **femmcli is the least battle-tested part of xfemm** (small maintainer base; this
  project found a real, previously-undocumented bug on its first production-scale test).
- **No mesh reuse** — every solve is a fresh process + full remesh from scratch. Fine at
  today's scale; would matter for an optimizer doing many near-duplicate solves.
- **Temp-directory cleanup relies on normal process exit** — a hard kill (OOM, SIGKILL)
  mid-solve would leak that call's temp directory.
- **No SPDX license** on the `crobarcro/xfemm` fork — unresolved; check before external
  distribution.
- **`backend/engine/pyproject.toml` still lists `pyfemm`** as a dependency, left over
  from the Wine path (only relevant if someone drives real FEMM directly again).

## 6. Concurrency: how solves actually run

**Concurrent vs. parallel, briefly:** concurrency is a code *structure* (start several
things, come back for the results later); parallelism is *hardware* actually running
things at the same instant. Python threads here only take turns (the GIL), but each
thread spends nearly all its time *waiting* on a `femmcli` subprocess — and those
subprocesses genuinely run on separate CPU cores at once. The threads are just the hands
that launch and wait on the real, parallel work.

### The pool

`frontend/src/frontend/services/job_runner.py` (physically a frontend file, but this is
where "run a solve" concurrency actually lives — both Manual Runs and the AI agent go
through it): `JobRunner` owns a `ThreadPoolExecutor(max_workers=os.cpu_count())` and a
result cache.

```python
def submit(self, coilset, config_id, config_name, distance):
    job = Job(id=..., coilset=coilset, distance=distance)
    self.jobs[job.id] = job
    self._futures[job.id] = EXECUTOR.submit(self._run, job.id)   # returns instantly
    return job

def _run(self, job_id):
    job = self.jobs[job_id]
    sim_result = simulate(job.coilset, job.distance)     # the only call site of simulate()
    job.result = dataclasses.asdict(sim_result)
```

`submit()` never blocks — it hands the request to the pool and returns a `Job` record the
caller polls (`job.status`: `queued` → `running` → `done`/`failed`). The rule that makes
this fast: **start everything first, then wait for all of it**, not one-at-a-time.

- **Caching:** `find_cached()`/`coilset_signature()` fingerprint a coilset's *content*
  (every field except `config_name`, so renaming doesn't invalidate the cache) plus the
  distance. An identical request reuses the saved result instead of re-solving; `touch()`
  bumps `last_used_at` on a reuse so History reflects recent use without claiming a fresh
  solve.
- **Cancel** drops queued-but-not-yet-started jobs; a solve already running finishes (its
  result still lands in the cache, useful to whatever asks for it next) but its answer is
  discarded by the caller that cancelled.
- **Why the AI agent doesn't run its search loop on this same pool:** the loop spends most
  of its time *waiting* on solves from this pool. Sharing the pool with the work it's
  waiting on risks starving or deadlocking it as concurrent agent runs approach the
  worker count — so each agent run gets its own dedicated thread instead (see
  `Agent.md`).

### Hazards this design avoids

| Hazard | Mitigation here |
|---|---|
| Race condition (shared file) | Each solve gets its own temp directory (`run_femmcli`) — no shared `tmp.fem` the way the old Wine backend had |
| Deadlock | Agent search loop runs on its own thread, never inside the pool it waits on |
| Oversubscription | Pool capped at `cpu_count()`; extra jobs simply queue |
| Mixed-up results | Each `Job` is its own object; nothing is matched by array index |

### Known limits

- Worker count is just `os.cpu_count()` — not configurable, only lightly tested (4 jobs
  on 4 cores).
- Each `femmcli` call has a hard 120 s subprocess timeout, untested against a genuinely
  large/slow mesh.
- `Job` status is mutated by a worker thread and read by a render thread with no lock —
  works today via the GIL and no read-modify-write race, not stress-tested under
  rapid-fire concurrent submission.

## 7. Running FEMM directly (Wine)

FEMM only exists for Windows; on Linux it runs through **Wine** (a compatibility layer —
not a VM or emulator; no real speed penalty). Installed and verified: Debian 13, Wine
10.0, FEMM 4.2 (21Apr2019, x64), at `~/.wine/drive_c/femm42/`. A `femm` launcher script
is on `PATH` (`~/.local/bin/femm`). Headless scripting uses FEMM's own language, Lua:

```bash
wine ~/.wine/drive_c/femm42/bin/femm.exe -lua-script='c:\path\to\script.lua'
```

(Windows-form path, since FEMM is a Windows program.) Don't click inside the FEMM window
while a script drives it — it steals focus and can interrupt the run; this applies to
this repo's own automation too, not just manual use. Full install/reproduce steps:
`docs/archive/FEMM_ON_LINUX.md`.

The GUI's **"Open in FEMM"** button does exactly the command above, generated from
`build_geometry()` (§4) with no `quit()`, so the window stays open on the model. Files
land in `backend/data/femm_models/<design>/` — regenerated on every click, safe to
delete the whole folder.

## 8. The database (engine-owned tables)

One shared SQLite file, `backend/data/femm_automation.db` (gitignored). A fresh
checkout starts empty, and at launch the app loads the reference designs from
`backend/data/starter_configs/*.json` if there are no configs
(`coilset_repository.seed_starter_configs()`); Backup All/Restore All in the GUI toolbar is
how data moves between machines. `backend/engine/repositories/`:

| File | Owns |
|---|---|
| `db.py` | The connection helper + the engine's own tables. Foreign keys are deliberately OFF (SQLite default) — links between tables (e.g. `runs.config_id` → `configs.id`) are documented, not enforced. |
| `coilset_repository.py` | CRUD for saved designs (`configs` table). Built-in configs are editable but not deletable by default (the GUI bypasses that guard on explicit user request — see `GUI.md`). |
| `run_result_repository.py` | CRUD for run results (`runs` table) — every solve, cached and historical, manual or an AI-agent candidate. Each row stores the exact geometry it solved (`coilset_json`), so a run never depends on a saved config; `config_id` is NULL for an agent candidate, whose `config_name` is `<baseline> [AI <hash>]`. Databases from before the column existed are migrated on connect (`db.connect()`). Older rows without it fall back to rebuilding from the config. |
| `constants_repository.py` | Reads `backend/data/constants.json`: fixed test-rig values shared by every run — `freq: 6.78e6`, FEMM group tags, per-coil probe-current targets, backplane size. |

The agent's own tables (`ai_runs`, `ai_run_messages`) live in a separate repository —
see `Agent.md`.

## 9. Quick file reference

| Path | Role |
|---|---|
| `engine/core.py` | The physics entry point — `simulate()` and everything it calls |
| `engine/constants.py` | Fixed test-rig values (computes `omega` from `freq`) |
| `engine/main.py` | Tiny CLI example (`python -m engine.main`) |
| `engine/models/coilset.py`, `result.py` | `CoilSet` (the design), `SimResult` (one solve's output) |
| `engine/services/femm_service.py` | Geometry/circuit helpers shared by the solve and the launcher; `check_valid_coilset()` |
| `engine/services/xfemm_backend.py` | `LuaScript`, `run_femmcli()` — actually drives the solver |
| `engine/services/femm_launcher.py` | Opens a design directly in interactive FEMM |
| `engine/repositories/*.py` | SQLite CRUD (§8) |
| `engine/vendor/` | The bundled, patched xfemm binaries |
| `engine/tests/` | The engine's own tests |
| `frontend/src/frontend/services/job_runner.py` | The concurrency layer (§6) — physically a frontend file, documented here |
| `backend/conftest.py` | Shared test fixture: every test gets its own empty temp database |
| `backend/data/` | Runtime data — gitignored |

**Import rule:** `engine/` imports nothing from `agent/` or `frontend/`. Everything flows
one way, into it.

## 10. History (superseded, kept in `docs/archive/`)

Before the current layout, this project was two separate, ad hoc repos (`noelle/`, the
mature but structurally tangled 1300-line script-driven simulator, and `genki/`, a
smaller cleaner rewrite plus bench/lab tooling) — both removed on 2026-09-18
(recoverable at git commit `321b8d7`) once the current `engine/` fully replaced them.
`docs/archive/CODEBASE_OVERVIEW.md` and `docs/archive/REFACTOR_NOTES.md` document that
era for anyone reading old commit history or run logs; nothing in them describes
current code. `docs/archive/wine_vs_xfemm_validation.md` and
`docs/archive/xfemm_spike_notes.md` hold the raw validation numbers behind §5's summary.
