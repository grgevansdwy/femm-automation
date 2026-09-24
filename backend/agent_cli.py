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
import sys
import tempfile
from pathlib import Path

from engine.core import simulate, DEFAULT_RC_TEST_DISTANCE
from engine.models.coilset import CoilSet
from engine.repositories import coilset_repository as repo

MAX_SWEEP_POINTS = 8
_METRICS = ('L_loop', 'L_tank', 'L_rc', 'M12', 'M13', 'M23', 'loop_esr', 'tank_esr', 'rc_esr')

# This is how the agent propose new geometry (adding pairs into a baseline coilset)
def _apply_overrides(coilset, pairs):
    """Return a copy of `coilset` with `key=value` overrides applied.

    Values are coerced to the type of the field's current value (int/float);
    unknown keys raise so a typo doesn't silently simulate the base design.
    """
    valid = {f.name for f in dataclasses.fields(CoilSet)}
    changes = {}
    for pair in pairs or []:
        key, sep, raw = pair.partition('=')
        if not sep or key not in valid:
            raise ValueError(f"Bad override {pair!r}: expected key=value with key in {sorted(valid)}")
        current = getattr(coilset, key)
        changes[key] = type(current)(float(raw)) if isinstance(current, (int, float)) else raw
    return dataclasses.replace(coilset, **changes)

# reduces a full config record to {id, name, builtin}
def _summary(record):
    return {'id': record['id'], 'name': record['name'], 'builtin': record['builtin']}

def _baseline(config_id, distance):
    """SimResult dict of the UNMODIFIED config, cached on disk.

    Results are deterministic, so the cache key is a hash of the coilset's
    contents + distance: editing the config (e.g. in the GUI) changes the key,
    so a stale baseline can never be served.
    """
    base = repo.get_coilset(config_id)
    key = hashlib.sha1(json.dumps([dataclasses.asdict(base), distance], sort_keys=True).encode()).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / 'femm_agent_cache' / f'{key}.json'
    if path.exists():
        return json.loads(path.read_text())
    result = dataclasses.asdict(simulate(base, distance))
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result))
    return result

def _pct_vs_base(result, baseline):
    return {k: round((result[k] / baseline[k] - 1) * 100, 3) for k in _METRICS}

def _simulate_with_delta(coilset, distance, baseline):
    result = dataclasses.asdict(simulate(coilset, distance))
    return {**result, 'pct_vs_base': _pct_vs_base(result, baseline)}

def _sweep(config_id, param, values, distance, pairs):
    """One simulation per value of `param`, other overrides held fixed.

    A point whose solve crashes (e.g. overlapping traces) reports its own
    error instead of aborting the whole sweep.
    """
    points = [float(v) for v in values.split(',') if v.strip()]
    if not 2 <= len(points) <= MAX_SWEEP_POINTS:
        raise ValueError(f"--values needs 2 to {MAX_SWEEP_POINTS} comma-separated numbers, got {len(points)}")
    base = _apply_overrides(repo.get_coilset(config_id), pairs)
    _apply_overrides(base, [f'{param}={points[0]}'])  # bad --set / --param fails the whole call, not every point
    baseline = _baseline(config_id, distance)
    out = []
    for v in points:
        try:
            coilset = _apply_overrides(base, [f'{param}={v}'])
            out.append({'value': v, **_simulate_with_delta(coilset, distance, baseline)})
        except Exception as e:
            out.append({'value': v, 'error': str(e)})
    return {'param': param, 'baseline': baseline, 'points': out}

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
    except (KeyError, ValueError) as e:
        print(json.dumps({'error': str(e.args[0] if e.args else e)}))
        return 1
    except Exception as e:  # solver failures (e.g. "trace space ... less than trace width")
        print(json.dumps({'error': f'Simulation failed: {e}'}))
        return 1
    print(json.dumps(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
