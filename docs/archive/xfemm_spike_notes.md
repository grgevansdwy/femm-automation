> **Kept from the `xfemm_spike/` research folder, which was deleted on 2026-09-18.** This write-up
> is the only part worth keeping. The scripts (`build_and_solve*.lua`, `bench_timing.lua`), the
> benchmark output and the unpatched xfemm binaries it refers to are gone - the numbers and
> conclusions below stand on their own, and the binaries can be re-downloaded from the release
> URL in section 1. The production copy of xfemm (with the flux-linkage patch applied) is in
> `backend/engine/vendor/`. See also `xfemm.md` (the full story) and
> `wine_vs_xfemm_validation.md` (the later check of the production engine on real configs).

# xfemm vs. FEMM-via-Wine — validation spike results

Spike artifacts, throwaway. Nothing here is imported by `backend/`,
`noelle/`, or `webapp/`. See `/home/george/.claude/plans/did-some-research-on-iterative-kitten.md`
for the plan this executes.

## 1. Getting xfemm running natively

**No build required.** A prebuilt Linux x86-64 binary release exists:
https://github.com/crobarcro/xfemm/releases/download/v4.0/xfemm-4.0-Linux64.tar.gz
(v4.0, 2023-04-02). Extracted `cfemm/bin/*` (fmesher, femmcli, fsolver, hsolver,
esolver, + test binaries) plus `cfemm/femmcli/release/*.dat` and
`cfemm/femmcli/release/init.lua` into `xfemm_spike/bin/xfemm-4.0-linux64/`.
All binaries run directly on this machine (Debian 13, glibc current) — no
missing shared libraries, no compilation needed.

**Gotcha:** `femmcli` needs **both** `--lua-base-dir=<dir>` (material .dat
files) **and** `--lua-init=<dir>/init.lua` (the Lua prelude that defines
convenience functions like `mi_drawrectangle`/`mi_drawline`/`mi_makeABC` —
these are NOT native C++ builtins, they're Lua wrappers over lower-level
primitives like `mi_addnode`/`mi_addsegment`). Without `--lua-init`, any
script using them fails with `attempt to call global 'mi_drawrectangle' (a
nil value)`.

Example invocation used throughout this spike:
```
femmcli --lua-base-dir <bin-dir> --lua-init <bin-dir>/init.lua --lua-script <script>.lua
```

## 2 & 3. Accuracy: xfemm/femmcli vs. real FEMM under Wine

**Method.** `xfemm_spike/build_and_solve.lua` builds a minimal single-turn
loop coil (1A drive, 6.78 MHz, axisymmetric — same problem class as
`backend/engine.py`) purely via `mi_*` Lua calls, so the *identical*
script runs unmodified against both backends:
- Real FEMM 4.2 under Wine: `wine femm.exe -lua-script=...`
- xfemm: `femmcli --lua-script=...`

This sidesteps `pyfemm`'s Python/COM layer entirely (which doesn't work in
this environment anyway — `win32com` isn't installed in the project's Linux
venv; `docs/FEMM_ON_LINUX.md` already flagged this bridge as unfinished).
Both backends read/write the same plain-text `.fem`/`.ans` format.

**Compatibility bug found:** production's `init_femm()` calls `mi_makeABC()`
(an asymptotic/Kelvin-transform open boundary). Under femmcli this fails at
mesh time — `Label has invalid block type index: -1` (×4) — because the
shell-region block labels `mi_makeABC`'s Lua implementation creates never
actually get written to the `.fem` (`NumBlockLabels` stayed at 2, not 6).
**Worked around** for this spike with a plain far-field Dirichlet `A=0` box
boundary, applied identically on both backends, so the accuracy comparison
below isolates solver-core behavior from this specific gap. `mi_makeABC`
itself remains **unverified/broken** under femmcli — flagged for follow-up.

**Results — trivial case (single loop, no ferrite):**

| | real FEMM (Wine) | xfemm (femmcli) | rel. diff |
|---|---|---|---|
| Flux linkage | 6.983834180293713e-08 − j6.098237868591498e-08 | 6.983834194611157e-08 − j6.098237857710166e-08 | **2.0e-7 %** |
| Voltage (→ ESR) | 0.01273654778566939 + j2.974885484242561 | 0.01273654779805804 + j2.974885484244234 | **9.7e-8 %** |

**Results — with ferrite** (`build_and_solve_ferrite.lua`: adds a Ferrite
block, μ=2000, loss tangent 0.05 via `mi_modifymaterial` slots 1/2/10/11 —
same properties `engine.py` sets):

| | real FEMM (Wine) | xfemm (femmcli) | rel. diff |
|---|---|---|---|
| Flux linkage | 9.695867727816343e-08 − j1.551788103361189e-07 | 9.695867723306045e-08 − j1.551788103184956e-07 | **2.6e-8 %** |
| Voltage (→ ESR) | 0.01598337158936914 + j4.130200555367342 | 0.01598337161086586 + j4.130200555288521 | **2.0e-9 %** |

**Verdict: PASS, by a wide margin.** Both cases land ~6-7 orders of magnitude
under the plan's 1% "go" threshold — essentially machine-precision agreement,
not just "close enough." This is consistent with xfemm's `fsolver` being a
direct port of FEMM's own linear time-harmonic solver core, and this repo's
problem (`mi_probdef(6780000, 'millimeters', 'axi', ...)`, all-linear
materials, no BH curves) being exactly the solver class that core was built
for — unlike the "far different results" report from a pyleecan user, which
was for an unspecified, likely nonlinear/rotating-machine case.

**Coverage caveat:** this only exercises `mi_probdef`, `mi_getmaterial`,
`mi_addmaterial`, `mi_modifymaterial`, `mi_addblocklabel`, `mi_selectlabel`,
`mi_setblockprop`, `mi_drawrectangle`/`mi_drawline` (Lua wrappers),
`mi_selectsegment`, `mi_setsegmentprop`, `mi_selectrectangle`, `mi_setgroup`,
`mi_addcircprop`, `mi_modifycircprop`, `mi_analyze`, `mi_loadsolution`,
`mo_getcircuitproperties`. **Not exercised:** `mi_makeABC` (found broken, see
above), `mi_movetranslate`, `mi_clearselected` interactions at production
geometry complexity, and the full `femm_service.py` draw loop with multiple
turns/layers/parallel traces.

## 4. Performance

**Single-solve wall clock**, identical trivial geometry, 5 runs each:

| Backend | mean | notes |
|---|---|---|
| Real FEMM (Wine) | ~2.2 s | includes Wine/GUI process startup each run |
| xfemm (femmcli) | ~0.16 s | **~13-14x faster**, headless, no GUI ever spawns |

The gap should widen further on real production geometries (many turns,
ferrite, shields, backplane) since Wine's fixed GUI/process overhead stays
roughly constant while meshing cost grows on both backends similarly.

**Concurrency:** launched 8 independent `femmcli` processes simultaneously
(4-core machine), each in its own working directory. All 8 completed in
**0.37 s total**, all produced identical, correct, non-cross-contaminated
results. This directly confirms `job_runner.py`'s `ThreadPoolExecutor(max_workers=1)`
constraint — imposed specifically because only one Wine/FEMM process is safe
at a time — would **not** apply to an xfemm/femmcli backend; concurrent
solves are safe and cheap.

**Cursor/focus-stealing:** confirmed firsthand — the Wine runs in this spike
had to be killed with `timeout`/`pkill` because `femm.exe`'s GUI window
stays open (and grabs focus) until an explicit `quit()` call, exactly as
`docs/FEMM_ON_LINUX.md` warned ("Window-hiding doesn't work under Wine").
`femmcli` never opens a window — this class of problem doesn't exist with it,
by construction, not by workaround.

## 5. License

GitHub's license detector finds no recognized SPDX license on
`crobarcro/xfemm` (FEMM's own license, the Aladdin Free Public License, has
redistribution restrictions). **Not resolved by this spike** — not a blocker
for internal/non-distributed use, but do an explicit license check
(read `cfemm/femmcli/release/license.txt` in the release archive) before any
external distribution of a tool built on this.

## Go/No-Go

**GO**, with scope caveats. Accuracy and performance both clear the bar by a
wide margin for this repo's actual problem class (linear, axisymmetric,
harmonic magnetics). Before committing to a production migration, still need
to:

1. Fix or route around `mi_makeABC` (file/investigate the femmcli bug, or
   permanently adopt a manual far-field boundary box like this spike's
   workaround — worth comparing against production's actual boundary
   distance/behavior first).
2. Exercise the fuller `mi_*` vocabulary `femm_service.py`/`engine.py`
   actually use (multi-turn/multi-layer coils, `mi_movetranslate`, shields,
   backplane) — this spike only covered a single-turn, no-shield case.
3. Do the actual license check before any distribution.

## Sketch of a future `xfemm_service.py` (not implemented — future task)

If items 1-2 above check out, `backend/services/femm_service.py`
could get an alternate backend with matching function signatures
(`init_femm()`, `draw_coil_traces(...)`, `get_inductances(...)`), built as:
- A Lua-script generator translating each `mi_*` Python call in
  `femm_service.py` to an equivalent Lua line (mechanical — this spike shows
  the vocabulary maps directly, `mi_makeABC` aside).
- A `run_femmcli(script_path, workdir) -> ans_path` subprocess wrapper,
  each call in its own temp directory (as this spike's concurrency test did).
- A result parser mirroring `loop_inductance()`/`loop_ESR()`'s math, reading
  the Lua script's `openfile()`/`write()`/`closefile()` output file instead
  of a live `mo_getcircuitproperties()` COM call.

`engine.py`/`run_comparison.py` would need no changes beyond an import swap,
and `job_runner.py`'s `max_workers=1` could be raised once this backend is in
place.
