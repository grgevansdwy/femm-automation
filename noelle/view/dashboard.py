# =============================================================================
# dashboard.py
#
# The results figure: a 6x4 grid holding the line charts, the coil
# cross-section, and the input/output tables. Owns every matplotlib object in
# the program - nothing outside this file holds a pane or a Figure.
#
# This is the view layer. It reads models/ (it formats ConfigResult values for
# the tables) but never services/ or repositories/: the figure is handed to
# RunResultRepository.save_figure() by the composition root, so file I/O stays
# where it belongs.
# =============================================================================
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import numpy as np
from quantiphy import Quantity


class Dashboard:
    """The 6x4 results figure. One instance per run."""

    def __init__(self, run_id):
        # One colour per swept config, shared by its chart lines and its table
        # column header.
        self.cm = plt.get_cmap('gist_rainbow')
        # Where to drop the run-number label on the VPA chart; not an axis limit.
        self.VPA_CHART_Y_LIM = 250
        # Axis box (mm) for the coil cross-section pane, disp_pane.
        self.neg_x_dim = 0
        self.pos_x_dim = 250
        self.neg_y_dim = -20
        self.pos_y_dim = 30
        self._results_cells = None
        self.row_labels = None
        self.config_table = None
        self.results_table = None

        self.fig2, self.bxs = plt.subplots(6, 4, num=f'Sim-{run_id}')
        self.vpa_pane = self.bxs[0,3]
        self.ipa_pane = self.bxs[0,2]
        self.Iloop_pane = self.bxs[1,2]
        self.Itank_pane = self.bxs[2,2]
        self.Irc_pane = self.bxs[3,2]
        self.tank_tuning_pane = self.bxs[4,2]
        self.tank_lateral_tuning_pane = self.bxs[5,2]
        self.pbatt_pane = self.bxs[3,3]
        # NOTE: self.efficiency_pane is styled below but never plotted into - renders empty.
        self.efficiency_pane = self.bxs[1,3]
        self.vrect_pane = self.bxs[2,3]
        for i in range(6):
            for j in range(2):
                self.bxs[i,j].remove()
        self.bxs[4,3].remove()
        self.bxs[5,3].remove()
        # self.bs and self.bs2 are the SAME gridspec fetched twice; self.bs2 is redundant.
        self.bs = self.bxs[3, 0].get_gridspec()
        self.bs2 = self.bxs[3,0].get_gridspec()
        self.config_pane = self.fig2.add_subplot(self.bs[0:3, 0:2])
        self.results_pane = self.fig2.add_subplot(self.bs2[3:6, 0:2])
        self.disp_pane = self.fig2.add_subplot(self.bs[4:, 3])

    # ---- the only handles the rest of the program may hold -----------------

    @property
    def figure(self):
        """The Figure, for RunResultRepository.save_figure()."""
        return self.fig2

    @property
    def cross_section_pane(self):
        """disp_pane - femm_service sketches the coil geometry onto this."""
        return self.disp_pane

    def color_for(self, run_count, num_configs):
        """This config's colour, by its position in the sweep."""
        return self.cm(1. * run_count / num_configs)

    def plot_config(self, cfg, *, label, run_number):
        """One config's charts, read from cfg.sweep."""
        distances = [sp.distance for sp in cfg.sweep]
        vpa    = [sp.vpa for sp in cfg.sweep]
        ipa    = [sp.ipa for sp in cfg.sweep]
        iloop  = [sp.iloop for sp in cfg.sweep]
        itank  = [sp.itank for sp in cfg.sweep]
        irc    = [sp.irc for sp in cfg.sweep]
        vrect  = [sp.vrect for sp in cfg.sweep]
        pbatt  = [sp.pbatt for sp in cfg.sweep]
        tank_f = [sp.tank_f for sp in cfg.sweep]

        # ---- Plotting: every chart shares the x axis (RC distance in mm) and is
        # annotated with the run number so overlapping traces stay identifiable.
        #top_right.plot(distances, vlvss, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75)
        self.vpa_pane.plot(distances, vpa, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75, marker=".")
        self.ipa_pane.plot(distances, ipa, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75, marker=".")

        self.vrect_pane.plot(distances, vrect, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75, marker=".")
        #self.efficiency_pane.plot(distances, efficiency_tests, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75)


        self.Iloop_pane.plot(distances, iloop, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75, marker=".")
        self.Itank_pane.plot(distances, itank, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75, marker=".")
        self.Irc_pane.plot(distances, irc, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75)

        self.Iloop_pane.text(distances[-1]+5, iloop[-1], str(run_number), fontsize=(20/cfg.mag_normal), color=cfg.color)
        self.Itank_pane.text(distances[-1]+5, itank[-1], str(run_number), fontsize=(20/cfg.mag_normal), color=cfg.color)
        self.Irc_pane.text(distances[-1]+5, irc[-1], str(run_number), fontsize=(20/cfg.mag_normal), color=cfg.color)

        #self.tank_lateral_tuning_pane.plot(rc_x_translations, vratio_lateral_tests, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75)
        self.pbatt_pane.plot(distances, pbatt, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75, marker=".")


        label_pos_x = distances[0]-5
        label_pos_y = vpa[0]

        self.tank_tuning_pane.plot(distances, tank_f, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75, marker=".")

        if (vpa[0] < self.VPA_CHART_Y_LIM):
            for i in range(0,len(distances),1):
                if (vpa[i] > self.VPA_CHART_Y_LIM):
                    self.vpa_pane.text(distances[i]-5, vpa[i], str(run_number), fontsize=(11/cfg.mag_normal), color=cfg.color)
                    break

        self.vpa_pane.text(label_pos_x, label_pos_y, str(run_number), fontsize=(11/cfg.mag_normal), color=cfg.color)
        self.ipa_pane.text(distances[0]-5, ipa[0], str(run_number), fontsize=(11/cfg.mag_normal), color=cfg.color)
        #if(iloop[0] < 2):
        #    self.efficiency_pane.text(distances[0]-5, efficiency_tests[0], str(run_number), fontsize=(20/cfg.mag_normal), color=cfg.color)
        if(vrect[0] > 40 and vrect[0] < 100 ):
            self.vrect_pane.text(label_pos_x, vrect[0], str(run_number), fontsize=(20/cfg.mag_normal), color=cfg.color)

        # Old 3-D radiation-pattern experiment; that pane no longer exists.
        # if(radiation_pattern_testing):
            # radiation_pattern_pane.plot_surface(rad_x, rad_y, vlvs_radiated, label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75)
        # else:
            # rad iation_pattern_pane.plot(rad_x, vlvs_radiated[0], label=label, color=cfg.color, linewidth=3/cfg.mag_normal, alpha=0.75)

    def render_results_table(self, run, closest_mm):
        """The 24 output rows, formatted here from raw ConfigResult floats."""
        cells = []
        self.row_labels = [f'k13 @ {closest_mm}mm', f'k23 @ {closest_mm}mm', 'k12', f'M13 @ {closest_mm}mm', f'M23 @ {closest_mm}mm', 'M12', 'L_loop - just PCB (L1)','L_loop - full stack-up (L1)', 'L_tank  - just PCB (L2)', 'L_tank  - full stack-up (L2)','L_rc - just PCB (L3)', 'L_rc - full stack-up (L3)','Max flux in TC Ferrite (T)','Max flux in RC Ferrite (T)', 'Magnitude of normalized currents', f'Efficiency at  @ {closest_mm}mm', 'Loop ESR (mOhms)', 'Tank ESR (mOhms)', 'RC ESR (mOhms)','TC Ferrite Losses (W)', 'TC Shield Losses (W)', 'RC Ferrite Losses (W)', 'RC Shield Losses (W)', 'Total Losses (W)']

        # One row per `rows` entry, in the same order. Each pulls a raw value off every
        # ConfigResult and formats it here - the values themselves are plain floats.
        cells.append([f'{c.k13:.3f}'                 for c in run.configs])
        cells.append([f'{c.k23:.3f}'                 for c in run.configs])
        cells.append([f'{c.k12:.3f}'                 for c in run.configs])
        cells.append([Quantity(c.M13, 'H')           for c in run.configs])
        cells.append([Quantity(c.M23, 'H')           for c in run.configs])
        cells.append([Quantity(c.M12, 'H')           for c in run.configs])
        cells.append([Quantity(c.L_loop_solo, 'H')      for c in run.configs])
        cells.append([Quantity(c.L_loop_assembled, 'H') for c in run.configs])
        cells.append([Quantity(c.L_tank_solo, 'H')      for c in run.configs])
        cells.append([Quantity(c.L_tank_assembled, 'H') for c in run.configs])
        cells.append([Quantity(c.L_rc_solo, 'H')        for c in run.configs])
        cells.append([Quantity(c.L_rc_assembled, 'H')   for c in run.configs])
        cells.append([Quantity(c.tc_ferrite_flux, 'T')  for c in run.configs])
        cells.append([Quantity(c.rc_ferrite_flux, 'T')  for c in run.configs])
        cells.append([f'{c.mag_normal:.3f}'          for c in run.configs])
        cells.append([f'{c.efficiency*100:.3f}%'     for c in run.configs])
        cells.append([Quantity(c.loop_esr, 'Ohms')   for c in run.configs])
        cells.append([Quantity(c.tank_esr, 'Ohms')   for c in run.configs])
        cells.append([Quantity(c.rc_esr, 'Ohms')     for c in run.configs])
        cells.append([Quantity(c.tc_ferrite_loss, 'W')  for c in run.configs])
        cells.append([Quantity(c.tc_shield_loss, 'W')   for c in run.configs])
        cells.append([Quantity(c.rc_ferrite_loss, 'W')  for c in run.configs])
        cells.append([Quantity(c.rc_shield_loss, 'W')   for c in run.configs])
        cells.append([Quantity(c.total_loss, 'W')       for c in run.configs])


        self._results_cells = cells
        return cells

    def render_config_table(self, run, parameter_outputs, row_labels,
                            no_tank_ferrite, no_rc_ferrite, no_backplane):
        """The input table and the cross-section pane, including row pruning.

        Must run after render_results_table - both tables are drawn here.
        """
        cells = self._results_cells


        # Older single-table layout, superseded by results_table at the bottom.
        # bot_right.set_axis_off()
        # results_table = bot_right.table(cellText=config_results, rowLabels=rows,colColours=colors,colLabels=configs,loc='center', cellLoc='center')


        #Display of traces
        # Coil cross-section pane: fixed mm box so every config is drawn to the same scale.
        self.disp_pane.set_ylim(self.neg_y_dim, self.pos_y_dim)
        self.disp_pane.set_xlim(self.neg_x_dim, self.pos_x_dim)
        self.disp_pane.grid(axis='y', color='0.95')


        self.config_pane.set_axis_off()
        self.results_pane.set_axis_off()


        # CONFIG TABLE ROW PRUNING - drop rows carrying no information for this run.
        # param_names and parameter_outputs must be popped IN LOCKSTEP.
        # FRAGILE: these indices are hard-coded positions into the `parameters` dict, so
        # reordering a key up there silently deletes the wrong rows. The repeated
        # pop(0)/pop(5)/pop(10) calls each remove a contiguous run one at a time.
        # The flags also come from `parameters`, not from the coilsets actually simulated.
        param_names = list(row_labels)

        #remove config name from param list
        param_names.pop(0)
        parameter_outputs.pop(0)

        if(no_backplane):
            param_names.pop(10)
            param_names.pop(10)
            parameter_outputs.pop(10) #deletes 5th element
            parameter_outputs.pop(10) #deletes what was the 6th element before the first pop

        if(no_tank_ferrite and no_rc_ferrite):
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)
            #deletes param_names[1:6] (that's exclusive!)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
        elif(no_tank_ferrite):
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)
            param_names.pop(0)

            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
            parameter_outputs.pop(0)
        elif(no_rc_ferrite):
            param_names.pop(5)
            param_names.pop(5)
            param_names.pop(5)
            param_names.pop(5)
            param_names.pop(5)

            parameter_outputs.pop(5)
            parameter_outputs.pop(5)
            parameter_outputs.pop(5)
            parameter_outputs.pop(5)
            parameter_outputs.pop(5)

        # Both tables fill their whole pane rather than matplotlib's default centring.
        pane_bbox = [0, 0, 1, 1]

        #print(parameter_outputs)


        # Bold any cell differing from the config to its left, so deltas pop out.
        # CAUTION: the comparison casts with float(), so a string-valued parameter raises here.
        # the pruned labels, kept so callers (and tests) can see what survived
        self.config_row_labels = param_names
        self.config_table = self.config_pane.table(cellText=parameter_outputs, rowLabels=param_names,colColours=run.colors,colLabels=run.labels, cellLoc='center', bbox=pane_bbox)
        for (row, col), cell in self.config_table.get_celld().items():
            if (col > 0 and row > 0):
                if (float(cell.get_text().get_text()) != float(self.config_table[row, col-1].get_text().get_text())):
                    cell.set_text_props(fontproperties=FontProperties(weight='bold'))


        self.config_table.auto_set_font_size(False)
        #param_table.set_fontsize(12)

        # Shrink the fonts so a wide sweep still fits; PAD tightens the cell margins.
        for key,cell in self.config_table.get_celld().items():
            cell.PAD = 0.03
            if(key[0] > 0 and key[1] > -1): #This is a result cell
                cell.set_fontsize(5)
            else:
                cell.set_fontsize(5)


        #print(config_results)
        #print(rows)

        self.results_table = self.results_pane.table(cellText=cells, rowLabels=self.row_labels,colColours=run.colors,colLabels=run.labels, cellLoc='center', bbox=pane_bbox)

        self.results_table.auto_set_font_size(False)

        for key,cell in self.results_table.get_celld().items():
            cell.PAD = 0.03
            if(key[0] > 0 and key[1] > -1): #This is a result cell
                cell.set_fontsize(9)
            else:
                cell.set_fontsize(6)


        #top.set_ylabel('Flux Density underneath center turn of coil (T)')\
        # fig.set_size_inches(36, 18)
        #wide monitor resolution: 3440x1440

        #300dpi ->


    def finish(self):
        """Axis styling for every pane, then final canvas sizing."""
        # ===========================================================================
        # #self.fig2.suptitle('FEMM Analysis ' + ("{:%m-%d-%y}".format(datetime.datetime.today()))+'\nTest ID: '+timestamp, fontsize=12)

        self.vpa_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
        self.vpa_pane.set_ylabel('VPA (V)', alpha=.2)
        self.vpa_pane.set_title('VPA @ Max Power Point vs distance')
        self.vpa_pane.grid(axis='y', color='0.95')
        self.vpa_pane.grid(axis='x', color='0.95', alpha=.2)
        #self.vpa_pane.set_ylim(0, VPA_CHART_Y_LIM)
        self.vpa_pane.set_xticks(np.arange(0, 100.1, 5))
        #self.vpa_pane.tick_params(axis='both', which=u'both', alpha=.3)
        plt.setp(self.vpa_pane.get_xticklabels(), alpha=0.3)


        self.ipa_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
        self.ipa_pane.set_ylabel('IPA (A)', alpha=.2)
        self.ipa_pane.set_title('IPA @ Max Power Point vs distance')
        self.ipa_pane.grid(axis='y', color='0.95')
        self.ipa_pane.grid(axis='x', color='0.95', alpha=.2)
        self.ipa_pane.set_xticks(np.arange(0, 100.1, 5))
        plt.setp(self.ipa_pane.get_xticklabels(), alpha=0.3)

        self.Iloop_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
        self.Iloop_pane.set_title('Iloop @ Max Power Point vs distance')
        self.Iloop_pane.set_ylabel('Iloop Peak (A)', alpha=.2)
        #self.Iloop_pane.set_yticks(np.arange(0, 20.1, 5))
        #self.Iloop_pane.set_xticks(np.arange(0, 100.1, 50))
        self.Iloop_pane.grid(axis='y', color='0.95')
        self.Iloop_pane.grid(axis='x', color='0.95')
        self.Iloop_pane.set_xticks(np.arange(0, 100.1, 5))
        plt.setp(self.Iloop_pane.get_xticklabels(), alpha=0.3)
        plt.setp(self.Iloop_pane.get_xticklabels(), alpha=0.3)

        self.Itank_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
        self.Itank_pane.set_title('Itank @ Max Power Point vs distance')
        self.Itank_pane.set_ylabel('Itank Peak (A)', alpha=.2)
        #self.Itank_pane.set_yticks(np.arange(0, 20.1, 5))
        #self.Itank_pane.set_xticks(np.arange(0, 100.1, 50))
        self.Itank_pane.grid(axis='y', color='0.95')
        self.Itank_pane.grid(axis='x', color='0.95', alpha=.5)
        self.Itank_pane.set_xticks(np.arange(0, 100.1, 5))
        plt.setp(self.Itank_pane.get_xticklabels(), alpha=0.3)

        self.Irc_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
        self.Irc_pane.set_title('Irc @ Max Power Point vs distance')
        self.Irc_pane.set_ylabel('Irc Peak (A)', alpha=.2)
        #self.Irc_pane.set_yticks(np.arange(0, 20.1, 5))
        #self.Irc_pane.set_xticks(np.arange(0, 100.1, 50))
        self.Irc_pane.grid(axis='y', color='0.95')
        self.Irc_pane.grid(axis='x', color='0.95', alpha=.5)
        self.Irc_pane.set_xticks(np.arange(0, 100.1, 5))
        plt.setp(self.Irc_pane.get_xticklabels(), alpha=0.3)

        # self.efficiency_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
        # self.efficiency_pane.set_title('Efficiency @ Max Power point vs distance')
        # self.efficiency_pane.set_ylabel('Efficiency', alpha=.2)
        # self.efficiency_pane.grid(axis='y', color='0.95')
        # self.efficiency_pane.grid(axis='x', color='0.95', alpha=.5)
        # self.efficiency_pane.set_xticks(np.arange(0, 100.1, 5))
        # plt.setp(self.efficiency_pane.get_xticklabels(), alpha=0.3)
        # #self.efficiency_pane.set_ylim(0, 2)

        self.vrect_pane.set_xlabel('RC longitudinal offset (mm)', alpha=.2)
        self.vrect_pane.set_title('Vrect @ Max Power Point vs distance')
        self.vrect_pane.set_ylabel('Vrect', alpha=.2)
        self.vrect_pane.grid(axis='y', color='0.95')
        self.vrect_pane.grid(axis='x', color='0.95', alpha=.5)
        self.vrect_pane.set_xticks(np.arange(0, 100.1, 5))
        #self.vrect_pane.set_ylim(0, 100)
        plt.setp(self.vrect_pane.get_xticklabels(), alpha=0.3)

        self.tank_lateral_tuning_pane.set_xlabel('RC latitudinal offset (mm)', alpha=.2)
        self.tank_lateral_tuning_pane.set_title('Tank De-tuned Resonant Frequency (Hz) vs RC lateral offset')
        self.tank_lateral_tuning_pane.set_ylabel('MHz', alpha=.2)
        self.tank_lateral_tuning_pane.grid(axis='y', color='0.95')
        self.tank_lateral_tuning_pane.grid(axis='x', color='0.95', alpha=.5)
        plt.setp(self.tank_lateral_tuning_pane.get_xticklabels(), alpha=0.3)

        self.pbatt_pane.set_xlabel('RC latitudinal offset (mm)', alpha=.5)
        self.pbatt_pane.set_title('Max Output Power vs RC Distance)')
        self.pbatt_pane.set_ylabel('Power (W)', alpha=.2)
        self.pbatt_pane.grid(axis='y', color='0.95')
        self.pbatt_pane.grid(axis='x', color='0.95', alpha=.5)
        self.pbatt_pane.set_xticks(np.arange(0, 100.1, 5))
        plt.setp(self.pbatt_pane.get_xticklabels(), alpha=0.3)

        self.tank_tuning_pane.set_xlabel('RC longitudinal offset (mm)', alpha=.2)
        self.tank_tuning_pane.set_title('Tank De-tuned Resonant Frequency vs RC y-distance')
        self.tank_tuning_pane.set_ylabel('MHz', alpha=.2)
        self.tank_tuning_pane.grid(axis='y', color='0.95')
        self.tank_tuning_pane.grid(axis='x', color='0.95', alpha=.2)
        self.tank_tuning_pane.set_xticks(np.arange(0, 100.1, 5))
        plt.setp(self.tank_tuning_pane.get_xticklabels(), alpha=0.3)


        #self.vrect_pane.set_ylim(0, 2)

        # if(radiation_pattern_testing):
        #     radiation_pattern_pane.set_xlabel('RC longitudinal offset (mm)')
        #     #radiation_pattern_pane.set_title('Itank/Irc vs Longitudinal offset')
        #     radiation_pattern_pane.set_ylabel('RC latitudinal offset (mm)')
        #     radiation_pattern_pane.set_zlabel('result')
        #     radiation_pattern_pane.grid(axis='z', color='0.95')
        # else:
        #     radiation_pattern_pane.set_ylabel('VL/VS')
        #     radiation_pattern_pane.set_title(f'VL/VS @ {rc_test_distances[0]}mm offset, lateral sweep')
        # radiation_pattern_pane.set_xlabel('RC lateral offset (mm)')
        # radiation_pattern_pane.grid(axis='y', color='0.95')
        # radiation_pattern_pane.grid(axis='x', color='0.95')


        # `rows` must stay in the SAME ORDER as the config_results.append calls below -
        # the table zips them positionally.
        # Wide-monitor canvas, saved as SVG so tables stay legible when zoomed.
        # subplots_adjust is hand-tuned to the 6x4 layout above.
        self.fig2.set_size_inches(36, 12)
        # fig.tight_layout(pad=1.0, w_pad=1.0, h_pad=1.0)
        # fig.savefig('./results/ID'+timestamp+'/result_chart.svg', dpi=300, format="svg")

        self.fig2.subplots_adjust(left=0.062, bottom=0.036, right=.99, top=.979, wspace=.15, hspace=.5)

    def show(self):
        """Blocks until the figure window is closed."""
        plt.show()
