# FEMM devtools

A read-only, visual guide to how this codebase works, wired to the **real backend**. Built for understanding, not for operating the app.

```
cd devtools
npm install      # first time only
npm run dev      # http://127.0.0.1:4400
```

Needs the repo's `.venv` (from `uv sync`) and `backend/data/femm_automation.db`. It only listens on localhost.

## Pages

| Page | What it shows | Live from the backend |
|---|---|---|
| The big picture | Layers, the three ways the app is used, one AI search end to end | database counts, real time split (model vs. physics) |
| What is being measured | L, M, k, ESR and the three coils | a real design's geometry, a real solve, a 4-distance parallel sweep |
| The engine | `simulate()` step by step | validity check, currents, generated Lua script, real solver run with timings |
| The AI agent | interview, search loop, memory, scoring, failure handling | the real prompts rendered for any design, the real tool schemas |
| Running many at once | threads, pool, cache | a scheduler you can play with, seeded from your real timings |
| AI runs explorer | replay of every saved search | rounds, candidates, scores, time split, interview transcript |
| The database | tables and how they relate | schema and newest row of each table |
| Code map & viewer | every file with a one-line explanation | reads the source, links to exact lines |

## How it is connected

```
browser ──> Next.js (app/, route handlers in app/api/*)
                └─ lib/bridge.ts ──execFile──> bridge/bridge.py   (run with cwd = backend/)
                                                  ├─ imports the real engine + agent code
                                                  └─ opens the SQLite database read-only (mode=ro)
```

- `bridge/bridge.py` prints one JSON document per command (`overview`, `configs`, `config`, `geometry`, `pipeline`, `simulate`, `prompts`, `runs`, `run`, `tables`). It never writes to your database.
- `simulate` and `prompts` (only when a baseline has no saved solve) run the real solver in a temporary folder, exactly like the app. Nothing they compute is saved.
- The code viewer serves plain-text source files inside the repo only (no `.git`, `.venv`, databases or binaries).
- Set `REPO_ROOT` if you move this folder out of the repo.

## Keeping it accurate

Explanations are hand-written in `app/**/page.tsx` and `lib/codemap.ts`. Numbers, geometry, prompts, run history and every "open in code" line number come from the live code, so they follow the code. The prose does not: if the design changes, update the matching page.
