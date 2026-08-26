# /// script
# dependencies = [
#     "coloredlogs",
#     "matplotlib",
#     "numpy",
#     "pandas",
#     "PyQt6",
# ]
# ///

import argparse
import collections
import io
import logging
import os
import pprint
import re
import tarfile
import zipfile

import coloredlogs
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np
import pandas

log = logging.getLogger(os.path.basename(__file__))
coloredlogs.install(logger=log, fmt="%(asctime)s %(name)s %(levelname)s: %(message)s", level=logging.INFO, reconfigure=False)

plt.rcParams['figure.figsize'] = (12, 7)
plt.rcParams['lines.markersize'] = 2
plt.rcParams['lines.marker'] = 'o'

MATPLOTLIB_LINESTYLES = ['-', '--', '-.', ':', '']

Label = collections.namedtuple('Label', ['pretty', 'color'])

LABELS = {
    "Duration": Label(pretty="Duration (s)", color='lightgray'),
    "RxBattPower": Label(pretty="Battery Power", color='#CFB1D8'),
    "RxFlags": Label(pretty="RX Flags", color='pink'),
    "RxIBattery": Label(pretty="Battery Current", color='#E6828D'),
    "RxTargetIBatt": Label(pretty="Target Battery Current", color='#060926'),
    "RxTBoard": Label(pretty="OC Rectifier Temperature", color='#9618DF'),
    "RxVMonBatt": Label(pretty="Battery Voltage", color='#716B7C'),
    "RxVRect": Label(pretty="VRect", color='#2F8743'),
    "TxAdmittance": Label(pretty='Admittance', color='darkgray'),
    "TxMaxIPA": Label(pretty='MaxIPA', color='orange'),
    "TxFlags": Label(pretty="TX Flags", color='#56645C'),
    "TxIGD": Label(pretty="IPA", color='#6EC791'),
    "TxIMonGateDriver": Label(pretty="IGD", color='#2E3EF3'),
    "TxIMonPa": Label(pretty="IPA", color='#6EC791'),
    "TxPSys": Label(pretty="TR PA Sys Power", color='black'),
    "TxPower": Label(pretty="TR PA Power", color='#A8575A'),
    "TxPowerLoss": Label(pretty="System PowerLoss", color='black'),
    "TxTMonAmb": Label(pretty="TR DC-DC Temperature", color='#8CB9B3'),
    "TxTMonPa": Label(pretty="TR PA Temperature", color='#1BB526'),
    "TxVMonPa": Label(pretty="VPA", color='#DE0EE4'),
}

TX_RENAME_COLUMNS = {
    "AmbTemp": "TMonAmb",
    "IGD": "IMonGateDriver",
    "IPA": "IMonPa",
    "RTC": "Timestamp",
    "StatusFlags": "Flags",
    "Temp": "TMonPa",
    "Ticks": "PacketCount",
    "VPA": "VMonPa",
}

RX_RENAME_COLUMNS = {
    "IBatt": "IBattery",
    "IBattTarget": "TargetIBatt",
    "RTC": "Timestamp",
    "StatusFlags": "Flags",
    "Temp": "TBoard",
    "Ticks": "PacketCount",
    "VBatt": "VMonBatt",
}

def timestamp_to_t(run_data):
    return (run_data['TxTimestamp'] + run_data['TxPacketCount'] / 1000) - run_data['TxTimestamp'][0]

def run_duration(run_data):
    return run_data['TxTimestamp'][-1] - run_data['TxTimestamp'][0]

def last_ten_percent(arr):
    return arr[int(len(arr) * 0.9):]

def source_to_sweep_name(file):
    if os.path.isdir(file):
        return os.path.basename(os.path.dirname(file))
    else:
        return os.path.basename(file)

class Data:
    def __init__(self, files):
        self.data = collections.defaultdict(dict) # run_name -> sweep_name -> data
        # TODO DRY this code
        for toplevel_filename in files:
            sweep_name = source_to_sweep_name(toplevel_filename)
            if toplevel_filename.endswith('.zip'):
                # Coilbot run
                zip = zipfile.ZipFile(toplevel_filename, 'r')
                for entry_filename in sorted(zip.namelist()):
                    if not entry_filename.endswith('.csv'):
                        continue
                    run_name = entry_filename
                    with zip.open(entry_filename) as csv:
                        run_data = pandas.read_csv(csv)
                        if sweep_name in self.data[run_name]:
                            raise Exception(f"Duplication: Sweep Name ({sweep_name}) and Run Name ({run_name})")
                        if 'RxPacketCount' not in run_data.columns:
                            log.info(f"No OC data for {run_name} in {sweep_name}")
                            for label in LABELS.keys():
                                if label.startswith("Rx") and (label not in run_data.columns):
                                    log.info(f"Adding zeros for: {label}")
                                    run_data[label] = 0
                        self.data[run_name][sweep_name] = run_data
            elif toplevel_filename.endswith('.tar'):
                # Tarball of logs downloaded from TR
                with tarfile.open(toplevel_filename, 'r') as tar:
                    for member in tar.getmembers():
                        if member.name.endswith('.CSV') and member.name.startswith('TX'):
                            f = tar.extractfile(member)
                            run_name = str(int(member.name[len('TX_'):len('TX_0000')]))
                            csv_tx = io.TextIOWrapper(f, encoding='utf-8')
                            csv_rx = io.TextIOWrapper(tar.extractfile(member.name.replace('TX', 'RX')), encoding='utf-8')
                            self._parse_tx_rx(sweep_name, run_name, csv_tx, csv_rx)
            elif os.path.isdir(toplevel_filename):
                # Directory of logs downloaded from TR GUI, or taken with vendor CLI.
                dir_name = toplevel_filename
                for file in os.listdir(dir_name):
                    if file.upper().endswith('.CSV'):
                        log.info(f"Parsing: {dir_name}/{file}")
                        if file.startswith('TX'):
                            self._parse_file_gui(sweep_name, f"{dir_name}/{file}")
                        elif file.startswith('tx'):
                            self._parse_file_cli(sweep_name, f"{dir_name}/{file}")
            elif toplevel_filename.lower().endswith('.csv'):
                log.info(f"Parsing: {toplevel_filename}")
                if os.path.basename(toplevel_filename).startswith('TX_'):
                    # Single log file - downloaded from GUI Diagnostics Page.
                    self._parse_file_gui(sweep_name, toplevel_filename)
                elif os.path.basename(toplevel_filename).startswith('tx'):
                    # Single log file - from vendor utility.
                    self._parse_file_cli(sweep_name, toplevel_filename)
                else:
                    # Download from GUI Plot Page.
                    run_name = sweep_name
                    data = pandas.read_csv(toplevel_filename)
                    data = data.rename(columns={'time': 'TxTimestamp'})
                    for col in data.columns[1:]:
                        parse = re.match(r'(^[rt]x\S*)x([0-9\.]+)$', col)
                        if parse is None:
                            raise Exception(f"Plot-downloaded CSV column ({col}) is missing scale")
                        scale = float(parse.group(2))
                        data[col] = data[col] / scale
                        data = data.rename(columns={col: parse.group(1)[0].upper() + parse.group(1)[1:]})
                    data['TxPacketCount'] = np.zeros_like(data['TxTimestamp'])
                    data = data.rename(columns={'RxPower': 'RxBattPower'})
                    self.data[run_name][sweep_name] = data
            else:
                assert(False)
        for run_name in self.data:
            for sweep_name in self.data[run_name]:
                self.data[run_name][sweep_name]["TxAdmittance"] = self.data[run_name][sweep_name]["TxIMonPa"] / (self.data[run_name][sweep_name]["TxVMonPa"] + 1e-12)
                self.data[run_name][sweep_name]["TxPowerLoss"] = (self.data[run_name][sweep_name]["TxVMonPa"] * self.data[run_name][sweep_name]["TxIMonPa"]) - self.data[run_name][sweep_name]["RxBattPower"]
                self.data[run_name][sweep_name]["TxAdmittanceTarget"] = (32000 + np.maximum(0, 9250 * (self.data[run_name][sweep_name]["RxVRect"] - 70) / 10)) * 1e-6
                self.data[run_name][sweep_name]["TxMaxIPA"] = self.data[run_name][sweep_name]["TxVMonPa"] * self.data[run_name][sweep_name]["TxAdmittanceTarget"]
                self.data[run_name][sweep_name] = self.data[run_name][sweep_name].to_records(index = False)

    def _parse_file_cli(self, sweep_name, toplevel_filename):
        file_base = os.path.basename(toplevel_filename)
        run_name = file_base[len('tx'):file_base.index('.csv')]
        if run_name == "":
            run_name = "_0"
        csv_tx = toplevel_filename
        csv_rx = toplevel_filename.replace('tx', 'rx')
        self._parse_tx_rx(sweep_name, run_name, csv_tx, csv_rx)

    def _parse_file_gui(self, sweep_name, toplevel_filename):
        dir_name = os.path.dirname(toplevel_filename)
        file_base = os.path.basename(toplevel_filename)
        run_name = str(int(file_base[len('TX_'):len('TX_0000')]))
        csv_tx = os.path.join(dir_name, file_base)
        csv_rx = csv_tx.replace('TX', 'RX')
        self._parse_tx_rx(sweep_name, run_name, csv_tx, csv_rx)

    def _parse_tx_rx(self, sweep_name, run_name, csv_tx, csv_rx):
        data_tx = pandas.read_csv(csv_tx)
        data_rx = pandas.read_csv(csv_rx)

        data_tx = data_tx.rename(columns=TX_RENAME_COLUMNS)
        data_rx = data_rx.rename(columns=RX_RENAME_COLUMNS)

        data_tx['Power'] = data_tx['VMonPa'] * data_tx['IMonPa']
        data_rx['BattPower'] = data_rx['VMonBatt'] * data_rx['IBattery']

        if '_HostTime' in data_tx:
            data_tx = data_tx.add_prefix('Tx')
            data_rx = data_rx.add_prefix('Rx')
            run_data = pandas.merge_asof(
                data_tx,
                data_rx,
                left_on="Tx_HostTime",
                right_on="Rx_HostTime",
                direction="nearest"
            ).ffill()
        else:
            min_len = min(len(data_tx), len(data_rx))
            run_data = pandas.concat([data_tx.head(min_len).add_prefix('Tx'), data_rx.head(min_len).add_prefix('Rx')], axis=1)

        if sweep_name in self.data[run_name]:
            raise Exception(f"Duplication: Sweep Name ({sweep_name}) and Run Name ({run_name})")
        if len(run_data) == 0:
            # It's nice to be able to assume that there's at least one row in each run (e.g. so can take average/maximum without special-casing 0-length runs)
            run_data.loc[0] = [0] * len(run_data.columns)
        self.data[run_name][sweep_name] = run_data

    def data_types(self):
        return list(list(self.data.items())[0][1].items())[0][1].dtype

    def _plot_trace(self, x, run_data, y_key, scale=1, label_prefix="", label_suffix="", linestyle='-'):
        label = f"{label_prefix}{LABELS[y_key].pretty}{"" if scale == 1 else f" * {scale}"}{label_suffix}"
        plt.plot(x, run_data[y_key] * scale, label=label, c=LABELS[y_key].color, linestyle=linestyle)

    def print_coilbot_alarms(self, sweep_name):
        def pretty_alarms(x):
            return ', '.join(set(x['Alarm'].replace(', [<DeviceID.TX: 1>]: Alarm: RX is in alarm state', '').replace('[<DeviceID.RX_1: 2>]: Alarm: ', '').split(', ')))
        def summarize_alarms(data):
            return list(f"{x['Distance']}mm: {pretty_alarms(x)}" for x in filter(lambda x: type(x['Alarm']) is str, data))
        for run_name in self.data:
            if "complete_" not in run_name:
                continue
            print(sweep_name, run_name)
            pprint.pp(summarize_alarms(self.data[run_name][sweep_name]), width=150)
            print()

    def _coilbot_group_names(self):
        group_names = set()
        for run_name in self.data:
            if run_name.startswith('walkaway'):
                group_names.add(run_name[:-len('_XXmm.csv')])
            else:
                group_names.add(run_name[:run_name.index('/')])
        return group_names

    def plot_coilbot_comparison(self, sweep_names):
        sweep_name_to_short_name = { sweep_name: f"#{idx}" for idx, sweep_name in enumerate(sweep_names) }
        for group_name in sorted(self._coilbot_group_names()):
            plt.figure()
            for idx, sweep_name in enumerate(sweep_names):
                xs, ys = [], collections.defaultdict(list)
                no_data = True
                for run_name in self.data:
                    if "complete_" in run_name:
                        continue
                    if not run_name.startswith(group_name):
                        continue
                    if sweep_name not in self.data[run_name]:
                        continue
                    no_data = False
                    run_data = self.data[run_name][sweep_name]
                    num_idx = run_name.index('mm') - 2
                    xs.append(int(run_name[num_idx:num_idx+2]))
                    for key in run_data.dtype.names:
                        ys[key].append(np.average(last_ten_percent(run_data[key])))
                if no_data:
                    log.warning(f"No data for {group_name} for {sweep_name}")
                    continue
                for key in ys:
                    ys[key] = np.array(ys[key])
                label_prefix = f"[{sweep_name_to_short_name[sweep_name]}] "

                linestyle = MATPLOTLIB_LINESTYLES[idx]
                self._plot_trace(xs, ys, 'TxVMonPa', linestyle=linestyle, label_prefix=label_prefix)
                self._plot_trace(xs, ys, 'TxIMonPa', scale=10, linestyle=linestyle, label_prefix=label_prefix)
                self._plot_trace(xs, ys, 'TxTMonPa', linestyle=linestyle, label_prefix=label_prefix)
                self._plot_trace(xs, ys, 'RxVRect', linestyle=linestyle, label_prefix=label_prefix)
                self._plot_trace(xs, ys, 'RxBattPower', scale=0.1, linestyle=linestyle, label_prefix=label_prefix)

            ax = plt.gca()
            ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))

            title = f"{group_name}\n" + "\n".join([f"Sweep {short_name}: {sweep_name}" for sweep_name, short_name in sweep_name_to_short_name.items()])
            plt.title(title)
            plt.xlabel("Distance (mm)")
            plt.legend()
            plt.show()

    def plot_run(self, sweep_name, run_name):
        plt.figure()
        run_data = self.data[run_name][sweep_name]
        t = timestamp_to_t(run_data)
        self._plot_trace(t, run_data, 'TxVMonPa')
        self._plot_trace(t, run_data, 'TxIMonPa', scale=10)
        # self._plot_trace(t, run_data, 'TxMaxIPA', scale=10)
        # self._plot_trace(t, run_data, 'TxAdmittance', scale=1000)
        # self._plot_trace(t, run_data, 'TxPower', scale=0.1)
        # self._plot_trace(t, run_data, 'TxPowerLoss')
        # self._plot_trace(t, run_data, 'TxIMonGateDriver', scale=100)
        # self._plot_trace(t, run_data, 'TxTMonPa')
        self._plot_trace(t, run_data, 'TxFlags', scale=10)
        self._plot_trace(t, run_data, 'RxFlags')
        self._plot_trace(t, run_data, 'RxVRect')
        # self._plot_trace(t, run_data, 'RxVMonBatt')
        # self._plot_trace(t, run_data, 'RxTBoard')
        self._plot_trace(t, run_data, 'RxIBattery', scale=10)
        self._plot_trace(t, run_data, 'RxBattPower', scale=0.1)
        self._plot_trace(t, run_data, 'RxTargetIBatt')
        plt.xlabel("Time (s)")
        plt.title(f"Run {run_name} from \"{sweep_name}\"")
        plt.legend(loc='lower right')
        plt.grid(linestyle='--')
        plt.show()

    def _find_missing_run(self, sweep_name):
        possible = set(range(101))
        found = {int(r) for r in self.data if sweep_name in self.data[r]}
        missing = possible - found
        assert(len(missing) >= 1)
        if len(missing) == 1:
            x = list(missing)[0]
            log.info(f"Found the wrap-point (missing entry in runset): {x}")
            return x
        elif max(found) == 100:
            log.info(f"Found all 101 logs somehow...")
        else:
            log.info(f"Only found logs: 0 - {max(found)}")

    @staticmethod
    def _wrap_adjusted_run(missing_run, run_name):
        run = int(run_name)
        return run if ((missing_run is None) or (run < missing_run)) else run - 101

    @staticmethod
    def _unwrap_adjusted_run(run):
        return str(run) if run >= 0 else str(run + 101)

    def plot_runset(self, sweep_name, min_duration=-1, x='Run'):
        missing_run = self._find_missing_run(sweep_name)
        entries = collections.defaultdict(list)
        key_customizations = {
            'TxIMonGateDriver': ("Avg of last 90%", np.average, 1000),
            'TxAdmittance': ("Avg of last 90%", np.average, 1000),
            'TxTMonPa': ("Max", np.max, 1),
            'TxPowerLoss': ("Max", np.max, 1),
            'RxVMonBatt': ("Max", np.max, 1),
            'RxIBattery': ("Avg of last 90%", np.average, 1),
            'RxVRect': ("Max", np.max, 1),
        }
        for wrap_adjusted_run in sorted(self._wrap_adjusted_run(missing_run, x) for x in self.data):
            run = self._unwrap_adjusted_run(wrap_adjusted_run)
            run_data = self.data[run][sweep_name]
            if run_duration(run_data) < min_duration:
                continue
            entries['Run'].append(wrap_adjusted_run)
            for key in key_customizations:
                if key_customizations[key][0] == 'Max':
                    entries[key].append(key_customizations[key][1](run_data[key]))
                else:
                    entries[key].append(key_customizations[key][1](last_ten_percent(run_data[key])))
            entries['Duration'].append(run_duration(run_data))
        for key in entries:
            entries[key] = np.array(entries[key])
        plt.figure()
        x_scale = 1 if x == 'Run' else key_customizations[x][2]
        linestyle = '-' if x == 'Run' else ''
        for y in key_customizations:
            if y != x:
                self._plot_trace(entries[x] * x_scale, entries, y, scale=key_customizations[y][2], label_suffix=f" [{key_customizations[y][0]} of run]", linestyle=linestyle)
        self._plot_trace(entries[x] * x_scale, entries, 'Duration', linestyle=linestyle)
        if x == 'Run':
            plt.gca().format_coord = lambda x, y: f"run = {float(self._unwrap_adjusted_run(x)):.1f}, y = {y:.1f}"
            plt.xlabel("Wrap-Adjusted Run # (hover to see original)")
        else:
            plt.xlabel(f"{x} * {key_customizations[x][2]} [{key_customizations[y][0]} of run]")
        ax = plt.gca()
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
        plt.title(sweep_name)
        plt.legend()
        plt.show()

    def plot_each_run(self, sweep_name, starting_run=None):
        if "coilbot" in sweep_name:
            for run in sorted(self.data):
                if run.startswith("longitudinal_1") and ("complete" not in run) and (sweep_name in self.data[run]):
                    self.plot_run(sweep_name, run)
        elif sweep_name.startswith('tx'):
            for run in self.data:
                self.plot_run(sweep_name, run)
        else:
            missing_run = self._find_missing_run(sweep_name)
            for wrap_adjusted_run in sorted(self._wrap_adjusted_run(missing_run, x) for x in self.data):
                if starting_run is not None:
                    if wrap_adjusted_run < self._wrap_adjusted_run(missing_run, starting_run):
                        continue
                run = self._unwrap_adjusted_run(wrap_adjusted_run)
                self.plot_run(sweep_name, run)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Vendor log plotter")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--coilbot-alarms", action='store_true', help="Print coilbot alarm summaries")
    group.add_argument("--plot-coilbot", action='store_true', help="Plot coilbot sweep summaries, comparing each sweep")
    group.add_argument("--plot-run", type=str, help="Plot a specific run")
    group.add_argument("--plot-runset", action='store_true', help="Plot a runset")
    group.add_argument("--plot-correlation", type=str, help="Plot correlation with the given column")
    group.add_argument("--plot-each-run", action='store_true', help="Plot each run individually starting from the earliest run")
    group.add_argument("--plot-each-run-starting-at", type=int, help="Plot each run individually starting from a specific run")

    parser.add_argument("files", nargs='*', help="Path to the data file(s) to process")
    args = parser.parse_args()

    data = Data(args.files)
    if args.plot_coilbot:
        data.plot_coilbot_comparison([os.path.basename(file) for file in args.files])
    if args.coilbot_alarms:
        for file in args.files:
            data.print_coilbot_alarms(os.path.basename(file))
    if args.plot_run:
        for file in args.files:
            data.plot_run(source_to_sweep_name(file), args.plot_run)
    if args.plot_runset:
        for file in args.files:
            data.plot_runset(source_to_sweep_name(file))
    if args.plot_correlation:
        for file in args.files:
            data.plot_runset(source_to_sweep_name(file), min_duration=20, x=args.plot_correlation)
    if args.plot_each_run:
        for file in args.files:
            data.plot_each_run(source_to_sweep_name(file))
    if args.plot_each_run_starting_at:
        for file in args.files:
            data.plot_each_run(source_to_sweep_name(file), starting_run=args.plot_each_run_starting_at)
