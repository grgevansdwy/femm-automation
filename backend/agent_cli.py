# =============================================================================
# agent_cli.py
#
# JSON-in/JSON-out CLI over the coil simulator, so an agent harness (pi
# extension, MCP server, Excel bridge, ...) can drive it without importing
# Python. Prints exactly one JSON document on stdout; errors are
# {"error": "..."} with exit code 1.
#
#   python agent_cli.py list
#   python agent_cli.py show <config_id>
#   python agent_cli.py simulate --config <id> [--distance MM] [--set key=value ...]
#   python agent_cli.py save --config <id> --name <name> [--set key=value ...]
#
# Run from backend/ (the `engine` package is imported relative to it).
# =============================================================================
import argparse
import dataclasses
import json
import sys

from engine.core import simulate, DEFAULT_RC_TEST_DISTANCE
from engine.models.coilset import CoilSet
from engine.repositories import coilset_repository as repo

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

# consists of 4 different function
# list --> returns the summaries of every config
# show --> returns the full config record
# simulate --> loads the base config, applied override, runs simulate()
# save --> loads the base, applied override, then writes a new config
def _run(args):
    if args.command == 'list':
        return [_summary(r) for r in repo.list_configs()]
    if args.command == 'show':
        record = repo.get_config(args.config_id)
        if record is None:
            raise KeyError(f"No such config: {args.config_id!r}")
        return record
    coilset = _apply_overrides(repo.get_coilset(args.config), args.set)
    if args.command == 'simulate':
        return dataclasses.asdict(simulate(coilset, args.distance))
    if args.command == 'save':
        return _summary(repo.save_config(coilset, args.name, cloned_from=args.config))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    show = sub.add_parser('show')
    show.add_argument('config_id')
    for name in ('simulate', 'save'):
        p = sub.add_parser(name)
        p.add_argument('--config', required=True)
        p.add_argument('--set', action='append', metavar='key=value')
        if name == 'simulate':
            p.add_argument('--distance', type=float, default=DEFAULT_RC_TEST_DISTANCE)
        else:
            p.add_argument('--name', required=True)
    args = parser.parse_args(argv)
    try:
        out = _run(args)
    except (KeyError, ValueError) as e:
        print(json.dumps({'error': str(e.args[0] if e.args else e)}))
        return 1
    print(json.dumps(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
