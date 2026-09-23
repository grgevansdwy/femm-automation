# =============================================================================
# compare_wine_vs_xfemm.py
#
# Runs every config in data/configs/ through BOTH backends - real FEMM.exe
# under Wine (engine/tests/wine_femm_backend.py) and xfemm's femmcli (production,
# engine/services/xfemm_backend.py) - using the exact same generated Lua script for
# both (engine.build_script()), and writes a go/no-go + timing report to
# docs/archive/wine_vs_xfemm_validation.md.
#
# This is the same "one script, two backends, diff" methodology already used
# to find and fix the mutual-inductance bug (docs/Engine.md), now run across
# all 16 configs instead of the 3 checked by hand at the time - closing the
# gap docs/archive/xfemm.md itself flagged ("Only 3 of 16 production configs were
# individually validated").
#
# Needs real FEMM under Wine installed (docs/Engine.md) - each config
# pops up FEMM's window briefly, same as any other Wine-driven run.
#
#     cd backend && uv run --package backend python3 -m engine.tests.compare_wine_vs_xfemm
#
# Writes only the markdown report below - no JSON result files anywhere.
# =============================================================================
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from engine.core import build_script, extract_result, DEFAULT_RC_TEST_DISTANCE
from engine.repositories import coilset_repository as cc
from engine.services import femm_service as nff
from engine.services import xfemm_backend
from tests import wine_femm_backend

TOLERANCE_PCT = 1.0
REPORT_PATH = Path(__file__).resolve().parents[2] / 'docs' / 'wine_vs_xfemm_validation.md'

FIELDS = ['L_loop', 'L_tank', 'L_rc', 'M12', 'M13', 'M23',
          'loop_esr', 'tank_esr', 'rc_esr', 'k12', 'k13', 'k23']

ZERO_ATOL = 1e-15  # below this, a value counts as "legitimately zero" (e.g. k12 with no tank coil)


@dataclass
class BackendRun:
    result: object = None       # SimResult, or None on failure
    elapsed_s: float = None
    error: str = None


@dataclass
class ConfigOutcome:
    config_id: str
    wine: BackendRun = None
    xfemm: BackendRun = None
    setup_error: str = None     # invalid coilset / script build failure - neither backend ran


def rel_diff_pct(wine_val, xfemm_val):
    if abs(wine_val) < ZERO_ATOL:
        return 0.0 if abs(xfemm_val) < ZERO_ATOL else float('inf')
    return abs(xfemm_val - wine_val) / abs(wine_val) * 100.0


def is_valid_coilset(coilset):
    reason, valid = nff.check_valid_coilset(
        coilset.loop_r, coilset.num_loop_turns, coilset.num_parallel_loop_traces,
        coilset.loop_trace_spacing, coilset.loop_turn_spacing, coilset.loop_trace_width,
        coilset.tank_center_r, coilset.num_tank_turns, coilset.num_parallel_tank_traces,
        coilset.tank_trace_spacing, coilset.tank_turn_spacing, coilset.tank_trace_width,
        coilset.rc_radius, coilset.num_rc_turns, coilset.num_parallel_rc_traces,
        coilset.rc_trace_spacing, coilset.rc_turn_spacing, coilset.rc_trace_width)
    return valid, reason


def run_backend(run_fn, script_text, coilset, distance):
    start = time.perf_counter()
    try:
        raw = run_fn(script_text)
        result = extract_result(raw, coilset, distance)
        return BackendRun(result=result, elapsed_s=time.perf_counter() - start)
    except Exception as e:
        return BackendRun(elapsed_s=time.perf_counter() - start, error=str(e))


def run_one_config(config_id, distance=DEFAULT_RC_TEST_DISTANCE):
    coilset = cc.get_coilset(config_id)

    valid, reason = is_valid_coilset(coilset)
    if not valid:
        return ConfigOutcome(config_id, setup_error=f"invalid coilset: {reason}")

    try:
        script_text = build_script(coilset, distance).render()
    except Exception as e:
        return ConfigOutcome(config_id, setup_error=f"build_script failed: {e}")

    print(f"  [{config_id}] running xfemm...")
    xfemm_run = run_backend(xfemm_backend.run_femmcli, script_text, coilset, distance)
    print(f"  [{config_id}] running wine femm.exe (window will pop up)...")
    wine_run = run_backend(wine_femm_backend.run_wine_femm, script_text, coilset, distance)

    return ConfigOutcome(config_id, wine=wine_run, xfemm=xfemm_run)


def worst_diff(outcome):
    """(worst_pct, field_name) across all fields, or (None, None) if either
    backend failed and there's nothing to compare."""
    if outcome.setup_error or outcome.wine.error or outcome.xfemm.error:
        return None, None
    worst_pct, worst_field = -1.0, None
    for field in FIELDS:
        pct = rel_diff_pct(getattr(outcome.wine.result, field), getattr(outcome.xfemm.result, field))
        if pct > worst_pct:
            worst_pct, worst_field = pct, field
    return worst_pct, worst_field


def is_go(outcome):
    if outcome.setup_error or outcome.wine.error or outcome.xfemm.error:
        return False
    pct, _ = worst_diff(outcome)
    return pct <= TOLERANCE_PCT


def fmt(value):
    return f"{value:.6g}"


def fmt_time(seconds):
    return "-" if seconds is None else f"{seconds:.2f}"


def render_report(outcomes):
    lines = []
    lines.append("# Wine (real FEMM) vs xfemm validation")
    lines.append("")
    lines.append(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}. "
                 f"Same generated Lua script run through both real FEMM.exe under Wine and "
                 f"xfemm's femmcli, at the default RC distance ({DEFAULT_RC_TEST_DISTANCE}mm). "
                 f"Go/no-go tolerance: {TOLERANCE_PCT}% relative difference on every field.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Config | Go/No-Go | Worst diff | Field | Wine time (s) | xfemm time (s) | Speedup |")
    lines.append("|---|---|---|---|---|---|---|")
    for o in outcomes:
        if o.setup_error:
            lines.append(f"| {o.config_id} | NO-GO | - | setup error: {o.setup_error} | - | - | - |")
            continue
        pct, field = worst_diff(o)
        go = "GO" if is_go(o) else "NO-GO"
        pct_str = "-" if pct is None else (f"{pct:.2e}%" if pct != float('inf') else "inf")
        field_str = field or (o.wine.error and f"wine error: {o.wine.error}") or (o.xfemm.error and f"xfemm error: {o.xfemm.error}") or "-"
        wine_t = fmt_time(o.wine.elapsed_s)
        xfemm_t = fmt_time(o.xfemm.elapsed_s)
        speedup = "-"
        if o.wine.elapsed_s and o.xfemm.elapsed_s:
            speedup = f"{o.wine.elapsed_s / o.xfemm.elapsed_s:.1f}x"
        lines.append(f"| {o.config_id} | {go} | {pct_str} | {field_str} | {wine_t} | {xfemm_t} | {speedup} |")
    lines.append("")

    lines.append("## Per-config detail")
    for o in outcomes:
        lines.append("")
        lines.append(f"### {o.config_id}")
        lines.append("")
        if o.setup_error:
            lines.append(f"Setup error (neither backend ran): {o.setup_error}")
            continue
        if o.wine.error:
            lines.append(f"Wine run failed: {o.wine.error}")
        if o.xfemm.error:
            lines.append(f"xfemm run failed: {o.xfemm.error}")
        if o.wine.error or o.xfemm.error:
            continue
        lines.append("| Field | Wine | xfemm | Rel diff (%) | Within 1%? |")
        lines.append("|---|---|---|---|---|")
        for field in FIELDS:
            wv = getattr(o.wine.result, field)
            xv = getattr(o.xfemm.result, field)
            pct = rel_diff_pct(wv, xv)
            pct_str = f"{pct:.2e}" if pct != float('inf') else "inf"
            within = "yes" if pct <= TOLERANCE_PCT else "no"
            lines.append(f"| {field} | {fmt(wv)} | {fmt(xv)} | {pct_str} | {within} |")
    lines.append("")
    return "\n".join(lines)


def main():
    configs = [record['id'] for record in cc.list_configs()]
    print(f"Comparing {len(configs)} configs (wine femm.exe vs xfemm femmcli)...")
    outcomes = [run_one_config(config_id) for config_id in configs]

    report = render_report(outcomes)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)

    go_count = sum(1 for o in outcomes if not o.setup_error and is_go(o))
    print(f"\n{go_count}/{len(outcomes)} configs GO. Report written to {REPORT_PATH}")


if __name__ == '__main__':
    main()
