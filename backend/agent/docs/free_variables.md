# Free variables the agent can change

25 `CoilSet` fields, grouped by coil (loop / tank / rc). Everything else on
`CoilSet` (ferrite, shield, backplane, PCB thickness) is fixed — not in
this list, so the model literally cannot touch it. Source: `FREE_FIELDS` in
`agent_prototype.py`.

Effect claims marked **(exact)** are copied directly from
`services/femm_service.py`'s `check_valid_coil()` — the actual formula this
codebase uses, not a guess. Everything else is standard coil-theory
heuristic, unverified against this specific topology.

## Shared geometry formula (applies to every coil below)

```
turn_width  = (num_parallel_traces - 1) * trace_spacing + trace_width
coil_width  = (num_turns - 1) * turn_spacing + turn_width
outer_diameter = 2 * (center_radius + coil_width / 2)
```

A coil is **invalid** if `center_radius < coil_width / 2` (winding crosses
the center), or if `trace_spacing < trace_width` / `turn_spacing <
turn_width` (traces or turns physically overlap). This is the exact rule
that rejected `loop_trace_width=4.5` in the real run — the trace got wider
than the spacing around it.

## Loop coil (9 fields)

| Field | Meaning | Effect on result |
|---|---|---|
| `loop_r` | Center radius (mm) | ↑ radius → ↑ self/mutual inductance, ↑ outer diameter, slightly ↑ ESR (longer trace per turn) |
| `num_loop_turns` | Turn count | ↑ turns → ↑ self/mutual inductance (~turns²), ↑ ESR (more copper), ↑ coil_width |
| `num_parallel_loop_traces` | Parallel traces per turn | ↑ → ↓ ESR (current splits across paths), ~no change to inductance, ↑ coil_width |
| `num_loop_layers` | Stacked PCB layers | ↑ → ↑ self/mutual inductance similar to more turns, but without the radial footprint cost — adds Z-height instead |
| `loop_trace_spacing` | Gap between parallel traces (mm) | ↑ → ↑ outer diameter; must stay ≥ `loop_trace_width` or invalid **(exact)** |
| `loop_turn_spacing` | Gap between turns (mm) | ↑ → ↑ outer diameter; must stay ≥ `turn_width` or invalid **(exact)** |
| `loop_trace_width` | Trace width (mm) | ↑ → ↓ ESR (more copper cross-section), but ↑ coil_width → ↑ outer diameter |
| `loop_trace_thickness` | Copper weight (mm) | Restricted to 0.035 / 0.07 / 0.14 (1oz/2oz/4oz). ↑ → ↓ ESR, ~no effect on inductance or diameter |
| `loop_offset` | Z offset from the tank PCB underside (mm) | Positioning only — shifts the loop's stack-up position; not a coil-shape parameter |

## Tank coil (8 fields)

| Field | Meaning | Effect on result |
|---|---|---|
| `tank_center_r` | Center radius (mm) | Same as `loop_r`, for the tank coil |
| `num_tank_turns` | Turn count | Same as `num_loop_turns` — **the one field the real run found most powerful**: dropping 5→4 turns cut tank ESR by -26%, but also broke the mutual-inductance gate every time it was tried, in either direction |
| `num_parallel_tank_traces` | Parallel traces per turn | Same as loop's — lowers ESR, widens footprint |
| `num_tank_layers` | Stacked PCB layers | Same as loop's |
| `tank_trace_spacing` | Gap between parallel traces (mm) | Same validity rule as loop's |
| `tank_turn_spacing` | Gap between turns (mm) | Same validity rule as loop's |
| `tank_trace_width` | Trace width (mm) | Same as loop's — **the safer lever the real run actually used**: widening this from 3.175→4.4mm gradually cut tank ESR (up to -1.0%) without breaking the M gate, until it hit the trace-overlap wall near 4.5mm |
| `tank_trace_thickness` | Copper weight (mm) | Same 3-value restriction as loop's |

## RC coil (8 fields)

| Field | Meaning | Effect on result |
|---|---|---|
| `rc_radius` | Center radius (mm) | Same as `loop_r`/`tank_center_r` |
| `num_rc_turns` | Turn count | Same as tank/loop turn count |
| `num_parallel_rc_traces` | Parallel traces per turn | Same as tank/loop |
| `num_rc_layers` | Stacked PCB layers | Same as tank/loop |
| `rc_trace_spacing` | Gap between parallel traces (mm) | Same validity rule |
| `rc_turn_spacing` | Gap between turns (mm) | Same validity rule |
| `rc_trace_width` | Trace width (mm) | Same as tank/loop |
| `rc_trace_thickness` | Copper weight (mm) | Same 3-value restriction |

## What's NOT in this list (fixed, not offered to the model)

`tank_ferrite_present`, `rc_ferrite_present` and their sub-fields (no
ferrite), `backplane`/`rc_backplane` (no metal backplane), `tc_shield`/
`rc_shield` (no shield), `tank_PCB_thickness`/`loop_PCB_thickness`/
`rc_PCB_thickness` (fixed PCB thickness) — these enforce the original
objective's hard constraints by simply never being offered as a tool
parameter, not by asking the model to respect them.
