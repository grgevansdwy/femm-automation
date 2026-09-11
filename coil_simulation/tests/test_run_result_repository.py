# =============================================================================
# test_run_result_repository.py
#
# Checks the repository that owns the `runs` table (repositories/db.py).
# Needs no FEMM:
#     python3 -m tests.test_run_result_repository  (run from coil_simulation/)
#
# Everything here runs against a temp directory holding its own
# femm_automation.db (root=<tempdir>), so it never touches the real one.
# =============================================================================
import re
import tempfile
from pathlib import Path

from repositories.run_result_repository import RunResultRepository


def make_payload(run_id, status='done', finished_at='2026-09-02T10:00:14+00:00'):
    return {
        "run_id": run_id, "config_id": "Gen3", "config_name": "Gen3",
        "distance": 5.0, "status": status,
        "created_at": "2026-09-02T10:00:00+00:00",
        "started_at": "2026-09-02T10:00:01+00:00",
        "finished_at": finished_at,
        "last_used_at": None,
        "error": None,
        "result": {"L_loop": 219.54e-9, "loop_esr": 0.19} if status == 'done' else None,
        "coilset_hash": None,
    }


def test_creates_database_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        r = RunResultRepository(run_id='abc123', root=root)
        r.save_result_json(make_payload('abc123'))
        assert (root / 'femm_automation.db').exists(), "femm_automation.db was not created"
    print("  ok  creates femm_automation.db under root on first write")


def test_generated_run_id_is_sortable_timestamp():
    r = RunResultRepository()
    assert re.fullmatch(r'\d{8}T\d{6}', r.run_id), r.run_id
    print(f"  ok  generated run_id is a sortable timestamp ({r.run_id})")


def test_save_result_json_round_trips():
    with tempfile.TemporaryDirectory() as tmp:
        r = RunResultRepository(run_id='abc123', root=tmp)
        payload = make_payload('abc123')
        r.save_result_json(payload)
        assert RunResultRepository.load_result('abc123', root=tmp) == payload
    print("  ok  save_result_json() / load_result() round-trip a run row")


def test_load_result_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert RunResultRepository.load_result('nope', root=tmp) is None
    print("  ok  load_result() returns None for a missing run_id")


def test_list_results_sorted_newest_first():
    with tempfile.TemporaryDirectory() as tmp:
        RunResultRepository(run_id='r1', root=tmp).save_result_json(
            make_payload('r1', finished_at='2026-09-02T09:00:00+00:00'))
        RunResultRepository(run_id='r2', root=tmp).save_result_json(
            make_payload('r2', finished_at='2026-09-02T12:00:00+00:00'))
        RunResultRepository(run_id='r3', root=tmp).save_result_json(
            make_payload('r3', status='queued', finished_at=None))

        results = RunResultRepository.list_results(root=tmp)
        assert [r['run_id'] for r in results] == ['r2', 'r3', 'r1'], results
    print("  ok  list_results() reads every run and sorts newest first")


def test_services_do_no_file_io():
    """The invariant this whole reorganisation buys.

    Nothing in services/ may touch the filesystem.
    """
    offenders = []
    services = Path(__file__).resolve().parent.parent / 'services'
    for f in sorted(services.glob('*.py')):
        for i, line in enumerate(f.read_text().split('\n'), 1):
            code = line.split('#')[0]
            if re.search(r'\bopen\(|savefig|mkdir|\.write\(', code):
                offenders.append(f"{f}:{i}: {line.strip()}")
    assert not offenders, "file I/O found in services/:\n  " + "\n  ".join(offenders)
    print("  ok  services/ performs no file I/O")


if __name__ == '__main__':
    print("checking RunResultRepository")
    test_creates_database_file()
    test_generated_run_id_is_sortable_timestamp()
    test_save_result_json_round_trips()
    test_load_result_missing_returns_none()
    test_list_results_sorted_newest_first()
    test_services_do_no_file_io()
    print("all run-result checks passed")
