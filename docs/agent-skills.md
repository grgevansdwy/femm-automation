---
owns: backend/agent/settings/prompts/*.txt (the criteria these three files are held to)
last-verified: 2026-09-23
---

# What makes a good system prompt

A checklist for writing or editing any of this project's system prompts
(`backend/agent/settings/prompts/interview_system.txt`, `search_system.txt`,
`followup_system.txt`), distilled from actually restructuring all three.
Each point names which prompt demonstrates it, so "why" is never just asserted -
you can go read the real example. See `docs/Agent.md` for what each prompt is
*for*; this doc is about how any of them should be *written*.

## The checklist

**1. Structure it into named sections, not one wall of prose.**
Use a small number of top-level containers (this project uses XML tags:
`<background_information>`, `<output_description>`, `<instructions>`) with
`##` markdown subheads inside each for individual rules. This isn't
decoration - it lets both the model and the next person editing the file find
"the rule about X" without re-reading everything. All three prompts now
follow this shape.

**2. State the role and the scope boundary up front.**
Say what the model is, and say explicitly what's out of bounds and what to do
when asked about it - don't leave scope implicit. `interview_system.txt`:
*"Only discuss this coilset's FEMM simulation objective - politely decline
anything unrelated... and steer back to the task."*

**3. Never leave the model to guess on missing or ambiguous input.**
Give it an explicit fallback: either surface a stated default as a question
("ask ONE short question... stating the default you'd otherwise use"), or an
explicit "say you don't know" rule (`followup_system.txt`: *"if something
isn't in it, say plainly that you don't have that information - never guess
or infer..."*). Every prompt in this project now has its own `## Handling
incomplete...` section naming this rule for its specific situation - the
right fallback is different in a live chat (ask) versus a data lookup
(admit not knowing) versus an autonomous search loop (read the specific
rejection reason, don't guess a different unrelated change).

**4. Density over length - minimal doesn't mean shallow.**
Cut filler and true restatement; keep every sentence doing distinct work.
Don't mistake *deliberate* repetition for bloat: `interview_system.txt`
says twice, in different words, that having a feature (like ferrite) does
not make it changeable - that's targeted reinforcement of a rule the model
is specifically prone to getting wrong, not padding.

**5. Every instruction should resolve to exactly one action.**
If a sentence could reasonably be read two ways, a model will sometimes pick
the wrong one under pressure - split it into explicit branches instead of one
run-on conditional. `followup_system.txt` used to handle "search running" vs.
"search finished" as one sentence with two inline template blocks; it's now
two separate, complete sentences, each unambiguous on its own.

**6. Say which source of truth wins when things conflict.**
Heuristics and physical/data reality will occasionally disagree - decide that
in advance instead of leaving the model to referee it live.
`search_system.txt`: *"FEMM is ground truth: when a returned result
contradicts anything below, trust the result, not the heuristic."*

**7. Describe the output contract precisely, and keep it in sync with the real tool schema.**
If the model calls a tool, the prompt's description of each field should
match what the code actually validates - not an approximation that drifts
over time. Both `interview_system.txt` (`finalize_spec`) and
`search_system.txt` (`propose_coilset`) carry a dedicated
`<output_description>` section mirroring the tool's real JSON schema in
`backend/agent/femm_agent/models/schema.py` / `input_chat/services/interview.py`.

**8. Tell it what happens *after* it acts, not just how to act.**
Describe the feedback loop so the next turn isn't a surprise.
`search_system.txt`'s `## Reading feedback and finding your bottleneck`
explains exactly what comes back after a proposal (value, % change, margin)
and how to use it.

**9. State hard boundaries as prohibitions, not just as absent permissions.**
A boundary that's only ever phrased positively ("your job is to answer
questions") can be reasoned around. A hard negative closes that gap:
`followup_system.txt`: *"You cannot change this search in any way... not
even as a hypothetical suggestion."*

**10. Keep placeholders few, clearly named, and validated by code - not just by convention.**
Every `{{placeholder}}` should map to something the calling code actually
computes (`backend/agent/settings/prompt_defs.py`'s `PromptDef.placeholders`),
and the ones the prompt can't work without should be enforced
(`required=(...)`, checked by `templating.check_placeholders()`). This is
what let all three prompts here get fully restructured without silently
breaking - the check ran the same before and after every edit.

**11. Verify against the real render pipeline before trusting it, not just by reading the template.**
A template can read fine and still render broken - a mis-paired conditional
block, a stray blank line, a placeholder silently dropped. Render it with
real input through the actual code path (this project: rendering each
prompt for a real baseline config) before calling an edit done.

**12. Match the output format to what actually renders it - don't assume markdown.**
If the reply lands in a plain-text widget, formatting marks show up as literal
characters, not styling. `interview_system.txt` and `followup_system.txt` both
learned this the hard way: a reply like `**M12**` rendered as literal asterisks
in the desktop app's chat feed (`imgui.text_colored`, no markdown rendering -
see `docs/GUI.md` §7), so both now explicitly say plain text only, no
`**bold**`/`_italics_`/backtick spans/markdown lists.

## Applying it

When editing one of the three prompts:
1. Find which section (by the containers in point 1) the change belongs to - don't bolt it onto the nearest paragraph.
2. Check it against points 3, 5, and 9 specifically - these are the ones a quick edit most easily violates.
3. Re-run `check_placeholders()` and render the prompt for a real baseline (see `docs/Agent.md` for how) before considering the edit finished.
