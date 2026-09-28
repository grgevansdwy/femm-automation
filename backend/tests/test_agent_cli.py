"""agent_cli: input validation, sweep behaviour and the baseline cache. No FEMM solves (fake solver)."""
import dataclasses

import pytest

import agent_cli
from engine.models.result import SimResult
from engine.repositories import coilset_repository as repo


def _base():
    return repo.get_coilset('base_coil')


def ov(*pairs):
    return agent_cli._apply_overrides(_base(), list(pairs))


# ---- override validation ----------------------------------------------------
def test_fractional_count_is_rejected_not_truncated():
    with pytest.raises(agent_cli.InputError, match='whole number'):
        ov('num_tank_turns=5.7')


def test_whole_count_is_accepted_as_int():
    coilset = ov('num_tank_turns=6.0')
    assert coilset.num_tank_turns == 6 and isinstance(coilset.num_tank_turns, int)


@pytest.mark.parametrize('pair', ['num_tank_turns=0', 'num_rc_layers=-1', 'num_parallel_tank_traces=0'])
def test_counts_need_at_least_one(pair):
    with pytest.raises(agent_cli.InputError, match='at least 1'):
        ov(pair)


@pytest.mark.parametrize('pair', ['tank_ferrite_present=2', 'backplane=-1', 'rc_shield=5'])
def test_flags_are_zero_or_one(pair):
    with pytest.raises(agent_cli.InputError, match='0 or 1'):
        ov(pair)


@pytest.mark.parametrize('raw', ['nan', 'inf', '-inf', 'abc', ''])
def test_non_numeric_or_non_finite_values_are_rejected(raw):
    with pytest.raises(agent_cli.InputError):
        ov(f'tank_trace_thickness={raw}')


@pytest.mark.parametrize('pair', ['tank_trace_thickness=0', 'tank_trace_thickness=-1', 'rc_radius=-3', 'tank_center_r=0'])
def test_core_sizes_must_be_positive(pair):
    with pytest.raises(agent_cli.InputError, match='greater than 0'):
        ov(pair)


def test_optional_sizes_may_be_zero_but_never_negative():
    assert ov('tank_ferrite_thickness=0').tank_ferrite_thickness == 0
    with pytest.raises(agent_cli.InputError, match='negative'):
        ov('tank_ferrite_thickness=-1')


def test_float_field_stored_as_int_keeps_its_fraction():
    # base_coil stores tank_ferrite_y_offset as the int 0 although the field is a float.
    assert isinstance(_base().tank_ferrite_y_offset, int)
    assert ov('tank_ferrite_y_offset=1.5').tank_ferrite_y_offset == 1.5


def test_unknown_key_and_missing_equals_are_rejected():
    for bad in ('bogus=1', 'tank_trace_thickness'):
        with pytest.raises(agent_cli.InputError, match='expected key=value'):
            ov(bad)


def test_string_field_passes_through():
    assert ov('config_name=hello').config_name == 'hello'


@pytest.mark.parametrize('distance', [0, -5, 200.01, 1000, float('nan'), float('inf')])
def test_distance_out_of_range_is_rejected(distance):
    with pytest.raises(agent_cli.InputError, match='--distance'):
        agent_cli._check_distance(distance)


@pytest.mark.parametrize('distance', [0.1, 5, 200])
def test_distance_in_range_is_accepted(distance):
    agent_cli._check_distance(distance)


# ---- sweep and cache, with a fake solver ------------------------------------
def _fake_result(coilset, distance):
    return SimResult(config_name='x', distance=distance, L_loop=1.0, L_tank=1.0, L_rc=1.0, M12=1.0, M13=1.0, M23=1.0,
                     loop_esr=1.0, tank_esr=coilset.tank_trace_thickness, rc_esr=1.0, k12=.1, k13=.1, k23=.1)


@pytest.fixture
def fake_solver(monkeypatch):
    monkeypatch.setattr(agent_cli, 'simulate', _fake_result)
    monkeypatch.setattr(agent_cli, '_baseline', lambda cid, d: dataclasses.asdict(_fake_result(repo.get_coilset(cid), d)))


def test_sweep_dedupes_values_and_reports_pct_vs_base(fake_solver):
    base = _base().tank_trace_thickness
    out = agent_cli._sweep('base_coil', 'tank_trace_thickness', f'{base},{base},{base * 2}', 5.0, None)
    assert [p['value'] for p in out['points']] == [base, base * 2]
    assert out['points'][0]['pct_vs_base']['tank_esr'] == 0
    assert out['points'][1]['pct_vs_base']['tank_esr'] == pytest.approx(100, abs=1e-3)


def test_sweep_needs_two_distinct_numeric_values(fake_solver):
    for bad in ('0.1', '0.1,0.1', '0.1,abc', ','.join(str(i / 100 + 0.01) for i in range(9))):
        with pytest.raises(agent_cli.InputError, match='--values'):
            agent_cli._sweep('base_coil', 'tank_trace_thickness', bad, 5.0, None)


def test_sweep_rejects_string_and_unknown_params(fake_solver):
    with pytest.raises(agent_cli.InputError, match='Cannot sweep'):
        agent_cli._sweep('base_coil', 'config_name', '1,2', 5.0, None)
    with pytest.raises(agent_cli.InputError, match='Cannot sweep'):
        agent_cli._sweep('base_coil', 'bogus', '1,2', 5.0, None)


def test_sweep_bad_fixed_override_fails_the_whole_call(fake_solver):
    with pytest.raises(agent_cli.InputError):
        agent_cli._sweep('base_coil', 'rc_radius', '57,58', 5.0, ['num_tank_turns=5.5'])


def test_sweep_point_failure_does_not_abort_the_rest(monkeypatch, fake_solver):
    def flaky(coilset, distance):
        if coilset.tank_trace_thickness == 0.2:
            raise RuntimeError('trace space of 0 is less than trace width')
        return _fake_result(coilset, distance)
    monkeypatch.setattr(agent_cli, 'simulate', flaky)
    points = agent_cli._sweep('base_coil', 'tank_trace_thickness', '0.1,0.2,0.3', 5.0, None)['points']
    assert 'error' not in points[0] and 'trace space' in points[1]['error'] and 'error' not in points[2]


def test_sweep_stops_after_a_solver_timeout(monkeypatch, fake_solver):
    def hangs(coilset, distance):
        if coilset.tank_trace_thickness >= 0.2:
            raise RuntimeError("Command ['femmcli'] timed out after 120 seconds")
        return _fake_result(coilset, distance)
    monkeypatch.setattr(agent_cli, 'simulate', hangs)
    points = agent_cli._sweep('base_coil', 'tank_trace_thickness', '0.1,0.2,0.3,0.4', 5.0, None)['points']
    assert 'error' not in points[0] and 'timed out' in points[1]['error']
    assert all(p['error'].startswith('skipped') for p in points[2:])


def test_baseline_cache_hits_and_is_invalidated_by_an_engine_change(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_cli.tempfile, 'gettempdir', lambda: str(tmp_path))
    solves = []
    monkeypatch.setattr(agent_cli, 'simulate', lambda c, d: solves.append(1) or _fake_result(c, d))
    monkeypatch.setattr(agent_cli, '_engine_fingerprint', lambda: 'engine-v1')
    agent_cli._baseline('base_coil', 5.0); agent_cli._baseline('base_coil', 5.0)
    assert len(solves) == 1                                   # second call served from the cache
    agent_cli._baseline('base_coil', 10.0)
    assert len(solves) == 2                                   # a different distance is a different key
    monkeypatch.setattr(agent_cli, '_engine_fingerprint', lambda: 'engine-v2')
    agent_cli._baseline('base_coil', 5.0)
    assert len(solves) == 3                                   # the engine changed: never serve the stale baseline


# ---- error reporting ----------------------------------------------------------
def test_solver_error_hints():
    assert 'rejected the geometry' in agent_cli._solver_error(ValueError("could not convert string to float: 'L_loop=0'"))
    assert 'rejected the geometry' in agent_cli._solver_error(Exception('FEASolver::LoadProblemFile Material properties have not been defined'))
    assert 'extreme' in agent_cli._solver_error(Exception("Command ['femmcli'] timed out after 120 seconds"))


def test_main_reports_input_errors_as_json_with_exit_code_1(capsys):
    assert agent_cli.main(['simulate', '--config', 'nope']) == 1
    assert '"error"' in capsys.readouterr().out
    assert agent_cli.main(['simulate', '--config', 'base_coil', '--set', 'num_tank_turns=5.5']) == 1
    assert 'whole number' in capsys.readouterr().out
    assert agent_cli.main(['simulate', '--config', 'base_coil', '--distance', '0']) == 1
    assert '--distance' in capsys.readouterr().out


def test_corrupt_baseline_cache_file_is_recomputed(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_cli.tempfile, 'gettempdir', lambda: str(tmp_path))
    monkeypatch.setattr(agent_cli, '_engine_fingerprint', lambda: 'engine-v1')
    solves = []
    monkeypatch.setattr(agent_cli, 'simulate', lambda c, d: solves.append(1) or _fake_result(c, d))
    agent_cli._baseline('base_coil', 5.0)
    (cache_file,) = (tmp_path / 'femm_agent_cache').glob('*.json')
    cache_file.write_text('{"half written')
    assert agent_cli._baseline('base_coil', 5.0)['distance'] == 5.0
    assert len(solves) == 2 and not list((tmp_path / 'femm_agent_cache').glob('*.tmp'))


def test_sweep_summary_table_matches_the_points(fake_solver):
    base = _base().tank_trace_thickness
    out = agent_cli._sweep('base_coil', 'tank_trace_thickness', f'{base},{base * 2}', 5.0, None)
    header, first, second = out['summary']
    assert 'tank_esr' in header and 'M23' in header
    assert first.startswith(f'{base:g} | +0.00 |')
    assert second.startswith(f'{base * 2:g} | +100.00 |')


def test_sweep_summary_shows_point_errors(monkeypatch, fake_solver):
    monkeypatch.setattr(agent_cli, 'simulate', lambda c, d: (_ for _ in ()).throw(RuntimeError('boom')))
    out = agent_cli._sweep('base_coil', 'tank_trace_thickness', '0.1,0.2', 5.0, None)
    assert out['summary'][1].endswith('boom') and out['summary'][2].endswith('boom')
