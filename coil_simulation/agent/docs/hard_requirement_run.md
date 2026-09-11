# Harder-requirement run: what happened

**Status: this run did not actually test the harder requirement.** It hit a
real bug on the first call and repeated the exact same failure for 14
straight rounds before being stopped. The bug is now fixed and verified,
but the harder-requirement question itself (below) is still open — see
"Next step."

## What changed vs. the earlier (easy) run

The earlier run's `goal_met` accepted *any* tank ESR reduction, however
marginal, once the mutual-inductance gate passed — it found a -1.5%
incidental win (from changing an unrelated field) and stopped after 1 call.
To make the requirement harder and force real search:

| | Easy run | Hard run |
|---|---|---|
| `M_TOLERANCE_PCT` | 5.0 | 2.0 |
| ESR bar | any reduction (`< baseline`) | `MIN_ESR_IMPROVEMENT_PCT = 10.0` — must drop by **at least 10%** |
| System prompt | "reduce tank ESR" | "reduce tank ESR by AT LEAST 10% — a small or marginal reduction does not count" |

## What actually happened

All **14 of 14** tool calls failed identically, before any FEMM solve ran:

```
[1] {'num_parallel_tank_traces': '1', 'tank_trace_spacing': '0', 'tank_turn_spacing': '7.444', ...}
    -> INVALID: unsupported operand type(s) for -: 'str' and 'int'
[2] ... -> INVALID: unsupported operand type(s) for -: 'str' and 'int'
...
[14] ... -> INVALID: unsupported operand type(s) for -: 'str' and 'int'
```

Full raw log: every value in every proposed delta was a **JSON string**
(`'1'`, `'0.07'`, `'72.688'`) instead of a number (`1`, `0.07`, `72.688`).
`dataclasses.replace(baseline, **delta)` accepted these silently — `CoilSet`
is a plain, unvalidated dataclass — and the failure only surfaced much
later, deep inside `check_valid_coil()`'s arithmetic (`num_turns - 1` etc.),
as an opaque `TypeError` with no field name attached.

I stopped the run manually after 14 rounds rather than letting it burn
through the remaining 36-call budget: the failure was already 100%
reproducible with zero variation, and the tool-result error message
(`"unsupported operand type(s) for -: 'str' and 'int'"`) gives the model no
actionable signal — it doesn't say which field, or what type was expected —
so there was no reason to expect call 15 to differ from call 1.

## Root cause

Same model (`llama3.2`), same machine, same script — but in the **earlier**
run it emitted proper numeric JSON types (`'loop_trace_width': 3.175`), and
in **this** run it emitted the same values as strings (`'loop_trace_width':
'3.175'`). Small local models are not fully reliable about JSON type
fidelity in tool-call arguments, and that appears to vary between runs of
the *same* model against the *same* schema, not just between models. The
script never validated or coerced the delta's types before using them —
it trusted the tool schema would be honored.

## Fix applied

Added `_coerce_delta()` to `agent_prototype.py`: converts every value in a
proposed delta to the type its `FREE_FIELDS` schema declares (`int` or
`float`) before building the candidate, raising a clear
`'<field>' must be a <type>, got <value>` error on genuine bad input instead
of letting a bad type reach FEMM's arithmetic unnoticed. Called immediately
before `dataclasses.replace()` in `main()`.

Verified directly against the exact failing delta from round 1 of this run:
coercion succeeds, the resulting candidate evaluates correctly
(`primary_pass=True`), and deliberately-bad input (a non-numeric string, an
unknown field name) is still rejected with a specific, actionable message.

## Re-run with the fix: what actually happened

The type bug is gone — every value in round 1's delta came back correctly
typed (`'tank_center_r': 72.688`, not `'72.688'`). The stricter goal also
worked exactly as designed: round 1's candidate (only `rc_turn_spacing`
changed, 6.457 → 7.444) got `tank_esr_change_pct: -0.55`, correctly
`goal_met: false` since -0.55% is nowhere near the required 10% reduction.

But the run still only produced **1 real candidate** before stopping — on
round 2, the model returned plain text instead of another tool call, which
the loop (correctly, per its own logic) treats as "the model is done":

> "...this proposal is a step in the right direction, but further
> refinement is needed to meet the full objectives. **I will propose
> another candidate to build upon this result.**"

It says it's going to keep going, then doesn't call the tool — so the loop
ends on round 2 with only one real data point. That's a genuine bug in the
stop-condition design, not the model "giving up": `main()` treats *any*
tool-call-free response as a deliberate stop, but the system prompt's own
instruction ("stop calling the tool... once you've found a good candidate
**or are stuck**") apparently reads as permission to produce a reflective
text turn without committing to stop or continue. The loop can't tell the
difference between "I'm done" and "let me think out loud first."

**Its reasoning also doesn't match the data it was given** — checked
directly against the real `_tool_result()` for that exact candidate:

| Model's claim | Actual value | |
|---|---|---|
| "tank ESR... reduction... is greater than the required 10.0%" | `tank_esr_change_pct: -0.55`, `goal_met: false` | Contradicts its own prior sentence ("still falls short") in the same paragraph |
| "outer diameters of the tank and RC coils have changed by 1.54% and 3.39%" | `tank_outer_diameter_change_pct: 0.0`, `rc_outer_diameter_change_pct: 1.54` | 1.54% is RC's diameter change, not tank's; tank's is 0.0% (only an RC field changed) |
| (same sentence, "3.39%") | `rc_self_inductance_change_pct: -3.39` | This is RC self-inductance, not a diameter figure at all - two different metrics conflated |

So the one visible piece of model reasoning collected across every run so
far is measurably wrong, not just imprecise - it swaps which coil a number
belongs to, conflates diameter with self-inductance, and states a
conclusion its own preceding sentence contradicts.

## What this reveals about the prototype's current limitations

- **Tool-call argument types aren't reliable even within the same model** —
  this needs boundary validation, not an assumption that the JSON schema is
  honored. That's now fixed, but it's a reminder that anything crossing the
  LLM/code boundary needs the same skepticism as any other untrusted input.
- **The feedback loop doesn't give the model anything to correct with** when
  it makes a systematic mistake — 14 identical failures with an unhelpful
  error message produced 14 identical retries, not adaptation. The fix
  above improves the error message specifically so a future occurrence is
  at least self-correctable; a broader gap (no visible reasoning from the
  model between attempts, flagged in an earlier session) is still open.
- **A single bad run can silently invalidate an entire experiment.** Every
  number reported in the earlier "easy run" summary was real (that run's
  values were correctly typed), but this run produced zero usable data
  about whether the *harder requirement itself* is achievable — the search
  never started.
- **The stop condition can't distinguish "done" from "thinking out loud."**
  The re-run (after the type fix) ended after a single real candidate
  because the model produced a reflective text turn instead of a tool call
  - even while explicitly stating it intended to keep going. `main()`'s
  "no tool call = stop" rule is too blunt for that. A steadier version would
  either push the model to keep calling the tool until it says something
  unambiguous like "FINISHED", or send one nudge ("continue proposing")
  before accepting a text-only turn as a real stop.
- **The model's stated reasoning doesn't reliably match the numbers it was
  given.** The one real piece of visible reasoning collected so far
  misattributed a diameter change to the wrong coil, conflated a diameter
  figure with a self-inductance figure, and asserted a conclusion its own
  previous sentence contradicted. If a future version surfaces this
  reasoning to a human reviewer as justification for a candidate, it can't
  be trusted at face value - the actual `_tool_result()` numbers are the
  only ground truth.

## Fix #2: the nudge, and the third run

Added a fix to `main()`: a text-only reply no longer stops the loop
immediately. Instead it gets one nudge ("Continue - call propose_coilset
with your next candidate...") appended as a user message, and only a
*second consecutive* text-only reply counts as a real stop
(`no_tool_call_streak >= 2`).

Re-ran with the fix. Result, over 16 rounds before I stopped it (see below
for why): a perfectly alternating pattern -

```
round 1  -> real proposal -> INVALID (trace_spacing=0 vs. trace_width)
round 2  -> text only, "Trying to increase num_parallel_rc_traces..." -> nudged
round 3  -> real proposal -> INVALID (same reason)
round 4  -> text only, "Trying to decrease loop_trace_width..." -> nudged
... (repeats identically through round 16)
```

**The nudge fix works exactly as designed** - every text-only reply was
followed by a real tool call on the very next round, with zero further
premature stops. That specific bug is closed.

**But every single real proposal (8 of 8: rounds 1,3,5,7,9,11,13,15) was
invalid, for the identical structural reason every time**: the model kept
increasing `num_parallel_tank_traces` / `num_parallel_rc_traces` /
`num_parallel_loop_traces` (up to 11 in one attempt) without ever setting a
matching nonzero `*_trace_spacing` - so traces overlap
(`trace space of 0 is less than trace width of 3.0`). This exact constraint
is spelled out explicitly in the system prompt's `PARAM_EFFECTS` text. Eight
repeated identical-category error messages did not change its approach - it
varied the numbers (trace width, offsets, which coil) but never touched the
one field that would have fixed the failure.

I stopped the run manually at round 16 (~20 minutes in, 34 calls still
budgeted) rather than let it exhaust the full 50: the pattern was already
decisive at 8-for-8 identical failures with a perfectly regular alternating
rhythm, and running the rest of the budget would have cost another ~40
minutes to confirm the same conclusion, not learn anything new.

## Overall conclusion (three runs, two real bugs fixed)

| Run | Outcome | What it exposed |
|---|---|---|
| 1 (easy requirement) | "Succeeded" in 1 call | The requirement itself was too weak (`goal_met` accepted any ESR reduction) - fixed by adding `MIN_ESR_IMPROVEMENT_PCT` |
| 2 (hard requirement, v1) | 14/14 calls invalid | A real type-coercion bug (`str` args reaching arithmetic) - fixed with `_coerce_delta()`, verified against the exact failing input |
| 3 (hard requirement, v2) | 1 real candidate, then stopped | A real stop-condition bug (text-only reply treated as "done") - fixed with the one-nudge-then-stop logic above |
| 4 (hard requirement, v3) | 8/8 real proposals invalid, same reason each time; stopped manually at round 16/50 | Not a code bug - a genuine model-capability limit: the search never adapts past a specific, clearly-stated geometric constraint despite repeated identical feedback |

The code is now demonstrably correct at the mechanics level: types are
validated, the loop can't be fooled by a reflective non-tool-call turn, and
invalid geometry is rejected cleanly without crashing. What three iterations
of fixing real bugs eventually surfaced is a **different, harder problem
than any of the bugs**: `llama3.2` (3B, CPU) does not reliably use the
physics guidance it's given to avoid a specific, repeated, well-explained
mistake, even across 8 identical corrections. That's not something another
code fix addresses - it's the actual open question flagged back in
`docs/ai_agent_design.md` §5.1 ("is LLM-driven search sample-efficient
enough in practice?") and §7 ("which model?"), now with concrete evidence:
under the harder requirement, this specific small model never got past the
same structural mistake. Worth trying either a larger/different model, or
adding the trace_spacing relationship as a computed default in the tool
response rather than something the model has to remember unprompted, before
concluding the approach itself doesn't work.

## Run 5: trying a bigger model (qwen2.5:7b)

Same harder requirement, same fixes in place, only `AGENT_MODEL` changed
from `llama3.2` (3B) to `qwen2.5:7b` (7B) - both confirmed tool-calling
capable via `ollama show`.

**Qualitatively different behavior from the first token.** Round 1 was a
baseline sanity check; from round 2 onward it ran real, varied,
purposeful exploration - never once repeated an identical mistake the way
llama3.2 did 8/8 times:

```
[2]  num_tank_turns: 5->4          -> primary_pass=False  tank_esr=-26.0%
[3]  loop_trace_width: 3.175->3.5  -> primary_pass=True   tank_esr=-0.3%
[4]  loop_trace_width: ->3.75      -> primary_pass=True   tank_esr=-0.8%
[5]  loop_trace_width: ->4.0       -> primary_pass=True   tank_esr=-1.0%  (best safe result)
[6]  loop_trace_width: ->4.25      -> primary_pass=True   tank_esr=-0.3%  (worse - backed off)
[7]  loop_trace_width: ->4.5       -> INVALID (hit the trace-spacing geometry limit)
[8]  loop_trace_width: ->4.4       -> primary_pass=True   tank_esr=-0.7%  (retreated from the wall)
[9]  turns:4 + width:4.25          -> primary_pass=False  tank_esr=-27.2%
[10] turns:3, loop_turns:1         -> primary_pass=False  tank_esr=-51.1%
[11] turns:6, width:3.75           -> primary_pass=False  tank_esr=+28.5% (turns up also breaks M)
[12-15] more turns+width combinations, all primary_pass=False, -26% to -51% ESR
```

It discovered a **real structural tension** in this specific baseline +
tolerance combination: changing `num_tank_turns` at all - fewer *or* more -
breaks the 2% M-tolerance gate at some distance in the sweep, even though
turn-count changes are what deliver the large ESR wins (-26% to -51%, far
past the 10% bar). The only lever that preserves the M gate
(`loop_trace_width`) tops out around -1.0% ESR before diminishing returns
and a geometry wall. Neither lever alone reaches `goal_met`. It was
starting to combine both levers together (rounds 9, 12-15) - the right next
move for a human engineer too - but hadn't found a working combination by
round 15.

**It never converged, and the run ended in a crash, not a stop condition:**
at round 16, the Ollama request itself failed with `TimeoutError: timed
out` - a real bug, not a modeling limitation. `call_ollama()` has a
hardcoded timeout, and the conversation had grown large enough (15+ rounds
of history, resent in full every request, on a 7B CPU-only model) that one
request exceeded it. The script had **no handling for this** - it crashed
with an unhandled exception, and because the final report only runs after
the loop, **the best candidate found (round 5's `loop_trace_width=4.0`,
-1.0% ESR) was never printed anywhere** despite being tracked correctly in
memory the whole time.

**Fix applied:** wrapped `call_ollama()`'s call site in `main()`'s loop in
a `try/except` - a failed request now prints a message and breaks out of
the loop cleanly, so the final "best candidate found" report still runs
off of whatever was tracked so far. Also raised the timeout from 300s to
600s to make this less likely to trigger at all, though that's a mitigation,
not a real fix - a long enough run on slow hardware will always eventually
hit *some* timeout as the resent conversation keeps growing every round.

## Verdict: did the bigger model close the gap?

**Partially, and in a meaningfully different way than "success/failure".**
qwen2.5:7b never reached `goal_met=True` either - so neither model actually
solved the stated harder requirement in these runs. But the *character* of
the failure is completely different:

| | llama3.2 (3B) | qwen2.5:7b |
|---|---|---|
| Repeated an identical mistake | Yes - 8/8 real proposals, same root cause, no adaptation across 8 corrections | No - every proposal explored something new |
| Used the physics guidance correctly | No - ignored the explicit trace_spacing constraint every time | Yes - varied trace width right up to the geometry limit, backed off correctly when it failed |
| Found a real trade-off insight | No | Yes - correctly identified that turn count is the lever that matters for ESR but breaks M-matching either direction |
| Reason it didn't converge | Never got past a basic, explained constraint | Hit a genuinely hard region of the actual design space (may not be solvable within 2% tolerance without a 3rd lever) |

So the bigger model didn't magically solve the objective, but it did close
the exact gap the earlier runs found: it uses feedback to adapt, and its
failure mode now looks like "this design space is genuinely difficult"
rather than "the model can't follow an explicit instruction." That's the
qualitative signal worth trusting more than either run's raw `goal_met`
result.
