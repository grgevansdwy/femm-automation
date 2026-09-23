"""The FEMM simulation engine: everything that turns a CoilSet into solved
inductance / resistance numbers. It knows nothing about any UI or about the
AI agent - agent/ and frontend/ both build on top of it.

- core.py - the physics entry point: simulate(coilset, distance) builds the
  FEMM script, runs it and returns a SimResult.
- constants.py - the fixed test-rig values a run uses (frequency, currents...).
- models/ - CoilSet (one design) and SimResult (one solve's output).
- services/ - talking to FEMM: geometry/circuit helpers (femm_service), the
  xfemm solver backend (xfemm_backend), opening a design in FEMM's own
  window (femm_launcher).
- repositories/ - the local SQLite store for coil configs and manual-run
  results, plus the shared connection helper (db.py).
- vendor/ - the bundled xfemm binaries.
- main.py - a tiny CLI example: `python -m engine.main` from backend/.

Import it like `from engine.core import simulate`,
`from engine.models.coilset import CoilSet` - backend/ itself must be
on sys.path (frontend/src/frontend/_sim_path.py and agent/__init__.py both do that).
"""
