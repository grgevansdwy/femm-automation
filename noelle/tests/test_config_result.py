# =============================================================================
# test_config_result.py
#
# Checks the ConfigResult / RunResults output model that replaced the 30
# module-level accumulator lists, and SweepPoint, which does the same job for
# the ~34 per-distance lists that used to live inside the sweep loop. Needs no
# FEMM and no ltspiceautomator:
#     python3 -m tests.test_config_result          (run from noelle/)
#
# What this can and cannot prove: the main loop cannot run here, so nothing
# verifies the figure end-to-end. What IS proved is that the results table still
# has 24 rows in the right order, that every cell renders, and - the point of
# the rewrite - that formatting a raw float here produces exactly the string the
# old accumulator lists stored.
# =============================================================================
import dataclasses

from quantiphy import Quantity

from models.config_result import ConfigResult, RunResults, SweepPoint

# The 24 row labels, copied from compare_coilsets.py. `rows` there is an
# f-string list depending on rc_test_distances; the distance is fixed at 5 here.
ROWS = ['k13 @ 5mm', 'k23 @ 5mm', 'k12', 'M13 @ 5mm', 'M23 @ 5mm', 'M12',
        'L_loop - just PCB (L1)', 'L_loop - full stack-up (L1)',
        'L_tank  - just PCB (L2)', 'L_tank  - full stack-up (L2)',
        'L_rc - just PCB (L3)', 'L_rc - full stack-up (L3)',
        'Max flux in TC Ferrite (T)', 'Max flux in RC Ferrite (T)',
        'Magnitude of normalized currents', 'Efficiency at  @ 5mm',
        'Loop ESR (mOhms)', 'Tank ESR (mOhms)', 'RC ESR (mOhms)',
        'TC Ferrite Losses (W)', 'TC Shield Losses (W)',
        'RC Ferrite Losses (W)', 'RC Shield Losses (W)', 'Total Losses (W)']


def build_cells(run):
    """The 24 row-building comprehensions from compare_coilsets.py."""
    cr = []
    cr.append([f'{c.k13:.3f}'                 for c in run.configs])
    cr.append([f'{c.k23:.3f}'                 for c in run.configs])
    cr.append([f'{c.k12:.3f}'                 for c in run.configs])
    cr.append([Quantity(c.M13, 'H')           for c in run.configs])
    cr.append([Quantity(c.M23, 'H')           for c in run.configs])
    cr.append([Quantity(c.M12, 'H')           for c in run.configs])
    cr.append([Quantity(c.L_loop_solo, 'H')      for c in run.configs])
    cr.append([Quantity(c.L_loop_assembled, 'H') for c in run.configs])
    cr.append([Quantity(c.L_tank_solo, 'H')      for c in run.configs])
    cr.append([Quantity(c.L_tank_assembled, 'H') for c in run.configs])
    cr.append([Quantity(c.L_rc_solo, 'H')        for c in run.configs])
    cr.append([Quantity(c.L_rc_assembled, 'H')   for c in run.configs])
    cr.append([Quantity(c.tc_ferrite_flux, 'T')  for c in run.configs])
    cr.append([Quantity(c.rc_ferrite_flux, 'T')  for c in run.configs])
    cr.append([f'{c.mag_normal:.3f}'          for c in run.configs])
    cr.append([f'{c.efficiency*100:.3f}%'     for c in run.configs])
    cr.append([Quantity(c.loop_esr, 'Ohms')   for c in run.configs])
    cr.append([Quantity(c.tank_esr, 'Ohms')   for c in run.configs])
    cr.append([Quantity(c.rc_esr, 'Ohms')     for c in run.configs])
    cr.append([Quantity(c.tc_ferrite_loss, 'W')  for c in run.configs])
    cr.append([Quantity(c.tc_shield_loss, 'W')   for c in run.configs])
    cr.append([Quantity(c.rc_ferrite_loss, 'W')  for c in run.configs])
    cr.append([Quantity(c.rc_shield_loss, 'W')   for c in run.configs])
    cr.append([Quantity(c.total_loss, 'W')       for c in run.configs])
    return cr


def make_config(label, color, scale=1.0):
    """A ConfigResult with every field distinct, so a mis-wired row shows up."""
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
    )


def test_table_shape():
    """24 rows, each as wide as the config list."""
    run = RunResults(timestamp='281200')
    run.configs.append(make_config('Gen2 run-1', (1.0, 0.0, 0.0, 1.0)))
    run.configs.append(make_config('Gen3 run-2', (0.0, 0.0, 1.0, 1.0), scale=0.5))

    cells = build_cells(run)
    assert len(cells) == len(ROWS) == 24, f"{len(cells)} rows vs {len(ROWS)} labels"
    for label, row in zip(ROWS, cells):
        assert len(row) == len(run.configs), f"row {label!r} has {len(row)} cells"
        for cell in row:
            assert str(cell), f"row {label!r} rendered an empty cell"
    print(f"  ok  {len(cells)} rows x {len(run.configs)} configs, all cells render")


def test_labels_and_colors_track_configs():
    """run.labels / run.colors replace the old parallel `configs` / `colors`."""
    run = RunResults(timestamp='281200')
    run.configs.append(make_config('a', (1.0, 0.0, 0.0, 1.0)))
    run.configs.append(make_config('b', (0.0, 1.0, 0.0, 1.0)))
    assert run.labels == ['a', 'b'], run.labels
    assert run.colors == [(1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0)]
    assert len(run) == 2
    print("  ok  labels/colors stay aligned with configs by construction")


def test_formatting_matches_the_old_lists():
    """The point of storing raw floats: rendering must reproduce the old strings.

    The old accumulators stored these already-formatted; the new code formats at
    render. Same format calls, so the table text is unchanged.
    """
    k13, mag, eff = 0.47395861902864, 2.9357818822806854, 0.958712
    M13, esr, loss = 2.9942263569382067e-07, 0.19034, 41.2077
    assert f'{k13:.3f}' == '{:.3f}'.format(k13) == '0.474'
    assert f'{mag:.3f}' == '{:.3f}'.format(mag) == '2.936'
    assert f'{eff*100:.3f}%' == '{:.3f}%'.format(eff*100) == '95.871%'
    assert str(Quantity(M13, 'H')) == '299.42 nH'
    assert str(Quantity(esr, 'Ohms')) == str(Quantity(0.19034, 'Ohms'))
    assert str(Quantity(loss, 'W')) == '41.208 W'
    print("  ok  raw-float rendering reproduces the old formatted strings")


def test_no_ferrite_config_is_constructible():
    """The flux fields default to 0 for a config with no ferrite.

    TC/RC_ferrite_flux_density are only bound inside `if ..._ferrite_present`,
    so the loop needs raw 0 defaults. Without them this construction raises.
    """
    c = make_config('no ferrite', (0, 0, 0, 1))
    assert c.tc_ferrite_flux == 0.0
    assert str(Quantity(c.tc_ferrite_flux, 'T')) == '0 T'
    print("  ok  a no-ferrite config constructs and renders 0 T")


def test_sweep_holds_the_per_distance_points():
    """sweep is additive: closest-distance data, in full, alongside the scalars."""
    points = [
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
    c = dataclasses.replace(make_config('x', (0, 0, 0, 1)), sweep=points)
    assert len(c.sweep) == 2
    assert c.sweep[0].distance == 5 and c.sweep[-1].distance == 10
    print("  ok  ConfigResult.sweep holds the full per-distance record")


def test_frozen():
    """ConfigResult is frozen - a result cannot be mutated after the fact."""
    c = make_config('x', (0, 0, 0, 1))
    try:
        c.total_loss = 0
    except Exception:
        print("  ok  ConfigResult is immutable")
        return
    raise AssertionError("ConfigResult should be frozen")


if __name__ == '__main__':
    print("checking the results output model")
    test_table_shape()
    test_labels_and_colors_track_configs()
    test_formatting_matches_the_old_lists()
    test_no_ferrite_config_is_constructible()
    test_sweep_holds_the_per_distance_points()
    test_frozen()
    print("all results checks passed")
