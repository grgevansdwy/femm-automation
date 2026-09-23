# Example: the full prompt the AI gets each round

This is the exact request the AI agent would send for **round 5** of a real search, built by the current code (`optimize_loop()` in `backend/agent/optimize.py`) from that run's saved results. It's run `d0a6ab4617f9` (Gen2_coil, 15 Sep 2026), the one that timed out on round 5. Rounds 1-4 had 4 designs each.

It's a snapshot from 15 Sep 2026: if the prompt code changes later, this file won't update by itself.

## What gets sent, and how big each part is

| # | Role | What it is | Size |
|---|---|---|---|
| 1 | system | Goal, baseline, physics guidance | 9.4 KB |
| 2 | user | The task | 0.1 KB |
| 3 | user | **Search memory**: best 10 designs + every other design tried | 4.3 KB |
| 4 | assistant | The AI's own round-4 proposals, with its chain of thought removed | 1.3 KB |
| 5-8 | tool | **Full result** for each round-4 proposal | 18.9 KB total |
| - | (request field) | The `propose_coilset` tool definition | 3.7 KB |
| | | **Total** | **37.8 KB** (the old format sent ~129 KB) |

Messages 3 to the end are the **feedback**: everything the AI learns about how its designs did.

---

## Message 1 - system prompt

Built once per run by `system_prompt()` in `agent/prompts.py`.

```text
You are proposing coilset geometries for a wireless power transfer coil, using
the propose_coilset tool. The operating frequency is 6.78MHz. Each turn, propose up to 5 candidates by calling propose_coilset up to 5 times
in one reply - one call per candidate. All of a turn's candidates are simulated at the
same time, and you get every result back together (one tool result per call, same
order) before your next turn. Candidates in the same turn can't learn from each
other, so make each one test a different idea:
- probe: from the best candidate so far, change ONE lever per candidate, to learn
  which lever moves which objective
- bracket: try the same lever in both directions (a bit up, a bit down)
- exploit: once a lever clearly helps, try two or three step sizes of it
Use all 5 unless you're confident fewer would be more useful right now - e.g.
one or two careful refinements when a candidate is already very close to the goal.
Never propose the same candidate twice.
Each turn you're shown: your previous turn's full results, the 10 best
candidates found so far (one line each, best first), and a shorter line for every
other candidate already tried - so you can build on what worked and never repeat
a candidate.
Only propose valid tool calls - no free text needed once you understand the task. Always fill in the
tool's `rationale` argument with your reasoning summarized in 1-2 sentences
max: what the previous results told you (if there were any) and why this
specific change should move the objective. That summary is the only
reasoning the person watching the search reads, so make it plain and
specific, no lists.

Baseline: Gen2_coil. Fields you may change, with their baseline values:
{
  "rc_ferrite_y_offset": 0,
  "loop_offset": 10,
  "loop_r": 65,
  "num_loop_turns": 1,
  "num_parallel_loop_traces": 5,
  "num_loop_layers": 2,
  "loop_trace_spacing": 4.464,
  "loop_turn_spacing": 0,
  "tank_center_r": 72.688,
  "num_tank_turns": 5,
  "num_parallel_tank_traces": 1,
  "num_tank_layers": 2,
  "tank_trace_spacing": 0,
  "tank_turn_spacing": 7.444,
  "rc_radius": 56,
  "num_rc_turns": 3,
  "num_parallel_rc_traces": 1,
  "num_rc_layers": 2,
  "rc_trace_spacing": 0,
  "rc_turn_spacing": 6.457,
  "tank_trace_width": 5.544,
  "loop_trace_width": 3.175,
  "rc_trace_width": 3.175,
  "loop_trace_thickness": 0.07,
  "tank_trace_thickness": 0.07,
  "rc_trace_thickness": 0.07
}
Every other field is fixed - you cannot change it.

Objectives, each compared to the ORIGINAL baseline above:
  REQUIRED: match M12 (loop <-> tank mutual inductance (H)) - within +/-5% of baseline  [every distance; baseline 5.975e-07 to 6.012e-07 across distances]
  REQUIRED: match M13 (loop <-> RC mutual inductance (H)) - within +/-5% of baseline  [every distance; baseline 9.968e-08 to 2.251e-07 across distances]
  REQUIRED: match M23 (tank <-> RC mutual inductance (H), the power-transfer link) - within +/-5% of baseline  [every distance; baseline 6.882e-07 to 1.481e-06 across distances]
  REQUIRED: reduce tank_esr (tank coil ESR, AC resistance (Ohm)) - >= 20% lower  [every distance; baseline 0.3865 to 0.4272 across distances]
  REQUIRED: reduce loop_esr (loop coil ESR, AC resistance (Ohm)) - no higher than baseline  [every distance; baseline 0.01673 to 0.01738 across distances]
  REQUIRED: reduce rc_esr (RC coil ESR, AC resistance (Ohm)) - no higher than baseline  [every distance; baseline 0.1626 to 0.1785 across distances]
  REQUIRED: reduce loop_outer_diameter (loop coil outer diameter (mm)) - no higher than baseline  [no distance; baseline 151]
  REQUIRED: reduce tank_outer_diameter (tank coil outer diameter (mm)) - no higher than baseline  [no distance; baseline 180.7]
  REQUIRED: reduce rc_outer_diameter (RC coil outer diameter (mm)) - no higher than baseline  [no distance; baseline 128.1]
  REQUIRED: reduce L_loop (loop coil self-inductance (H)) - no higher than baseline  [every distance; baseline 1.91e-07 to 1.915e-07 across distances]
  REQUIRED: reduce L_tank (tank coil self-inductance (H)) - no higher than baseline  [every distance; baseline 4.633e-06 to 4.662e-06 across distances]
  REQUIRED: reduce L_rc (RC (receive) coil self-inductance (H)) - no higher than baseline  [every distance; baseline 1.608e-06 to 1.618e-06 across distances]
  Simulated quantities are checked at every distance ([10.0, 20.0, 30.0, 40.0] mm) and the
  worst distance decides; design variables and outer diameters have no distance.
  ALL required objectives must pass at once for the goal to be met - a large
  win on one does not make up for missing another. Coils are
  proximity-coupled, so a change aimed at one quantity can move another -
  watch every objective, not just the one you are working on. Preferred
  objectives only break ties between candidates; nothing else is ranked.

Physics reference - heuristics to guide your search. FEMM is ground truth: when a returned result contradicts anything below, trust the result.

Which levers move which quantity:
- Self-inductance (L) and mutual inductance / coupling (M, k) are set mainly
  by coil AREA and TURN COUNT: radius (*_r, *_radius, *_center_r),
  num_*_turns, num_*_layers - plus any ferrite or metal near the coils.
- ESR is set mainly by COPPER CROSS-SECTION and PROXIMITY LOSS: trace
  thickness, trace width, turn spacing, parallel traces.
These two sets barely overlap: to move one family while holding the other,
change that family's own levers and leave the other family's alone.

Per lever (each works in both directions - reverse the change to reverse
the effect):
- radius: larger -> higher L, higher M/k with the coils it faces, larger
  outer diameter, slightly higher ESR (longer trace per turn).
- num_*_turns: self-L scales roughly with turns SQUARED; M between two
  coils scales with the PRODUCT of their turn counts (N_a * N_b). More turns
  also raise ESR (more copper in series) and widen coil_width.
- num_*_layers: stacks turns in Z instead of radially - raises L and M like
  more turns, without the outer-diameter cost. Adds Z-height, which slightly
  increases effective separation. Layers share the uneven-current problem
  of parallel traces.
- *_trace_thickness (allowed values: 0.035 (1oz), 0.07 (2oz), 0.14 (4oz)): thicker lowers ESR
  only while thickness stays at or under the copper skin depth (~0.206mm at
  100kHz, ~0.146mm at 200kHz, ~0.065mm at 1MHz); beyond ~2x skin depth it
  stops helping and proximity loss grows. Little effect on L, M or diameter.
- *_turn_spacing: wider spacing lowers proximity loss (lower ESR) and
  slightly lowers L (weaker turn-to-turn coupling); tighter does the
  opposite. Changes outer diameter by (num_turns-1) * the spacing change.
- *_trace_width: wider -> more copper -> lower DC resistance, but the
  benefit saturates and can reverse (eddy currents across a wide trace in
  its own field). Bounded by validity: with one parallel trace,
  turn_width = trace_width must stay below turn_spacing.
- num_parallel_*_traces: lowers ESR, but current does not split evenly (the
  inner trace is shorter and lower-impedance, so current crowds into it),
  so the gain is well short of 1/N - judge it by the returned ESR. Widens
  the coil by (num_parallel_traces-1)*trace_spacing.
- *_trace_spacing: mostly geometry/validity; same proximity logic as
  turn_spacing at a smaller scale.

Geometry validity (exact - from services/femm_service.py check_valid_coil):
  turn_width = (num_parallel_traces-1)*trace_spacing + trace_width
  coil_width = (num_turns-1)*turn_spacing + turn_width
INVALID if center_radius < coil_width/2 (winding crosses the axis). Overlap
checks (trace_spacing < trace_width, turn_spacing < turn_width) apply only
where there are multiple parallel traces / multiple turns respectively - a
single-trace or single-turn coil legitimately has spacing 0.
Outer diameter = 2*(center_radius + coil_width/2). Widening traces or
opening spacing grows coil_width, which grows outer diameter AND pushes
toward the center_radius < coil_width/2 wall - re-check both.

Distance (RC separation) is NOT proposable: every candidate is evaluated at [10.0, 20.0, 30.0, 40.0] mm.
Coil center radii at the start point: loop 65mm, tank 72.688mm, rc 56mm - so the largest gap is 0.71x the smallest radius. When the gap is small compared with the radii, coupling is
near-field and changes gently with distance (not a steep 1/d^3 dipole falloff), so an offset M/k
tends to show at every distance at once; as the gap approaches the radius, M/k fall off
increasingly steeply and the farthest distance becomes the hardest to hold.

After each proposal you'll get the result back: per objective, its value,
its % change vs. baseline (at each distance, for simulated quantities),
whether it passed, and its margin (>= 0 passes; negative = how far short,
as a fraction of its tolerance, so margins of different objectives compare
directly). The required objective with the most negative margin is your
current bottleneck - fix that one next, while keeping any "match"
objectives inside their bands. goal_met is true only once every required
objective passes. Stop calling the tool (respond with plain text instead)
once you believe you've found a good candidate or are stuck.
```

## Message 2 - task

```text
Find a candidate that meets the objective. Start proposing.
```

## Message 3 - search memory (feedback, summarised)

Rebuilt every round by `search_memory()` in `agent/optimize.py`. It has two lists:

- **Best 10 designs so far**, best first: every objective with its worst-distance change and ok/MISS.
- **Other designs already tried**: only the objectives each one missed (or why it was rejected), so the AI doesn't repeat them.

```text
Best 10 candidate(s) so far, best first (each = baseline + the listed changes):
  #1 round 4: {'num_tank_layers': 8} -> goal_met=False: M12 -0.3% ok, M13 -0.2% ok, M23 -0.2% ok, tank_esr -17.4% MISS, loop_esr -8.1% ok, rc_esr -8.3% ok, loop_outer_diameter +0.0% ok, tank_outer_diameter +0.0% ok, rc_outer_diameter +0.0% ok, L_loop -0.1% ok, L_tank -0.3% ok, L_rc +0.2% MISS
  #2 round 3: {'num_tank_layers': 5} -> goal_met=False: M12 -0.2% ok, M13 -0.1% ok, M23 -0.1% ok, tank_esr -13.2% MISS, loop_esr -7.2% ok, rc_esr -7.1% ok, loop_outer_diameter +0.0% ok, tank_outer_diameter +0.0% ok, rc_outer_diameter +0.0% ok, L_loop -0.1% ok, L_tank -0.3% ok, L_rc +0.2% MISS
  #3 round 4: {'num_tank_layers': 6} -> goal_met=False: M12 -0.2% ok, M13 -0.2% ok, M23 -0.1% ok, tank_esr -15.7% MISS, loop_esr -8.5% ok, rc_esr -7.7% ok, loop_outer_diameter +0.0% ok, tank_outer_diameter +0.0% ok, rc_outer_diameter +0.0% ok, L_loop +0.1% MISS, L_tank -0.3% ok, L_rc +0.4% MISS
  #4 round 4: {'num_tank_layers': 6, 'num_parallel_tank_traces': 2, 'tank_trace_spacing': 3.0, 'tank_trace_width': 3.0, 'tank_turn_spacing': 7.3} -> goal_met=False: M12 -0.3% ok, M13 -0.2% ok, M23 -0.3% ok, tank_esr -10.8% MISS, loop_esr -10.4% ok, rc_esr -10.2% ok, loop_outer_diameter +0.0% ok, tank_outer_diameter -0.1% ok, rc_outer_diameter +0.0% ok, L_loop -0.2% ok, L_tank -0.8% ok, L_rc +0.2% MISS
  #5 round 4: {'num_tank_layers': 6, 'tank_trace_thickness': 0.14} -> goal_met=False: M12 -0.2% ok, M13 +0.1% ok, M23 -0.1% ok, tank_esr -13.3% MISS, loop_esr -6.4% ok, rc_esr -6.9% ok, loop_outer_diameter +0.0% ok, tank_outer_diameter +0.0% ok, rc_outer_diameter +0.0% ok, L_loop -0.1% ok, L_tank -0.7% ok, L_rc +0.6% MISS
  #6 round 3: {'num_tank_layers': 4} -> goal_met=False: M12 -0.2% ok, M13 -0.2% ok, M23 -0.1% ok, tank_esr -7.8% MISS, loop_esr -6.2% ok, rc_esr -6.9% ok, loop_outer_diameter +0.0% ok, tank_outer_diameter +0.0% ok, rc_outer_diameter +0.0% ok, L_loop -0.1% ok, L_tank -0.3% ok, L_rc +0.1% MISS
  #7 round 2: {'num_tank_layers': 3} -> goal_met=False: M12 -0.2% ok, M13 -0.2% ok, M23 -0.1% ok, tank_esr -7.3% MISS, loop_esr -6.9% ok, rc_esr -7.7% ok, loop_outer_diameter +0.0% ok, tank_outer_diameter +0.0% ok, rc_outer_diameter +0.0% ok, L_loop -0.1% ok, L_tank -0.1% ok, L_rc +0.2% MISS
  #8 round 2: {'num_parallel_tank_traces': 2, 'tank_trace_spacing': 3.0, 'tank_trace_width': 3.0, 'tank_turn_spacing': 7.3} -> goal_met=False: M12 -0.1% ok, M13 +0.2% ok, M23 +0.1% ok, tank_esr -5.6% MISS, loop_esr -7.2% ok, rc_esr -6.8% ok, loop_outer_diameter +0.0% ok, tank_outer_diameter -0.1% ok, rc_outer_diameter +0.0% ok, L_loop -0.1% ok, L_tank -0.2% ok, L_rc +0.2% MISS
  #9 round 1: {'tank_trace_thickness': 0.14} -> goal_met=False: M12 -0.2% ok, M13 +0.1% ok, M23 -0.2% ok, tank_esr -4.3% MISS, loop_esr +0.8% MISS, rc_esr +0.6% MISS, loop_outer_diameter +0.0% ok, tank_outer_diameter +0.0% ok, rc_outer_diameter +0.0% ok, L_loop -0.2% ok, L_tank -0.4% ok, L_rc +0.2% MISS
  #10 round 1: {'loop_trace_thickness': 0.14} -> goal_met=False: M12 -0.4% ok, M13 -0.4% ok, M23 -0.3% ok, tank_esr -0.7% MISS, loop_esr -6.7% ok, rc_esr +0.0% MISS, loop_outer_diameter +0.0% ok, tank_outer_diameter +0.0% ok, rc_outer_diameter +0.0% ok, L_loop -0.6% ok, L_tank -0.0% ok, L_rc +0.2% MISS
Other candidates already tried - don't propose these again:
  round 1: {'rc_trace_thickness': 0.14} -> misses: tank_esr +0.0%, loop_esr +0.3%, L_loop +0.0%, L_tank +0.1%, L_rc +0.1%
  round 1: {'num_parallel_tank_traces': 2, 'tank_trace_spacing': 5.6, 'tank_turn_spacing': 6.0} -> rejected: Exception('turn space of 6.0 is less than total turn width of 11.143999999999998, ')
  round 2: {'tank_trace_width': 3.0} -> misses: tank_esr +13.6%, L_loop +0.6%, L_tank +5.0%, L_rc +0.5%
  round 2: {'tank_trace_width': 1.5} -> misses: tank_esr +58.4%, rc_esr +0.4%, L_loop +0.8%, L_tank +9.1%, L_rc +0.4%
  round 3: {'tank_trace_thickness': 0.035} -> misses: tank_esr +25.6%, loop_esr +3.6%, rc_esr +1.6%, L_tank +0.3%, L_rc +0.2%
  round 3: {'num_parallel_tank_traces': 2, 'tank_trace_spacing': 3.5, 'tank_trace_width': 3.5, 'tank_turn_spacing': 7.0} -> misses: tank_esr +395.6%, loop_esr +22.9%, rc_esr +51.6%, L_rc +0.2%
Your latest turn's full results follow.
```

## Message 4 - the AI's previous proposals

The AI's round-4 reply, sent back so it knows which result belongs to which proposal. The chain of thought is removed; only the tool calls remain. (Tool call ids aren't saved in History, so `call_1`... stand in for the server's real ids.)

```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [
    {
      "id": "call_1",
      "type": "function",
      "function": {
        "name": "propose_coilset",
        "arguments": {
          "num_tank_layers": 6,
          "rationale": "Tank layers kept paying off (2\u21925 layers gave -13.2% ESR with L and M flat), so push to 6 layers to see if the trend continues."
        }
      }
    },
    {
      "id": "call_2",
      "type": "function",
      "function": {
        "name": "propose_coilset",
        "arguments": {
          "num_tank_layers": 8,
          "rationale": "Bracket the layer lever higher at 8 tank layers, since layers cost no outer diameter and barely move L or M, to find where tank ESR flattens out."
        }
      }
    },
    {
      "id": "call_3",
      "type": "function",
      "function": {
        "name": "propose_coilset",
        "arguments": {
          "num_tank_layers": 6,
          "tank_trace_thickness": 0.14,
          "rationale": "Test whether the two helpful tank levers stack: 6 layers plus 4oz (0.14mm) copper, which alone gave -4.4%, to see if total cuts approach the required 20%."
        }
      }
    },
    {
      "id": "call_4",
      "type": "function",
      "function": {
        "name": "propose_coilset",
        "arguments": {
          "num_tank_layers": 6,
          "num_parallel_tank_traces": 2,
          "tank_trace_spacing": 3.0,
          "tank_trace_width": 3.0,
          "tank_turn_spacing": 7.3,
          "rationale": "Second parallel tank trace added on top of 6 layers but with a real 1.3mm gap between turns (7.3 spacing, 6.0 turn width), since the earlier touching-turn packing blew up ESR."
        }
      }
    }
  ]
}
```

## Messages 5-8 - full results for the latest round (feedback, full detail)

One `tool` message per proposal, answering its `propose_coilset` call (linked by `tool_call_id`). Built by `tool_result()` in `agent/scorer.py`. It's **sent as one compact line**; it's indented here only so you can read it. Each objective carries: the requirement, % change at every distance, the worst change, the raw value and the baseline value, pass/fail, and the margin (how far inside or outside the requirement, as a fraction of its tolerance).

### Result 1 - `{'num_tank_layers': 6}` (4.2 KB as sent)

```json
{
  "valid": true,
  "goal_met": false,
  "objectives": [
    {
      "metric": "M12",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": -0.17,
        "20.0": -0.12,
        "30.0": -0.17,
        "40.0": -0.13
      },
      "worst_change_pct": -0.17,
      "value": 5.96441e-07,
      "baseline_value": 5.97461e-07,
      "passed": true,
      "margin": 0.966
    },
    {
      "metric": "M13",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": -0.2,
        "20.0": 0.04,
        "30.0": 0.06,
        "40.0": 0.16
      },
      "worst_change_pct": -0.2,
      "value": 2.24677e-07,
      "baseline_value": 2.2512e-07,
      "passed": true,
      "margin": 0.961
    },
    {
      "metric": "M23",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": -0.15,
        "20.0": -0.07,
        "30.0": 0.04,
        "40.0": 0.06
      },
      "worst_change_pct": -0.15,
      "value": 1.47871e-06,
      "baseline_value": 1.48087e-06,
      "passed": true,
      "margin": 0.971
    },
    {
      "metric": "tank_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 20.0,
      "required": true,
      "requirement": ">= 20% lower",
      "change_pct_by_distance": {
        "10.0": -15.67,
        "20.0": -16.09,
        "30.0": -16.2,
        "40.0": -16.21
      },
      "worst_change_pct": -15.67,
      "value": 0.36026,
      "baseline_value": 0.427199,
      "passed": false,
      "margin": -0.217
    },
    {
      "metric": "loop_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -8.46,
        "20.0": -8.51,
        "30.0": -8.48,
        "40.0": -8.47
      },
      "worst_change_pct": -8.46,
      "value": 0.015907,
      "baseline_value": 0.0173771,
      "passed": true,
      "margin": 8.46
    },
    {
      "metric": "rc_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -8.29,
        "20.0": -8.05,
        "30.0": -7.67,
        "40.0": -8.03
      },
      "worst_change_pct": -7.67,
      "value": 0.150886,
      "baseline_value": 0.163422,
      "passed": true,
      "margin": 7.671
    },
    {
      "metric": "loop_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 151.031,
      "baseline_value": 151.031,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "tank_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 180.696,
      "baseline_value": 180.696,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "rc_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 128.089,
      "baseline_value": 128.089,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "L_loop",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.05,
        "20.0": -0.01,
        "30.0": -0.06,
        "40.0": 0.05
      },
      "worst_change_pct": 0.05,
      "value": 1.91587e-07,
      "baseline_value": 1.91489e-07,
      "passed": false,
      "margin": -0.051
    },
    {
      "metric": "L_tank",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.35,
        "20.0": -0.37,
        "30.0": -0.37,
        "40.0": -0.34
      },
      "worst_change_pct": -0.34,
      "value": 4.64632e-06,
      "baseline_value": 4.66234e-06,
      "passed": true,
      "margin": 0.344
    },
    {
      "metric": "L_rc",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.07,
        "20.0": 0.2,
        "30.0": 0.14,
        "40.0": 0.42
      },
      "worst_change_pct": 0.42,
      "value": 1.62497e-06,
      "baseline_value": 1.61825e-06,
      "passed": false,
      "margin": -0.415
    }
  ]
}
```

### Result 2 - `{'num_tank_layers': 8}` (4.2 KB as sent)

```json
{
  "valid": true,
  "goal_met": false,
  "objectives": [
    {
      "metric": "M12",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": -0.17,
        "20.0": -0.27,
        "30.0": -0.31,
        "40.0": -0.28
      },
      "worst_change_pct": -0.31,
      "value": 5.99022e-07,
      "baseline_value": 6.00905e-07,
      "passed": true,
      "margin": 0.937
    },
    {
      "metric": "M13",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": -0.11,
        "20.0": -0.17,
        "30.0": -0.09,
        "40.0": 0.08
      },
      "worst_change_pct": -0.17,
      "value": 1.6744e-07,
      "baseline_value": 1.67731e-07,
      "passed": true,
      "margin": 0.965
    },
    {
      "metric": "M23",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": -0.09,
        "20.0": -0.17,
        "30.0": -0.01,
        "40.0": 0.01
      },
      "worst_change_pct": -0.17,
      "value": 1.12777e-06,
      "baseline_value": 1.12974e-06,
      "passed": true,
      "margin": 0.965
    },
    {
      "metric": "tank_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 20.0,
      "required": true,
      "requirement": ">= 20% lower",
      "change_pct_by_distance": {
        "10.0": -17.42,
        "20.0": -17.87,
        "30.0": -18.14,
        "40.0": -18.14
      },
      "worst_change_pct": -17.42,
      "value": 0.352761,
      "baseline_value": 0.427199,
      "passed": false,
      "margin": -0.129
    },
    {
      "metric": "loop_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -8.17,
        "20.0": -8.13,
        "30.0": -8.1,
        "40.0": -8.16
      },
      "worst_change_pct": -8.1,
      "value": 0.0154492,
      "baseline_value": 0.0168112,
      "passed": true,
      "margin": 8.102
    },
    {
      "metric": "rc_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -8.97,
        "20.0": -8.85,
        "30.0": -8.38,
        "40.0": -8.34
      },
      "worst_change_pct": -8.34,
      "value": 0.149046,
      "baseline_value": 0.16261,
      "passed": true,
      "margin": 8.341
    },
    {
      "metric": "loop_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 151.031,
      "baseline_value": 151.031,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "tank_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 180.696,
      "baseline_value": 180.696,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "rc_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 128.089,
      "baseline_value": 128.089,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "L_loop",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.09,
        "20.0": -0.2,
        "30.0": -0.25,
        "40.0": -0.2
      },
      "worst_change_pct": -0.09,
      "value": 1.90834e-07,
      "baseline_value": 1.91014e-07,
      "passed": true,
      "margin": 0.094
    },
    {
      "metric": "L_tank",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.34,
        "20.0": -0.4,
        "30.0": -0.43,
        "40.0": -0.43
      },
      "worst_change_pct": -0.34,
      "value": 4.61712e-06,
      "baseline_value": 4.63306e-06,
      "passed": true,
      "margin": 0.344
    },
    {
      "metric": "L_rc",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.08,
        "20.0": 0.04,
        "30.0": 0.2,
        "40.0": 0.25
      },
      "worst_change_pct": 0.25,
      "value": 1.62233e-06,
      "baseline_value": 1.61825e-06,
      "passed": false,
      "margin": -0.252
    }
  ]
}
```

### Result 3 - `{'num_tank_layers': 6, 'tank_trace_thickness': 0.14}` (4.2 KB as sent)

```json
{
  "valid": true,
  "goal_met": false,
  "objectives": [
    {
      "metric": "M12",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": -0.16,
        "20.0": -0.13,
        "30.0": -0.14,
        "40.0": -0.09
      },
      "worst_change_pct": -0.16,
      "value": 5.96483e-07,
      "baseline_value": 5.97461e-07,
      "passed": true,
      "margin": 0.967
    },
    {
      "metric": "M13",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": 0.09,
        "20.0": 0.11,
        "30.0": 0.11,
        "40.0": 0.14
      },
      "worst_change_pct": 0.14,
      "value": 9.98201e-08,
      "baseline_value": 9.9682e-08,
      "passed": true,
      "margin": 0.972
    },
    {
      "metric": "M23",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": -0.1,
        "20.0": -0.15,
        "30.0": -0.03,
        "40.0": -0.11
      },
      "worst_change_pct": -0.15,
      "value": 1.1281e-06,
      "baseline_value": 1.12974e-06,
      "passed": true,
      "margin": 0.971
    },
    {
      "metric": "tank_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 20.0,
      "required": true,
      "requirement": ">= 20% lower",
      "change_pct_by_distance": {
        "10.0": -13.27,
        "20.0": -13.7,
        "30.0": -13.87,
        "40.0": -13.87
      },
      "worst_change_pct": -13.27,
      "value": 0.370494,
      "baseline_value": 0.427199,
      "passed": false,
      "margin": -0.336
    },
    {
      "metric": "loop_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -6.4,
        "20.0": -6.48,
        "30.0": -6.5,
        "40.0": -6.52
      },
      "worst_change_pct": -6.4,
      "value": 0.0162647,
      "baseline_value": 0.0173771,
      "passed": true,
      "margin": 6.401
    },
    {
      "metric": "rc_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -6.86,
        "20.0": -7.12,
        "30.0": -7.43,
        "40.0": -7.4
      },
      "worst_change_pct": -6.86,
      "value": 0.166286,
      "baseline_value": 0.178539,
      "passed": true,
      "margin": 6.863
    },
    {
      "metric": "loop_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 151.031,
      "baseline_value": 151.031,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "tank_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 180.696,
      "baseline_value": 180.696,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "rc_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 128.089,
      "baseline_value": 128.089,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "L_loop",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.13,
        "20.0": -0.1,
        "30.0": -0.13,
        "40.0": -0.07
      },
      "worst_change_pct": -0.07,
      "value": 1.91348e-07,
      "baseline_value": 1.91489e-07,
      "passed": true,
      "margin": 0.074
    },
    {
      "metric": "L_tank",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.72,
        "20.0": -0.68,
        "30.0": -0.69,
        "40.0": -0.66
      },
      "worst_change_pct": -0.66,
      "value": 4.63139e-06,
      "baseline_value": 4.66234e-06,
      "passed": true,
      "margin": 0.664
    },
    {
      "metric": "L_rc",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.14,
        "20.0": 0.17,
        "30.0": 0.43,
        "40.0": 0.56
      },
      "worst_change_pct": 0.56,
      "value": 1.62738e-06,
      "baseline_value": 1.61825e-06,
      "passed": false,
      "margin": -0.564
    }
  ]
}
```

### Result 4 - `{'num_tank_layers': 6, 'num_parallel_tank_traces': 2, 'tank_trace_spacing': 3.0, 'tank_trace_width': 3.0, 'tank_turn_spacing': 7.3}` (4.2 KB as sent)

```json
{
  "valid": true,
  "goal_met": false,
  "objectives": [
    {
      "metric": "M12",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": -0.3,
        "20.0": -0.22,
        "30.0": -0.28,
        "40.0": -0.34
      },
      "worst_change_pct": -0.34,
      "value": 5.99148e-07,
      "baseline_value": 6.01182e-07,
      "passed": true,
      "margin": 0.932
    },
    {
      "metric": "M13",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": -0.16,
        "20.0": 0.04,
        "30.0": -0.12,
        "40.0": -0.07
      },
      "worst_change_pct": -0.16,
      "value": 2.24768e-07,
      "baseline_value": 2.2512e-07,
      "passed": true,
      "margin": 0.969
    },
    {
      "metric": "M23",
      "kind": "match",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 5.0,
      "required": true,
      "requirement": "within +/-5% of baseline",
      "change_pct_by_distance": {
        "10.0": 0.17,
        "20.0": -0.01,
        "30.0": -0.21,
        "40.0": -0.32
      },
      "worst_change_pct": -0.32,
      "value": 6.85953e-07,
      "baseline_value": 6.8818e-07,
      "passed": true,
      "margin": 0.935
    },
    {
      "metric": "tank_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 20.0,
      "required": true,
      "requirement": ">= 20% lower",
      "change_pct_by_distance": {
        "10.0": -11.09,
        "20.0": -11.1,
        "30.0": -11.09,
        "40.0": -10.83
      },
      "worst_change_pct": -10.83,
      "value": 0.344667,
      "baseline_value": 0.386549,
      "passed": false,
      "margin": -0.458
    },
    {
      "metric": "loop_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -10.45,
        "20.0": -10.57,
        "30.0": -10.57,
        "40.0": -10.43
      },
      "worst_change_pct": -10.43,
      "value": 0.0149829,
      "baseline_value": 0.0167274,
      "passed": true,
      "margin": 10.429
    },
    {
      "metric": "rc_esr",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -10.24,
        "20.0": -10.82,
        "30.0": -10.91,
        "40.0": -10.65
      },
      "worst_change_pct": -10.24,
      "value": 0.16025,
      "baseline_value": 0.178539,
      "passed": true,
      "margin": 10.244
    },
    {
      "metric": "loop_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 151.031,
      "baseline_value": 151.031,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "tank_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": -0.07,
      "value": 180.576,
      "baseline_value": 180.696,
      "passed": true,
      "margin": 0.066
    },
    {
      "metric": "rc_outer_diameter",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {},
      "worst_change_pct": 0.0,
      "value": 128.089,
      "baseline_value": 128.089,
      "passed": true,
      "margin": -0.0
    },
    {
      "metric": "L_loop",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.29,
        "20.0": -0.22,
        "30.0": -0.27,
        "40.0": -0.32
      },
      "worst_change_pct": -0.22,
      "value": 1.90897e-07,
      "baseline_value": 1.91319e-07,
      "passed": true,
      "margin": 0.221
    },
    {
      "metric": "L_tank",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.77,
        "20.0": -0.76,
        "30.0": -0.81,
        "40.0": -0.84
      },
      "worst_change_pct": -0.76,
      "value": 4.61476e-06,
      "baseline_value": 4.65026e-06,
      "passed": true,
      "margin": 0.763
    },
    {
      "metric": "L_rc",
      "kind": "reduce",
      "target_pct": 0.0,
      "target_value": null,
      "tolerance_pct": 0.0,
      "required": true,
      "requirement": "no higher than baseline",
      "change_pct_by_distance": {
        "10.0": -0.22,
        "20.0": 0.13,
        "30.0": 0.19,
        "40.0": -0.04
      },
      "worst_change_pct": 0.19,
      "value": 1.61996e-06,
      "baseline_value": 1.61692e-06,
      "passed": false,
      "margin": -0.188
    }
  ]
}
```

---

## Appendix - the tool definition

Sent in the request's `tools` field (not a message). It defines what the AI may change and how.

```json
[
  {
    "type": "function",
    "function": {
      "name": "propose_coilset",
      "description": "Propose one candidate coilset as a delta from the Gen2_coil baseline. Call it several times in one reply to try several candidates at once - they're simulated together, and each call gets its own result. Only include fields you want to change from baseline - anything omitted keeps its baseline value. You'll get back each objective's value and % change vs. baseline (at every distance, for simulated quantities) with pass/fail and margin, and whether it meets the goal.",
      "parameters": {
        "type": "object",
        "properties": {
          "loop_offset": {
            "type": "number",
            "description": "Loop Z offset (mm).",
            "minimum": 0.01
          },
          "loop_r": {
            "type": "number",
            "description": "Loop Center radius.",
            "minimum": 0.01
          },
          "num_loop_turns": {
            "type": "integer",
            "description": "Number of Loop Turns.",
            "minimum": 0
          },
          "num_parallel_loop_traces": {
            "type": "integer",
            "description": "Number of Parallel Loop traces.",
            "minimum": 0
          },
          "num_loop_layers": {
            "type": "integer",
            "description": "Number of Loop Layers.",
            "minimum": 0
          },
          "loop_trace_spacing": {
            "type": "number",
            "description": "Loop Trace Center-Center Spacing (mm).",
            "minimum": 0.01
          },
          "loop_turn_spacing": {
            "type": "number",
            "description": "Loop Turn Center-Center Spacing (mm).",
            "minimum": 0.01
          },
          "loop_trace_width": {
            "type": "number",
            "description": "Loop Trace Width (mm).",
            "minimum": 0.01
          },
          "loop_trace_thickness": {
            "type": "number",
            "enum": [
              0.035,
              0.07,
              0.14
            ],
            "description": "Loop Trace Thickness (mm). Must be one of 0.035 (1oz), 0.07 (2oz), 0.14 (4oz)."
          },
          "tank_center_r": {
            "type": "number",
            "description": "Tank Center radius (mm).",
            "minimum": 0.01
          },
          "num_tank_turns": {
            "type": "integer",
            "description": "Number of Tank Turns.",
            "minimum": 0
          },
          "num_parallel_tank_traces": {
            "type": "integer",
            "description": "Number of Parallel Tank Traces.",
            "minimum": 0
          },
          "num_tank_layers": {
            "type": "integer",
            "description": "Number of Tank Layers.",
            "minimum": 0
          },
          "tank_trace_spacing": {
            "type": "number",
            "description": "Tank Trace Center-Center Spacing (mm).",
            "minimum": 0.01
          },
          "tank_turn_spacing": {
            "type": "number",
            "description": "Tank Turn Center-Center Spacing (mm).",
            "minimum": 0.01
          },
          "tank_trace_width": {
            "type": "number",
            "description": "Tank Trace Width (mm).",
            "minimum": 0.01
          },
          "tank_trace_thickness": {
            "type": "number",
            "enum": [
              0.035,
              0.07,
              0.14
            ],
            "description": "Tank Trace Thickness (mm). Must be one of 0.035 (1oz), 0.07 (2oz), 0.14 (4oz)."
          },
          "rc_radius": {
            "type": "number",
            "description": "RC central turn radius (mm).",
            "minimum": 0.01
          },
          "num_rc_turns": {
            "type": "integer",
            "description": "Number of RC Turns.",
            "minimum": 0
          },
          "num_parallel_rc_traces": {
            "type": "integer",
            "description": "Number of Parallel RC Traces.",
            "minimum": 0
          },
          "num_rc_layers": {
            "type": "integer",
            "description": "Number of RC Layers.",
            "minimum": 0
          },
          "rc_trace_spacing": {
            "type": "number",
            "description": "RC trace Center-Center spacing (mm).",
            "minimum": 0.01
          },
          "rc_turn_spacing": {
            "type": "number",
            "description": "RC turn Center-Center spacing (mm).",
            "minimum": 0.01
          },
          "rc_trace_width": {
            "type": "number",
            "description": "RC Trace Width (mm).",
            "minimum": 0.01
          },
          "rc_trace_thickness": {
            "type": "number",
            "enum": [
              0.035,
              0.07,
              0.14
            ],
            "description": "RC Trace Thickness (mm). Must be one of 0.035 (1oz), 0.07 (2oz), 0.14 (4oz)."
          },
          "rc_ferrite_y_offset": {
            "type": "number",
            "description": "RC Ferrite +Y offset (mm).",
            "minimum": 0.01
          },
          "rationale": {
            "type": "string",
            "description": "Your reasoning, summarized in 1-2 sentences max: what the previous result told you (if there was one) and why this change should move the objective. Plain words, no lists."
          }
        },
        "required": [
          "rationale"
        ],
        "additionalProperties": false
      }
    }
  }
]
```
