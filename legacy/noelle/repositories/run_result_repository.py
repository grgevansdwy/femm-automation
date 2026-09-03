# =============================================================================
# run_result_repository.py
#
# Owns everything one run writes to disk: ./results/ID<run_id>/ plus its log and
# figure. Before this, that was smeared across three layers - femm_model opened
# the log file, compare_coilsets did the mkdir and the savefig - so nothing owned
# the output and services/ did file I/O.
#
# Paths and savefig arguments are kept exactly as they were, so output lands
# where it always has.
# =============================================================================
import dataclasses
import datetime
import json
from pathlib import Path

from quantiphy import Quantity


class RunResultRepository:
    """The run's directory on disk: ./results/ID<run_id>/.

    Created eagerly, because the log is appended to throughout the run rather
    than written once at the end.
    """

    def __init__(self, run_id=None, root=Path('./results')):
        # GOTCHA: '{:%d%H%M}' is day-hour-minute - no month, no year. Two runs a
        # month apart at the same minute overwrite each other, and the folders
        # do not sort chronologically. This is now the one place that decides a
        # run id, so it is also the one place to fix it.
        self.run_id = run_id or '{:%d%H%M}'.format(datetime.datetime.today())
        self.dir = Path(root) / f'ID{self.run_id}'
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self):
        return self.dir / f'log_{self.run_id}.txt'

    @property
    def figure_path(self):
        return self.dir / f'ID{self.run_id}.svg'

    @property
    def summary_json_path(self):
        return self.dir / f'summary_{self.run_id}.json'

    def log(self, text):
        """Append one line to the run log AND echo it to stdout.

        Was femm_model.print_and_log(text, timestamp), which had no callers
        inside femm_model at all - a repository concern parked in a service.
        """
        with open(self.log_path, 'a') as log:
            log.write(text + '\n')
        print(text)

    @staticmethod
    def _spice_param_table(name, distances, values):
        """One LTspice `.param X=table(D, d0, v0, d1, v1, ...)` directive.

        Pasteable into a spice deck as a distance-dependent coupling model.
        """
        pairs = "".join(f", {d}, {v}" for d, v in zip(distances, values))
        return f".param {name}=table(D{pairs})"

    def log_run_summary(self, coilset, run_count, num_configs, sweep,
                         C1, C2, C3, Z1, Z2, Z3):
        """This coilset's text summary: L/M/k vs. distance, plus the spice tables.

        `sweep` is the list of SweepPoint for this coilset - read the same way
        Dashboard.plot_config reads cfg.sweep. Takes the list directly, not a
        ConfigResult, because ConfigResult isn't built yet at the call site:
        it needs the ferrite/loss field solve that runs after this logging,
        and this log must land before that solve's mi_analyze() can throw.
        """
        distances = [sp.distance for sp in sweep]
        last = sweep[-1]

        self.log(f"\n\n\nRun {run_count}/{num_configs}; {coilset.config_name}\n---------------------------------")
        self.log("---------------------------------")
        self.log(str(coilset))
        self.log("\n---------------------------------")
        self.log("Inductances:")
        self.log('L_rc: ' + str(Quantity(last.L_rc, 'H')))
        self.log('\nL_loop: ' + str(Quantity(last.L_loop, 'H')))
        self.log('L_tank: ' + str(Quantity(last.L_tank, 'H')))
        self.log('\nParameters vs Distance:')
        self.log(f"RC Test Distances (mm): {distances}")
        self.log('L_loops: ' + str([Quantity(sp.L_loop, 'H') for sp in sweep]))
        self.log("L_tanks: " + str([Quantity(sp.L_tank, 'H') for sp in sweep]))
        self.log("L_rcs: " + str([Quantity(sp.L_rc, 'H') for sp in sweep]))
        self.log('k13s: ' + str([sp.k13 for sp in sweep]))
        self.log("k23s: " + str([sp.k23 for sp in sweep]))
        self.log("k12s: " + str([sp.k12 for sp in sweep]))
        self.log('\nMutual Inductances:')
        self.log("M13: " + str([sp.M13 for sp in sweep]))
        self.log("M23: " + str([sp.M23 for sp in sweep]))
        self.log("M12: " + str([sp.M12 for sp in sweep]))
        self.log('M21: ' + str([sp.M21 for sp in sweep]))
        self.log('M32: ' + str([sp.M32 for sp in sweep]))
        self.log('M31: ' + str([sp.M31 for sp in sweep]))

        self.log(self._spice_param_table("M12", distances, [sp.M12 for sp in sweep]))
        self.log(self._spice_param_table("k12", distances, [sp.k12 for sp in sweep]))
        self.log(self._spice_param_table("M13", distances, [sp.M13 for sp in sweep]))
        self.log(self._spice_param_table("k13", distances, [sp.k13 for sp in sweep]))
        self.log(self._spice_param_table("M23", distances, [sp.M23 for sp in sweep]))
        self.log(self._spice_param_table("k23", distances, [sp.k23 for sp in sweep]))

        self.log("C1: " + str(C1))
        self.log("C2: " + str(C2))
        self.log("C3: " + str(C3))

        self.log("Z1: " + str(Z1))
        self.log("Z2: " + str(Z2))
        self.log("Z3: " + str(Z3))

    def save_summary_json(self, run):
        """Every ConfigResult from this run as JSON, next to the SVG.

        Results used to be write-only - locked in prose logs and SVGs, so
        nothing could be diffed or re-plotted without re-solving in FEMM. This
        is the raw data the results table already renders, so serializing it
        costs nothing new.
        """
        with open(self.summary_json_path, 'w') as f:
            json.dump([dataclasses.asdict(c) for c in run.configs], f, indent=2)

    def save_figure(self, fig):
        """Write the dashboard as SVG so the tables stay legible when zoomed."""
        fig.savefig(self.figure_path, dpi=400, format="svg", bbox_inches="tight")
