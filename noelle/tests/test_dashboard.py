# =============================================================================
# test_dashboard.py
#
# Checks the view layer that owns the results figure. Needs no FEMM:
#     python3 -m tests.test_dashboard          (run from noelle/)
#
# The decisive check here is test_results_table_matches_the_old_output: the 24
# table rows are rendered from a fixed ConfigResult and compared, cell by cell,
# against the strings the pre-extraction code produced.
# =============================================================================
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use('Agg')          # no display needed
from quantiphy import Quantity

import matplotlib.pyplot as plt

from models.config_result import ConfigResult, RunResults, SweepPoint
from view.dashboard import Dashboard


def fresh(run_id='281200'):
    """A Dashboard on a clean figure.

    The program builds exactly one Dashboard per run; these tests build several,
    and matplotlib refuses to reuse a figure name. Not a product concern.
    """
    plt.close('all')
    return Dashboard(run_id=run_id)

PANES = ['vpa_pane', 'ipa_pane', 'Iloop_pane', 'Itank_pane', 'Irc_pane',
         'tank_tuning_pane', 'tank_lateral_tuning_pane', 'pbatt_pane',
         'efficiency_pane', 'vrect_pane', 'config_pane', 'results_pane',
         'disp_pane']

DISTANCES = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 75, 100]


def make_sweep(n):
    """Per-distance SweepPoints, the shape plot_config now expects."""
    return [
        SweepPoint(
            distance=DISTANCES[i],
            L_loop=219.54e-9, M13=2.9942e-07, M12=6.8675e-07,
            L_tank=5.5219e-6, M23=1.9372e-06, M21=6.8675e-07,
            L_rc=1.8179e-6, M31=2.9942e-07, M32=1.9372e-06,
            k12=0.6237, k13=0.4739, k23=0.6114,
            tank_f=6.75e6,
            iloop=9.2, itank=11.4, irc=12.7,
            vpa=200.0, ipa=6.1, pbatt=900.0, vrect=85.0,
            abs_Iloop=2.77, abs_Itank=0.97,
        )
        for i in range(n)
    ]


def make_config(label, color, scale=1.0):
    return ConfigResult(
        label=label, color=color,
        k12=0.6237 * scale, k13=0.4739 * scale, k23=0.6114 * scale,
        M12=6.8675e-07 * scale, M13=2.9942e-07 * scale, M23=1.9372e-06 * scale,
        L_loop_solo=219.54e-9, L_tank_solo=5.5219e-6, L_rc_solo=1.8179e-6,
        L_loop_assembled=220.67e-9, L_tank_assembled=5.5926e-6,
        L_rc_assembled=1.8351e-6,
        L_tank_at_closest=5.5219e-6, L_rc_at_closest=1.8179e-6,
        abs_Iloop=2.77, abs_Itank=0.97,
        tc_ferrite_flux=0.0, rc_ferrite_flux=0.031,
        loop_esr=0.19, tank_esr=1.836, rc_esr=0.512,
        tc_ferrite_loss=0.0, rc_ferrite_loss=3.2,
        tc_shield_loss=0.0, rc_shield_loss=1.1,
        total_loss=41.2, efficiency=0.9587, mag_normal=2.9357,
        sweep=make_sweep(len(DISTANCES)),
    )


def make_run(n=2):
    run = RunResults(timestamp='281200')
    run.configs.append(make_config('Gen2 run-1', (1.0, 0.0, 0.0, 1.0)))
    if n > 1:
        run.configs.append(make_config('Gen3 run-2', (0.0, 0.0, 1.0, 1.0), 0.5))
    return run


def test_owns_every_pane():
    d = fresh()
    seen = {}
    for name in PANES:
        ax = getattr(d, name, None)
        assert ax is not None, f"Dashboard has no {name}"
        assert id(ax) not in seen, f"{name} is the same axes as {seen[id(ax)]}"
        seen[id(ax)] = name
    assert d.figure is d.fig2
    assert d.cross_section_pane is d.disp_pane
    print(f"  ok  owns {len(PANES)} distinct panes, exposes figure + cross_section_pane")


def test_color_for_spans_the_colormap():
    d = fresh()
    a, b = d.color_for(1, 2), d.color_for(2, 2)
    assert a != b and len(a) == 4, (a, b)
    print("  ok  color_for() gives each config a distinct RGBA")


def test_results_table_matches_the_old_output():
    """The 24 rows must render exactly as they did before the extraction.

    `expected` is the pre-extraction expression list from compare_coilsets.py,
    evaluated against the same ConfigResult.
    """
    run = make_run()
    d = fresh()
    cells = d.render_results_table(run, closest_mm=5)

    expected = [
        [f'{c.k13:.3f}' for c in run.configs],
        [f'{c.k23:.3f}' for c in run.configs],
        [f'{c.k12:.3f}' for c in run.configs],
        [Quantity(c.M13, 'H') for c in run.configs],
        [Quantity(c.M23, 'H') for c in run.configs],
        [Quantity(c.M12, 'H') for c in run.configs],
        [Quantity(c.L_loop_solo, 'H') for c in run.configs],
        [Quantity(c.L_loop_assembled, 'H') for c in run.configs],
        [Quantity(c.L_tank_solo, 'H') for c in run.configs],
        [Quantity(c.L_tank_assembled, 'H') for c in run.configs],
        [Quantity(c.L_rc_solo, 'H') for c in run.configs],
        [Quantity(c.L_rc_assembled, 'H') for c in run.configs],
        [Quantity(c.tc_ferrite_flux, 'T') for c in run.configs],
        [Quantity(c.rc_ferrite_flux, 'T') for c in run.configs],
        [f'{c.mag_normal:.3f}' for c in run.configs],
        [f'{c.efficiency * 100:.3f}%' for c in run.configs],
        [Quantity(c.loop_esr, 'Ohms') for c in run.configs],
        [Quantity(c.tank_esr, 'Ohms') for c in run.configs],
        [Quantity(c.rc_esr, 'Ohms') for c in run.configs],
        [Quantity(c.tc_ferrite_loss, 'W') for c in run.configs],
        [Quantity(c.tc_shield_loss, 'W') for c in run.configs],
        [Quantity(c.rc_ferrite_loss, 'W') for c in run.configs],
        [Quantity(c.rc_shield_loss, 'W') for c in run.configs],
        [Quantity(c.total_loss, 'W') for c in run.configs],
    ]
    assert len(cells) == len(expected) == 24, (len(cells), len(expected))
    for i, (got, want) in enumerate(zip(cells, expected)):
        assert [str(x) for x in got] == [str(x) for x in want], \
            f"row {i} ({d.row_labels[i]}): {[str(x) for x in got]} != {[str(x) for x in want]}"
    assert d.row_labels[0] == 'k13 @ 5mm', d.row_labels[0]
    assert d.row_labels[-1] == 'Total Losses (W)', d.row_labels[-1]
    print("  ok  all 24 rows render identically to the pre-extraction code")


def test_config_table_pruning_is_unchanged():
    """Row pruning must still drop the same rows for each flag combination.

    The counts come from the pre-extraction behaviour, verified when the
    parameters dict was folded into CoilSet metadata.
    """
    from models.coilset import table_labels
    labels = table_labels()
    expected = {(True, True, True): 42, (False, False, False): 54,
                (True, False, True): 47, (False, True, False): 49}
    for flags, want in expected.items():
        no_tank, no_rc, no_bp = flags
        run = make_run()
        d = fresh()
        d.render_results_table(run, closest_mm=5)
        outputs = [[0.0] * len(run.configs) for _ in labels]
        d.render_config_table(run, outputs, labels, no_tank, no_rc, no_bp)
        got = len(d.config_row_labels)
        assert got == want, f"flags {flags}: {got} rows, expected {want}"
    print("  ok  pruning unchanged across all 4 flag combinations (42/54/47/49)")


def test_full_render_produces_an_svg():
    run = make_run()
    from models.coilset import table_labels
    labels = table_labels()
    d = fresh()
    for i, cfg in enumerate(run.configs, 1):
        d.plot_config(cfg, label=f'run-{i}', run_number=i)
    d.render_results_table(run, closest_mm=DISTANCES[0])
    d.render_config_table(run, [[0.0] * len(run.configs) for _ in labels],
                          labels, True, True, True)
    d.finish()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'ID281200.svg'
        d.figure.savefig(out, dpi=400, format='svg', bbox_inches='tight')
        assert out.exists() and out.stat().st_size > 10_000, out.stat().st_size
        assert out.read_text(errors='ignore').lstrip().startswith('<?xml')
    print("  ok  plot_config + tables + finish() render a real SVG")


def test_plot_config_reads_distances_from_sweep():
    d = fresh()
    cfg = make_config('x', (0, 0, 0, 1))
    d.plot_config(cfg, label='run-1', run_number=1)
    got = d.vpa_pane.lines[-1].get_xdata().tolist()
    want = [sp.distance for sp in cfg.sweep]
    assert got == want, (got, want)
    print("  ok  plot_config's x-axis comes from cfg.sweep, not a passed-in list")


def test_main_holds_no_matplotlib():
    """compare_coilsets.py must not touch matplotlib any more."""
    import re
    src = Path(__file__).resolve().parent.parent / 'compare_coilsets.py'
    bad = []
    for i, line in enumerate(src.read_text().split('\n'), 1):
        code = line.split('#')[0]
        if re.search(r'\bplt\.|\bfig2\b|\bbxs\b|matplotlib|FontProperties|xfrms', code):
            bad.append(f"{i}: {line.strip()}")
    assert not bad, "matplotlib in compare_coilsets.py:\n  " + "\n  ".join(bad)
    print("  ok  compare_coilsets.py holds no matplotlib")


if __name__ == '__main__':
    print("checking the dashboard view")
    test_owns_every_pane()
    test_color_for_spans_the_colormap()
    test_results_table_matches_the_old_output()
    test_config_table_pruning_is_unchanged()
    test_full_render_produces_an_svg()
    test_plot_config_reads_distances_from_sweep()
    test_main_holds_no_matplotlib()
    print("all dashboard checks passed")
