# femm_automation

Automates FEMM (magnetics FEA) simulations of a 3-coil wireless power
transfer (WPT) link — Loop, Tank, and Receive coils at 6.78 MHz — to get
self/mutual inductance and ESR for a given coil design and separation
distance, and lets an AI agent search for better designs. The solve backend
is [xfemm](backend/engine/vendor/README.md), a vendored open-source,
Linux-native reimplementation of FEMM (no Wine, no Windows install needed).

## Running it (Linux)

The solver binary is Linux-only. With [`uv`](https://docs.astral.sh/uv/)
installed:

```bash
uv sync
uv run --package frontend python frontend/src/frontend/main.py
```

That opens the desktop app (Dear ImGui — it needs a real display, there is no
headless mode). It has two modes: **Manual Runs** (edit a coil config, solve it
at a distance) and **AI Agent** (describe an objective in chat, then watch the
agent search). See [`frontend/README.md`](frontend/README.md).

Configs and results are kept in one local SQLite database,
`backend/data/femm_automation.db` (git-ignored — a fresh checkout starts
empty; **Backup All / Restore All** in the app's toolbar moves data between
machines).

### The AI agent without the GUI

```bash
cd backend
uv run python -m agent
```

A no-chat run against the defaults in `backend/agent/config.py`. It talks
to an OpenAI-compatible LLM server (`LLM_API_BASE` / `AGENT_MODEL`, see that file).

### Tests

```bash
uv run pytest backend frontend/tests
```

`backend/` (about 8 s) needs neither an LLM nor FEMM. `frontend/tests/` (about
6–10 min) makes real FEMM solves and calls the real LLM server.

## Layout

- `backend/engine/` — the FEMM simulation engine (`core.py`), its xfemm
  backend and the local database; the core that everything else calls into.
- `backend/agent/` — the AI agent, built on the engine (never the other
  way round): `input_chat/` turns a chat into a structured objective, `femm_agent/`
  searches for designs that meet it.
- `frontend/` — the native desktop UI; see its own README.
- `docs/` — deeper background, one file per component: [`Engine.md`](docs/Engine.md)
  (physics + the FEMM/xfemm backend), [`GUI.md`](docs/GUI.md) (the desktop app),
  [`Agent.md`](docs/Agent.md) (the AI search agent). Start with
  [`docs/README.md`](docs/README.md) if you're not sure which one you need.
