# 2026-08-28 — `noelle/` restructure

Working log for one day's session. Continues `REFACTOR_NOTES.md`, which was
written 2026-08-26 and paused after step 1 of its "Suggested next steps".

**Nothing here was executed against FEMM.** The main loop still cannot run in
this environment (`ltspiceautomator/` is an empty submodule; FEMM is absent).
Every claim below is backed by static analysis, import resolution, or a test
that runs without FEMM — never by a real solve. The first real run on a FEMM
machine is still the acceptance test.

---

## Where it started and where it ended

| | Start of day | End of day |
|---|---|---|
| Production files | 3 | 9 |
| Production lines | 2677 | 2666 |
| `compare_coilsets.py` | 1596 | 757 |
| Test files | 0 | 4 (574 lines) |
| Layers | none | models / repositories / services / view |

```
noelle/
  compare_coilsets.py                 757   composition root
  config.py                            54   the operating point
  models/
    coilset.py                        120   CoilSet + its table labels
    config_result.py                   89   ConfigResult, RunResults
  repositories/
    coilset_repository.py             609   16 named coilsets
    run_result_repository.py           52   owns ./results/ID<run_id>/
  services/
    femm_service.py                   446   draw / solve / extract
    circuit_solver.py                  89   the 3-mesh math
  view/
    dashboard.py                      450   the whole figure
  tests/                              574   4 suites
  results/                                  59 MB archive, untouched
```

Dependency direction: `models` depends on nothing; `repositories`, `services`
and `view` depend on `models`; only `compare_coilsets.py` touches every layer.

---

## What was done, in order

### 1. Extracted the low-risk modules (`REFACTOR_NOTES.md` step 1)

`sim_constants.py`, `config_table_spec.py`, `coilset_variants.py` moved out
verbatim. Added `circuit_model.py` — the mesh impedances, the Cramer's-rule
current solve, the power accounting, the normalised currents. That block is pure
scalar arithmetic with no FEMM dependency, which made it the first code in the
repo that could actually be executed and checked here.

Verified that every code line from the original appeared verbatim in a new
module; the only token that changed was `ZL_eq` becoming a `Z_load` parameter.

### 2. Condensed the comments

77 comment blocks collapsed to one-liners, 136 comment lines removed. Verified
by comparing every non-string token before and after: **identical**. The four
files without docstrings had byte-identical ASTs. No commented-out code touched.

### 3. Replaced 30 parallel accumulator lists with a result class

The 30 module-level lists (`k13_displays`, `loop_losses`, …) were tied together
only by index position, appended from **30 sites spread across lines 189–778**.
Replaced with `ConfigResult` (frozen, 30 fields) collected in `RunResults`; one
construction site instead of thirty.

Values are now **raw floats**, formatted at render time. The old lists stored
`Quantity` objects and pre-formatted strings, which is precisely why the 114
archived runs cannot be re-plotted.

### 4. Split into folders, extracted `CoilSet`

`CoilSet` lived in `femm_model.py`, which does `import femm` at module level —
so reading the coil library dragged in the whole FEMM package. `femm_model` only
ever *defined* `CoilSet`, never used it, so the lift was clean. All 57 fields
compared name/type/default: identical.

Result: the data layer now imports with **no FEMM installed at all**.

### 5. Removed the real redundancy

`config_table_spec.parameters` was a **55-key positional mirror of `CoilSet`'s
first 55 fields**, existing only to supply display labels, matched by position
with nothing enforcing it — and already out of step (55 vs 57). Labels moved
onto the fields themselves:

```python
tank_mu: float = _label('Tank Ferrite Permeabilitiy')
```

`config_table_spec.py` deleted. `parameter_count` and the `fields(coilset)[:55]`
slice gone; membership is now "has a label". `coilset_variants.py` merged into
`config_coil.py` (8 + 8 = 16 coilsets in one place).

Verified the generated labels are byte-identical to the old dict keys, and that
row pruning produces identical output under all four flag combinations
(42 / 54 / 47 / 49 rows).

### 6. `models` / `repositories` / `services`

Adopted the layered layout. The finding that shaped it: persistence was smeared
across three layers, and the write side had no home —

| Where | What it did |
|---|---|
| `femm_model.py:191` | a **service** opened the run log and wrote to it |
| `compare_coilsets.py:49` | **main** created the run directory |
| `compare_coilsets.py:1132` | **main** wrote the SVG |

`RunResultRepository` now owns all three. `print_and_log` (which had zero
callers inside `femm_model`) was deleted from the service.

**Deliberately skipped:** abstract repository interfaces and a `dependencies.py`
DI factory. One source of coilsets, one sink — an ABC with a single
implementation is indirection that never branches. Worth adding when the GUI
needs coilsets from JSON.

### 7. Deleted dead code

Nine constants removed from `config.py`, every one already annotated
`UNUSED`/`DEAD` and confirmed by a read-reference count of zero:
`Admittance_FW_limit`, `R_source`, `R_load`, `R_load_1kW`,
`rc_backplane_group`, `tc_backplane_group`, `Pout`, `loop_config`,
`wire_diameter`. Plus three dead imports and six dead locals in
`femm_service.py`. The import list in `compare_coilsets.py` went 38 → 25 names.

### 8. Extracted `view/dashboard.py`

161 of 1150 lines touched matplotlib, across **14 separate regions**. All of it
moved into one `Dashboard` class: the 6×4 grid, 13 panes, the 8 per-config chart
calls, ~110 lines of axis styling, both tables, and the row pruning.

`compare_coilsets.py` now holds **zero matplotlib** and three handles:
`.figure`, `.cross_section_pane`, `.color_for()`.

The sweep data is still passed as 9 explicit keyword arguments, because it
remains loop-local scratch — see follow-ups.

---

## Findings

**The `results/` corpus is not a regression baseline downstream of `ZL_eq`.**
Reconstructing the pipeline from `results/Gen2_baseline` reproduced its logged
`Z1`, `Z2` and `mag_normal` to ~1e-13 — but only using a load impedance of
`43.027 - 8.728j`, back-solved from the log. Today's constants give
`94.174 + 31.333j`, and `mag_normal` then comes out 3.1169 against a logged
2.9358. The logs record the full `CoilSet` but **not one global constant**, so
each archived run's operating point is recoverable only by back-solving. This is
the "results are write-only" problem biting the first time the corpus is used
for its stated purpose.

**17 lists were provably never read** — 4 per-config (`L_rc_displays`,
`L_tank_displays`, `Itank_displays`, `Iloop_displays`) and 13 per-distance
(`Ypa_tests`, `fixed_Y_Vpas`, the `*_frequency_shifts`, both lateral ones, …).
`Iloop_displays`/`Itank_displays` look less like junk and more like a table row
someone forgot to wire up — the data is computed and formatted, but `rows` has
no entry for it. Kept as fields; still not displayed.

**`loop_losses` / `tank_losses` / `rc_losses` were misnamed** — they hold ESR in
ohms, not losses in watts. Now `loop_esr` / `tank_esr` / `rc_esr`. The table row
labels still say "mOhms" while the values are ohms; that mismatch is unchanged.

**`coil_configs.py` had a `SyntaxError`** — a dropped `)` at what is now
`config_coil.py:200`, which made the entire package unimportable. Restored.

**`test/` cannot be a package name.** `test` is a CPython stdlib package, and
regular packages beat namespace packages regardless of `sys.path` order, so
`python3 -m test.foo` resolved to the stdlib. Renamed to `tests/`.

---

## How things were verified

No FEMM, so every check is static or FEMM-free:

- **Token/AST equality** — for pure moves and comment edits, compare every
  non-string token before and after.
- **Value equality** — re-import a moved constants module and compare all 29
  values, derived ones included.
- **Output equality** — render the 24 table rows from a fixed `ConfigResult`
  both ways and compare cell strings; replay row pruning across all four flag
  combinations.
- **Reference counting** — before deleting anything, count read references by
  AST. Zero deletions were made on the strength of a comment claiming deadness.
- **Import resolution** — every module `compare_coilsets.py` imports, with
  `femm` and `ltspiceautomator` stubbed.
- **Layer invariants**, as tests rather than review notes: `services/` performs
  no file I/O; `models/` imports nothing upward; `view/` imports no service or
  repository; `compare_coilsets.py` holds no matplotlib.
- `pyflakes` clean across all 13 files.

### Test suites (all new today)

| Suite | Covers |
|---|---|
| `tests/test_circuit_solver.py` | pins the mesh math against `results/Gen2_baseline` |
| `tests/test_config_result.py` | the output model, 24-row shape, format equivalence |
| `tests/test_run_result_repository.py` | run directory, log, SVG, no-I/O-in-services |
| `tests/test_dashboard.py` | 13 panes, table identity, pruning counts, SVG render |

Run from `noelle/`: `python3 -m tests.<name>`.

---

## Known bugs carried forward, not fixed

- **Run IDs collide.** `'{:%d%H%M}'` is day-hour-minute — no month, no year.
  Two runs a month apart at the same minute overwrite each other. Now isolated
  to one place (`RunResultRepository.__init__`) with a `GOTCHA` comment, and a
  test pins the current format so changing it is deliberate.
- **TC and RC share one `'Ferrite'` material**, so `coilset.rc_mu` and
  `rc_loss_angle` are set by some configs but never applied.
- **Shield blocks are indented inside the ferrite `if`**, so a config asking for
  a shield but no ferrite silently gets no shield.
- **`Vpa` is fixed at 200 V** on the active path, which is why the VPA chart is
  flat.
- **Config-table row labels say "mOhms" but the values are ohms.**
- **`Gen3_backplane` and `Gen2_4Layer` never set `config_name`**, so they
  inherit the baseline's name — run either against its baseline and both table
  columns carry the same label.
- `genki/scripts/thermal.py:28` is still a plain `SyntaxError`. Untouched.

## Next steps

1. **Emit CSV/JSON next to the SVG.** `RunResultRepository.save_data(run)` via
   `dataclasses.asdict`. Now nearly free, and it is the fix for the write-only
   results problem above. Highest value per effort.
2. **`SweepPoint` on `ConfigResult`.** Collapses `Dashboard.plot_config`'s 9
   keyword arguments to `plot_config(cfg)` and removes 9 more parallel lists.
3. **Make `services/` matplotlib-free.** `femm_service` draws onto a passed-in
   axis. The headless twins already exist (`draw_coil_traces`,
   `draw_rectangular_material_no_plot`) and their FEMM-drawing bodies are
   **identical** once plotting statements are stripped (verified by AST). The
   dashboard would need to re-derive the cross-section rectangles first.
4. **`dataclasses.replace` instead of `copy_coilset`** — that function is ~65
   hand-written field assignments; add a `CoilSet` field and forget it there and
   every variant silently inherits a wrong value. Would also let `CoilSet`
   become frozen like `ConfigResult`.
5. **A `run(coilset, sweep) -> RunResults` entry point**, and the
   `if __name__ == '__main__':` guard. Importing `compare_coilsets` still runs it.

## Stale docs

`CODEBASE_OVERVIEW.md` and `FEMM_AND_WPT_101.md` still describe the pre-cleanup
tree and are now further out of date — every filename in them has changed.
`REFACTOR_NOTES.md` steps 1, 4 and part of 6 are done; its file-layout section
is superseded by this document.
