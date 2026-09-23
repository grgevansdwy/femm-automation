---
owns: docs/*.md (routing table only — see the frontmatter on each doc for what it owns)
last-verified: 2026-09-23
---

# docs/ — how to find what you need

This folder holds three reference docs (one per component of the project), one
narrow reference on prompt-writing itself, and this router. **Read this file
first.** It exists so an AI coding agent (or a person) searching these docs
picks the right one on the first try instead of grepping all of them.

```
docs/
  README.md          <- you are here: which doc to read
  Engine.md           <- the physics + FEMM/xfemm simulation backend
  GUI.md               <- the Dear ImGui desktop app
  Agent.md             <- the AI search agent (interview + optimize loop)
  agent-skills.md      <- criteria for writing/editing a system prompt (not project-specific facts)
  archive/             <- superseded/historical/generated docs, kept for the record
```

## Pick a doc by topic

| If your question is about... | Read |
|---|---|
| Self/mutual inductance (L, M, k), ESR, skin/proximity effect, ferrite/shield physics | **Engine.md** |
| `CoilSet`, `SimResult`, `engine.core.simulate()` and its pipeline | **Engine.md** |
| The xfemm/femmcli solver, the 90° mutual-inductance bug, the vendored patch | **Engine.md** |
| Running FEMM directly / Wine setup / "Open in FEMM" | **Engine.md** |
| How simulations run in parallel (thread pool, `JobRunner`, result caching) | **Engine.md** (§Concurrency) |
| The database tables `configs`/`runs`, `engine/repositories/` | **Engine.md** |
| The desktop app's screens/panels, Manual Runs mode, the coil diagram | **GUI.md** |
| Config list/edit/Save/Clone/Export/Delete behavior, name collisions | **GUI.md** |
| The AI Agent mode's chat UI, Settings screen, History tab | **GUI.md** (UI side) — see **Agent.md** for what runs behind it |
| Known GUI bugs/concerns (screenshot capture, delete confirmation, diagram fidelity) | **GUI.md** |
| The interview (chat → objective), `finalize_spec`, consent groups | **Agent.md** |
| The search loop, `propose_coilset`, scoring, margins, ranking | **Agent.md** |
| Batch proposals, probe/bracket/exploit, why batching helps | **Agent.md** (§Concurrency) |
| System prompts, `{{placeholders}}`, `AgentSettings`, what's user-editable | **Agent.md** |
| What makes a good system prompt / a checklist for editing one | **agent-skills.md** |
| LLM request handling: timeouts, retries, error kinds, the outcome report | **Agent.md** |
| Which model/server is actually in use (URL, context size) | **Agent.md** |
| Project goals, what's done vs. not yet built | **Agent.md** (§Goals) — also `Engine.md`/`GUI.md` note their own gaps |
| "Which file calls which file" across the whole repo | Any of the three has its own file table; for the full cross-package trace, still read `archive/architecture.md` |

Two things span more than one doc on purpose:

- **Concurrency** has an engine side (parallel FEMM solves, caching — infrastructure that
  lives in `frontend/src/frontend/services/job_runner.py` but is documented in
  **Engine.md** because it's about how a simulation executes) and an agent side (why and
  how the AI batches several proposals per round — **Agent.md**). If you're unsure which
  you need, read Engine.md's version first; it's the mechanism Agent.md builds on.
- **The database** is one shared SQLite file. `configs`/`runs` (engine-owned) are in
  Engine.md; `ai_runs`/`ai_run_messages` (agent-owned) are in Agent.md.

## Checking whether a doc is still fresh

Each of the five files above starts with a small frontmatter block:

```
---
owns: backend/agent/**
last-verified: 2026-09-22
---
```

`owns` is the path/glob this doc is responsible for describing — grep these five
frontmatter blocks (`grep -A2 '^owns:' docs/*.md`) to find which doc claims a path
*before* opening any of them. `last-verified` is the date someone last checked this doc's
statements against the code (or the diff) — not the date of the last edit necessarily; a
doc can be re-verified and found still accurate with no prose change at all. If you're
about to rely on a claim in one of these docs for code that's changed a lot more recently
than its `last-verified` date, read the code before trusting the doc.

## For "how did this get this way" — use git, not these docs

These five files describe the **current** state (four) or the current standard (one) only. They are edited surgically in
place (see the `/recap` skill), not appended to — a fact that's no longer true gets
corrected or removed, not struck through and kept. If you need the history behind a
decision or a fact, these two are almost always more reliable than prose:

```bash
git log --oneline -- docs/Agent.md        # when this doc's claims last changed, and why (commit subjects)
git log --oneline -- backend/agent/       # when the actual code behind it changed
git log -p --follow -- <path>             # the full diffs, oldest to newest
```

`docs/archive/` (below) is the exception — those files *are* point-in-time snapshots and
design-rationale writeups, kept deliberately un-updated. Reading one plus its own
timestamp is usually faster than reconstructing the same history from git log.

## Why only a handful of files

Before 2026-09-22 this folder had 16 files: some living reference docs, several dated
session logs, a couple of generated validation reports, and one large from-scratch
primer describing a codebase (`noelle/`, `genki/`) that no longer exists (removed
2026-09-18, commit `321b8d7`). Anyone — human or agent — searching this folder had to
know which of the 16 were current. They're consolidated now: **Engine.md, GUI.md and
Agent.md are the entire current, living reference for the project itself.**
Everything superseded, historical, or generated moved to `docs/archive/` unchanged, in
case a specific number or an old design rationale is needed — see `archive/README.md`
for what's there and why.

`agent-skills.md` (added 2026-09-23) is the one deliberate exception to "fold it into
one of the three": it isn't a fact about *this* project, it's a checklist for *how to
write* the prompts Engine/GUI/Agent.md describe the role of — a different kind of
content (a standard to hold new edits to) than the other three (what the system
currently does). Don't use this as precedent for adding a fifth, sixth, ... file for
every new sub-topic; it's an exception because its content genuinely doesn't fit
"describes current project state," not because one narrow doc is always fine to add.

## Keeping these current

These docs — and this router, if the topic map above stops matching reality — are
what the `/recap` skill (`.claude/skills/recap/SKILL.md`) updates at the end of a work
session, scoped to what actually changed. If you're an agent making a change by hand
outside that skill: edit the one doc that owns the topic, surgically (don't rewrite
the whole file for a small fact), and don't invent facts — back every claim by the code
or the diff, the same rule the recap skill follows. Either way, bump that doc's
`last-verified` date (§"Checking whether a doc is still fresh") to the day you checked it
— even on a pass where nothing needed changing, since that date is itself the useful
signal, not a record of edits.
