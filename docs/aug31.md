# 2026-08-31 — Objective #1 status review + Genki code comparison

Working log for one day's session, ahead of a meeting with Genki. Research
only — **no code was changed today.** No commits, no edits. This session read
`docs/goals.md`, the git history, `REFACTOR_NOTES.md`, `docs/aug28.md`, and
both `noelle/` and `genki/` source, to answer: what does Objective #1 actually
require, what's left to close it out, and how does Genki's code differ from
the already-refactored `noelle/` side.

---

## Objective #1: what it actually is, and what's left

Per `docs/goals.md`: given a coilset geometry, reliably output self-inductance,
self-resistance, and mutual inductance, through code that is *organized* —
not the flat, unimportable scripts Noel and Genki each had. It is not about
the log/JSON output specifically; that was one cheap, high-value fix that
happened to land during the structural work (see `aug28.md`, "Next steps" #1).

**Done, confirmed today:**
- `noelle/` is layered: `models/` / `repositories/` / `services/` / `view/`.
- `run_comparison(coilsets, rc_test_distances=None, run_id=None, root=...)`
  exists as a real callable entry point (`noelle/run_comparison.py`),
  returning `(RunResults, RunResultRepository)`.
- `compare_coilsets.py` is now a 13-line CLI shim that just calls
  `run_comparison()` with one default coilset — it used to *be* the whole
  1596-line program. This closes `aug28.md`'s "Next steps" #5.
- JSON summary written alongside the SVG (`RunResultRepository.save_summary_json`,
  closes `aug28.md`'s "Next steps" #1).
- LTspice import unblocked: `ltspiceautomator` is no longer imported
  unconditionally (it was dead-blocking every import of the file); the import
  is now commented out with an explanation (`run_comparison.py:28-32`).

**Still open:**
- Genki's code is not merged in at all. `femm_caps.py`, `femm_coils.py`,
  `femm_toroid.py` remain untouched in `genki/em_sim/`.
- Three separate `CoilSet` definitions still exist: `noelle/models/coilset.py`
  (57 fields), `noelle/component_sims/genki_femm_simple.py` (23 fields — this
  is Noel's own clean-room rewrite, *not* actually Genki's code despite the
  filename), and Genki's own `femm_coils.py` (2 fields, no RC coil).
- Known bugs carried forward from `aug28.md`, none fixed since: run-ID
  collision (day-hour-minute, no month/year), TC/RC sharing one `'Ferrite'`
  material, shield blocks nested inside the ferrite `if` (a config can request
  a shield with no ferrite and silently get none), `Vpa` fixed at 200V,
  `copy_coilset` still ~65 hand-written field assignments instead of
  `dataclasses.replace`.
- Still cannot run against real FEMM in this environment — every check to
  date (today and Aug 28) is static/AST-level, never a real solve.

---

## Objective #2, clarified

Not yet started, and not blocked on anything except #1. Purpose: replace the
manual "guess a geometry → run FEMM → read L/M/R → adjust → repeat" loop with
an agent that takes a target (e.g. target mutual inductance at a given
separation, minimize resistance) and searches geometries automatically,
calling `run_comparison()` per candidate. `goals.md` doesn't specify the
search method (Bayesian optimization / genetic algorithm / LLM-driven loop
all fit "AI agent" — open question for the team).

---

## LTspice: not part of either objective

`goals.md` never mentions LTspice. Two separate things exist in the repo
under that name, both outside scope:
- `noelle/ltspiceautomator/` — empty, uninitialized private Bitbucket
  submodule. Previously imported unconditionally, which blocked every import
  of `run_comparison.py`; that import is now disabled (see above). The only
  code that used it (a max-power-point solve) is dead/commented.
- `genki/LTspice_Automation/` — a separate, apparently unrelated effort doing
  circuit-level (SPICE) simulation of the power electronics, distinct from
  the FEMM (electromagnetic field) simulation this project is about.

---

## Genki vs. Noelle code comparison

Full detail from a sub-agent review; key points:

| | Genki (`em_sim/femm_coils.py`) | Noelle (`models/coilset.py` + `services/`) |
|---|---|---|
| Geometry | explicit `turn_radii` list | derived from `central_radius` + spacing formula |
| Coils modeled | loop + tank only (RC commented out) | loop + tank + rc |
| Trace shape | trapezoid / circle cross-section | flat rectangle |
| Self-inductance isolation | `PositionTweak` context manager | manual shift/simulate/restore, 15 positional params |
| Circuit solve | none — raw R/L/M only | full 3-mesh solve (efficiency, ESR, losses) |
| Output | printed to stdout only | SVG dashboard + log + JSON |
| Materials | Copper only | + Ferrite, AluFoil, backplane |

Genki's code is *not* the flat-script mess Noel's original was — it already
uses dataclasses and functions. Two things in it are arguably better than
what's currently in `noelle/`: the trapezoidal trace cross-sections, and the
`PositionTweak` pattern, which is cleaner than Noelle's current 15-parameter
manual version. `REFACTOR_NOTES.md` independently flagged similar "better but
currently dead" code in `femm_model.py` as a direction worth adopting — same
idea applies here.

`femm_caps.py` (interdigitated capacitor) draws geometry but never extracts a
result (ends in `input()`). `femm_toroid.py`'s own docstring says it hasn't
been reviewed yet. **Neither has any equivalent in `noelle/` currently.**

**Bottom line:** merging Genki's coil code in is real design work, not a
rename — the geometry parameterization differs (explicit radii vs. derived
spacing), Genki's model has no RC coil or circuit-level solve, and the two
codebases disagree on which of several genuinely good ideas (trapezoidal
traces, `PositionTweak`) should become the standard.

---

## Open questions prepared for the meeting

**Objective #1 scope**
1. Is the `noelle/` refactor "done" for #1, or does #1 require Genki's coil/
   cap/toroid code merged into the same structure first?
2. Which `CoilSet` definition is canonical — can the other two be retired?
3. Rewrite `femm_caps.py`/`femm_toroid.py` onto `femm_service.py`/
   `circuit_solver.py`, or port as-is?

**Bug priority**
4. Fix the run-ID collision now, or leave it (150 existing result folders
   already use the old scheme)?
5. Is the TC/RC shared-ferrite-material issue known/accepted, or has it been
   silently skewing past results?
6. Does `Vpa` need to be MPP-accurate, or is the fixed 200V fine for now?

**LTspice**
7. Get access to the private `ltspiceautomator` Bitbucket repo, or remove the
   dead path entirely?
8. Is `genki/LTspice_Automation` meant to eventually connect to this
   pipeline, or stay fully separate?

**Objective #2**
9. Exact objective function — fixed target M with minimum R, or a tradeoff
   range? Other constraints (geometry bounds, manufacturability,
   "non-detunable")?
10. Preferred optimization approach, or open to propose?
11. "Local AI agent" — no external API calls, or just "runs on our hardware"?

**Verification**
12. No FEMM install here, can't run-test end-to-end — which machine should
    changes be validated on before landing?
