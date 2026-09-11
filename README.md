# femm_automation

Automates FEMM (magnetics FEA) simulations of a 3-coil wireless power
transfer (WPT) link — Loop, Tank, and Receive coils at 6.78 MHz — to get
self/mutual inductance and ESR for a given coil design and separation
distance. The solve backend is [xfemm](coil_simulation/vendor/README.md), a
vendored open-source, Linux-native reimplementation of FEMM (no Wine, no
Windows install needed).

## Quick run (Docker)

The fastest way to get results without setting up Python/`uv` locally, and
the only way that works if you're not on Linux (the solver binary is
Linux-only, but Docker runs it in a Linux container regardless of your
host OS):

```bash
docker build -t femm-coil-sim .
docker run -p 8000:8000 -v $(pwd)/coil_simulation/data:/app/coil_simulation/data femm-coil-sim
```

Then open **http://localhost:8000/** — browse existing coil configs,
start a new run, and see results. The volume mount keeps configs/results
on your host so they survive `docker run` restarts — they live in
`coil_simulation/data/femm_automation.db` (a SQLite database), which the
mount already covers since it binds the whole `data/` directory.

The same routes work from the command line too (no browser needed), e.g.:

```bash
curl -X POST http://localhost:8000/runs/new -d "config_id=TR302_and_225_000056" -d "distance=5.0"
```

## Running natively (Linux only)

If you're on Linux and have [`uv`](https://docs.astral.sh/uv/) installed,
you can skip Docker:

```bash
uv sync
uv run --package webapp uvicorn webapp.main:app --app-dir legacy/webapp/src --host 127.0.0.1 --port 8000
```

There's also a native desktop GUI (Dear ImGui, no browser) — see
[`gui/README.md`](gui/README.md) — but it requires a real display, so it's
Linux-desktop-only and not something Docker can help with.

## Layout

- `coil_simulation/` — the simulation engine (`engine.py`) and its xfemm
  backend; the core that every UI below calls into.
- `legacy/webapp/` — the FastAPI browser UI (used above for the quick run).
- `gui/` — the native desktop UI; see its own README.
- `legacy/` (`noelle/`, `genki/`, `wine_backend/`) — earlier iterations,
  kept for reference/rollback, not the primary path.
- `docs/` — deeper background (`FEMM_AND_WPT_101.md`, `FEMM_ON_LINUX.md`,
  `xfemm.md`, `wine_vs_xfemm_validation.md`).
