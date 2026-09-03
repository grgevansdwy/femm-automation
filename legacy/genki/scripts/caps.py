# /// script
# dependencies = [
#     "coloredlogs",
# ]
# ///

import argparse
import itertools 
import logging
import math
import os

import coloredlogs

log = logging.getLogger(os.path.basename(__file__))
coloredlogs.install(
    logger=log,
    fmt="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO, reconfigure=False)

# Note: These weights are only used if `iterative` is True
WEIGHT_BOM_COUNT = 10
WEIGHT_ZEROS_PER_AVG_CAP = 0.25
WEIGHT_IMBALANCE_WITHIN_BANK = 1
WEIGHT_IMBALANCE_BETWEEN_BANKS = 4

def average(x):
    return sum(x) / len(x)

def resonant_c(f, x):
    return 1/((2 * math.pi * f)**2 * x)

class Caps:
    """
    Each 'cfg' is an array of banks.
    Each 'bank' is an array of caps.
    """

    def __init__(self, caps_per_bank, num_banks, c_options_pF, c_bounds_pF, L, iterative=False, require_balanced_zeros=False):
        self.caps_per_bank = caps_per_bank
        self.num_banks = num_banks
        self.c_options_pF = c_options_pF + [0]
        self.c_bounds_pF = c_bounds_pF
        self.L = L
        self.iterative = iterative
        self.require_balanced_zeros = require_balanced_zeros

        self.c_avg_pF = (average(self.c_bounds_pF) * self.num_banks) / self.caps_per_bank
        self.c_options_pF.sort(key=lambda c: abs(self.c_avg_pF - c))

    @staticmethod
    def c_eff_pf(cfg):
        return round(1/(sum(1/sum(bank) for bank in cfg)))

    @staticmethod
    def bom_count(cfg):
        return len(set(itertools.chain(*cfg)) - set([0]))

    @staticmethod
    def num_zeros(cfg):
        return sum(sum(1 if c == 0 else 0 for c in bank) for bank in cfg)

    @staticmethod
    def max_imbalance_within_bank(cfg):
        return max((abs(c1 - c2) if (c1 != 0 and c2 != 0) else 0) for bank in cfg for c1, c2 in itertools.combinations(bank, 2))

    @staticmethod
    def max_imbalance_between_banks(cfg):
        if len(cfg) == 1:
            return 0
        return max(abs(sum(b1) - sum(b2)) for b1, b2 in itertools.combinations(cfg, 2))

    def resonant_f(self, c):
        if self.L is None:
            return 0
        else:
            return 1/(2 * math.pi * math.sqrt(self.L * c))

    def worth_considering(self, cfg):
        if any(all(c == 0 for c in bank) for bank in cfg):
            return False
        if self.require_balanced_zeros and len(set(sum(1 if c == 0 else 0 for c in bank) for bank in cfg)) != 1:
            return False
        return self.c_bounds_pF[0] <= self.c_eff_pf(cfg) <= self.c_bounds_pF[1]

    def cfg_sort(self, cfg):
        return self.bom_count(cfg), self.num_zeros(cfg), self.max_imbalance_between_banks(cfg), self.max_imbalance_within_bank(cfg), self.resonant_f(self.c_eff_pf(cfg)), cfg

    def weighted_imbalances(self, cfg):
        return (
            self.bom_count(cfg) * WEIGHT_BOM_COUNT,
            self.num_zeros(cfg) * self.c_avg_pF * WEIGHT_ZEROS_PER_AVG_CAP,
            self.max_imbalance_between_banks(cfg) * WEIGHT_IMBALANCE_BETWEEN_BANKS,
            self.max_imbalance_within_bank(cfg) * WEIGHT_IMBALANCE_WITHIN_BANK,
        )

    def better_cfg(self, cfg1, cfg2):
        return sum(self.weighted_imbalances(cfg2)) < sum(self.weighted_imbalances(cfg1))

    def make_bank_options(self, banks, p1, n, bank):
        """
        Make all capacitor bank options using recursion.

        @banks All banks constructed thus far.
        @p1 Tracker to keep all cap banks in sorted order (avoids duplicates).
        @n How many more slots to add to the current bank.
        @bank The current bank being worked on.
        """
        if n == 0:
            # [Base case]
            # We've filled up the bank. Return it!
            banks.append(bank)
        else:
            # [Recursive step]
            # For each (C) of the capacitor options:
            #   Add C to the current bank.
            #   Recurse, but passing along only higher capacitor options (p2 >= p1).
            for p2 in range(p1, len(self.c_options_pF)):
                self.make_bank_options(banks, p2, n - 1, bank + [self.c_options_pF[p2]])

    def print_cfg(self, cfg):
        print(
            cfg,
            f"f={self.resonant_f(self.c_eff_pf(cfg)):0.2f}MHz",
            f"Ceq={self.c_eff_pf(cfg):0.1f}pF",
            f"bom_count={self.bom_count(cfg)}",
            f"zeros={self.num_zeros(cfg)}",
            f"imbal-banks={self.max_imbalance_between_banks(cfg)}p",
            f"imbal-caps={self.max_imbalance_within_bank(cfg)}p",
        )

    def run(self):
        bank_options = []
        log.info("Initializing bank options...")
        self.make_bank_options(bank_options, 0, self.caps_per_bank, [])
        log.info("Initializing bank combinations...")
        cfgs = itertools.combinations_with_replacement(bank_options, self.num_banks)

        # Calculate len(cfgs) without forcing iteration
        num_configs = len(bank_options) ** self.num_banks
        log.info(f"Number of configs to consider: {num_configs}")

        if self.iterative:
            log.info('Iteratively exploring options, printing only better ones...')
            best_cfg = None
            for cfg in cfgs:
                if self.worth_considering(cfg) and ((best_cfg is None) or self.better_cfg(best_cfg, cfg)):
                    self.print_cfg(cfg)
                    best_cfg = cfg
        else:
            log.info("Sorting all (unique) valid tunings...")
            cfgs = [cfg for cfg in cfgs if self.worth_considering(cfg)]
            cfgs.sort(key=self.cfg_sort, reverse=True)
            log.info("Printing...")
            for cfg in cfgs:
                self.print_cfg(cfg)

C_OPTIONS_PF = {
    'atc-all': [39, 43, 47, 51, 56, 62, 68, 200],
    'atc-have-some': [39, 43, 47, 51, 56, 62],
    'atc-have-lots': [47, 51, 56],
    'knowles-all': [33, 36, 47, 51, 56, 62, 82, 91, 100, 120, 150, 180, 220, 270, 330, 390, 560, 680],
    'knowles-1000V': [33, 36, 47, 51, 56, 62, 82, 91, 100, 120, 150, 180, 220],
    'knowles-1000V-630V': [33, 36, 47, 51, 56, 62, 82, 91, 100, 120, 150, 180, 220, 270, 330, 390],
    'knowles-have-lots': [33, 36, 47, 51, 56, 62, 82, 91, 100, 180, 220, 330, 390, 560, 680],
    'knowles-have-lots-1000V': [33, 36, 47, 51, 56, 62, 82, 91, 100, 180, 220],
    'knowles-have-lots-1000V-630V': [33, 36, 47, 51, 56, 62, 82, 91, 100, 180, 220, 330, 390],
    'knowles-have-lots-1000V-630V-500V': [33, 36, 47, 51, 56, 62, 82, 91, 100, 120, 150, 180, 220, 330, 390, 560, 680],
}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Cap configuration finder")
    parser.add_argument("--iterative", action="store_true", default=False, help="Iteratively print heuristically-better options rather than sorting all first")
    parser.add_argument("--require-balanced-zeros", action="store_true", default=False, help="Require balanced number-of-open-slots between banks")
    parser.add_argument("--caps-per-bank", default=5, type=int, help="Capacitors per bank")
    parser.add_argument("--num-banks", default=2, type=int, help="Number of capacitor banks")
    parser.add_argument("--cap-options", default=list(C_OPTIONS_PF.keys())[0], type=str, help=f"Capacitor options (choose from {list(C_OPTIONS_PF.keys())})")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--L-and-freq", nargs=3, type=float, metavar=("L_UH", "FREQ_MIN_MHZ", "FREQ_MAX_MHZ"), help="Specify L (uH) and Resonant Frequency (MHz)")
    group.add_argument("--c-bounds", nargs=2, type=float, metavar=("MIN", "MAX"), help="Specify capacitor bounds (pF)")
    args = parser.parse_args()

    if args.L_and_freq:
        L = args.L_and_freq[0] * 1e-6
        c_bounds_pF = [resonant_c(args.L_and_freq[i] * 1e6, L) * 1e12 for i in [2, 1]]
    elif args.c_bounds:
        L = None
        c_bounds_pF = args.c_bounds

    caps = Caps(args.caps_per_bank, args.num_banks, C_OPTIONS_PF[args.cap_options], c_bounds_pF, L, args.iterative, args.require_balanced_zeros)
    caps.run()
