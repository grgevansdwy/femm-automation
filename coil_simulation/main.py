# =============================================================================
# main.py                                                         Noel Hosking
#
# Thin CLI entry point. The simulation itself lives in engine.py so it can be
# called from other code (e.g. the web UI) as well as from the shell.
# =============================================================================
import dataclasses
from pathlib import Path

from repositories import coilset_repository as cc
from repositories.run_result_repository import RunResultRepository
from engine import simulate, DEFAULT_RC_TEST_DISTANCE

if __name__ == '__main__':
    coilset = cc.get_coilset('TR302_and_225_000056')
    result = simulate(coilset, DEFAULT_RC_TEST_DISTANCE)

    repo = RunResultRepository(root=Path('./results'))
    repo.save_result_json({
        "run_id": repo.run_id,
        "config_id": "TR302_and_225_000056",
        "config_name": coilset.config_name,
        "distance": DEFAULT_RC_TEST_DISTANCE,
        "status": "done",
        "created_at": None,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": dataclasses.asdict(result),
    })
    print(f"Results written to {repo.result_path}")
