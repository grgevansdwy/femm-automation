---
name: coil-sim
description: Find a wireless-power coil geometry (Loop/Tank/Receive at 6.78 MHz) that hits a target mutual inductance with minimum resistance, using the femm_* simulation tools. Use when asked to simulate, compare or optimize coils.
---

# Coil simulation workflow

Tools: `femm_list_configs`, `femm_get_config`, `femm_simulate`, `femm_save_config`.
Each `femm_simulate` call takes ~5 s, so you can afford ~10-15 per task.

## Units
- Geometry in **mm**; inductances (L, M) in **H**; ESR in **ohms**; k is unitless.
- Turn/layer counts are integers. Ferrite/backplane/shield `*_present` flags are 0/1.
- Coils: `loop` (drive), `tank` (transmit), `rc` (receive). `M12` loop-tank, `M13` loop-rc, `M23` tank-rc.
  Unless the user says otherwise, the target mutual is **M23** (tank to receive) and the resistance to minimise is **rc_esr + tank_esr**.

## Steps
1. **Get the target.** Ask for (or confirm) the target mutual inductance, the RC separation distance (mm, default 5), and any geometry limits (e.g. max radius). Convert units (1 uH = 1e-6 H).
2. **Pick a base.** Call `femm_list_configs`, choose the closest builtin (default `base_coil`), and call `femm_get_config` to see the parameters and their current values.
3. **Baseline.** `femm_simulate` the base config with no overrides at the target distance. Record the result.
4. **Iterate.** Change one or two parameters per run via `overrides` (typical levers: `num_tank_turns`, `num_rc_turns`, `rc_radius`, `tank_center_r`, `*_turn_spacing`, `*_trace_width`, ferrite radius/thickness). Reason from the last result: more turns raises L and M but also ESR; larger radius raises coupling at distance.
5. **Track runs.** Keep a table in your replies: run #, overrides, M (target), ESR sum, error vs target.
6. **Stop** when the target M is within 5% and ESR is no longer improving, or after 15 runs.
7. **Report.** State the best overrides, its full result, and how it compares with the baseline. If the user wants to keep it, call `femm_save_config`.

## Gotchas
- An override key that is not a CoilSet field returns an error; use the names from `femm_get_config`.
- Do not change trace geometry so that turns overlap (turn spacing must exceed trace width).
- Results are for a single distance; say so if the user cares about a sweep.
