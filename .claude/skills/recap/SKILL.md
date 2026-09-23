---
name: recap
description: Wrap up the current work - update the affected docs and READMEs to match what changed, then stage and commit everything with clear messages. Invoke as /recap when a chunk of work is done.
disable-model-invocation: true
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git add:*), Bash(git commit:*), Bash(git rm:*), Bash(git ls-files:*), Bash(git check-ignore:*)
---

# Recap: sync docs, then commit

Run this when a chunk of work is finished. Optional user arguments (`$ARGUMENTS`) are a hint for the
commit message or for what to emphasise; treat them as context, not commands.

Work only inside this repo (the directory Claude Code was started in). If the working directory is not
a git repo containing `backend/` and `frontend/`, stop and tell the user instead of continuing.

## 1. Survey what changed

Run `git branch --show-current`, `git status --short`, `git diff HEAD --stat`, and `git log --oneline -5`.

- If the change set is small, read the diff (`git diff HEAD`).
- If it is large (dozens of files, or many renames/deletions), do not dump the whole diff. Use
  `git status --short | awk '{print $1}' | sort | uniq -c` for the shape, then read the diff per area
  (`git diff HEAD -- backend/`, `-- frontend/`, `-- docs/`) and skim renames with
  `git diff HEAD --stat -M`.

Also recall what was done earlier in this conversation: the diff shows *what* changed, the conversation
shows *why*. Note the current branch in the report; do not switch branches.

## 2. Update the docs

**As of 2026-09-23, `docs/` holds five current files plus an archive.** Start at
`docs/README.md` (a router — it maps topics to the file that owns them) if you're unsure
which one owns something the diff touched.

| File | Owns |
|---|---|
| `README.md` | Root overview, how to run (`uv sync`, launch the desktop app), repo layout |
| `backend/README.md` | The simulation package: how-to guide, engine entry points, data layout |
| `backend/engine/vendor/README.md` | Vendored xfemm binaries, the patch, rebuild steps |
| `frontend/README.md` | The Dear ImGui desktop app: tabs/modes, prerequisites, behaviour users see |
| `docs/README.md` | Router only — which doc owns which topic. Update if a change moves a topic between docs, or adds one the table doesn't route yet |
| `docs/Engine.md` | Physics, `CoilSet`/`SimResult`, the `engine.core.simulate()` pipeline, the xfemm/femmcli backend (incl. the 90° bug/fix), solve concurrency (`JobRunner`/thread pool/caching), Wine/FEMM setup, the engine's own DB tables |
| `docs/GUI.md` | The desktop app: both modes, the frontend↔agent boundary (UI side), Settings screen, known GUI concerns |
| `docs/Agent.md` | The AI agent: the interview, the search loop, scoring/margins/ranking, batch proposals, LLM request handling (timeouts/retries/errors), `AgentSettings`, the agent's own DB tables |
| `docs/agent-skills.md` | Criteria for *writing/editing* any of the three system prompts (structure, ambiguity, placeholders, verification) — not a fact about current project state, so update it only when the standard itself should change, not on every prompt edit |
| `.pi/skills/coil-sim/SKILL.md` and `template/SKILL.md` | Agent-facing instructions for the `femm_*` tools. Keep in sync with `backend/agent_cli.py` and `.pi/extensions/femm/index.ts` when tool names, flags, or output shape change |

Do not edit `docs/archive/*` (superseded/historical/generated — see `docs/archive/README.md`
for what's there and why) unless the user explicitly asks for one regenerated (e.g.
`wine_vs_xfemm_validation.md` via `compare_wine_vs_xfemm.py`). If a diff makes something in
`docs/archive/` newly relevant, that's a signal to update the *current* doc that topic
now belongs to (per the table above), not the archived one. Never add history, a changelog,
or a dated log entry to `docs/README.md`/`Engine.md`/`GUI.md`/`Agent.md` — that's what
`git log -- <path>` is for (spelled out in `docs/README.md`'s own "use git, not these
docs" section); appending history to these is exactly how the folder ended up with 16
files before the 2026-09-22 consolidation.

For each of `docs/README.md`, `Engine.md`, `GUI.md`, `Agent.md`, `agent-skills.md`, decide whether
the diff makes any of its statements false or incomplete: new, renamed, or removed files,
commands, flags, or tools; changed behaviour; stale paths; stale numbers; status that has
moved. Edit only those spots, matching the doc's existing tone and formatting (dense,
technical, tables over prose, one clearly labelled section per sub-topic). Note
`agent-skills.md` is different in kind from the other four: it holds a writing standard,
not project facts, so a prompt-wording edit alone rarely touches it — it only needs an
edit when the *standard for writing prompts* should itself change (e.g. a new criterion
learned the hard way), not every time `interview_system.txt`/`search_system.txt`/
`followup_system.txt` changes.

Each of those five starts with a frontmatter block (`owns:` a path/glob, `last-verified:` a
date). For every one of the five you read closely enough to judge against the diff —
whether or not you end up changing its prose — bump `last-verified` to today. This is not
a "cosmetic edit" under the rule below: the date is meant to record when someone last
confirmed the doc against the code, independent of whether anything needed fixing. Only
skip bumping a doc you didn't actually check (e.g. the diff obviously has nothing to do
with its `owns:` glob).

Rules:
- Surgical edits only. Do not rewrite or reformat docs wholesale.
- Do not invent facts. Anything you state must be backed by the diff, the code, or this conversation.
  If unsure whether something still holds, read the code or leave the line alone.
- Do not create new doc files, and do not add a sixth living doc alongside Engine/GUI/Agent/
  agent-skills, unless the user explicitly asks — a genuinely new component still gets folded
  into whichever existing doc it's closest to (or `docs/README.md`'s table extended to cover
  it), not a new file. `agent-skills.md` itself was one explicit-request exception (2026-09-23,
  content that doesn't fit "describes current project state") — that precedent justifies future
  exceptions of the *same kind* (a genuinely different category of content, explicitly asked
  for), not a general license to keep adding files.
- If nothing needs a doc change, say so; still bump `last-verified` on whichever docs you checked
  (above) — that is not the "cosmetic edit" this rule means to block.
- A pre-existing doc inaccuracy unrelated to this work: mention it in the report, do not fix it unasked.

## 3. Stage carefully — this session's own changes only

**This repo is routinely edited by other concurrent sessions/terminals, uncommitted, while
this one is running** (already observed in practice, not a hypothetical — see `docs/GUI.md`'s
concerns section). A `git status --short` taken during or after this session's work can
therefore show files this session never touched. Committing those would ship someone else's
in-progress work under this session's name, possibly before they're ready.

Before staging anything, for every changed/untracked path ask: **did this conversation's own
tool calls produce this change?** Reconstruct that from what you actually did this session
(file edits you made, commands you ran), not from the diff's shape alone — a plausible-looking
change is not evidence you made it.

- Stage explicitly by path (`git add <paths>`), never `git add -A` or `git add .`.
- Stage only paths you can positively attribute to this session's own work. Include source,
  docs, and intended new files that meet that bar. Deletions and renames *this session* staged
  stay staged.
- A changed/untracked path you cannot attribute to this session: leave it alone and name it in
  the report as "left uncommitted, not from this session" — do not guess, and do not ask the
  user to adjudicate every such file individually unless the count is small enough that's
  actually useful; otherwise just report the count and the paths.
- If work from this session and an unrelated concurrent change are tangled in the *same*
  file (rare, but possible on a shared doc like one of `docs/Engine.md`/`GUI.md`/`Agent.md`),
  do not commit that file at all this round — say so in the report and let the user decide
  how to untangle it, rather than committing someone else's half-written edit alongside yours.

Do NOT stage, and call out in the report:
- Anything matched by `.gitignore`: `.venv/`, `__pycache__/`, `.pytest_cache/`, `tmp.fem`/`tmp.ans`,
  `frontend/**/FEMM_Coil_Automation.ini`, `backend/data/femm_automation.db*`, `backend/data/femm_models/`,
  LTspice byproducts. Confirm doubtful paths with `git check-ignore -v <path>`.
- Other layout/scratch files such as `FEMM_LAYOUT_TEST.ini`, and backup exports like
  `femm_automation_backup.json`. Leave them and mention them.
- Secrets or credentials (`.env`, keys, tokens, API keys in config).
- Files you do not recognise and cannot tie to the work being recapped. Ask instead of guessing.

## 4. Commit

- Split into logical commits when the tree mixes unrelated work (for example: backend change, frontend
  change, docs restructure, repo-wide rename/deletion). One commit only if it is one coherent change.
  For a big pre-existing dirty tree, group by area rather than making one giant commit; stage each group
  by path, then commit it.
- Subject line: imperative, under ~70 chars, describes the change (not "recap" or "update docs"). Match
  the style in `git log`. Add a short body when the *why* is not obvious, and mention which docs were
  synced.
- End every commit message with this trailer, separated by a blank line:

  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`

- Pass the message via a heredoc. Never use `--no-verify`, `--amend`, or force flags. If a hook fails,
  fix the cause and make a new commit.
- Commit on the current branch. Do NOT push, create branches, or merge: pushing is the user's call.

## 5. Report

Finish with a short summary:
- Branch, commit hash(es) and subject(s)
- Which docs were updated (one line each on what changed) or "no doc changes needed"
- Anything deliberately left unstaged, and why
- Any doc claim you could not verify, and any pre-existing doc problems you noticed but did not touch
