# Human test cases: AI Agent interview + search (Gen2_coil)

Five scripted conversations for testing the generalized objective system by
hand in the GUI (AI Agent mode, baseline **Gen2_coil**). Each case lists:

- **Tests**: what the case is designed to catch.
- **Script**: what to type, turn by turn. "If the AI asks..." lines are
  conditional. Only send them if the AI actually asks.
- **Expected plan**: what the "Proposed plan" card should say before you
  click Start Search. This is the correct `finalize_spec` result.
- **Watch for**: specific failure modes, including wording problems.

Mark each **Watch for** item PASS/FAIL and copy any confusing AI sentence
verbatim into your notes.

## Gen2_coil reference values (to judge the AI's numbers)

| Quantity | Baseline (3 / 5 / 8 / 12 mm) |
|---|---|
| L_loop | 190.3 / 190.5 / 190.8 / 191.1 nH |
| L_tank | 4.597 / 4.610 / 4.625 / 4.635 uH |
| L_rc | 1.572 / 1.588 / 1.601 / 1.607 uH |
| M13 | 282 / 264 / 240 / 211 nH |
| M23 | 1.815 / 1.706 / 1.565 / 1.397 uH |
| k23 | 0.675 / 0.631 / 0.575 / 0.512 |
| loop / tank / rc ESR | 18.1 / 472 / 210 mOhm at 3 mm (drops ~5-17% by 12 mm) |
| Outer diameter | loop 151.0 mm, tank 180.7 mm, rc 128.1 mm |

Design facts that matter below:
- **No active features:** no ferrite, shields or backplanes.
- **Trace thickness:** all 0.07 mm (2 oz). Only 1/2/4 oz (0.035 / 0.07 / 0.14) are allowed.
- **Integer fields:** `num_loop_turns` = 1, `num_tank_turns` = 5, `num_rc_turns` = 3.
- **Fields that are 0 on the baseline:** `rc_trace_spacing`, `tank_trace_spacing`, `loop_turn_spacing`.
- **PCB thickness:** 1.6 mm, fixed unless the user says otherwise.
- **Distance cap:** at most 5 distances (`MAX_DISTANCES`).

## General "Watch for" checklist (every case)

Wording and interview behavior:
1. **One question per turn.** Does it ever ask two or more, or send a numbered list?
2. **Default stated.** Does each question state the default it would use?
3. **Nothing re-asked.** Does it ever ask something your message already answered?
4. **No unexplained jargon.** Does it surface internal names (`rc_trace_spacing`, `k23`,
   `target_value`, `fixed_values`) without saying what they mean?
5. **Plan matches the conversation.** Does the plan card match what the AI said in chat?
   A mismatch means the chat summary and the structured spec drifted apart.
6. **Silent rejection.** If you see "(finalizing the search objective...)" and then no plan
   card, the model's `finalize_spec` was rejected by validation. The chat hides the error
   message. Record the turn, then type "what went wrong?" and see whether it can explain.

After the search:
7. **Result table.** It should list exactly the plan's objectives, nothing extra
   (no M23 or ESR rows you didn't ask for).
8. **Structural check.** "What changed" should say "no out-of-scope fields changed", and
   pinned values should appear in the diff.

---

## Case 1: "the old system could never say this"

**Tests:**
- **Generalization breadth:** every objective method in one request:
  - a relative match with a target (+8% ±1)
  - a match holding baseline (±3%)
  - an absolute floor on a simulated quantity (k23 >= 0.50 at every distance)
  - a design-variable objective (trace width)
  - an absolute ceiling on a derived quantity (diameter)
  - a preference
- **No hidden objectives:** nothing about ESR is required unless stated.
- **A genuinely hard search:** raising L_rc lowers k23 (k = M / sqrt(L_tank * L_rc)), and
  a bigger RC radius raises M23 and the diameter. The floor and ceilings all bind.

**Script**

> **Turn 1:** Make the RC coil's self-inductance about 8% higher, with a ±1% margin, while keeping M23, L_tank and the loop ESR roughly the same - within 3%. On top of that, the tank-to-RC coupling coefficient must never drop below 0.50 at any distance, the RC trace width should end up at least 15% wider than it is now, and the RC outer diameter can't go over 135 mm. If it's possible, I'd also like the tank ESR not to get worse, but that's only a nice-to-have. Test at 4, 7 and 11 mm.

- If the AI asks whether "about 8%" means "at least 8%": *"About 8%, ±1% like I said."*
  (FAIL item 3: it was already answered.)
- If the AI asks about ferrite/shields: *"Leave all structural features as they are."*

**Expected plan**
- Distances: `[4, 7, 11]`
- Required: `L_rc` within +/-1% of +8% vs baseline
- Required: `M23` within +/-3% of baseline
- Required: `L_tank` within +/-3% of baseline
- Required: `loop_esr` within +/-3% of baseline
- Required: `k23` >= 0.5 (absolute, `increase` + `target_value`)
- Required: `rc_trace_width` >= 15% higher
- Required: `rc_outer_diameter` <= 135 (absolute)
- Preferred: `tank_esr` no higher than baseline
- No `fixed_values`, no enabled features

**Watch for**
- **Wrong k23 method:** k23 must be an *absolute* floor (0.50), not "+/-x% of baseline" and
  not a relative increase.
- **Treated as a pin:** "at least 15% wider" must be an objective on `rc_trace_width`, not a
  pinned value (`fixed_values`).
- **Required/preference mix-up:** "nice-to-have" must be a preference, and "roughly the same"
  must be required.
- **Invented objectives:** the plan must contain exactly 8 objectives. Extra M12/M13/k12 or
  diameter entries are a FAIL.
- **After the search:** k23 is binding at 11 mm (baseline ~0.53, and +8% L_rc alone pushes
  it to ~0.51). Check whether the result's worst k23 distance is 11 mm. If the budget runs
  out, look at which objective's margin stayed most negative.

---

## Case 2: vague, colloquial, mixed units, then a revision

**Tests:**
- **Interview wording under ambiguity:** "knock 15% off" and "basically the same".
- **Two possible meanings:** "power link" could be M23 or k23.
- **Unit conversion:** nH to H.
- **"Stuck with" a value** that already equals the baseline.
- **Unstated distances:** it must ask and state a default.
- **Revision after the plan appears:** the old plan card must vanish, then return updated.

**Script**

> **Turn 1:** can you make the tank a bit less lossy? like knock 15% off the tank ESR. keep the power link basically the same, maybe 3% slack. loop inductance of around 190 nH is fine, just don't let it wander more than 2%. oh and 2oz copper everywhere is what we're stuck with

Answer only what it asks, one reply per question:
- If it asks at least vs about 15%: *"At least 15% - more is better."*
- If it asks M23 vs k23 (or "which link"): *"The mutual inductance, M23."*
- If it asks about distances: *"Just use whatever you think is sensible."* (Record which
  default it picks, and whether it says why.)
- Anything else: *"Your default is fine."*

When the plan card appears, **don't click Start Search**. Send:

> **Revision:** actually give the power link 4% instead of 3%, and also the RC ESR shouldn't get any worse

**Expected plan (after the revision)**
- Required: `tank_esr` >= 15% lower
- Required: `M23` within +/-4% of baseline
- Required: `L_loop` within +/-2% of 1.9e-07 (absolute, in H). A relative ±2% of baseline
  is also acceptable, because the baseline is already ~190 nH.
- Required: `rc_esr` no higher than baseline
- Thickness: all three `*_trace_thickness` kept fixed at 0.07, via `excluded_fields` or
  `fixed_values`, never as an objective
- Distances: whatever default it proposed

**Watch for**
- **Unit conversion (most important):** any `target_value` for L_loop other than ~1.9e-07
  (e.g. `190` or `1.9e-06`) is a FAIL. Such a spec fails every candidate from round 1.
- **Unasked ambiguity:** did it ask about "knock 15% off" and "power link"? Silently picking
  is OK only if it picked correctly *and* the plan card makes the choice visible.
- **Plan card during the revision:** the old card must disappear as soon as you send the
  revision. The new card must show 4%, the new rc_esr line, and still all the old lines.
- **Revision drops objectives:** a common LLM failure. Every original objective must still
  be in the revised plan.
- **"Stuck with 2oz" becomes an objective:** no `trace_thickness` entry may appear in the
  objective list.

---

## Case 3: the 5-distance guardrail

**Tests:**
- **The `MAX_DISTANCES` = 5 cap.** Here's how it's enforced today:
  - it's in the `finalize_spec` tool schema (`maxItems: 5`)
  - `_build_spec()` rejects more than 5
  - the interview system prompt does **not** mention the cap
- **Holding the line:** does the AI keep the cap when pushed?
- **Bad distance values:** there is no validation for 0 or negative distances, or for duplicates.

**Script**

> **Turn 1:** Hold M23 within 5% and cut the RC ESR by at least 10%. I need this checked at 1, 2, 3, 4, 6, 9, 12 and 15 mm - all eight of them, the customer spec covers that whole range.

Then, depending on what happens:
- **If it says there's a limit and asks you to pick:** push back:
  > I really need all eight. Can't you just run them all? I don't mind waiting.

  It should hold the limit and explain why (cost per candidate). Then send:
  > Fine - use 1, 4, 8, 12 and 15.
- **If a plan appears with 5 distances you didn't choose:** that's a silent truncation (see
  Watch for). Note which were dropped, then send:
  > Why did you drop some of my distances? Use 1, 4, 8, 12 and 15.
- **If it shows "(finalizing...)" and then no plan:** the 8-distance spec was rejected
  silently. Send "what went wrong?" and record the answer. Then send the 5-distance list above.

Once there's a valid 5-distance plan, probe the edges (**don't** start the search yet):

> **Probe A:** swap 1 mm for 0 mm, and add -3 mm as a sixth one

(Expected: it refuses the sixth. Ideally it also questions 0 and -3 mm.)

> **Probe B:** ok no negatives. use 0, 4, 8, 12, 12

(A duplicate, plus a 0 mm gap.)

> **Final:** use 2, 4, 8, 12, 15

**Expected plan**
- Distances: `[2, 4, 8, 12, 15]`
- Required: `M23` within +/-5% of baseline
- Required: `rc_esr` >= 10% lower

**Watch for**
- **Explaining the limit:** does the AI state the limit *before* trying to finalize 8
  distances? It can only know from the tool description, not the system prompt.
- **Silent truncation:** the server may enforce `maxItems` by grammar and cut the list to the
  first 5 (1, 2, 3, 4, 6) without telling you. That's a FAIL, because 9/12/15 mm are lost
  unannounced.
- **Caving under pushback:** it must not agree to run 8, and must not finalize 8 (that gets
  rejected silently).
- **0 mm and -3 mm:** code-level validation doesn't exist, so only the AI's judgment protects
  you here. Does it ask whether 0 mm (touching coils) is intended? Does it reject -3 mm?
- **Duplicate 12, 12:** does it notice? It's harmless in the code (solves are keyed by
  distance, so the duplicate collapses into one), but the plan card will show 12 twice, and
  the user asked for 5 distances while effectively getting 4.
- **Asking 8-vs-5 questions one at a time:** it should do so, without dumping options.

---

## Case 4: pins, a non-existent copper weight, adding ferrite, and a contradiction

**Tests:**
- **Pinned values:** "force 6 turns" becomes `fixed_values`.
- **An invalid pin value:** 3 oz isn't an allowed thickness, so the AI should ask.
- **Enabling a feature the baseline lacks:** RC ferrite.
- **An absolute L target in uH.**
- **"Don't touch the loop at all":** a whole-coil exclusion.
- **An ambiguous opt-in:** "make the PCB thinner" (which PCB? how much?). It also contradicts
  "don't touch the loop" if it means the loop PCB.

**Script**

> **Turn 1:** Use 3 oz copper on the tank, force the tank to 6 turns, and add ferrite behind the RC coil. With that setup, get the tank inductance to 5.5 uH, give or take 2%, and keep the tank outer diameter under 190 mm. Don't touch the loop coil at all. Oh, and make the PCB thinner.

- If it says 3 oz isn't available: *"Go with 4 oz then."*
- If it asks which PCB, or how much thinner: *"Only the tank PCB, at least 25% thinner."*
- If it asks about the RC ferrite's size/material: *"Let the search pick those."*
- If it asks about distances: *"5 and 10 mm."*
- If it finalizes without asking about the PCB: send *"which PCB did you make thinner, and
  by how much?"* and record that it assumed.

**Expected plan**
- `fixed_values`: `tank_trace_thickness = 0.14`, `num_tank_turns = 6`. Ideally also
  `rc_ferrite_present = 1` (see Watch for).
- Enabled features: `rc_ferrite`
- PCB: `include_pcb_thickness = true`, with `loop_PCB_thickness` excluded (and ideally
  `rc_PCB_thickness` too, since the user said "only the tank PCB")
- Excluded: every `loop_*` field (`loop_offset, loop_r, num_loop_turns,
  num_parallel_loop_traces, num_loop_layers, loop_trace_spacing, loop_turn_spacing,
  loop_trace_width, loop_trace_thickness`)
- Required: `L_tank` within +/-2% of 5.5e-06 (absolute)
- Required: `tank_outer_diameter` <= 190
- Required: `tank_PCB_thickness` >= 25% lower
- Distances: `[5, 10]`

**Watch for**
- **3 oz** must never reach the plan. If the AI finalizes `0.105`, validation rejects it
  (another silent rejection, checklist item 6).
- **"Add ferrite" semantic gap:** enabling `rc_ferrite` only makes its fields *free*,
  including the on/off flag. If `rc_ferrite_present` isn't pinned to 1, the search may simply
  leave ferrite off, since the baseline has it off. After the search, check "What changed":
  is `rc_ferrite_present` 0 -> 1? If not, the user's "add ferrite" was silently not done.
  Record which way it went.
- **"Don't touch the loop coil":** did it exclude *all* nine loop fields, or just a few?
  Missing ones will show up in "What changed" after the search.
- **PCB contradiction:** "make the PCB thinner" plus "don't touch the loop". Did it ask,
  assume, or notice the conflict?
- **Units:** `L_tank` must be 5.5e-06, not 5.5 and not 5.5e-09.
- **Physics sanity (after the search):** 6 turns alone takes L_tank to ~4.6 x 36/25 ≈ 6.6 uH,
  above the 5.5 target. So the search must pull L down some other way (radius, spacing),
  while the diameter stays under 190 (6 turns already makes it ~188 mm). Expect a hard,
  possibly failing search. Judge whether the AI's round-by-round reasoning notices that
  tension.

---

## Case 5: off-topic, impossible, zero-baseline, contradictory, all-preferences

**Tests:**
- **Scope guardrail:** off-topic chat.
- **An impossible integer objective:** the loop has 1 turn, so "20% fewer" is impossible.
- **A % change of a zero-baseline field:** `rc_trace_spacing` = 0.
- **Two contradictory requirements on one quantity:** this is also a duplicate, which
  validation rejects.
- **Turning the only requirement into a preference:** validation needs at least one required.

**Script**

> **Turn 1:** hey before we start, what's the best PCB fab house in Shenzhen?

(Expected: a polite decline, steering back to the coil objective.)

> **Turn 2:** ok fine. I'd like the loop to have 20% fewer turns, open up the RC trace spacing by 10%, and M13 should be at least 5% higher and also stay within 2% of baseline.

Expected: it pushes back on all three, ideally one at a time:
- **Loop turns:** the loop already has 1 turn, so 20% fewer is impossible. Answer: *"Right,
  forget the loop turns."*
- **RC trace spacing:** it's 0 on the baseline, so "+10%" is meaningless, and it only matters
  with multiple parallel traces (the RC has 1). Answer: *"Then just make the RC trace spacing
  0.5 mm."*
- **M13:** "at least +5%" and "within ±2%" can't both hold. Answer: *"Just keep M13 within 2%."*

> **Turn 3:** nothing else is required. a smaller RC diameter would be nice though, not essential. just check at 6 mm.

**Expected plan**
- Distances: `[6]`
- `fixed_values`: `rc_trace_spacing = 0.5`
- Required: `M13` within +/-2% of baseline
- Preferred: `rc_outer_diameter` no higher than baseline (or a reduction preference)
- Nothing about loop turns

Then probe the "at least one required" rule. **Don't start the search:**

> **Probe:** actually make the M13 one optional too - everything's a nice-to-have.

(Expected: it explains that at least one requirement is needed, otherwise the first
candidate "meets the goal" instantly, and asks what should be required. If you see
"(finalizing...)" and no plan, record it as a silent rejection, checklist item 6.)

> **Close:** ok keep M13 required.

**Watch for**
- **Off-topic:** the decline should be short and friendly, and must not answer the fab question.
- **Loop turns:** does it catch the impossibility *before* finalizing? If it finalizes
  `num_loop_turns` reduce 20, the spec is valid code-wise but can never pass. The search
  would burn its full 50-round budget. FAIL.
- **Zero-baseline spacing:** does it catch "+10% of 0" itself? If not, validation rejects it
  (silent rejection again). Does it offer an absolute value?
- **M13 contradiction:** does it notice both parts are about M13 and conflict, rather than
  quietly keeping one?
- **"Make it 0.5 mm"** must become a pin (`fixed_values`), not a match objective.
- **Question count:** Turn 2 has three separate problems. Does it raise them one per turn
  (as instructed), or dump all three? Both are defensible here; record which it did and
  whether the result was clear.
- **All-optional probe:** the answer should be understandable to a non-programmer. No
  "required=false" or "objectives array" jargon.
