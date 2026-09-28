# =============================================================================
# agent_cli.py
#
# JSON-in/JSON-out CLI over the coil simulator, so an agent harness (pi
# extension, MCP server, Excel bridge, ...) can drive it without importing
# Python. Prints exactly one JSON document on stdout; errors are
# {"error": "..."} with exit code 1 (bad input AND solver crashes).
#
#   python agent_cli.py list
#   python agent_cli.py show <config_id>
#   python agent_cli.py simulate --config <id> [--distance MM] [--set key=value ...]
#   python agent_cli.py sweep    --config <id> --param <key> --values v1,v2,... [--distance MM] [--set key=value ...]
#   python agent_cli.py save     --config <id> --name <name> [--set key=value ...]
#
# Overrides are validated before any solve: numbers must be finite, counts whole
# and >= 1, flags 0/1, core sizes > 0, distance 0-200 mm. Bad input and solver
# failures both come back as {"error": ...}.
#
# simulate/sweep results carry `pct_vs_base`: the % change of every output vs
# the UNMODIFIED config at the same distance, so the caller never does the
# arithmetic itself.
#
# Run from backend/ (the `engine` package is imported relative to it).
# =============================================================================
import argparse
import dataclasses
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

from engine.core import simulate, DEFAULT_RC_TEST_DISTANCE
from engine.models.coilset import CoilSet
from engine.repositories import coilset_repository as repo

class InputError(ValueError):
    """The caller's arguments are wrong (as opposed to the solver failing)."""


MAX_SWEEP_POINTS = 8
MAX_DISTANCE_MM = 200.0     # 1000 mm hangs the solver for ~2 min and then fails
_FLAG_FIELDS = {'backplane', 'rc_backplane', 'tc_shield', 'rc_shield'}
# Sizes that must be > 0 for the coil to exist. (Other sizes - ferrite/shield thickness or radius,
# trace spacing - are legitimately 0 in some configs when the part is absent, but never negative.)
_MUST_BE_POSITIVE = {
    'tank_trace_width', 'loop_trace_width', 'rc_trace_width',
    'tank_trace_thickness', 'loop_trace_thickness', 'rc_trace_thickness',
    'tank_PCB_thickness', 'loop_PCB_thickness', 'rc_PCB_thickness',
    'rc_radius', 'tank_center_r', 'loop_r',
}
_SIZE_SUFFIXES = ('_thickness', '_width', '_radius', '_spacing', '_outer_r')
_METRICS = ('L_loop', 'L_tank', 'L_rc', 'M12', 'M13', 'M23', 'loop_esr', 'tank_esr', 'rc_esr')

def _parse_value(key, raw, kind):
    """One `key=raw` override -> a validated number (int for count/flag fields, else float).

    `kind` is the field's DECLARED type: a float field can be stored as an int
    in the database (e.g. base_coil's `tank_ferrite_y_offset: 0`), and taking
    the stored type would silently truncate 1.5 to 1.
    """
    try:
        value = float(raw)
    except ValueError:
        raise InputError(f"Bad override {key}={raw!r}: the value must be a number")
    if not math.isfinite(value):
        raise InputError(f"Bad override {key}={raw}: the value must be finite")
    if kind is not int:
        if key in _MUST_BE_POSITIVE and value <= 0:
            raise InputError(f"Bad override {key}={raw}: {key} must be greater than 0")
        if key.endswith(_SIZE_SUFFIXES) and value < 0:
            raise InputError(f"Bad override {key}={raw}: {key} cannot be negative")
        return value
    if value != int(value):
        raise InputError(f"Bad override {key}={raw}: {key} is a whole number (turns, layers, traces or a 0/1 flag)")
    value = int(value)
    if key.startswith('num_') and value < 1:
        raise InputError(f"Bad override {key}={raw}: needs at least 1")
    if (key.endswith('_present') or key in _FLAG_FIELDS) and value not in (0, 1):
        raise InputError(f"Bad override {key}={raw}: must be 0 or 1")
    return value

# This is how the agent propose new geometry (adding pairs into a baseline coilset)
def _apply_overrides(coilset, pairs):
    """Return a copy of `coilset` with `key=value` overrides applied.

    Numbers are validated by _parse_value (whole numbers for counts, finite,
    counts >= 1, flags 0/1); unknown keys raise so a typo doesn't silently
    simulate the base design.
    """
    kinds = {f.name: f.type for f in dataclasses.fields(CoilSet)}
    changes = {}
    for pair in pairs or []:
        key, sep, raw = pair.partition('=')
        if not sep or key not in kinds:
            raise InputError(f"Bad override {pair!r}: expected key=value with key in {sorted(kinds)}")
        changes[key] = raw if kinds[key] is str else _parse_value(key, raw, kinds[key])
    return dataclasses.replace(coilset, **changes)

def _solver_error(e):
    """Message for a failed solve, with a hint for the solver's opaque failures."""
    msg = f'Simulation failed: {e}'
    if 'could not convert string to float' in msg or 'Material properties have not' in msg:
        msg += ' (the solver rejected the geometry: a size is probably zero, negative, or so large that regions overlap)'
    elif 'timed out' in msg:
        msg += ' (the geometry is probably extreme, e.g. a very large radius; stay within about 0.5x-3x of the saved value)'
    return msg

def _check_distance(distance):
    if not math.isfinite(distance) or not 0 < distance <= MAX_DISTANCE_MM:
        raise InputError(f"--distance must be above 0 and at most {MAX_DISTANCE_MM:g} mm, got {distance:g}")

# reduces a full config record to {id, name, builtin}
def _summary(record):
    return {'id': record['id'], 'name': record['name'], 'builtin': record['builtin']}

def _engine_fingerprint():
    """Hash of everything that decides a solve's numbers besides the coilset: the engine's
    code (minus tests) and the constants file. A physics/mesh change alters it."""
    root = Path(__file__).resolve().parent
    files = sorted(p for p in (root / 'engine').rglob('*.py') if 'tests' not in p.parts)
    files += [root / 'data' / 'constants.json']
    h = hashlib.sha1()
    for p in files:
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()

def _baseline(config_id, distance):
    """SimResult dict of the UNMODIFIED config, cached on disk.

    Results are deterministic, so the cache key is a hash of the coilset's
    contents + distance + the engine fingerprint: editing the config (e.g. in
    the GUI) or changing the engine changes the key, so a stale baseline can
    never be served.
    """
    base = repo.get_coilset(config_id)
    key = hashlib.sha1(json.dumps([dataclasses.asdict(base), distance, _engine_fingerprint()], sort_keys=True).encode()).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / 'femm_agent_cache' / f'{key}.json'
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):      # no cache yet, or a torn/corrupt file: just solve again
        pass
    result = dataclasses.asdict(simulate(base, distance))
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(result))
    os.replace(tmp, path)              # atomic: parallel callers never see a half-written file
    return result

def _pct_vs_base(result, baseline):
    return {k: round((result[k] / baseline[k] - 1) * 100, 3) for k in _METRICS}

def _simulate_with_delta(coilset, distance, baseline):
    result = dataclasses.asdict(simulate(coilset, distance))
    return {**result, 'pct_vs_base': _pct_vs_base(result, baseline)}

_SUMMARY_COLUMNS = ('tank_esr', 'rc_esr', 'loop_esr', 'L_tank', 'M12', 'M23')

def _summary_table(points):
    """Compact text table of a sweep (% vs base), so callers can quote it instead of re-reading JSON."""
    rows = ['value | ' + ' | '.join(_SUMMARY_COLUMNS) + '   (% change vs unmodified config)']
    for pt in points:
        cells = pt['error'][:70] if 'error' in pt else ' | '.join(f"{pt['pct_vs_base'][c]:+.2f}" for c in _SUMMARY_COLUMNS)
        rows.append(f"{pt['value']:g} | {cells}")
    return rows

def _sweep(config_id, param, values, distance, pairs):
    """One simulation per value of `param`, other overrides held fixed.

    A point whose solve crashes (e.g. overlapping traces) reports its own
    error instead of aborting the whole sweep.
    """
    try:
        points = list(dict.fromkeys(float(v) for v in values.split(',') if v.strip()))  # de-duplicated, order kept
    except ValueError:
        raise InputError(f"--values must be comma-separated numbers, got {values!r}")
    if not 2 <= len(points) <= MAX_SWEEP_POINTS:
        raise InputError(f"--values needs 2 to {MAX_SWEEP_POINTS} distinct comma-separated numbers, got {len(points)}")
    _check_distance(distance)
    base = _apply_overrides(repo.get_coilset(config_id), pairs)
    if not isinstance(getattr(base, param, None), (int, float)):
        raise InputError(f"Cannot sweep {param!r}: only numeric CoilSet fields can be swept")
    _apply_overrides(base, [f'{param}={points[0]}'])  # bad --set / --param / value fails the whole call, not every point
    baseline = _baseline(config_id, distance)
    out = []
    for v in points:
        if out and 'timed out' in out[-1].get('error', ''):
            out.append({'value': v, 'error': 'skipped: the solver timed out on the previous point'})
            continue
        try:
            coilset = _apply_overrides(base, [f'{param}={v}'])
            out.append({'value': v, **_simulate_with_delta(coilset, distance, baseline)})
        except Exception as e:
            out.append({'value': v, 'error': _solver_error(e)})
    return {'param': param, 'summary': _summary_table(out), 'baseline': baseline, 'points': out}

# consists of 5 different function
# list --> returns the summaries of every config
# show --> returns the full config record
# simulate --> loads the base config, applied override, runs simulate(), adds pct_vs_base
# sweep --> simulate() once per value of one parameter
# save --> loads the base, applied override, then writes a new config
def _run(args):
    if args.command == 'list':
        return [_summary(r) for r in repo.list_configs()]
    if args.command == 'show':
        record = repo.get_config(args.config_id)
        if record is None:
            raise KeyError(f"No such config: {args.config_id!r}")
        return record
    if args.command == 'sweep':
        return _sweep(args.config, args.param, args.values, args.distance, args.set)
    coilset = _apply_overrides(repo.get_coilset(args.config), args.set)
    if args.command == 'simulate':
        _check_distance(args.distance)
        # No overrides -> this IS the baseline, so skip the second solve.
        baseline = _baseline(args.config, args.distance) if args.set else dataclasses.asdict(simulate(coilset, args.distance))
        return _simulate_with_delta(coilset, args.distance, baseline)
    if args.command == 'save':
        return _summary(repo.save_config(coilset, args.name, cloned_from=args.config))


# create the cli message
def main(argv=None):
    repo.seed_starter_configs()  # fresh checkout: load the starter designs so `list` isn't empty
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    show = sub.add_parser('show')
    show.add_argument('config_id')
    for name in ('simulate', 'sweep', 'save'):
        p = sub.add_parser(name)
        p.add_argument('--config', required=True)
        p.add_argument('--set', action='append', metavar='key=value')
        if name in ('simulate', 'sweep'):
            p.add_argument('--distance', type=float, default=DEFAULT_RC_TEST_DISTANCE)
        if name == 'sweep':
            p.add_argument('--param', required=True)
            p.add_argument('--values', required=True, metavar='v1,v2,...')
        if name == 'save':
            p.add_argument('--name', required=True)
    args = parser.parse_args(argv)
    try:
        out = _run(args)
    except (KeyError, InputError) as e:
        print(json.dumps({'error': str(e.args[0] if e.args else e)}))
        return 1
    except Exception as e:  # solver failures (e.g. "trace space ... less than trace width")
        print(json.dumps({'error': _solver_error(e)}))
        return 1
    print(json.dumps(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
