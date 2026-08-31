# =============================================================================
# config_result.py                            extracted from compare_coilsets.py
#
# The output model for one run of compare_coilsets.py: a ConfigResult per
# coilset, gathered in a RunResults. This replaces the 30 module-level parallel
# lists (k13_displays, loop_losses, tank_ferrite_max_flux, ...) whose entries
# were tied together only by index position. SweepPoint does the same job one
# level down: one instance per RC test distance, replacing the ~34 lists that
# used to accumulate per-distance results in lockstep inside the sweep loop.
#
# Every value here is RAW - plain floats, not quantiphy Quantity objects or
# pre-formatted strings. Formatting happens where the results table is built.
# That is deliberate: the old lists stored display values, which is why the
# archived runs in results/ cannot be re-plotted or diffed. Holding numbers is
# what lets a later step emit CSV/JSON alongside the SVG.
#
# Plain data: no FEMM, no matplotlib, no formatting.
# =============================================================================
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SweepPoint:
    """One coilset's results at one RC separation distance.

    Field order follows the order the inner distance-sweep loop in
    compare_coilsets.py computes them, not the order anything renders them.
    """
    distance: float     # mm, RC separation - same value rc_test_distances holds

    L_loop: float       # H
    M13: float          # H
    M12: float          # H
    L_tank: float       # H
    M23: float          # H
    M21: float          # H, reciprocity check against M12
    L_rc: float         # H
    M31: float          # H, reciprocity check against M13
    M32: float          # H, reciprocity check against M23

    k12: float
    k13: float
    k23: float

    tank_f: float       # Hz, tank's detuned resonant freq at this distance

    iloop: float        # A peak
    itank: float        # A peak
    irc: float          # A peak
    vpa: float          # V
    ipa: float          # A
    pbatt: float        # W
    vrect: float        # V

    abs_Iloop: float    # |Iloop/Irc|, feeds mag_normal
    abs_Itank: float    # |Itank/Irc|, feeds mag_normal


@dataclass(frozen=True)
class ConfigResult:
    """One coilset's results: the summary row-values for the results table.

    Field order follows the order the main loop computes them, not the order
    the table renders them.
    """
    label: str          # was `configs`
    color: tuple        # was `colors`

    # Coupling and mutual inductance, at the CLOSEST test distance.
    k12: float
    k13: float
    k23: float
    M12: float          # H
    M13: float          # H
    M23: float          # H

    # Self-inductance: bare PCB ("solo"), then in the full stack-up.
    L_loop_solo: float
    L_tank_solo: float
    L_rc_solo: float
    L_loop_assembled: float
    L_tank_assembled: float
    L_rc_assembled: float

    # Computed but NOT currently shown - `rows` in compare_coilsets.py has no
    # entry for these four. Kept so the data is not lost; each is one row away
    # from being surfaced.
    L_tank_at_closest: float
    L_rc_at_closest: float
    abs_Iloop: float
    abs_Itank: float

    # From the final solve at the real currents the circuit model predicts.
    tc_ferrite_flux: float      # T
    rc_ferrite_flux: float      # T
    # NOTE: these three are ESR in OHMS, not losses in watts. The old names
    # (loop_losses / tank_losses / rc_losses) said otherwise - see the
    # LABEL MISMATCH comment at the loop_ESR call site.
    loop_esr: float             # Ohms
    tank_esr: float             # Ohms
    rc_esr: float               # Ohms
    tc_ferrite_loss: float      # W
    rc_ferrite_loss: float      # W
    tc_shield_loss: float       # W
    rc_shield_loss: float       # W
    total_loss: float           # W
    efficiency: float           # fraction, NOT percent - the % is added at render
    mag_normal: float

    # Every SweepPoint from the distance sweep, closest first. Additive: the
    # closest-distance scalars above are kept too, since render_results_table
    # reads them directly.
    sweep: list = field(default_factory=list)


@dataclass
class RunResults:
    """Every ConfigResult from one run, in the order the configs were swept."""
    timestamp: str
    configs: list = field(default_factory=list)

    @property
    def labels(self):
        """Column headers for both output tables."""
        return [c.label for c in self.configs]

    @property
    def colors(self):
        """Column header colours, matching each config's chart lines."""
        return [c.color for c in self.configs]

    def __len__(self):
        return len(self.configs)
