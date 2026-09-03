# =============================================================================
# test_run_result_repository.py
#
# Checks the repository that owns ./results/ID<run_id>/. Needs no FEMM:
#     python3 -m tests.test_run_result_repository  (run from noelle/)
#
# Everything here runs against a temp directory, so it never touches the real
# results/ archive.
# =============================================================================
import io
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use('Agg')          # no display needed
import matplotlib.pyplot as plt

from repositories.run_result_repository import RunResultRepository
from models.config_result import ConfigResult, RunResults, SweepPoint


def test_creates_run_directory():
    with tempfile.TemporaryDirectory() as tmp:
        r = RunResultRepository(run_id='281200', root=tmp)
        assert r.dir == Path(tmp) / 'ID281200', r.dir
        assert r.dir.is_dir(), "run directory was not created"
    print("  ok  creates ./<root>/ID<run_id>/")


def test_log_appends_and_echoes():
    """One file per run, appended to - and the text still reaches stdout.

    The old print_and_log did both; callers rely on the stdout half.
    """
    with tempfile.TemporaryDirectory() as tmp:
        r = RunResultRepository(run_id='281200', root=tmp)
        captured, sys.stdout = io.StringIO(), io.StringIO()
        try:
            r.log('first line')
            r.log('second line')
            captured = sys.stdout
        finally:
            sys.stdout = sys.__stdout__
        assert r.log_path.name == 'log_281200.txt', r.log_path
        body = r.log_path.read_text()
        assert body == 'first line\nsecond line\n', repr(body)
        assert captured.getvalue() == 'first line\nsecond line\n', repr(captured.getvalue())
    print("  ok  log() appends to one file and echoes to stdout")


def test_save_figure_writes_svg():
    with tempfile.TemporaryDirectory() as tmp:
        r = RunResultRepository(run_id='281200', root=tmp)
        fig, ax = plt.subplots()
        ax.plot([0, 1], [0, 1])
        r.save_figure(fig)
        plt.close(fig)
        assert r.figure_path.name == 'ID281200.svg', r.figure_path
        assert r.figure_path.exists(), "SVG was not written"
        assert r.figure_path.read_text(errors='ignore').lstrip().startswith('<?xml')
    print("  ok  save_figure() writes ID<run_id>.svg")


def test_generated_run_id_matches_the_old_format():
    """A generated id must still be the DDHHMM the archive uses.

    KNOWN BUG, preserved deliberately: no month, no year - two runs a month
    apart at the same minute collide. This repository is now the one place
    that decides a run id, so it is where that gets fixed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        r = RunResultRepository(root=tmp)
        assert len(r.run_id) == 6 and r.run_id.isdigit(), r.run_id
    print(f"  ok  generated run_id is DDHHMM ({r.run_id})")


@dataclass
class FakeCoilset:
    """Stand-in for a CoilSet - log_run_summary only needs config_name and str()."""
    config_name: str = "TestCoil"


def make_sweep_points():
    return [
        SweepPoint(distance=5, L_loop=219.54e-9, M13=2.9942e-07, M12=6.8675e-07,
                   L_tank=5.5219e-6, M23=1.9372e-06, M21=6.8675e-07,
                   L_rc=1.8179e-6, M31=2.9942e-07, M32=1.9372e-06,
                   k12=0.6237, k13=0.4739, k23=0.6114, tank_f=6.75e6,
                   iloop=9.2, itank=11.4, irc=12.7, vpa=200.0, ipa=6.1,
                   pbatt=900.0, vrect=85.0, abs_Iloop=2.77, abs_Itank=0.97),
        SweepPoint(distance=10, L_loop=219.0e-9, M13=2.9e-07, M12=6.8e-07,
                   L_tank=5.5e-6, M23=1.9e-06, M21=6.8e-07,
                   L_rc=1.8e-6, M31=2.9e-07, M32=1.9e-06,
                   k12=0.60, k13=0.46, k23=0.60, tank_f=6.74e6,
                   iloop=9.0, itank=11.0, irc=12.0, vpa=200.0, ipa=6.0,
                   pbatt=880.0, vrect=84.0, abs_Iloop=2.7, abs_Itank=0.95),
    ]


def test_log_run_summary_writes_expected_lines():
    """The extracted method must reproduce the original inline log block.

    compare_coilsets.py used to write these ~40 lines directly; log_run_summary
    replaced that block verbatim (see docs/text-logging plan). Check the labeled
    lines and the six LTspice .param tables all land in the log file.
    """
    with tempfile.TemporaryDirectory() as tmp:
        r = RunResultRepository(run_id='281200', root=tmp)
        sweep = make_sweep_points()
        r.log_run_summary(FakeCoilset(), 1, 1, sweep,
                           C1=100e-12, C2=200e-12, C3=300e-12,
                           Z1=1+2j, Z2=3+4j, Z3=5+6j)
        body = r.log_path.read_text()

        assert "Run 1/1; TestCoil" in body
        assert "RC Test Distances (mm): [5, 10]" in body
        assert "k13s: [0.4739, 0.46]" in body
        assert "M13: [2.9942e-07, 2.9e-07]" in body
        assert ".param M12=table(D, 5, 6.8675e-07, 10, 6.8e-07)" in body
        assert ".param k23=table(D, 5, 0.6114, 10, 0.6)" in body
        assert "C1: 1e-10" in body
        assert "Z1: (1+2j)" in body
    print("  ok  log_run_summary() reproduces the original log block")


def test_save_summary_json_round_trips_config_result():
    """The structured-output fix for REFACTOR_NOTES.md's write-only results.

    Every field on ConfigResult - including the nested per-distance sweep -
    must survive a save_summary_json() / json.load() round trip unchanged.
    """
    cfg = ConfigResult(
        label='Gen3 run-1', color=(1.0, 0.0, 0.0, 1.0),
        k12=0.6237, k13=0.4739, k23=0.6114,
        M12=6.8675e-07, M13=2.9942e-07, M23=1.9372e-06,
        L_loop_solo=219.54e-9, L_tank_solo=5.5219e-6, L_rc_solo=1.8179e-6,
        L_loop_assembled=220.67e-9, L_tank_assembled=5.5926e-6, L_rc_assembled=1.8351e-6,
        L_tank_at_closest=5.5219e-6, L_rc_at_closest=1.8179e-6,
        abs_Iloop=2.77, abs_Itank=0.97,
        tc_ferrite_flux=0.0, rc_ferrite_flux=0.031,
        loop_esr=0.19, tank_esr=1.836, rc_esr=0.512,
        tc_ferrite_loss=0.0, rc_ferrite_loss=3.2,
        tc_shield_loss=0.0, rc_shield_loss=1.1,
        total_loss=41.2, efficiency=0.9587, mag_normal=2.9357,
        sweep=make_sweep_points(),
    )
    run = RunResults(timestamp='281200')
    run.configs.append(cfg)

    with tempfile.TemporaryDirectory() as tmp:
        r = RunResultRepository(run_id='281200', root=tmp)
        r.save_summary_json(run)
        assert r.summary_json_path.name == 'summary_281200.json', r.summary_json_path
        loaded = json.loads(r.summary_json_path.read_text())

        assert len(loaded) == 1
        row = loaded[0]
        assert row['label'] == 'Gen3 run-1'
        assert row['total_loss'] == 41.2
        assert len(row['sweep']) == 2
        assert row['sweep'][0]['distance'] == 5
        assert row['sweep'][1]['M13'] == 2.9e-07
    print("  ok  save_summary_json() round-trips ConfigResult, sweep included")


def test_services_do_no_file_io():
    """The invariant this whole reorganisation buys.

    print_and_log used to open a file from inside femm_model. Nothing in
    services/ may touch the filesystem now.
    """
    import re
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
    test_creates_run_directory()
    test_log_appends_and_echoes()
    test_save_figure_writes_svg()
    test_generated_run_id_matches_the_old_format()
    test_log_run_summary_writes_expected_lines()
    test_save_summary_json_round_trips_config_result()
    test_services_do_no_file_io()
    print("all run-result checks passed")
