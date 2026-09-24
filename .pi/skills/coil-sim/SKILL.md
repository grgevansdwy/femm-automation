---
name: coil-sim
description: Simulate and tune wireless-power coils (Loop/Tank/Receive, 6.78 MHz) with the femm_* tools - hit a target mutual inductance, reduce a coil's ESR, or trade one output against another under limits. Use when asked to simulate, compare, reduce resistance, or optimize a coil design.
---

# Coil simulation workflow

## Rules (always)
1. **Numbers come only from the femm_* tools.** Never hand-derive inductance/resistance formulas, never read engine source, never run Python/scipy in bash to estimate. The simulator is the ground truth.
2. **Do not ask questions if the request has a target** or you were told it is non-interactive. State your assumptions in one line and go. Ask only when there is no target at all.
3. **Keep thinking short.** After each tool result write one line: what changed, which output moved, next move. Long reasoning burns the time limit - you can be cut off before you answer.
4. **Keep a BEST line.** After every result, restate `BEST so far: <overrides> -> <key % changes>` (or "none in band yet"). If you are cut off, that line is your answer.
5. **Stop rules are in step 6. Obey them.**

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
0. **Write the spec.** Turn the request into explicit bands, e.g. `tank_esr: -4..-6%`, `others: +-2%`. Note which outputs are constrained and which are free.
1. **Baseline.** `femm_get_config` for the base (default `base_coil`; use the one the user names), then `femm_simulate` with no overrides at the requested distance (default 5 mm). Copy the absolute baseline values from this result.
2. **Probe one lever at a time** with `femm_sweep` (3-5 values spanning the range). Pick levers from the table below; on an unfamiliar config sweep the 2-3 most likely ones first. Learn each lever's direction, size and side effects **before** combining anything.
3. **Combine** only levers that helped and whose side effects fit the bands. Simulate the combination, then narrow with a sweep around the best value.
4. **Robustness check (2-4 simulations).** Sweep the winning parameter at about +-1% and +-2% of its value. Results here are *not smooth* (see Solver noise): if neighbours differ by more than ~2 percentage points from the winner, it is a cliff - move to a plateau point and say the number is mesh-sensitive.
5. **Check limits** with `pct_vs_base` against every band from step 0.
6. **Stop and report** as soon as ONE holds:
   - every band is met and the primary target is within ~1 point of exact (if within band but further, allow at most 2 more simulations to get closer);
   - budget used >= 75% - stop exploring, spend the rest on the robustness check, then report BEST;
   - **infeasible:** two different levers both saturate short of the target, or every lever that helps breaks a constraint. Say the target looks infeasible under those constraints, name which constraint binds, and give BEST.
   Never finish with no answer. A partial result with a clear verdict beats a timeout.

## Levers measured on `Gen2_coil` at 5 mm (hints - re-verify on other configs)
| Lever | Effect |
|---|---|
| `tank_trace_thickness` 0.070 -> 0.078 / 0.105 / 0.12 / 0.136 | `tank_esr` about -5% / -9% / -11.5% / -14.7%; L, M change < 0.4%. **The clean lever.** Copper > 0.105 mm is heavy copper - respect any cap the user gives. |
| `tank_trace_width` up (5.544 -> 5.85 / 7.0) | `tank_esr` **rises** (+1.4% / +30%; +18% if thickness is already 0.105), L_tank falls (-0.5% / -3.3%), rc_esr rises too. Do not widen. Narrowing to 5.0: ESR -1%, L_tank +1% - that L headroom can pay for opening turn spacing (see below). |
| `num_tank_layers` 2 -> 3 | `tank_esr` -7.7% **but `rc_esr` -7.8% and `loop_esr` -7%** (breaks a "keep the rest the same" band). With thickness 0.105: -12.9% - about the ceiling under thickness <= 0.105 and layers <= 3. |
| `num_parallel_tank_traces` 2 | Solver error unless width/spacing are re-fitted; when it runs it is no better (-13.0%, same as without) or much worse (+18%). Avoid. |
| `tank_turn_spacing` up (7.4 -> 8.9 / 9.5) | Alone: `tank_esr` -13% / -17% but `L_tank` -5% / -7%, `M12` -3% / -4%, `M23` -2% / -3%. On top of layers 3 + 0.105 mm: -25% / -28% with the same L/M cost. Only if inductance may move. |
| **Best combined under thickness <= 0.105, layers <= 3, L/M within 1%** | layers 3 + thickness 0.105 + `tank_trace_width` 4.5 + `tank_turn_spacing` 8.0: `tank_esr` -18.4%, L_tank -0.6%, M12 -0.6%, M23 -0.9%, rc_esr -10%, loop_esr -7.7%. About the limit; -35% is not reachable there. |
| `tank_PCB_thickness` 1.6 -> 3.2 | `tank_esr` -3%, `L_tank` -3%, M -2%. Poor trade. |
| `rc_radius` 56 -> 59 / 62 | `M23` +10% / +19%, `M13` +7%, **`rc_esr` +7.5% / +17%**, tank_esr -1 to -5%. About +3.3% M23 per mm. |

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
Take % changes from `pct_vs_base` and absolute values from the tool results - do not recompute them.

## Gotchas
- A key that is not a CoilSet field is rejected with the list of valid keys.
- A solver crash (e.g. "trace space ... less than trace width") is returned as an error: read it, fix the geometry, do not retry the same overrides.
- Turns must not overlap: `*_turn_spacing` must exceed `*_trace_width`.
- Results are for one RC distance; say so if the user cares about a sweep over distance.
