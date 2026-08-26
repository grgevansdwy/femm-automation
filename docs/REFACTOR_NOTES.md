# Refactor notes — `noelle/`

Working notes for decomposing `compare_coilsets.py`. Written 2026-08-26, paused
mid-way. Goal behind all of it: a GUI + automation layer on top of FEMM.

---

## Where things stand

`noelle/` is now 59 MB (from 92 MB) and under git. Everything below is
recoverable with `git restore` / `git revert`.

| File | Lines | Role |
|---|---|---|
| `compare_coilsets.py` | 1596 | the program — flat script, no functions |
| `femm_model.py` | 515 | the FEMM layer — drawing, solving, extraction |
| `coil_configs.py` | 566 | named `CoilSet` design library |
| `component_sims/` | — | unrelated single-component sims, parked |
| `results/` | 59 MB | 114 archived runs — the regression corpus |

### Commits (oldest first)

```
f98a8ea  Initial commit: snapshot working copies as-is
9562d49  Remove dead files and dead code from noelle/
4cd268a  Remove Branches_and_one_offs/ experiment graveyard and scratch files
1be16b8  Move single-component sims into noelle/component_sims/
a43306e  Remove dead code from the three core WPT files
966d1e0  Rename core modules to describe what they do
aa30bc7  Trim verbose comment blocks in compare_coilsets.py
```

---

## Is 1600 lines the problem?

No. A 1600-line file with well-named functions would be fine. The real issues
are structural:

1. **Zero functions.** Importing the file *is* running it — FEMM window,
   `plt.show()` and all. A GUI cannot import it, only shell out.
2. **Module-level mutable state is the results model.** 33 accumulator lists
   that the main loop mutates and the tail reads.
3. **No entry point**, so nothing can call it with different inputs.

This is why a naive split would make things *worse*: implicit shared globals
inside one file become implicit shared globals across several files.

---

## The measurement that makes a split viable

Measured with AST on the current file:

```
head       lines    1- 445    157 statements, defines 112 names
main loop  lines  446-1252    807 lines
tail       lines 1253-1596    116 statements

head -> loop:  70 globals
head -> tail:  51 globals
loop -> tail:   1 global      <- rc_test_distances
```

**The tail reads exactly one name from the main loop.** Plotting and table
rendering are not entangled with the simulation — both sides only talk to the
accumulator lists declared in the head. That is what makes extraction feasible.

---

## Proposed split — move code, remove none

| New file | From | Contents | Risk |
|---|---|---|---|
| `sim_constants.py` | head | frequency, omega, source/load impedances, matching network, coax cable models, target currents, plot bounds | **low** — read-only constants |
| `coilset_variants.py` | head | the `parameters` dict, the derived `no_*_present` flags, named variants (`Gen3_backplane`, `new_coil_8_turn`, ...) | **low** — pure data |
| `results_figure.py` | head + tail | 6x4 grid construction, both summary tables, axis labels, SVG save | **medium** — needs the 51 names passed in |
| `compare_coilsets.py` | — | imports the above, runs the main loop | — |

Target: main file drops to roughly **800 lines** — the simulation and nothing else.

### Order

1. **Do the two low-risk extractions first.** Mechanical: constants and data
   literals, no back-references into the loop.
2. **Hold `results_figure.py`.** Passing 51 arguments is absurd, so it wants the
   accumulators bundled into a `Results` dataclass first. That is the same
   refactor the GUI needs anyway — so it is the first step of the real work, not
   tidying, and deserves to be done deliberately.

---

## Constraint: none of this can be run-tested

`compare_coilsets.py` cannot execute here:

- `ltspiceautomator/` is an empty, uninitialised private Bitbucket submodule,
  and line ~67 imports it unconditionally
- even fixed, it needs FEMM and ~36 solves per config

So refactors are verifiable only **statically**: AST comparison, import
resolution, name-binding analysis. Note that AST-comparison only proves a
*comment* change is safe — moving code changes the AST, so that check stops
working for the split itself. Lean on the `results/` corpus
(`Gen2_baseline`, `Gen3 POR Jun10_25`) for eventual numerical comparison.

---

## Deliberately left alone

- **The `parameters` dict.** Despite its LEGACY banner it is still read for the
  `no_*_present` table-pruning flags and `parameter_count` / row labels.
  Deleting it breaks the output figure.
- **`femm_model.draw_coil_traces` and `set_circuit_current`.** Dead, but they
  are the *better* designs — a headless (no-matplotlib) drawing path, and
  generic circuit naming instead of hardcoded `loop`/`tank`/`rc`. Adopt rather
  than delete; a GUI wants exactly these.
- **Unused `CoilSet`s in `coil_configs.py`** (`base_coil`, `TR302_and_OC150`,
  `TR302_and_mithril`, `plated_slot_coil`, `seedcoil`). These are hardware
  records, not dead code.

---

## Known bugs and gotchas found along the way

- **Run IDs collide.** `'{:%d%H%M}'` is day-hour-minute — no month or year. Two
  runs a month apart at the same minute overwrite each other, and directories
  do not sort chronologically. Live data-loss bug.
- **Results are write-only.** 114 runs, zero CSV/JSON. Every number is locked in
  an SVG or a prose log — nothing can be compared, re-plotted, or regression-checked
  without re-solving. Emitting structured data alongside the SVG is a small,
  isolated change at the tail and the highest value-per-effort item in the repo.
- **TC and RC share one `'Ferrite'` material.** The RC ferrite silently inherits
  the tank's permeability and loss angle; `coilset.rc_mu` and `rc_loss_angle`
  are set by some configs but never applied. Needs a second FEMM material.
- **`Vpa` is fixed at 200 V** on the active code path, which is why the VPA
  chart is flat. Only the commented-out LTspice MPP block recomputes it.
- **`genki/scripts/thermal.py:28`** has a botched paste and is a plain
  `SyntaxError`. Pre-existing, untouched.
- **Three `CoilSet` definitions** still exist: `femm_model` (55 fields),
  `component_sims/genki_femm_simple.py` (23), `genki/em_sim/femm_coils.py`.

---

## Suggested next steps

1. The two low-risk extractions above.
2. **Emit CSV/JSON next to the SVG** — small, isolated, unlocks everything.
3. Fix the run-ID timestamp to a full ISO datetime.
4. Introduce the `Results` dataclass, then extract `results_figure.py`.
5. Give the script a `run(coilset, sweep) -> Results` entry point. Templates
   already exist: `component_sims/genki_femm_simple.py:main()` and
   `genki/em_sim/femm_coils.py:main()`.
6. Replace `coil_configs.copy_coilset` (~65 hand-written field assignments) with
   `dataclasses.replace(coilset, **changes)`.

## Docs that are now stale

`CODEBASE_OVERVIEW.md` and `FEMM_AND_WPT_101.md` still describe the
pre-cleanup tree: the circular import (fixed), `Branches_and_one_offs/`
(deleted), `named_configs` (deleted), the old filenames in prose, and old line
counts.
