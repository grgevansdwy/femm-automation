# webapp

FastAPI front end for coil_simulation: browse and edit the config library
(built-in reference designs plus your own), and run a single config at a
given RC separation distance to get its self-inductance, mutual inductance,
and ESR — without touching `main.py` directly.

## Prerequisites

- Python >=3.13 and [`uv`](https://docs.astral.sh/uv/)
- For a run to actually solve (not just fail gracefully): FEMM installed
  under Wine, and `pyfemm` able to reach it. Without FEMM, the app still
  boots and every page works — submitted runs will just end up `failed`.

This package is a member of the repo's uv workspace (see the root
`pyproject.toml`), alongside `coil_simulation`, so dependencies are resolved together
and share one `uv.lock`. Run all commands from the repo root.

**Superseded by `gui/`** (a native Dear ImGui app on the same
`coil_simulation` engine) — kept here for reference/rollback, not the
primary UI going forward.

## Run it

```bash
uv sync
uv run --package webapp uvicorn webapp.main:app --app-dir legacy/webapp/src --reload --reload-dir legacy/webapp/src --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000/.

- `/` — run history, read from disk so it survives a restart
- `/configs` — the unified config library (from `coil_simulation/repositories/coilset_repository.py`): built-in reference designs and your own, all editable; built-ins can't be deleted
- `/runs/new` — pick one config and one RC separation distance, then start a run

Configs are written to `coil_simulation/data/configs/*.json`. Run output
(self/mutual inductance + ESR, JSON only) lands as a flat
`coil_simulation/data/results/<run_id>.json`, same as running `main.py` directly.

## Config

`SIM_DIR` env var overrides where the app looks for `coil_simulation/` (default:
the `coil_simulation/` directory at the repo root) — see
`legacy/webapp/src/webapp/_sim_path.py`.

## Layout

```
webapp/
  src/webapp/
    main.py              FastAPI app + startup
    job_runner.py         single-worker queue around coil_simulation's engine.simulate() (FEMM-via-Wine isn't safe for concurrent access); persists each job to data/results/<run_id>.json on completion
    coilset_forms.py      form <-> CoilSet field mapping/validation
    routers/               home, configs, runs
  templates/              Jinja2 templates
  static/css/             stylesheet
```

Config storage (`coilset_repository.py`) lives in `coil_simulation/`, not
here — see that package's own layout.
