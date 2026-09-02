# =============================================================================
# run_result_repository.py
#
# Owns everything a run writes to disk: one flat JSON file per run under
# ./results/. Generic - callers (main.py, webapp's job_runner.py) build their
# own payload dict; this repository doesn't know or care what's inside it.
# =============================================================================
import datetime
import json
from pathlib import Path


class RunResultRepository:
    def __init__(self, run_id=None, root=Path('./results')):
        self.run_id = run_id or '{:%Y%m%dT%H%M%S}'.format(datetime.datetime.now())
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def result_path(self):
        return self.root / f'{self.run_id}.json'

    def save_result_json(self, payload):
        self.result_path.write_text(json.dumps(payload, indent=2, default=str))

    @staticmethod
    def load_result(run_id, root=Path('./results')):
        path = Path(root) / f'{run_id}.json'
        if not path.exists():
            return None
        return json.loads(path.read_text())

    @staticmethod
    def list_results(root=Path('./results')):
        root = Path(root)
        if not root.is_dir():
            return []
        results = []
        for path in root.glob('*.json'):
            try:
                results.append(json.loads(path.read_text()))
            except (json.JSONDecodeError, OSError):
                continue
        results.sort(key=lambda r: r.get('finished_at') or r.get('created_at') or '', reverse=True)
        return results
