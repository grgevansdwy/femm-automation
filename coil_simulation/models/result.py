# =============================================================================
# result.py                                              extracted from main.py
#
# The output of one simulate() call: self-inductance, mutual inductance, and
# ESR for a single CoilSet at a single RC separation distance.
#
# Plain data: no FEMM, no matplotlib, no formatting.
# =============================================================================
from dataclasses import dataclass


@dataclass(frozen=True)
class SimResult:
    """One CoilSet's L/M/ESR at one RC separation distance.

    L_loop/L_tank/L_rc are self-inductance with ferrite, shields, backplane,
    and the RC coil all present at the real target distance - not the
    bare-PCB or RC-parked variants.
    """
    config_name: str
    distance: float     # mm, RC separation

    L_loop: float        # H
    L_tank: float         # H
    L_rc: float            # H

    M12: float              # H
    M13: float              # H
    M23: float              # H

    loop_esr: float          # Ohms
    tank_esr: float           # Ohms
    rc_esr: float              # Ohms

    k12: float
    k13: float
    k23: float
