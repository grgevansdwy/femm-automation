---
name: coil-sim
description: Simulate and tune wireless-power coils (Loop/Tank/Receive, 6.78 MHz) with the femm_* tools - hit a target mutual inductance, reduce a coil's ESR, or trade one output against another under limits. Use when asked to simulate, compare, reduce resistance, or optimize a coil design.
---

# Coil simulation workflow

## Rules (always)
1. **Numbers come only from the femm_* tools.** Never hand-derive inductance/resistance formulas, never read engine source, never run Python/scipy in bash to estimate. The simulator is the ground truth. **Do not search the filesystem** (`find`, `ls -R`) for files: everything you need is in this skill and the tools.
2. **Do not ask questions if the request has a target** or you were told it is non-interactive. State your assumptions in one line and go. Ask only when there is no target at all.
3. **Keep thinking short.** After each tool result write one line: what changed, which output moved, next move. Long reasoning burns the time limit - you can be cut off before you answer. Write **only the final report** at the end - no notes-to-yourself before it.
4. **Keep a BEST line.** After every result, restate `BEST so far: <overrides> -> <key % changes>` (or "none in band yet"). If you are cut off, that line is your answer.
5. **Watch the budget.** Every simulate/sweep result carries `session_simulations_used` (exact, includes this call) - use it, do not count yourself, and quote it in the report. Before a sweep of N values check `used + N <= budget`; shorten the sweep if not. Going over budget is a failure (a run once used 20 of 12 chasing a fifth decimal place; another reported "7 / 12" after using 12).
6. **Stop rules are in step 6. Obey them.**

## Names and units
- Geometry in **mm**; L and M in **H**; ESR in **ohms**; k unitless. Counts are integers, `*_present` flags 0/1.
- Coils: `loop` (drive), `tank` (transmit), `rc` (receive). `M12` loop-tank, `M13` loop-rc, `M23` tank-rc.
- Unless told otherwise the mutual to hit is **M23**, and "resistance" means `tank_esr` (plus `rc_esr` if asked).
- Every result includes `pct_vs_base` (% change vs the unmodified config). **Use it; do not do the arithmetic yourself.**

## Tools
| Tool | Use | Costs |
|---|---|---|
| `femm_list_configs` / `femm_get_config` | pick a base, see all parameters | free |
| `femm_simulate` | one design | 1 simulation |
| `femm_sweep` | one parameter x 2-8 values, others fixed | 1 per value |
| `femm_save_config` | keep a design - **only if the user asks** | free |

Default budget **12 simulations** unless the user gives one. Overrides **do not accumulate**: every call starts from the saved config, so resend all overrides each time. Results are deterministic (same overrides = same numbers), so a hit needs no re-run.

## Steps
0. **Write the spec.** Turn the request into explicit bands, e.g. `tank_esr: -4..-6%`, `others: +-2%`. Note which outputs are constrained and which are free. "Cut / raise by X%" means **aim at X%** (within about 1.5 points) - do not overshoot to "at least X%" unless the user says at least / no more than.
1. **Baseline.** `femm_get_config` for the base (default `base_coil`; use the one the user names), then `femm_simulate` with no overrides at the requested distance (default 5 mm). Copy the absolute baseline values from this result.
2. **Probe one lever at a time** with `femm_sweep` (3-5 values spanning the range). Pick levers from the table below; on an unfamiliar config sweep the 2-3 most likely ones first. Learn each lever's direction, size and side effects **before** combining anything. A weak or odd first sweep is not proof a lever is bad - it may have jumped over the useful range or landed on a cliff. Re-sweep at finer spacing before dropping it (for copper thickness use 1.1x-2x of the saved value, e.g. 0.078-0.14 for 0.070, **not** 4x).
3. **Combine** only levers that helped and whose side effects fit the bands. Simulate the combination, then narrow with a sweep around the best value. **Do not tune finer than ~1% of a value** (see Resolution): pick the middle of a plateau, never a value a hair from a step.
4. **Robustness check (2-4 simulations).** Sweep the winning parameter at about +-1% and +-2% of its value. Results here are *not smooth* (see Solver noise): if neighbours differ by more than ~2 percentage points from the winner, it is a cliff - move to a plateau point and say the number is mesh-sensitive.
5. **Check limits** with `pct_vs_base` against every band from step 0.
6. **Stop and report** as soon as ONE holds:
   - every band is met and the primary target is within ~1 point of exact (if within band but further, allow at most 2 more simulations to get closer);
   - budget used >= 75% - stop exploring, spend the rest on the robustness check, then report BEST;
   - **infeasible:** only after the checklist in "Before you say infeasible" below. Then say the target looks infeasible under those constraints, name which constraint binds, and give BEST.
   Never finish with no answer. A partial result with a clear verdict beats a timeout.

## Before you say "infeasible" (or "not met")
A wrong "infeasible" is worse than no answer: a run once claimed a target was impossible while a valid design existed (it had never tried ferrite size, or fixing the side effect on the other coil). Conclude it only after **all** of:
1. You tried at least 4 different levers from at least 3 groups: tank copper/geometry, tank ferrite/material (`tank_mu`, `tank_ferrite_outer_r`, `tank_ferrite_thickness`), receive-coil geometry (`rc_*`), layers/turns.
2. You tried **compensating**: the tank and receive coils are coupled, so a lever that moves a constrained output (say raises `M23`) can often be cancelled by a lever on an *unconstrained* output (shrink or grow `rc_radius` to pull `M23` back). Pairs of levers cancel side effects.
3. You can name the constraint that binds and the two best attempts against it.
If not, keep searching within budget and report "not found yet" with BEST - not "infeasible".

## Resolution and limits
- The solver responds in **steps, not smoothly**. `rc_radius` and ferrite thickness act in **whole mm** (58.0 to 58.8 give identical results; 1.5 mm ferrite = 1 mm). Do not sweep them in fractions, and never report a 4-decimal value as if it were precise. Report a range (the plateau) and pick its middle.
- Keep values within about 0.5x-3x of the saved value: a radius or distance far outside can hang the solver for 2 minutes. Counts must be whole numbers, `--distance` 0-200 mm; bad values are rejected with a message.

## Levers measured on `Gen2_coil` at 5 mm (hints - re-verify on other configs)
| Lever | Effect |
|---|---|
| `tank_trace_thickness` 0.070 -> 0.078 / 0.105 / 0.12 / 0.136 | `tank_esr` about -5% / -9% / -11.5% / -14.7%; L, M change < 0.4%. **The clean lever.** Copper > 0.105 mm is heavy copper - respect any cap the user gives. |
| **same lever on the other coils**: `rc_trace_thickness` (or `loop_trace_thickness`) | Works the same way: `rc_esr` -8.7% / -10.4% / -14.8% at 0.105 / 0.108 / 0.136 (L_rc and M23 move < 0.4%). Beyond 0.14 it stops helping (0.14 -> -3%, 0.20 -> -6%, 0.30 -> -4%). |
| `tank_trace_width` up (5.544 -> 5.85 / 7.0) | `tank_esr` **rises** (+1.4% / +30%; +18% if thickness is already 0.105), L_tank falls (-0.5% / -3.3%), rc_esr rises too. Do not widen. Narrowing to 5.0: ESR -1%, L_tank +1% - that L headroom can pay for opening turn spacing (see below). |
| `num_tank_layers` 2 -> 3 | `tank_esr` -7.7% **but `rc_esr` -7.8% and `loop_esr` -7%** (breaks a "keep the rest the same" band). With thickness 0.105: -12.9% - about the ceiling under thickness <= 0.105 and layers <= 3. |
| `num_parallel_tank_traces` 2 | Solver error unless width/spacing are re-fitted; when it runs it is no better (-13.0%, same as without) or much worse (+18%). Avoid. |
| `tank_turn_spacing` up (7.4 -> 8.9 / 9.5) | Alone: `tank_esr` -13% / -17% but `L_tank` -5% / -7%, `M12` -3% / -4%, `M23` -2% / -3%. On top of layers 3 + 0.105 mm: -25% / -28% with the same L/M cost. Only if inductance may move. |
| **Best combined under thickness <= 0.105, layers <= 3, L/M within 1%** | layers 3 + thickness 0.105 + `tank_trace_width` 4.5 + `tank_turn_spacing` 8.0: `tank_esr` -18.4%, L_tank -0.6%, M12 -0.6%, M23 -0.9%, rc_esr -10%, loop_esr -7.7%. About the limit; -35% is not reachable there. |
| `tank_PCB_thickness` 1.6 -> 3.2 | `tank_esr` -3%, `L_tank` -3%, M -2%. Poor trade. |
| `rc_radius` 56 -> 59 / 62 | `M23` +10% / +19%, `M13` +7%, **`rc_esr` +7.5% / +17%**, tank_esr -1 to -5%. About +3.3% M23 per mm. |

### Levers on `base_coil` (has tank ferrite; raising L_tank)
| Lever | Effect on L_tank / M23 / tank_esr |
|---|---|
| `tank_mu` 184 -> 250 / 400 / 1200 | L +1.6 / +3.6 / +5.9%, M23 +2.7 / +5.8 / +9.7%, ESR +0.7 to +2.6% (saturates) |
| `tank_ferrite_outer_r` 100 -> 110 / 120 (90 lowers) | L +4.3 / +5.9%, M23 +4.2 / +5.6%, ESR +2.3 / +3.1% |
| `tank_ferrite_thickness` 1 -> 2 / 3 (whole mm) | L +3.9 / +5.7%, M23 +6.0 / +8.6%, ESR +2.6% |
| `num_tank_turns` 5 -> 6 | L +36%, ESR +46%, M23 +22% - far too big a step |
| `tank_center_r` up | L up a little but M23 falls fast (5-7x the L gain) |
| **Compensation example** | `tank_ferrite_outer_r` 120 + `tank_mu` 1200 (L +14%, M23 +13%) then `rc_radius` 56 -> 52 pulls M23 to -0.9%: **L +14.0%, tank_esr +2.8%, M23 -0.9%**. Each mm of `rc_radius` is about 4.5% of M23. |

## Solver noise (important)
Outputs are not smooth in continuous dimensions. Example: `tank_esr` at `tank_trace_thickness` 0.130 / 0.134 / 0.136 / 0.138 / 0.140 is -13.8% / -13.2% / -14.7% / **-4.8%** / **-4.0%**. Wiggles of ~1-2 points are normal; a jump of 5+ points between close values is a cliff (mesh change), not physics. Do not interpolate across a cliff, do not pick a value right next to one, and mention it when reporting.

## Final report (always this shape)
```
Assumptions: <one line>
Overrides: { ... }   (base <config_id>, distance <mm>)
| output | baseline | final | % change | limit | ok |
Verdict: MET | NOT MET | INFEASIBLE (which constraint binds)
Robustness: neighbours at +-1%/+-2%: <tank_esr values> -> plateau | cliff
Simulations used: n / budget
```
Take % changes from `pct_vs_base` and absolute values from the tool results - do not recompute them. **Copy every number in the report from tool output (for sweeps, the `summary` table); never write one from memory or copy a value across rows** - runs have written wrong numbers in a robustness line and copied one row's values into another. Keep numbers to a minimum: the results table is for the chosen design only, taken from ONE exact `femm_simulate` of it; describe the sweep in words (plateau from A to B, cliff at C) and quote at most 3-4 `summary` rows.

## Gotchas
- A key that is not a CoilSet field is rejected with the list of valid keys.
- A solver crash (e.g. "trace space ... less than trace width") is returned as an error: read it, fix the geometry, do not retry the same overrides.
- Turns must not overlap: `*_turn_spacing` must exceed `*_trace_width`.
- Results are for one RC distance; say so if the user cares about a sweep over distance.
