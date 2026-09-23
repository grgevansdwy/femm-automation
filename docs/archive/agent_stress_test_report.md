# AI Agent Stress Test Report

**Date run:** 2026-09-14 · **Branch:** `ai-agent-panel-2026-09-11` (uncommitted working tree, including the k-coefficient JSON fix)
**LLM:** `Qwen3.8-Flash-Next-UD-Q4_K_XL` on the office llama.cpp server (`192.168.1.74:8854`) · **Solver:** xfemm `femmcli`, 4 CPU workers

## Contents
1. [Summary](#1-summary)
2. [How the test was run](#2-how-the-test-was-run)
3. [Phase 1 – Simulation engine](#3-phase-1--simulation-engine)
4. [Phase 2 – Scoring](#4-phase-2--scoring)
5. [Phase 3 – Plan checking](#5-phase-3--plan-checking)
6. [Phase 4 – Live end-to-end runs](#6-phase-4--live-end-to-end-runs)
7. [Phase 5 – Re-runs](#7-phase-5--re-runs)
8. [Bugs and suggested fixes](#8-bugs-and-suggested-fixes)
9. [Suggested order of work](#9-suggested-order-of-work)
10. [Open questions](#10-open-questions)

---

## 1. Summary

The scoring path that crashed the k-coefficient search is now solid: 26,740 scorings with no failures, and the
k-coefficient scenario met its goal live. The remaining problems are **before** the search (plans that are malformed,
in the wrong units, or have impossible distances) and **around** it (a slow LLM reply ends the whole search).

| Area | Checked | Result |
|---|---|---|
| Simulation engine | 54 fresh solves, all 18 configs | 0 bugs, 2 observations |
| Scoring | 26,740 scorings | 0 failures |
| Plan checking | 100 plans (50 cases × 2 baselines) | 42 accepted (incl. 12 that should have been rejected), 44 rejected cleanly, **14 crashed** |
| Distance limits | 6 extreme distances | **4 wrong or failed** |
| Live runs | 6 concurrent + 1 solo search, 28 follow-up questions | 4 goal met, 1 budget exhausted, **2 timed out**; 1 wrong-unit plan; 2 plans needed a nudge |

**Most serious bugs** (details and fixes in [section 8](#8-bugs-and-suggested-fixes)):

| # | Bug | Severity |
|---|---|---|
| B1 | Absolute target in wrong units silently accepted (7e-7 H saved as 7.0 H) | High |
| B2 | One slow LLM reply (> 600 s) fails the whole search, no retry | High |
| B3 | Malformed plan crashes the chat turn; GUI shows no error | High |
| B4 | No distance limits (0 / negative give meaningless numbers; huge values hang or crash) | High |
| B5 | A rejected plan stalls until the user types again | Medium |
| B6 | `required: "false"` (text) is treated as required | Medium |
| B7 | Follow-up answers occasionally wrong, time out, or name a non-existent button | Medium |
| B8 | Search feedback reports the baseline at a shifting "worst" distance, confusing the optimizer | Medium |
| B9 | Requests interpreted too narrowly (layers "1 or 2" frozen at 2; misleading copper-weight text) | Medium |
| B10–B13 | Shared cache labels, M23 trend check, unhelpful error messages, NaN accepted | Low |

---

## 2. How the test was run

- **Headless**, calling the same functions the GUI calls (`run_interview_turn`, `AgentRunner`, `run_followup_turn`,
  `JobRunner`) - not through the GUI window and not via `python -m agent` (that wrapper writes to the real database
  and runs 50 rounds).
- **Isolated database** per phase: a copy of the real configs with **no cached runs**, so every FEMM solve was fresh
  (fresh solves are the path that hid the original numpy bug). The real database was not written to.
- **Live searches capped at 3 rounds.** Phase 4 ran 3 searches at a time on the shared LLM server; a GUI search by
  the user (`b42473683910`, Gen2_coil) was also running during part of it.
- Test scripts: `stress/phase1_engine.py` … `phase5_reruns.py` (session scratch folder; not yet in the repo).

---

## 3. Phase 1 – Simulation engine

**What:** every config × 1, 5 and 30 mm = 54 fresh solves. Checked: every value is a plain Python number, finite,
L and ESR > 0, |k| ≤ 1, JSON round trip, and the cached copy equals the fresh result.

**Result: 54/54 solved. No type or value problems.**

### 3.1 All results (H for L/M, Ω for ESR)

<details><summary>Show all 54 rows</summary>

| Config | mm | L_loop | L_tank | L_rc | M12 | M13 | M23 | loop_esr | tank_esr | rc_esr | k12 | k13 | k23 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base_coil | 1 | 5.516e-07 | 7.505e-06 | 3.447e-06 | 1.061e-06 | 6.776e-07 | 2.66e-06 | 0.1234 | 1.126 | 0.8148 | 0.5214 | 0.4914 | 0.5229 |
| base_coil | 5 | 5.429e-07 | 7.579e-06 | 3.294e-06 | 1.06e-06 | 5.769e-07 | 2.57e-06 | 0.113 | 1.056 | 0.6149 | 0.5224 | 0.4314 | 0.5144 |
| base_coil | 30 | 5.245e-07 | 7.625e-06 | 2.943e-06 | 1.024e-06 | 2.418e-07 | 1.352e-06 | 0.1025 | 0.8626 | 0.3939 | 0.5122 | 0.1946 | 0.2854 |
| base_iterative_coil_test (copy) | 1 | 5.492e-07 | 7.42e-06 | 3.391e-06 | 1.019e-06 | 6.496e-07 | 2.609e-06 | 0.1191 | 1.109 | 0.795 | 0.5047 | 0.476 | 0.5202 |
| base_iterative_coil_test (copy) | 5 | 5.415e-07 | 7.496e-06 | 3.259e-06 | 1.019e-06 | 5.548e-07 | 2.529e-06 | 0.1102 | 1.043 | 0.6051 | 0.5057 | 0.4176 | 0.5117 |
| base_iterative_coil_test (copy) | 30 | 5.243e-07 | 7.536e-06 | 2.935e-06 | 9.852e-07 | 2.348e-07 | 1.338e-06 | 0.1009 | 0.8444 | 0.393 | 0.4956 | 0.1892 | 0.2844 |
| Gen2 | 1 | 1.899e-07 | 4.567e-06 | 1.538e-06 | 5.892e-07 | 3.037e-07 | 1.943e-06 | 0.01861 | 0.5108 | 0.2355 | 0.6327 | 0.562 | 0.7332 |
| Gen2 | 5 | 1.905e-07 | 4.61e-06 | 1.588e-06 | 5.944e-07 | 2.636e-07 | 1.706e-06 | 0.01782 | 0.4544 | 0.1953 | 0.6342 | 0.4794 | 0.6307 |
| Gen2 | 30 | 1.915e-07 | 4.659e-06 | 1.617e-06 | 6.009e-07 | 1.282e-07 | 8.758e-07 | 0.01681 | 0.3919 | 0.1634 | 0.6361 | 0.2304 | 0.3191 |
| Gen2_4Layer | 1 | 1.896e-07 | 4.552e-06 | 1.535e-06 | 5.882e-07 | 3.038e-07 | 1.946e-06 | 0.01751 | 0.4781 | 0.2264 | 0.6332 | 0.5632 | 0.7362 |
| Gen2_4Layer | 5 | 1.905e-07 | 4.598e-06 | 1.586e-06 | 5.94e-07 | 2.638e-07 | 1.708e-06 | 0.0167 | 0.4189 | 0.1838 | 0.6346 | 0.4799 | 0.6326 |
| Gen2_4Layer | 30 | 1.913e-07 | 4.645e-06 | 1.614e-06 | 5.997e-07 | 1.282e-07 | 8.759e-07 | 0.01576 | 0.3607 | 0.1515 | 0.6363 | 0.2308 | 0.3199 |
| Gen3 | 1 | 2.912e-07 | 5.521e-06 | 2.104e-06 | 8.746e-07 | 5.035e-07 | 2.551e-06 | 0.03025 | 0.6539 | 0.2649 | 0.6898 | 0.6433 | 0.7486 |
| Gen3 | 5 | 2.887e-07 | 5.55e-06 | 2.049e-06 | 8.706e-07 | 4.336e-07 | 2.241e-06 | 0.02847 | 0.5718 | 0.2199 | 0.6878 | 0.5638 | 0.6645 |
| Gen3 | 30 | 2.822e-07 | 5.523e-06 | 1.861e-06 | 8.508e-07 | 1.941e-07 | 1.091e-06 | 0.02603 | 0.4812 | 0.1822 | 0.6816 | 0.2678 | 0.3404 |
| Gen3_2mm_61_RC_ferrite | 1 | 2.832e-07 | 4.765e-06 | 2.524e-06 | 7.852e-07 | 5.433e-07 | 2.567e-06 | 0.02933 | 0.5374 | 0.4349 | 0.6759 | 0.6426 | 0.74 |
| Gen3_2mm_61_RC_ferrite | 5 | 2.788e-07 | 4.902e-06 | 2.502e-06 | 7.87e-07 | 4.673e-07 | 2.278e-06 | 0.02729 | 0.4773 | 0.3235 | 0.6732 | 0.5595 | 0.6504 |
| Gen3_2mm_61_RC_ferrite | 30 | 2.706e-07 | 5.193e-06 | 2.284e-06 | 7.927e-07 | 1.964e-07 | 1.078e-06 | 0.02523 | 0.445 | 0.2439 | 0.6688 | 0.2498 | 0.3131 |
| Gen3_2mm_RC_ferrite | 1 | 2.936e-07 | 4.896e-06 | 2.617e-06 | 8.213e-07 | 5.724e-07 | 2.674e-06 | 0.0303 | 0.5421 | 0.4498 | 0.685 | 0.653 | 0.7471 |
| Gen3_2mm_RC_ferrite | 5 | 2.882e-07 | 5.022e-06 | 2.583e-06 | 8.2e-07 | 4.92e-07 | 2.372e-06 | 0.02806 | 0.4808 | 0.3323 | 0.6815 | 0.5702 | 0.6585 |
| Gen3_2mm_RC_ferrite | 30 | 2.776e-07 | 5.28e-06 | 2.331e-06 | 8.17e-07 | 2.062e-07 | 1.12e-06 | 0.02582 | 0.4485 | 0.2477 | 0.6748 | 0.2563 | 0.3192 |
| Gen3_4mm_RC_ferrite | 1 | 3.036e-07 | 5.216e-06 | 2.734e-06 | 8.77e-07 | 6.058e-07 | 2.855e-06 | 0.03061 | 0.5652 | 0.4602 | 0.6969 | 0.665 | 0.756 |
| Gen3_4mm_RC_ferrite | 5 | 2.957e-07 | 5.272e-06 | 2.687e-06 | 8.629e-07 | 5.193e-07 | 2.521e-06 | 0.02828 | 0.4983 | 0.3397 | 0.691 | 0.5826 | 0.6698 |
| Gen3_4mm_RC_ferrite | 30 | 2.794e-07 | 5.34e-06 | 2.41e-06 | 8.27e-07 | 2.168e-07 | 1.181e-06 | 0.0258 | 0.4557 | 0.2506 | 0.6771 | 0.2643 | 0.3292 |
| Gen3_backplane | 1 | 2.765e-07 | 4.875e-06 | 1.999e-06 | 7.772e-07 | 4.643e-07 | 2.292e-06 | 0.0291 | 0.5697 | 0.2628 | 0.6695 | 0.6246 | 0.7341 |
| Gen3_backplane | 5 | 2.761e-07 | 5e-06 | 1.955e-06 | 7.872e-07 | 3.993e-07 | 2.015e-06 | 0.02762 | 0.5044 | 0.2148 | 0.67 | 0.5434 | 0.6443 |
| Gen3_backplane | 30 | 2.764e-07 | 5.284e-06 | 1.781e-06 | 8.138e-07 | 1.737e-07 | 9.606e-07 | 0.0258 | 0.4568 | 0.1761 | 0.6734 | 0.2476 | 0.3132 |
| new_coil_6_turn | 1 | 2.688e-07 | 5.298e-06 | 2.202e-06 | 4.732e-07 | 1.231e-07 | 4.284e-07 | 0.08299 | 0.541 | 0.5025 | 0.3965 | 0.16 | 0.1254 |
| new_coil_6_turn | 5 | 2.689e-07 | 5.297e-06 | 2.207e-06 | 4.731e-07 | 1.195e-07 | 4.241e-07 | 0.08293 | 0.5417 | 0.496 | 0.3964 | 0.1552 | 0.1241 |
| new_coil_6_turn | 30 | 2.681e-07 | 5.296e-06 | 2.195e-06 | 4.729e-07 | 6.687e-08 | 3.155e-07 | 0.08218 | 0.5354 | 0.5004 | 0.3969 | 0.08718 | 0.09255 |
| new_coil_7_turn | 1 | 2.688e-07 | 5.303e-06 | 2.808e-06 | 4.735e-07 | 1.452e-07 | 5.033e-07 | 0.08132 | 0.5368 | 0.6063 | 0.3966 | 0.1671 | 0.1304 |
| new_coil_7_turn | 5 | 2.685e-07 | 5.295e-06 | 2.811e-06 | 4.734e-07 | 1.409e-07 | 4.999e-07 | 0.08115 | 0.5368 | 0.5975 | 0.3971 | 0.1622 | 0.1296 |
| new_coil_7_turn | 30 | 2.685e-07 | 5.301e-06 | 2.797e-06 | 4.734e-07 | 7.852e-08 | 3.703e-07 | 0.0802 | 0.5284 | 0.6063 | 0.3968 | 0.09062 | 0.09617 |
| new_coil_8_turn | 1 | 2.683e-07 | 5.305e-06 | 3.287e-06 | 4.72e-07 | 1.616e-07 | 5.62e-07 | 0.07299 | 0.484 | 0.7188 | 0.3956 | 0.1721 | 0.1346 |
| new_coil_8_turn | 5 | 2.685e-07 | 5.306e-06 | 3.284e-06 | 4.717e-07 | 1.561e-07 | 5.556e-07 | 0.07277 | 0.4833 | 0.713 | 0.3952 | 0.1662 | 0.1331 |
| new_coil_8_turn | 30 | 2.689e-07 | 5.31e-06 | 3.285e-06 | 4.731e-07 | 8.54e-08 | 4.08e-07 | 0.07092 | 0.4682 | 0.7134 | 0.3959 | 0.09087 | 0.09769 |
| plated_slot_coil | 1 | 3.685e-07 | 4.049e-06 | 2.602e-06 | 3.471e-07 | 6.061e-07 | 7.403e-07 | 0.1056 | 0.9841 | 0.7746 | 0.2842 | 0.619 | 0.2281 |
| plated_slot_coil | 5 | 3.552e-07 | 4.103e-06 | 2.544e-06 | 3.54e-07 | 5.041e-07 | 7.59e-07 | 0.09816 | 0.9859 | 0.7061 | 0.2932 | 0.5303 | 0.2349 |
| plated_slot_coil | 30 | 3.293e-07 | 4.209e-06 | 2.42e-06 | 3.479e-07 | 1.802e-07 | 5.9e-07 | 0.08478 | 0.9624 | 0.587 | 0.2955 | 0.2018 | 0.1848 |
| RevA1 with Ferrite (copy) | 1 | 2.912e-07 | 5.521e-06 | 2.104e-06 | 8.746e-07 | 5.035e-07 | 2.551e-06 | 0.03025 | 0.6539 | 0.2649 | 0.6898 | 0.6433 | 0.7486 |
| RevA1 with Ferrite (copy) | 5 | 2.887e-07 | 5.55e-06 | 2.049e-06 | 8.706e-07 | 4.336e-07 | 2.241e-06 | 0.02847 | 0.5718 | 0.2199 | 0.6878 | 0.5638 | 0.6645 |
| RevA1 with Ferrite (copy) | 30 | 2.822e-07 | 5.523e-06 | 1.861e-06 | 8.508e-07 | 1.941e-07 | 1.091e-06 | 0.02603 | 0.4812 | 0.1822 | 0.6816 | 0.2678 | 0.3404 |
| seedcoil | 1 | 3.799e-07 | 1.594e-05 | 2.53e-06 | 4.353e-07 | 3.984e-07 | 2.299e-06 | 0.08821 | 1.383 | 0.3486 | 0.1769 | 0.4064 | 0.3621 |
| seedcoil | 5 | 3.806e-07 | 1.595e-05 | 2.538e-06 | 4.358e-07 | 3.379e-07 | 2.16e-06 | 0.08773 | 1.364 | 0.3333 | 0.1769 | 0.3437 | 0.3394 |
| seedcoil | 30 | 3.802e-07 | 1.599e-05 | 2.538e-06 | 4.364e-07 | 1.299e-07 | 1.426e-06 | 0.08712 | 1.287 | 0.3174 | 0.177 | 0.1322 | 0.2238 |
| TR302_and_225_000056 | 1 | 2.683e-07 | 5.296e-06 | 1.825e-06 | 4.735e-07 | 9.769e-08 | 3.42e-07 | 0.08272 | 0.535 | 0.4699 | 0.3972 | 0.1396 | 0.11 |
| TR302_and_225_000056 | 5 | 2.683e-07 | 5.296e-06 | 1.824e-06 | 4.733e-07 | 9.489e-08 | 3.391e-07 | 0.08266 | 0.5349 | 0.4717 | 0.3971 | 0.1357 | 0.1091 |
| TR302_and_225_000056 | 30 | 2.684e-07 | 5.301e-06 | 1.828e-06 | 4.744e-07 | 5.407e-08 | 2.54e-07 | 0.08216 | 0.5323 | 0.4699 | 0.3977 | 0.07719 | 0.08157 |
| TR302_and_mithril | 1 | 2.677e-07 | 5.291e-06 | 1.453e-06 | 4.724e-07 | 9.945e-08 | 3.458e-07 | 0.08285 | 0.5401 | 0.3464 | 0.3969 | 0.1595 | 0.1247 |
| TR302_and_mithril | 5 | 2.68e-07 | 5.291e-06 | 1.452e-06 | 4.73e-07 | 9.631e-08 | 3.423e-07 | 0.08261 | 0.541 | 0.3473 | 0.3972 | 0.1544 | 0.1235 |
| TR302_and_mithril | 30 | 2.682e-07 | 5.3e-06 | 1.452e-06 | 4.734e-07 | 5.374e-08 | 2.54e-07 | 0.08204 | 0.5359 | 0.3466 | 0.3971 | 0.08613 | 0.09157 |
| TR302_and_OC150 | 1 | 2.655e-07 | 5.274e-06 | 1.497e-06 | 4.643e-07 | 2.741e-07 | 8.366e-07 | 0.08586 | 0.551 | 0.211 | 0.3924 | 0.4349 | 0.2978 |
| TR302_and_OC150 | 5 | 2.674e-07 | 5.273e-06 | 1.498e-06 | 4.67e-07 | 2.475e-07 | 8.208e-07 | 0.08222 | 0.5457 | 0.2084 | 0.3932 | 0.3911 | 0.292 |
| TR302_and_OC150 | 30 | 2.691e-07 | 5.295e-06 | 1.5e-06 | 4.737e-07 | 1.064e-07 | 5.509e-07 | 0.07764 | 0.5149 | 0.2057 | 0.3968 | 0.1675 | 0.1955 |

</details>

### 3.2 Findings

| Config | Finding | Detail |
|---|---|---|
| plated_slot_coil | \|M23\| not decreasing with distance | [7.402687362402559e-07, 7.589591815855123e-07, 5.900039774169305e-07] |
| Gen3 @ 1 mm | cache differs from fresh | {'config_name': ['Gen3', 'RevA1 with Ferrite (copy)']} |
| Gen3 @ 5 mm | cache differs from fresh | {'config_name': ['Gen3', 'RevA1 with Ferrite (copy)']} |
| Gen3 @ 30 mm | cache differs from fresh | {'config_name': ['Gen3', 'RevA1 with Ferrite (copy)']} |

- **M23 not decreasing:** `plated_slot_coil` M23 is higher at 5 mm than at 1 mm. Probably ferrite/shield field
  shaping, but worth a physics sanity check ([B11](#b11--m23-rises-with-distance-on-plated_slot_coil)).
- **"Cache differs from fresh" (3 rows) is only a label:** `Gen3` and `RevA1 with Ferrite (copy)` have identical
  geometry, so they share cached results; the numbers are identical and only `config_name` differs
  ([B10](#b10--configs-with-identical-geometry-share-cache-labels)).

---

## 4. Phase 2 – Scoring

**What:** each config's baseline plus 5 candidates, solved fresh at 3 and 20 mm:

| Candidate | Change |
|---|---|
| thicker_traces | all trace thicknesses → 0.14 mm |
| smaller_rc | rc_radius − 3 mm |
| fewer_tank_turns | num_tank_turns − 1 |
| wider_tank_trace | tank_trace_width + 0.5 mm |
| invalid_huge_trace | loop_trace_width = 1000 mm (must be rejected) |

Each valid candidate was scored against every quantity (12 simulated results, 3 outer diameters, 4 design
variables) × every requirement type below, singly and all together, with both plain and numpy inputs:

| Requirement types |
|---|
| match ±5% · match −10% ±2% · reduce ≥10% · reduce "no higher" · increase ≥10% · increase "no lower" (preference) · match preference · absolute match ±5% · absolute at-most · absolute at-least |

For every scoring: JSON conversion of the tool result, finite margins, ranking, the LLM system prompt and tool schema,
the follow-up context, saving to History, and the GUI result-table builders.

**Result: 26740 scorings, 2 findings.**

| Config | Candidate | Stage | Message | Verdict |
|---|---|---|---|---|
| base_iterative_coil_test (copy) | wider_tank_trace | solve | Exception('turn space of 3.25 is less than total turn width of 3.5, ') | Correct rejection - the test candidate is invalid geometry for this config |
| base_coil | wider_tank_trace | solve | Exception('turn space of 3.25 is less than total turn width of 3.5, ') | Correct rejection - the test candidate is invalid geometry for this config |

The 1000 mm trace was rejected with a readable geometry message on all 18 configs.

---

## 5. Phase 3 – Plan checking

**What:** 50 `finalize_spec` argument sets (what the LLM sends as a plan) pushed through the real planning turn, with
only the LLM reply faked, on `Gen2_coil` and `base_coil`. Both baselines gave identical outcomes, so each case is
shown once.

**Result per case:** 21 accepted, 22 rejected with a clear message, 7 crashed (× 2 baselines = 42 / 44 / 14).

| Plan sent | Outcome | Detail / message returned to the LLM |
|---|---|---|
| distance null | **CRASHED** | TypeError("float() argument must be a string or a real number, not 'NoneType'") |
| distances not a list | **CRASHED** | TypeError("'int' object is not iterable") |
| objectives not a list | **CRASHED** | TypeError("string indices must be integers, not 'str'") |
| objective is a string | **CRASHED** | TypeError("string indices must be integers, not 'str'") |
| tolerance null | **CRASHED** | TypeError("float() argument must be a string or a real number, not 'NoneType'") |
| args is a list | **CRASHED** | TypeError('list indices must be integers or slices, not str') |
| args is a string | **CRASHED** | TypeError("string indices must be integers, not 'str'") |
| required as string 'false' | **ACCEPTED - should not be** | distances=[5.0]; tank_esr reduce 10; L_rc reduce 0 |
| duplicate distances | **ACCEPTED - should not be** | distances=[5.0, 5.0]; tank_esr reduce 10 |
| negative distance | **ACCEPTED - should not be** | distances=[-5.0]; tank_esr reduce 10 |
| zero distance | **ACCEPTED - should not be** | distances=[0.0]; tank_esr reduce 10 |
| nan tolerance | **ACCEPTED - should not be** | distances=[5.0]; tank_esr reduce nan |
| huge distance | **ACCEPTED - should not be** | distances=[1000000000.0]; tank_esr reduce 10 |
| 6 distances | Rejected | distances: at most 5 allowed, got 6 - ask the user which ones matter most |
| no distances | Rejected | distances must be a non-empty list |
| distance word | Rejected | could not convert string to float: 'five' |
| missing distances | Rejected | 'distances' |
| missing objectives | Rejected | 'objectives' |
| empty objectives | Rejected | objectives needs at least one required entry - with only preferences, the very first valid candidate would already meet the goal |
| only preferences | Rejected | objectives needs at least one required entry - with only preferences, the very first valid candidate would already meet the goal |
| unknown metric | Rejected | unknown quantity 'efficiency' - must be a simulated result ['L_loop', 'L_tank', 'L_rc', 'M12', 'M13', 'M23', 'loop_esr', 'tank_esr', 'rc_esr', 'k12', 'k13', 'k23'], a derived value ['loop_outer_diameter', 'tank_outer_dia |
| unknown kind | Rejected | unknown objective kind 'minimize' - must be one of ['match', 'reduce', 'increase'] |
| negative tolerance | Rejected | tank_esr: tolerance_pct must be >= 0, got -5.0 |
| tolerance missing | Rejected | 'tolerance_pct' |
| target_value 0 | Rejected | rc_esr: target_value can't be 0 - tolerances are a % of it |
| target_value string | Rejected | could not convert string to float: 'abc' |
| required match tol 0 | Rejected | M23: a required match needs tolerance_pct > 0 - an exact float match can never pass (to set a design variable to an exact value, pin it with fixed_values instead) |
| duplicate metric | Rejected | objectives lists the same quantity more than once - use one entry per quantity |
| unknown feature | Rejected | enabled_features contains unknown feature(s): ['magic'] |
| enabled_features string | Rejected | enabled_features contains unknown feature(s): ['_', 'a', 'e', 'f', 'i', 'k', 'n', 'r', 't'] |
| unknown excluded field | Rejected | excluded_fields contains 'nope', which is neither a CoilSet field nor a feature (['backplane', 'rc_backplane', 'rc_ferrite', 'rc_shield', 'tank_ferrite', 'tc_shield']) |
| fixed unknown field | Rejected | fixed_values contains ['nope'], which aren't numeric CoilSet fields |
| fixed_values non-number | Rejected | 'rc_trace_thickness' must be a number, got 'thick' |
| objective on pinned var | Rejected | tank_center_r is pinned by fixed_values, so it can't also be an objective |
| objective on PCB thickness (not free) | Rejected | tank_PCB_thickness isn't free in this search, so no candidate could ever change it - enable its feature / stop excluding it, or drop the objective |
| valid minimal | Accepted | distances=[5.0]; tank_esr reduce 10 |
| distances as strings | Accepted | distances=[5.0, 10.0]; tank_esr reduce 10 |
| tolerance as string | Accepted | distances=[5.0]; tank_esr reduce 10 |
| 5 distances (max) | Accepted | distances=[1.0, 2.0, 3.0, 4.0, 5.0]; tank_esr reduce 10 |
| absolute target | Accepted | distances=[5.0]; rc_esr reduce 0 target=0.1 |
| diameter objective | Accepted | distances=[5.0]; tank_esr reduce 10; tank_outer_diameter reduce 0 (pref) |
| design var objective (free) | Accepted | distances=[5.0]; tank_esr reduce 10; tank_center_r match 2 target=70 |
| enable ferrite feature | Accepted | distances=[5.0]; tank_esr reduce 10; enabled=['tank_ferrite'] |
| fixed 2oz thickness | Accepted | distances=[5.0]; tank_esr reduce 10; fixed={'rc_trace_thickness': 0.14} |
| fixed value as string | Accepted | distances=[5.0]; tank_esr reduce 10; fixed={'rc_trace_thickness': 0.14} |
| exclude feature + field | Accepted | distances=[5.0]; tank_esr reduce 10 |
| k objectives | Accepted | distances=[5.0]; k12 match 5; k13 match 5; k23 match 5; tank_esr reduce 10 |
| exclude everything | Accepted | distances=[5.0]; tank_esr reduce 10 |
| fixed_values list | Accepted | distances=[5.0]; tank_esr reduce 10; fixed={'rc_trace_thickness': 0.14} |
| objective_text null | Accepted | distances=[5.0]; tank_esr reduce 10 |

### 5.1 What the GUI sees when a plan crashes

| Check | Observed |
|---|---|
| Chat still "busy" afterwards | False |
| Error shown to the user (`state.error`) | None |
| Exception | only in the background thread: `TypeError("float() argument must be a string or a real number, not 'NoneType'")` |
| Transcript left as | ['user', 'assistant'] (a plan call with no result → bubble reads "Working out the plan...") |
| Next user message (live LLM) | Recovered: plan finalized in 21 s |

### 5.2 Extreme distances (real FEMM, `Gen2_coil`)

| Distance | Outcome | Time | M23 (H) | k23 | L_rc (H) | rc_esr (Ω) |
|---|---|---|---|---|---|---|
| −5 mm | "OK" - **meaningless** (coils overlap) | 9.0 s | 1.9e-06 | 0.7133 | 1.545e-06 | 0.2203 |
| 0 mm | "OK" - **meaningless** | 9.1 s | 2.047e-06 | 0.7924 | 1.485e-06 | 0.321 |
| 0.01 mm | `femmcli failed (exit 1)` | 0.2 s | - | - | - | - |
| 200 mm | OK | 9.6 s | 4.591e-08 | 0.0167 | 1.62e-06 | 0.1613 |
| 10,000 mm | **hung**, killed by the 120 s solver timeout | 120.1 s | - | - | - | - |
| 1e9 mm | **crash** parsing output (`could not convert string to float: 'M_rc_loop=M_loop_tank=R_tank=L_tank=0'`) | 0.1 s | - | - | - | - |

---

## 6. Phase 4 – Live end-to-end runs

**What:** 6 scenarios, each: planning chat → search (max 3 rounds) → one question during the search → three
questions after. 3 scenarios ran at a time.

### 6.1 Overview

| # | Baseline | Planning turns | Search status | Search time (s) | Rounds | Goal met | Issues flagged |
|---|---|---|---|---|---|---|---|
| S1 | Gen2 | 3 | budget_exhausted | 698 | 3 | False | 2 |
| S2 | Gen3_4mm_RC_ferrite | 1 | failed | 616 | 1 | None | 1 |
| S3 | new_coil_7_turn | 2 | failed | 605 | 1 | None | 1 |
| S4 | plated_slot_coil | 1 | goal_met | 668 | 2 | True | 1 |
| S5 | seedcoil | 2 | goal_met | 354 | 1 | True | 0 |
| S6 | TR302_and_OC150 | 2 | goal_met | 165 | 1 | True | 0 |

### 6.2 S1 user's real request · `Gen2`

**Request:** Your real Gen2 request (match mutual inductance within 5% at 10/20/30/40 mm, tank ESR ≥20% lower, loop/RC ESR maintain, diameter and self-L secondary, layers 1 or 2, no ferrite/shield, same PCB thickness, 1/2/4 oz copper), then "three of them"

**Planning chat**

| Turn | User | Time (s) | AI reply | Plan check |
|---|---|---|---|---|
| 1 | match mutual inductance within 5% at 10mm, 20mm, 30mm, 40mm /  / reduce ESR of TC Tank by at least 20% (if not more) /  ... | 108 | Which mutual inductance should I hold within ±5% of baseline — M23 (tank↔RC, the power-transfer link) only, or all three (M12, M13, M23)? I'd default to M23 unless you say otherwise. | - |
| 2 | three of them | 91 | (plan call) | ❌ M12: a required match needs tolerance_pct > 0 - an exact float match can never pass (to set a design variable to an exact value, pin it with fixed_values instead) |
| 3 | Use sensible defaults for anything I didn't specify and propose the plan now. | 78 | (plan call) | ✅ Spec finalized. |

**Saved plan**

| Field | Value |
|---|---|
| Distances (mm) | [10.0, 20.0, 30.0, 40.0] |
| Objectives | **M12**: within ±5% of baseline - required<br>**M13**: within ±5% of baseline - required<br>**M23**: within ±5% of baseline - required<br>**rc_esr**: no higher than baseline - required<br>**loop_esr**: no higher than baseline - required<br>**tank_esr**: ≥20% lower than baseline - required<br>**rc_outer_diameter**: no higher than baseline - preference<br>**tank_outer_diameter**: no higher than baseline - preference<br>**loop_outer_diameter**: no higher than baseline - preference<br>**L_loop**: no higher than baseline - preference<br>**L_tank**: no higher than baseline - preference<br>**L_rc**: no higher than baseline - preference |
| Enabled features | - |
| Fixed values | - |
| Excluded fields | backplane, backplane_y_distance, loop_PCB_thickness, num_loop_layers, num_rc_layers, num_tank_layers, rc_PCB_thickness, rc_backplane, rc_backplane_y_distance, rc_ferrite_outer_r, rc_ferrite_present, rc_ferrite_thickness, rc_loss_angle, rc_mu, rc_shield, rc_shield_radius, rc_shield_thickness, rc_shield_y_distance, tank_PCB_thickness, tank_ferrite_outer_r, tank_ferrite_present, tank_ferrite_thickness, tank_ferrite_y_offset, tank_mu, tc_loss_angle, tc_shield, tc_shield_radius, tc_shield_thickness, tc_shield_y_distance |
| Plan text shown to user | Keep all three coupling coefficients (M12, M13, M23) within +/-5% of their baseline values at 10/20/30/40 mm separations, cut TC Tank ESR by at least 20%, and hold Loop and RC ESR no higher than baseline. Preferences only (tie-breakers): shrink or hold the outer diameter of each coil's outermost turn, and shrink or hold each coil's self-inductance. Constraints: layer counts (loop/tank/rc) and PCB thickness stay at baseline; no ferrite, no shields, no backplanes on either side; trace thickness is free but should land on a real copper weight (1/2/4 oz, i.e. ~0.033/0.067/0.133 mm finished copper) - the search is continuous, so results should be snapped to the nearest of those three. |

> ⚠️ Layer counts are excluded (frozen at the baseline's 2) although the user allowed 1 or 2, and the plan text's copper-weight values don't match the search's 0.035/0.07/0.14 mm. See [B9](#b9--requests-interpreted-too-narrowly--misleading-plan-text).

**Search rounds**

| Round | Change proposed | Valid | Goal met | Required misses | AI time (s) | Note |
|---|---|---|---|---|---|---|
| 1 | {'tank_trace_thickness': 0.14} | ✅ | False | rc_esr, loop_esr, tank_esr | 241.2 |  |
| 2 | {'num_parallel_tank_traces': 2, 'tank_trace_spacing': 2.844, 'tank_trace_thickness': 0.07, 'tank_trace_width': 2.7} | ✅ | False | tank_esr | 204.1 |  |
| 3 | {'num_parallel_tank_traces': 6, 'tank_trace_spacing': 0.5, 'tank_trace_thickness': 0.07, 'tank_trace_width': 1.9417} | ❌ | None | - | 226.3 | Exception('trace space of 0.5 is less than trace width of 1.9417, ') |

Final: **budget_exhausted** after 698 s.

**Question during the search**

| Question | AI answer | Actual state when answered |
|---|---|---|
| What is the search doing right now, and how many rounds are done? | Right now the search is still running. /  / - Completed rounds: 1 of at most 3 / - Current: Round 2, but no Round 2 proposal yet /  / Round 1 tested only one change: /  / - tank_trace_thickness: 0.07 mm to 0.14 mm /  / Result: goal not met. /  / Passes: / - M12, M13, M23 stayed within +/-5% of baseline across 10/20/30/40 mm / - Outer diameters held at baseline / - L_tank and L_loop held at baselin | 2 rounds, current=evaluating |

**Questions after the search**

| Question | AI answer | Named Restart |
|---|---|---|
| Did the search meet the goal? What changed in the best design, and by how much did each requirement move? | I don't have the finished search result data needed to answer that. /  / The information provided only says the search has finished and its result is final. It does not include: /  / - whether the goal was met / - how many rounds ran / - what changed from baseline / - baseline or candidate values / - pass/fail details for coupling, tank ESR, loop ESR, or RC ESR /  / So I can’t quote real numbers or compare the proposed design to the baseline from what’s provided. | ⚠️ no |
| Change the distance to 15mm and run it again. | This run is finished. I can’t change the search, requirements, distances, objective, requirements, requirements, requirements, requirements, requirements, or plan after completion. /  / To use 15 mm or run any other revised search, click Restart and start a new task. | ✅ |
| Propose a better design with one fewer tank turn and tell me the exact values to use. | This run is over. To try a new design, click New Task. | ⚠️ no |

**History:** saved status `budget_exhausted`, 3 round(s), best design saved: True.

**Issues flagged:**

- round 3 candidate rejected: Exception('trace space of 0.5 is less than trace width of 1.9417, ')
- follow-up to a change request didn't point to Restart: 'Propose a better design with one fewer tank turn and tell me the exact values to use.'

### 6.3 S2 k objectives (the crash case) · `Gen3_4mm_RC_ferrite`

**Request:** Keep k12, k13, k23 within 5% at 4, 8, 12 mm; reduce all three ESRs ≥10%; keep ferrite, shields, PCB thickness

**Planning chat**

| Turn | User | Time (s) | AI reply | Plan check |
|---|---|---|---|---|
| 1 | Keep k12, k13 and k23 within 5% of baseline at 4, 8 and 12 mm. Reduce all three coil ESRs by at least 10% each. Keep fer... | 77 | Everything's specified — I'll hold k12/k13/k23 to ±5% of baseline at 4/8/12 mm, require ≥10% ESR drops on all three coils, and freeze ferrite, shields, and PCB thickness. | ✅ Spec finalized. |

**Saved plan**

| Field | Value |
|---|---|
| Distances (mm) | [4.0, 8.0, 12.0] |
| Objectives | **k12**: within ±5% of baseline - required<br>**k13**: within ±5% of baseline - required<br>**k23**: within ±5% of baseline - required<br>**loop_esr**: ≥10% lower than baseline - required<br>**tank_esr**: ≥10% lower than baseline - required<br>**rc_esr**: ≥10% lower than baseline - required |
| Enabled features | - |
| Fixed values | - |
| Excluded fields | loop_PCB_thickness, rc_PCB_thickness, rc_backplane, rc_backplane_y_distance, rc_ferrite_outer_r, rc_ferrite_present, rc_ferrite_thickness, rc_loss_angle, rc_mu, rc_shield, rc_shield_radius, rc_shield_thickness, rc_shield_y_distance, tank_PCB_thickness, tank_ferrite_outer_r, tank_ferrite_present, tank_ferrite_thickness, tank_ferrite_y_offset, tank_mu, tc_loss_angle, tc_shield, tc_shield_radius, tc_shield_thickness, tc_shield_y_distance |
| Plan text shown to user | Keep k12, k13 and k23 within 5% of baseline at 4, 8 and 12 mm, while reducing loop, tank and RC coil ESR by at least 10% each. Ferrite, shields and PCB thickness stay exactly as on the baseline. |

**Search rounds**

| Round | Change proposed | Valid | Goal met | Required misses | AI time (s) | Note |
|---|---|---|---|---|---|---|
| 1 | - | - | - | - | - | no proposal (timed out) |

Final: **failed** after 616 s - error `timed out`.

**Questions after the search**

| Question | AI answer | Named Restart |
|---|---|---|
| Did the search meet the goal? What changed in the best design, and by how much did each requirement move? | No. The search finished as failed with the error: timed out. /  / It did not meet the goal because there is no valid best candidate. Round 1 produced no proposal, so no design changes were generated and no requirement movement can be reported for k12, k13, k23, loop ESR, tank ESR, or RC ESR. | ⚠️ no |
| Change the distance to 15mm and run it again. | This run is finished and cannot be changed or rerun. The completed search used distances 4.0, 8.0, and 12.0 mm; I have no 15 mm result. /  / To try a 15 mm distance, click Restart and start a new task. The finished run will stay in History. | ✅ |
| Propose a better design with one fewer tank turn and tell me the exact values to use. | I can't propose that or give values. This run is finished and can't be changed, and it never produced a valid candidate, so there is no design to improve or parameter set to hand you. /  / To try a design with one fewer tank turn, click Restart and start a new task. The finished run stays in History. | ✅ |

**History:** saved status `failed`, 1 round(s), best design saved: False.

**Issues flagged:**

- SEARCH FAILED: timed out

### 6.4 S3 absolute targets + pinned value · `new_coil_7_turn`

**Request:** At 5 mm: RC ESR at most 0.15 Ω, M23 at least 7e-7 H; RC trace thickness 2 oz; nothing else matters

**Planning chat**

| Turn | User | Time (s) | AI reply | Plan check |
|---|---|---|---|---|
| 1 | At 5 mm only: RC ESR must be at most 0.15 ohm, and M23 must be at least 7e-7 H. The RC trace thickness must be 2oz coppe... | 56 | (plan call) | ❌ 'rc_trace_thickness' must be one of [0.035, 0.07, 0.14], got 0.071 |
| 2 | Use sensible defaults for anything I didn't specify and propose the plan now. | 27 | (plan call) | ✅ Spec finalized. |

**Saved plan**

| Field | Value |
|---|---|
| Distances (mm) | [5.0] |
| Objectives | **rc_esr**: at most 0.15 (absolute) - required<br>**M23**: at least 7 (absolute) - required |
| Enabled features | - |
| Fixed values | {'rc_trace_thickness': 0.07} |
| Excluded fields | - |
| Plan text shown to user | At a 5 mm separation only: RC ESR must be at most 0.15 ohm and tank-to-RC mutual inductance (M23) must be at least 7e-7 H. RC trace thickness is pinned to 2oz copper (0.07 mm). Nothing else matters. |

> ⚠️ **Wrong unit:** the user asked for M23 ≥ 7e-7 H; the saved plan says **at least 7** (7 H). See [B1](#b1--absolute-target-in-wrong-units-silently-accepted).

**Search rounds**

| Round | Change proposed | Valid | Goal met | Required misses | AI time (s) | Note |
|---|---|---|---|---|---|---|
| 1 | - | - | - | - | - | no proposal (timed out) |

Final: **failed** after 605 s - error `timed out`.

**Questions after the search**

| Question | AI answer | Named Restart |
|---|---|---|
| Did the search meet the goal? What changed in the best design, and by how much did each requirement move? | No. The search finished with status failed due to timeout. /  / There is no best/valid proposed design. Round 1 was "no proposal (text-only reply)", so nothing changed from the baseline. /  / At 5 mm, the baseline new_coil_7_turn was: / RC ESR = 0.597483 ohm. Required: <= 0.15 ohm. Failed by 0.447483 ohm. / M23 = 4.99931e-07 H. Required: >= 7e-07 H. Failed by 2.00069e-07 H. / RC trace thickness = 0.07 mm, as pinned. /  / Movement for the requirements: none, because there was no valid candidate. | ⚠️ no |
| Change the distance to 15mm and run it again. | This run is finished and cannot be changed or rerun, so I cannot change the distance to 15 mm. /  / To try a different distance, click Restart and start a new task. This run, including the timeout failure, will remain in History. | ✅ |
| Propose a better design with one fewer tank turn and tell me the exact values to use. | I cannot propose a better design or provide exact values for one fewer tank turn. This run is finished and cannot be changed. /  / To try one fewer tank turn or any other new design, click Restart and start a new task. This run stays in History. | ✅ |

**History:** saved status `failed`, 1 round(s), best design saved: False.

**Issues flagged:**

- SEARCH FAILED: timed out

### 6.5 S4 increase + diameter preference · `plated_slot_coil`

**Request:** Increase M23 ≥10% at 3 and 10 mm; tank outer diameter must not grow; ferrite and shields unchanged

**Planning chat**

| Turn | User | Time (s) | AI reply | Plan check |
|---|---|---|---|---|
| 1 | Increase M23 by at least 10% at 3mm and 10mm. The tank outer diameter must not grow. Ferrite and shields must stay exact... | 66 | (plan call) | ✅ Spec finalized. |

**Saved plan**

| Field | Value |
|---|---|
| Distances (mm) | [3.0, 10.0] |
| Objectives | **M23**: ≥10% higher than baseline - required<br>**tank_outer_diameter**: no higher than baseline - required |
| Enabled features | - |
| Fixed values | - |
| Excluded fields | rc_ferrite_outer_r, rc_ferrite_present, rc_ferrite_thickness, rc_loss_angle, rc_mu, rc_shield, rc_shield_radius, rc_shield_thickness, rc_shield_y_distance, tank_ferrite_outer_r, tank_ferrite_present, tank_ferrite_thickness, tank_ferrite_y_offset, tank_mu, tc_loss_angle, tc_shield, tc_shield_radius, tc_shield_thickness, tc_shield_y_distance |
| Plan text shown to user | Increase tank<->RC mutual inductance M23 by at least 10% at 3 mm and 10 mm separations, without growing the tank outer diameter. Ferrite pieces and shields (tank/RC ferrite, TC/RC shields) are held exactly at their baseline values. |

**Search rounds**

| Round | Change proposed | Valid | Goal met | Required misses | AI time (s) | Note |
|---|---|---|---|---|---|---|
| 1 | {'num_tank_layers': 2, 'num_rc_layers': 2, 'tank_center_r': 76.0} | ✅ | False | M23 | 414.4 |  |
| 2 | {'num_tank_layers': 2, 'num_rc_layers': 2, 'tank_center_r': 76.0, 'rc_radius': 38.0} | ✅ | True | - | 175.9 |  |

Final: **goal_met** after 668 s.

**Question during the search**

| Question | AI answer | Actual state when answered |
|---|---|---|
| What is the search doing right now, and how many rounds are done? | The search is still running. /  / Round 1 is completed. Round 2 is currently in the proposing step, waiting for the optimizer model to propose the next design. /  / Rounds completed so far: 1. / Current round: 2 of at most 3. | 2 rounds, current=proposing |

**Questions after the search**

| Question | AI answer | Named Restart |
|---|---|---|
| Did the search meet the goal? What changed in the best design, and by how much did each requirement move? | Yes. The run finished and the goal was met. /  / Best design: found in round 2. /  / Changes from baseline: / - tank_center_r: 79 to 76.0 mm / - num_tank_layers: 1 to 2 / - rc_radius: 29 to 38.0 mm / - num_rc_layers: 1 to 2 /  / Requirement results: / - M23: 1.37222e-06 vs baseline 7.54644e-07, +81.84%, worst distance 3.0 mm, PASS requirement >= +10% / - tank_outer_diameter: 175.025 vs baseline 181.025, -3.31%, PASS requirement no higher than baseline | ⚠️ no |
| Change the distance to 15mm and run it again. | This run is finished and cannot be changed or rerun with a different distance. The completed search used distances of 3.0 mm and 10.0 mm only, so I do not have 15 mm results. /  / To try 15 mm, click Restart and start a new task. This finished run will remain in History. | ✅ |
| Propose a better design with one fewer tank turn and tell me the exact values to use. | I cannot propose a new design or give new parameter values. This run is finished and locked. | ⚠️ no |

**History:** saved status `goal_met`, 2 round(s), best design saved: True.

**Issues flagged:**

- follow-up to a change request didn't point to Restart: 'Propose a better design with one fewer tank turn and tell me the exact values to use.'

### 6.6 S5 too many distances + design variable · `seedcoil`

**Request:** Reduce tank ESR 15% at 3, 5, 8, 12, 20, 30 mm; tank center radius within 2% of 70 mm; then "drop 5 mm"

**Planning chat**

| Turn | User | Time (s) | AI reply | Plan check |
|---|---|---|---|---|
| 1 | Reduce tank ESR by 15% at 3, 5, 8, 12, 20 and 30 mm. Keep the tank center radius within 2% of 70 mm. | 33 | You listed six separations, but I can evaluate at most five per search — should I drop 30 mm (or tell me which one to cut)? | - |
| 2 | OK, drop 5 mm - use 3, 8, 12, 20 and 30. | 44 | (plan call) | ✅ Spec finalized. |

**Saved plan**

| Field | Value |
|---|---|
| Distances (mm) | [3.0, 8.0, 12.0, 20.0, 30.0] |
| Objectives | **tank_esr**: ≥15% lower than baseline - required<br>**tank_center_r**: within ±2% of 70 (absolute) - required |
| Enabled features | - |
| Fixed values | - |
| Excluded fields | - |
| Plan text shown to user | Reduce tank coil ESR by at least 15% versus baseline, evaluated at 3, 8, 12, 20 and 30 mm separations (worst distance decides), while keeping the tank center radius within 2% of 70 mm. |

**Search rounds**

| Round | Change proposed | Valid | Goal met | Required misses | AI time (s) | Note |
|---|---|---|---|---|---|---|
| 1 | {'tank_center_r': 70.0} | ✅ | True | - | 330.5 |  |

Final: **goal_met** after 354 s.

**Questions after the search**

| Question | AI answer | Named Restart |
|---|---|---|
| Did the search meet the goal? What changed in the best design, and by how much did each requirement move? | Yes, the search met the goal in round 1. /  / - tank_esr: requirement >= 15% lower vs baseline at worst distance. Result: 25.0% lower. PASS. / - tank_center_r: requirement within +/-2% of 70 mm. Result: 70.0 mm. PASS. /  / Changed from baseline: tank_center_r = 70.0 mm (baseline 96.47322052521042 mm). | ⚠️ no |
| Change the distance to 15mm and run it again. | This run is finished, so I can’t change the distances or rerun it. To evaluate 15 mm, click Restart and start a new task; this run’s results remain available in history. | ✅ |
| Propose a better design with one fewer tank turn and tell me the exact values to use. | This run is finished and cannot be changed. I cannot propose a new design, a one-turn change, or exact values. To try a different design, click Restart and start a new task; this run stays in History. | ✅ |

**History:** saved status `goal_met`, 1 round(s), best design saved: True.

### 6.7 S6 vague request + enable ferrite · `TR302_and_OC150`

**Request:** "make it better", then: reduce RC ESR ≥10% at 5 mm, tank ferrite allowed

**Planning chat**

| Turn | User | Time (s) | AI reply | Plan check |
|---|---|---|---|---|
| 1 | make it better | 11 | "Better" could mean a few things here — which quantity do you want improved, e.g. higher power-transfer coupling k23/M23, or lower ESR on one of the coils? | - |
| 2 | Reduce RC ESR by at least 10% at 5mm. You may add tank ferrite if it helps. Nothing else matters. | 34 | (plan call) | ✅ Spec finalized. |

**Saved plan**

| Field | Value |
|---|---|
| Distances (mm) | [5.0] |
| Objectives | **rc_esr**: ≥10% lower than baseline - required |
| Enabled features | ['tank_ferrite'] |
| Fixed values | - |
| Excluded fields | - |
| Plan text shown to user | Reduce RC coil ESR by at least 10% versus baseline at a 5 mm separation. Adding tank ferrite is allowed if it helps. Nothing else matters. |

**Search rounds**

| Round | Change proposed | Valid | Goal met | Required misses | AI time (s) | Note |
|---|---|---|---|---|---|---|
| 1 | {'rc_trace_thickness': 0.14} | ✅ | True | - | 158.8 |  |

Final: **goal_met** after 165 s.

**Questions after the search**

| Question | AI answer | Named Restart |
|---|---|---|
| Did the search meet the goal? What changed in the best design, and by how much did each requirement move? | Yes. The search met the goal. /  / Best design changed from baseline only: / - rc_trace_thickness: 0.07 mm -> 0.14 mm /  / Requirement movement at 5 mm: / - rc_esr: 0.208384 ohm -> 0.169621 ohm / - Change: -18.60% / - Requirement: at least 10% lower -> PASS /  / No tank ferrite change is listed in the best design. | ⚠️ no |
| Change the distance to 15mm and run it again. | This run is finished and cannot be changed. It was set to 5.0 mm only. To run at 15 mm, click Restart and start a new task; this run stays in History. | ✅ |
| Propose a better design with one fewer tank turn and tell me the exact values to use. | I cannot propose a new design with one fewer tank turn or give new values to use. This run is finished and cannot be changed. /  / The current best design only changed rc_trace_thickness from 0.07 mm to 0.14 mm. /  / To try one fewer tank turn, click Restart and start a new task. This run stays in History. | ✅ |

**History:** saved status `goal_met`, 1 round(s), best design saved: True.

### 6.8 Notes on the live runs

- **S2 and S3 timeouts** happened on round 1 while 3 searches plus the user's own GUI search were on the server.
  Completed rounds took 159–414 s of AI time under that load, so 600 s was within reach.
- **S3's saved plan is wrong:** `M23 increase target_value = 7.0` (7 H). The user asked for 7e-7 H. The follow-up
  answer quoted "7e-07 H" from the user's message, which hides the wrong saved value.
- **S1 round 3 reasoning** (optimizer): *"The baseline ESR numbers seem to shift a bit between runs (baseline rc_esr
  reported 0.1626-0.1483 in prompt; now baseline_value 0.358 for tank? ...)"* - see B8.
- **Follow-up change requests:** 14 change requests across Phase 4 and Phase 5. **None proposed new design values**
  (the only numbers in those replies restate the plan's distances or the existing best design). 10 of 14 named
  Restart; the other 4 said "start a new task", "click New Task" (no such button) or gave no way forward. One reply
  repeated "requirements" several times.

---

## 7. Phase 5 – Re-runs

### 7.1 S2 (k-coefficient objectives) alone, no other load

| Round | Change proposed | Valid | Goal met | Required misses | AI time (s) | FEMM time (s) |
|---|---|---|---|---|---|---|
| 1 | {'loop_trace_thickness': 0.14, 'tank_trace_thickness': 0.14, 'rc_trace_thickness': 0.14} | ✅ | True | - | 242 | 44.7 |
| 1 | {'tank_trace_spacing': 4.0, 'rc_trace_spacing': 3.0} | ✅ | False | loop_esr, tank_esr, rc_esr | 242 | 44.7 |
| 1 | {'loop_trace_width': 4.4, 'tank_trace_width': 4.4, 'rc_trace_width': 5.5} | ✅ | False | loop_esr, tank_esr, rc_esr | 242 | 44.7 |
| 1 | {'loop_trace_thickness': 0.14, 'loop_trace_width': 4.4, 'tank_trace_thickness': 0.14, 'tank_trace_spacing': 4.0, 'rc_trace_thickness': 0.14, 'rc_trace_spacing': 3.0} | ✅ | True | - | 242 | 44.7 |

Final: **goal_met** after 309 s. Four fresh candidates with k objectives were scored without error - **the k-coefficient crash fix is confirmed live.**

| Question | AI answer | Named Restart |
|---|---|---|
| Did the search meet the goal? What changed in the best design, and by how much did each requirement move? | Yes. The search finished successfully and proposed a design from round 1. /  / What changed from baseline: / - loop_trace_thickness: 0.07 to 0.14 / - tank_trace_thickness: 0.07 to 0.14 / - rc_trace_thickness: 0.07 to 0.14 /  / Requirement movement: / - k12: +0.09% vs baseline at 12.0 mm, PASS within +/-5% / - k13: +0.22% vs baseline at 8.0 mm, PASS within +/-5% / - k23: +0.40% vs baseline at 4.0 mm, PASS within +/-5% / - loop_esr: -17.90% at 4.0 mm, PASS >= 10% lower / - tank_esr: -21.53% at 12. | ⚠️ no |
| Change the distance to 15mm and run it again. | I can’t change this finished search or rerun it with 15 mm. The run stays final with distances 4, 8, and 12 mm. To try 15 mm, start a new task with that distance. | ⚠️ no |
| Propose a better design with one fewer tank turn and tell me the exact values to use. | I can't propose a new design or give exact values to use for one fewer tank turn. This search is final and locked to the run's parameters and distances: 4, 8, and 12 mm. /  / The run data does not provide values for a design with one fewer tank turn, and I don't have information about how that change would affect k12, k13, k23, or ESR. To try it, start a new task with that parameter changed. | ⚠️ no |

### 7.2 S1's "I don't have the data" follow-up, rebuilt and asked 5 times

The S1 run was rebuilt from its exact candidates (fresh solves) and the same question asked 5 times:
*"Did the search meet the goal? What changed in the best design, and by how much did each requirement move?"*
The run context given to the AI was 6,561 characters.

| Try | Outcome | Answer (start) |
|---|---|---|
| 1 | ❌ timed out | - |
| 2 | ❌ timed out | - |
| 3 | ✅ correct | No. The search finished without meeting the goal.  Best design: the round 2 candidate.  Changed from baseline: - num_parallel_tank_traces: 1 -> 2 - tank_trace_spacing: 0 -> 2.844 mm - tank_trace_width: 5.544 -> 2.7 mm  Requirement moves, worst-case vs baseline: - M12: 6.00602e-07 vs 6.01182e-07 H, - |
| 4 | ✅ correct | No. The search finished without meeting the goal.  Best design: found in round 2.  Changed from baseline: - num_parallel_tank_traces: 1 -> 2 - tank_trace_spacing: 0 -> 2.844 mm - tank_trace_width: 5.544 -> 2.7 mm  Requirement results: - M12: -0.10% vs baseline at worst, at 40.0 mm. PASS, within +/-5 |
| 5 | ✅ correct | No. The search finished without meeting the goal. The main failure was TC Tank ESR: it improved by only 7.38% versus baseline, but the requirement was at least 20% lower.  Best design changes from baseline, from round 2: - num_parallel_tank_traces: 1 -> 2 - tank_trace_spacing: 0 -> 2.844 - tank_trac |

The wrong answer did not reproduce, but 2 of 5 answers hit the 600 s limit with nothing else of the test running.

---

## 8. Bugs and suggested fixes

### B1 – Absolute target in wrong units silently accepted
**Severity:** High · **Evidence:** Phase 4 S3 - "M23 at least 7e-7 H" saved as `target_value: 7.0`.

**Impact:** the search chases an impossible goal for all 50 rounds; the plan card shows `>= 7`, easy to miss.

**Suggested fix:**
1. **Magnitude check in `interview._build_spec()`** (`backend/agent/input_chat.py`): compare each
   `target_value` with the baseline's value for that quantity and reject when the ratio is outside e.g. 1/1000–1000×,
   with a message the model can act on: *"M23 target 7.0 H is 14,000,000× the baseline 5.0e-7 H - check units"*.
   - Design variables and diameters: `design_values(baseline)` is already available there.
   - Simulated results (L, M, ESR, k): need the baseline solve. The GUI already solves the baseline at the plan's
     distances (`AgentPanelState.baseline_runs`); pass those results into the check, or re-check in
     `AgentPanelState` before enabling **Start Search**.
2. **Plan card** (`agent_panel._draw_plan_metrics_table`): for absolute targets show the ratio to baseline and
   highlight anything more than 10× away.
3. **Prompt** (`interview_system_prompt`): tell the model to write targets in SI units exactly as scientific
   notation (7e-7), and to restate the value with units in its reply.
4. **Fail fast in `optimize_loop`**: before round 1, if a required absolute target is more than 1000× from the
   baseline, stop with a clear error instead of spending rounds.

**Tests:** `_build_spec` rejects 7.0 H for M23 on a real baseline; accepts 7e-7 H; accepts a 2× target.

### B2 – One slow LLM reply fails the whole search
**Severity:** High · **Evidence:** Phase 4 S2 and S3 (`timed out` on round 1); Phase 5 follow-ups 1–2 (`timed out`).

**Impact:** a long search is lost because of one slow reply; the user must start over.

**Suggested fix:**
1. **`llm_client.call_llm()`** (`backend/agent/llm_client.py`): a timeout raises `TimeoutError`, which is not
   caught by the `URLError` handler - add it, and retry 2 times with backoff before giving up.
2. **Make the limit configurable** (`AGENT_LLM_TIMEOUT` env var in `agent/config.py`), default higher than 600 s for
   the optimize phase.
3. **Better: stream the reply** (`"stream": true`) and time out only when no tokens arrive for N seconds, so a long
   but progressing reply is never killed.
4. **`optimize_loop`**: on an LLM failure, retry the round (bounded) instead of setting the run to `failed`.
5. **Server:** check llama.cpp's parallel slot count (`-np`) and context size - 3–4 concurrent requests pushed
   rounds to 159–414 s.
6. **GUI:** show "AI still generating (N s)" on the round, and for a follow-up timeout keep the question in the input
   so it can be re-sent.

**Tests:** `call_llm` against a local stub server that sleeps past the timeout → retries, then raises a clear error;
`optimize_loop` with a stub LLM that fails once then succeeds → run completes.

### B3 – Malformed plan crashes the chat turn silently
**Severity:** High · **Evidence:** Phase 3 - 7 cases × 2 baselines raised `TypeError` (see 5.1).

**Impact:** chat shows "Working out the plan..." with no error; the user doesn't know to send another message.

**Suggested fix:**
1. **`run_interview_turn()`**: validate the argument *shape* first (`args` is a dict, `distances` a list of numbers,
   `objectives` a list of dicts, `tolerance_pct` a number) and return a rejected tool result naming the bad field.
   At minimum, catch `TypeError` alongside `ValueError`/`KeyError`.
2. **`InterviewRunner._run_turn()`** (`agent/gui_bridge.py`): catch any exception and put it in `state.error` so the
   GUI shows it; make sure every plan call in the transcript gets a tool result.

**Tests:** every "CRASHED" row in section 5 becomes a rejected tool result with a readable message; a GUI-bridge test
where the turn raises shows the error and leaves `busy` false.

### B4 – No distance limits
**Severity:** High · **Evidence:** Phase 3 (0, −5, 1e9 accepted) and 5.2 (FEMM behaviour).

**Impact:** 0 and −5 mm produce believable but meaningless numbers; 10,000 mm hangs 2 minutes; 1e9 mm crashes.

**Suggested fix:**
1. **One shared check** (e.g. `validate_distance()` in `backend/engine.py`): finite, ≥ a minimum and ≤ a
   maximum (see [open questions](#10-open-questions)), called from `simulate()`.
2. **`_build_spec()`**: apply it to every plan distance and remove duplicates.
3. **Manual Runs** (`runs_panel`): validate the distance field before submitting, with an inline message.
4. **`xfemm_backend`**: turn the `femmcli failed` / output-parse errors into a readable "solver failed at N mm".

**Tests:** `_build_spec` rejects 0, −5, 1e9, NaN; `simulate()` raises a clear error for out-of-range distances.

### B5 – A rejected plan stalls until the user types again
**Severity:** Medium · **Evidence:** Phase 4 S1 (`M12: a required match needs tolerance_pct > 0`) and S3
(`rc_trace_thickness must be one of [0.035, 0.07, 0.14], got 0.071`).

**Suggested fix:** in `run_interview_turn()`, when a plan is rejected, immediately call the LLM again with the tool
error (max 2 retries) so it corrects itself in the same turn. Only surface the error if retries run out.

**Tests:** stub LLM returns an invalid plan then a valid one → one user message ends with a finalized plan.

### B6 – `required: "false"` treated as required
**Severity:** Medium · **Evidence:** Phase 3 "required as string 'false'".

**Suggested fix:** a strict `_as_bool()` in `_build_spec()` accepting `true/false` and the strings `"true"/"false"`,
rejecting anything else; use it for `required` and `include_pcb_thickness`.

**Tests:** `"false"` → preference; `"maybe"` → rejected.

### B7 – Follow-up answers: occasional wrong answer, timeouts, inconsistent wording
**Severity:** Medium · **Evidence:** Phase 4 S1 ("I don't have the finished search result data"), "click New Task",
repeated words; Phase 5.2 timeouts.

**Suggested fix** (`backend/agent/followup.py`):
1. Send the run data as its own message **right before the user's question** (not only in the system prompt), with
   a one-line headline first ("Status: finished, goal NOT met, best = round 2").
2. If the reply claims to have no data while the run has results, retry once.
3. Name the button exactly in the prompt ("the **Restart** button at the top of the chat") and forbid other names.
4. Show a fixed, UI-rendered hint under follow-up replies ("To change anything, click Restart") so the guidance
   doesn't depend on the model's wording.
5. Timeouts: covered by B2.

**Tests:** prompt-building test that the run data message sits directly before the question; live smoke test asking
the goal question on a finished run.

### B8 – Search feedback reports the baseline at a shifting "worst" distance
**Severity:** Medium · **Evidence:** Phase 4 S1 round 3 optimizer reasoning (section 6.8).

**Suggested fix:** in `scorer.objective_result_dict()`, include per-distance `value_by_distance` and
`baseline_by_distance` next to `change_pct_by_distance`, and label the headline numbers "at worst distance X mm".
Explain this once in the optimizer's system prompt.

**Tests:** `tool_result` contains both per-distance maps for per-distance objectives.

### B9 – Requests interpreted too narrowly / misleading plan text
**Severity:** Medium · **Evidence:** Phase 4 S1 - layers "1 or 2" frozen at 2; plan text says copper weights
0.033/0.067/0.133 mm and "snapped", but the search uses 0.035/0.07/0.14 exactly.

**Suggested fix:**
1. Support **allowed values per field** in `VariableSpec` (e.g. `{"num_tank_layers": [1, 2]}`) so "1 or 2" can be
   expressed instead of frozen.
2. Plan card: show the structured constraints (free / fixed / allowed values) generated from the spec, and label
   the model's `objective_text` as the AI's summary.
3. Prompt: list the real copper-weight values (0.035/0.07/0.14 mm) so the text matches the search.

### B10 – Configs with identical geometry share cache labels
**Severity:** Low · **Evidence:** Phase 1 - `Gen3` / `RevA1 with Ferrite (copy)`.

**Suggested fix:** in `JobRunner.find_cached()`, prefer a cached row with the same `config_id` when several match.

### B11 – M23 rises with distance on `plated_slot_coil`
**Severity:** Low (observation) · **Evidence:** Phase 1 - 7.40e-7 (1 mm) → 7.59e-7 (5 mm) → 5.90e-7 (30 mm).

**Suggested action:** physics sanity check - solve at 0.5 mm steps from 1–10 mm and compare with the ferrite and
shields removed.

### B12 – Unhelpful plan error messages
**Severity:** Low · **Evidence:** Phase 3 - missing field returns just `'distances'`; `enabled_features: "tank_ferrite"`
lists single letters.

**Suggested fix:** handled by the shape validation in B3 (name the field and the expected type).

### B13 – NaN tolerance accepted
**Severity:** Low · **Evidence:** Phase 3 "nan tolerance" (Python's `json.loads` accepts `NaN`).

**Suggested fix:** reject non-finite numbers in `_build_spec()` (`math.isfinite`) for tolerances, targets and
distances.

---

## 9. Suggested order of work

| Order | Bugs | Why first | Rough size |
|---|---|---|---|
| 1 | B3, B12, B13, B6 | Plan input validation - one area, cheap, removes silent crashes and silent misreads | Small |
| 2 | B4 | Wrong numbers look real; also affects Manual Runs | Small (after range is agreed) |
| 3 | B1 | Silent impossible goals waste whole searches | Medium |
| 4 | B2 | Searches lost to slow replies | Medium (retry) / larger (streaming) |
| 5 | B5 | Removes the manual nudge | Small |
| 6 | B7, B8 | Answer and search quality | Medium |
| 7 | B9 | Needs a small `VariableSpec` extension | Medium |
| 8 | B10, B11 | Low impact | Small |

Each fix should add the matching test from section 8, and the Phase 2 scoring sweep and Phase 3 plan cases are worth
keeping as permanent tests.

---

## 10. Open questions

1. **Valid distance range:** what are the real minimum and maximum RC separations to allow (B4)? 0.01 mm already
   fails in FEMM; 200 mm solves normally.
2. **Timeout policy:** is a long wait acceptable as long as the AI is still producing output (streaming), or should
   there be a hard cap (B2)?
3. **Layers "1 or 2":** should allowed-value sets be supported for other integer fields too, e.g. turns (B9)?
4. **B11:** is M23 peaking around 5 mm on `plated_slot_coil` expected for that ferrite/slot design?

