# FEMM Automation — Codebase Overview

> **Purpose of this document:** a complete map of the `femm_automation` working folder — what every
> file is, how the pieces fit together, and where the seams are for the intended next step
> (a GUI + a real automation layer on top of FEMM).

---

## 1. The Big Picture

This folder is **not one project**. It is two separate engineering repos, dropped side by side,
that happen to attack the same problem from two directions:

```
femm_automation/
├── femm_coil_simulation/   ← "Noel's repo": the mature, large, script-driven FEMM WPT simulator
└── genki/                  ← "Genki's repo": lab/bench tooling + a cleaner, smaller FEMM rewrite
```

Neither directory currently contains a `.git` folder — they are working copies, not live clones.
There is no `README`, no `requirements.txt`, no `pyproject.toml`, and no test suite anywhere in
`femm_coil_simulation`. The only prose documentation in the whole tree is
`femm_coil_simulation/HOW-TO.md`.

### The engineering problem being solved

Both repos exist to design and validate **6.78 MHz wireless power transfer (WPT) coils** for what
is clearly a Vendor-style robot charging system. The physical system under study is a **three-coil
resonant link**:

| Coil | Role | Naming in code |
|---|---|---|
| **Loop** | Drive / feed coil, magnetically couples the amplifier into the tank | `loop`, group `1` |
| **Tank** | Transmitter resonator, high-Q, does the actual power radiating | `tank`, group `2` |
| **RC** | Receive coil on the robot, moves relative to the transmitter | `rc`, group `3` |

Plus the parasitic structures that ruin everything: **ferrite plates** (flux shaping),
**aluminium/copper shields and backplanes** (detuning + eddy losses), and **PCB stackup**
(trace width, copper weight, layer count, dielectric thickness).

### The simulation strategy (this is the core idea of the whole repo)

FEMM is a 2D magnetostatic/harmonic solver. These coils are round, so everything is modelled
**axisymmetric** (`femm.mi_probdef(..., 'axi', ...)`) — you draw a radial cross-section and FEMM
revolves it. Each *turn* of each coil is declared as its **own FEMM circuit** (`loop0`, `loop1`,
`tank0`, …). That is the key trick: by energising one coil's circuits and reading the flux linkage
reported on *every* circuit, you extract the full self- and mutual-inductance matrix in one solve:

```
        ┌ L11  M12  M13 ┐      L11 = loop self-inductance
   M =  │ M21  L22  M23 │      M13 = loop↔RC mutual
        └ M31  M32  L33 ┘      k   = M / sqrt(Li·Lj)
```

Coils are separated for "solo" inductance measurements by **translating a whole FEMM group 300 mm
away** rather than deleting it — cheap, and keeps the mesh geometry stable.

From that matrix, the code stops being an EM problem and becomes a **circuit problem**: tuning
capacitors are computed for the target resonant frequencies, First-Harmonic-Approximation circuit
equations are solved for coil currents / `Vrect` / `Vpa` / efficiency, and (optionally) LTspice is
invoked to find the maximum power point. Ferrite and shield **block losses** and peak **flux density**
(saturation check) are read back out of the same FEMM solution.

### The two repos, contrasted

| | `femm_coil_simulation` (Noel) | `genki` (Genki) |
|---|---|---|
| Scope | Full 3-coil WPT system, end to end | Small focused sims + bench/lab tooling |
| Style | One 1300-line top-level script, module-level globals | Dataclasses, `argparse`, `logging`, `__main__` guards |
| Config | Python literals edited in-place | CLI flags + dataclass tables |
| Deps | Undeclared | PEP 723 inline metadata (`# /// script`) → `uv run`-ready |
| Output | Timestamped SVG + log dir per run | stdout / matplotlib |
| Maturity | Battle-tested, 114 archived result runs | Cleaner, but partial (`femm_toroid.py` is an unreviewed copy of Noel's) |

### Where this is going (the automation/GUI goal)

Today a "simulation" means: open the script, hand-edit constants near the top, run it, watch FEMM
draw, wait, then open an SVG. Everything that a GUI would need to drive — geometry, materials,
sweep ranges, output selection — is currently **hard-coded module-level state in an import-time
side-effecting script**. Section 5 lists the specific obstacles.

---

## 2. `femm_coil_simulation/` — the primary simulator

### 2.1 Documentation

| File | What it is |
|---|---|
| `HOW-TO.md` | The only real doc in the repo. Explains prerequisites, the 5-phase execution flow of `compare_coilsets.py`, and how to run it. Slightly stale (it says `rc_test_distances` is hardcoded to `[30]`; it is actually a 12-point sweep). **Read this first.** |

### 2.2 The core three files

These are the heart of the repo. Everything else orbits them.

#### `compare_coilsets.py` — 1320 lines — *the main program*

The full 3-coil WPT sweep. There are no functions and no `if __name__ == '__main__'`; it is a flat
script that executes top to bottom on import. Structure:

1. **Constants block (lines ~20–90):** `freq = 6.78e6`, source/load impedances including the
   open-circuit matching network (`L_match_OC`, `C_match_OC`), target currents per coil, coax cable
   models (RG8x / RG213 / RG214), `Vpa = 200`, backplane geometry, plot window bounds.
2. **Result accumulators (lines ~90–165):** roughly **90 module-level empty lists** (`k12s`, `M13s`,
   `L_rc_displays`, `tc_ferrite_losses`, `efficiency_tests`, …) appended to inside the main loop.
   This is the single biggest structural liability in the file.
3. **Plot scaffolding (lines ~165–190):** builds a 6×4 `matplotlib` subplot grid, removes/merges
   cells to carve out two large table panes (`config_pane`, `results_pane`) and a geometry preview
   pane (`disp_pane`).
4. **`parameters` dict (lines ~190–265):** every design variable as a *list of values*. Fed through
   `itertools.product` to generate a full-factorial `coilset_array` of `CoilSet` objects. This is the
   built-in parameter-sweep mechanism.
5. **Named special configs (lines ~285–360):** `Gen3_4mm_RC_ferrite`, `Gen3_2mm_61_RC_ferrite`,
   `new_coil_8_turn`, … built by `cc.copy_coilset(...)` then mutating fields.
   **Note line ~365: `coilset_array = [cc.TR302_and_225_000056]` overwrites everything generated
   above.** The factorial sweep is effectively disabled and re-enabled by commenting lines.
6. **Main loop (lines ~380–1300):** per `CoilSet` —
   - register per-turn FEMM circuits; pick the coax impedance model
   - set ferrite material properties (µ, conductivity, loss angle — FJ7 `0.012544` vs `-61` `0.0073`)
   - draw RC, tank, loop via `nff.draw_and_plot_coil_traces`
   - measure **solo** self-inductances (other coils shifted 300 mm away)
   - add ferrites / shields / backplanes, re-measure **fully-assembled** inductances
   - compute tuning caps `C1`,`C2`,`C3` from `nff.resonant_capacitance`
   - **distance sweep** `rc_test_distances = [5,10,…,50,75,100]` mm: translate the RC group, re-solve,
     extract `M12/M13/M23` and `k` values, resonant-frequency shift, currents, `Vrect`, `Vpa`, `Ipa`,
     admittance, `Pbatt`, efficiency, ferrite peak flux, and per-block losses
   - optionally hand a `lta.Config` to the LTspice automator for MPP search (line ~653)
   - log everything through `nff.print_and_log`
7. **Output (tail):** sets the figure to 36×12 in, saves
   `./results/ID{DDHHMM}/ID{DDHHMM}.svg` at 400 dpi, `plt.show()`, `femm.closefemm()`.

#### `femm_model.py` — 531 lines — *the FEMM abstraction layer* (`nff`)

The reusable library. **This is the file a GUI/automation layer should be built on.**

- `@dataclass CoilSet` — ~55 fields describing an entire 3-coil design: per-coil radius, turns,
  parallel traces, layers, trace/turn spacing, trace width, trace thickness, PCB thickness, tuning
  frequency; ferrite presence/offset/thickness/µ/outer-radius for TC and RC; shields; backplanes;
  coax choice; loss angles. Every script's notion of "a design" is this object.
- `@dataclass self_and_mutual_inductance_container` — the 3×3 `L`/`M` result.
- **Drawing:** `draw_coil_traces` (geometry only) and `draw_and_plot_coil_traces` (geometry +
  matplotlib preview). Handles multi-layer PCBs, parallel sub-traces, wire-vs-trace cross sections.
  `draw_rectangular_material` / `..._no_plot` place ferrite/AluFoil/Copper slabs.
  `get_winding_center` computes per-trace radii.
- **Extraction:** `loop_inductance`, `loop_ESR`, `coil_get_prop`, and the big
  `get_inductances(...)` which drives one coil at a time, translates groups, solves, and returns the
  full matrix. `get_peak_flux_density_of_rect` (ferrite saturation), `get_block_losses`,
  `get_coil_losses`.
- **Circuit maths:** `resonant_capacitance`, `resonant_frequency`, `set_circuit_current(s)`,
  `get_turn_current` (splits a target current across layers × parallel traces).
- **Validation:** `check_valid_coil` / `check_valid_coilset` — geometric sanity (do the traces
  physically overlap?). Called before every run; raises on failure.
- **Setup:** `init_femm()` — `openfemm(1)`, new magnetics doc, `mi_probdef(6.78e6, 'millimeters',
  'axi', 1e-8, 1, 30)`, loads Air/Copper, defines `Ferrite` and `AluFoil` (σ = 37.7 MS/m) materials,
  places the far-field air label and an ABC boundary at r = 400 mm.
- `print_and_log` — tee to stdout and to `./results/ID{ts}/log_{ts}.txt`.

> ⚠️ **Circular import:** `femm_model` imports `coil_configs`, and `coil_configs` imports
> `femm_model`. It works only because of import ordering. Worth untangling.

#### `coil_configs.py` — 984 lines — *the design library* (`cc`)

A catalogue of concrete, named `CoilSet` instances — the record of every hardware generation that
has been simulated:

- `named_configs = [...]` — a large mostly-commented-out list of historical designs.
- Live configs: **`Gen3`** (RevA1 with ferrite), **`Gen2_coil`**, **`base_coil`**,
  `plated_slot_coil`, **`TR302_and_OC150`** (300 W), **`TR302_and_mithril`** (50 W),
  **`TR302_and_225_000056`** (currently the active config), `seedcoil`.
- `create_iter_base_coil(variables)` — builds a `CoilSet` from a variable tuple, for sweeps.
- `copy_coilset(coilset_to_copy)` — the standard "clone then mutate" idiom used everywhere.

This file is effectively a hand-maintained database. **It is the natural backing store for a GUI's
"load / save / duplicate design" feature.**

### 2.3 Supporting material library

| File | What it is |
|---|---|
| `ferrite_and_core_info.py` (390 lines) | The **materials & cores database**. Dataclasses `toroidal_core_shape` (od/id/h + `volume()`), `planar_e_core_shape` (with an ASCII diagram of the geometry, `cavity_length()`), `planar_plate_core`, `ferrite` (µi, µ″, resistivity, coercivity), `toroid_core`, `inductor`. Contains dozens of real parts: Micrometals **T12…T250** toroids, Fair-Rite **59xx/26xx** series, Ferroxcube **E58/PLT58**, planar **I14/I18/I22/I43/I64** plates, and ferrite mixes `-0,-1,-2,-5,-6,-7,-10`, Fair-Rite 95, FJ7, `-61`. Also FEMM helpers: `draw_planar_e_core` (with air gap + fillets + orientation), `draw_planar_plate`, `add_ferrite_to_sim`. |
| `toroid_parameter_selector.py` (27 lines) | Pure analytics, no FEMM. Brute-forces core × ferrite × turns × stack-count against `L = µ0·µi·N²·h·ln(od/id)/2π` to find every combination hitting a target inductance (0.9 µH ±10 %) that also physically fits the winding window. Prints matches. **A good candidate to become a GUI "core finder" tab.** |

### 2.4 Standalone component simulators

Single-purpose scripts that reuse `nff` / `fci`. All are edit-the-constants-then-run.

| File | What it is |
|---|---|
| `planar_inductor.py` (199 lines) | PCB planar inductor on an E-core + I-plate at 100 kHz. Inputs at the top: core (`fci.E18_ungapped`), plate (`fci.I18`), ferrite (`fci.Fair_rite_95`), turns, air gap in µm, copper weight, layer count, PCB thickness, `coupled_inductor` flag. |
| `Inductor_and_Xfrmr Sims/planar_inductor.py` | **A divergent copy** of the above (contents differ). Origin/intent unclear — likely a fork mid-experiment. |
| `toroidal_inductor.py` (246 lines) | Toroid on a real core (`fci.T184` + mix `-6`) at 6.78 MHz, with stacked cores, wire width/thickness, turn count. Has a `run_simulation(...)` function — **the only top-level script here with a proper entry point.** |
| `archive---toroidal_inductor.py` (245) and `-toroidal_inductor.py` (245) | **Byte-identical to each other**, and both older than `toroidal_inductor.py`. Dead copies; the leading-dash filename is also awkward to handle on a shell. |
| `transformer.py` (193 lines) | Wire-wound transformer modelling. Contains the cleanest small helpers in the repo: `draw_round_wire` (2 nodes + 2 arcs), `draw_coil_grid` (a rectangular winding window filled with discrete turns), `draw_wire_column`, `draw_polygon`. |
| `stacked_air_core_inductor_designer.py` (118) | Minimal, self-contained (no `nff`). Stacked flat-strip air-core inductor at 6.78 MHz / 45 A peak. Wire 10 × 2 mm, 4 turns, 3 mm spacing. Directly calls `openfemm`/`mi_probdef`/`makeABC`. |
| `planar_air_core_inductor_designer.py` (65) | Shortest FEMM script here. Uses `nff.init_femm()`, redefines Ferrite to µ = 125 / loss angle 0.012544, stacks AluFoil + ferrite slabs, and draws a 3-turn 45 A coil. |
| `genki_femm_simple.py` (261 lines) | **The clean-room rewrite, and the most instructive file in the repo.** Same physics as `compare_coilsets.py` for loop+tank only, but with private helpers (`_get_winding_radii_turns_and_parallel_traces`, `_draw_and_plot_coil_traces`, `_get_inductance`, `_get_inductances`) and a real `main()`. Its `CoilSet` is a trimmed ~20-field version. Carries TODO links to the FEMM user manual and pyfemm PDF. **This is the best structural template for a rewritten, GUI-drivable core.** |

### 2.5 `Branches_and_one_offs/` — the experiment graveyard

~33 MB of scratch work. Most `.py` files here descend from David Meeker's official pyFEMM
"Wound Copper Coil with an Iron Core" demo (the header comment is copy-pasted throughout).

| File | What it is |
|---|---|
| `inductance+coupling.py` (647) / `inductance+coupling_axi.py` (524) | The evolutionary ancestors of `compare_coilsets.py` — planar vs axisymmetric formulations of the coupling extraction. |
| `embedded_loop_exploration.py` (603) | Study of embedding the drive loop within the tank PCB stackup. |
| `tank_and_loop-ferrite.py` (421) | Tank + loop with ferrite, pre-refactor. |
| `radial_bar_ferrite_sweep.py` (340) | Sweeps *discrete radial ferrite bars* (count/width) instead of a solid plate — the `num_discrete_ferrites` idea still referenced by dead code in the main script. |
| `coil-ferrite-copper.py` (297) + `coil-ferrite-copper-snapshot_may14.py` (287) | A design and its dated snapshot. Real geometry: `inner_r=61.928`, `outer_r=90.376`, 5 tank turns, 10 A. |
| `demo2.py` (126) | Near-stock pyFEMM demo, kept as reference. |
| `test.py` (4) | Two trivial `print` loops. Scratch. |
| `*.FEM` / `*.ans` / `*.FEE` / `*.res` | Saved FEMM models and solved solutions: `coax_sim`, `multi_coax_sim`, `multiconductor_sim` (18.5 MB), `twisted_pairs`, `ferrite+coils`, `wip_pyfemm_coils`, `electrostatics` (an electrostatics problem, note the different extensions). |
| `ferrite_150x6_z-10mm` (5.2 MB) | Extension-less solution dump for a named geometry case. |
| `run_1_results.png`, `scratch` (0 bytes) | Leftovers. |

### 2.6 Outputs and stray state

| Path | What it is |
|---|---|
| `results/` (~59 MB, 114 dirs) | One directory per run. `ID{DDHHMM}/` names are **day-hour-minute only — no month or year, so they collide and sort meaninglessly.** Each holds an `.svg` composite figure (104 total) and a `log_*.txt` (114 total). Four dirs are hand-named milestones: `Gen2_baseline`, `Gen3 as designed May29_25`, `Gen3 POR Jun10_25`, `Ujjwals_DD_120mm_RC`. **No machine-readable results anywhere — no CSV, no JSON.** Every number ever computed is locked inside an SVG or a prose log. |
| `tmp.fem`, `tmp.ans` (1.2 MB) | Last FEMM model + solution, written to the repo root by a run. Untracked scratch. |
| `ltspiceautomator/` | **Empty.** Declared in `.gitmodules` as a submodule of `git@bitbucket.org:example-org/ltspiceautomator.git`, never initialised. `compare_coilsets.py` does `import ltspiceautomator.ltspice_automator as lta` at line 22 — so **the main script cannot currently be imported or run at all** without cloning that private repo. |
| `.gitmodules` | The submodule declaration above. |
| `.gitignore` | A generic template (Node, Java, Maven, JetBrains…) barely adapted. Does **not** ignore `*.ans`, `*.fem`, `results/`, or `__pycache__/` — which is why the multi-megabyte solution files are sitting in the tree. |

---

## 3. `genki/` — bench, lab, and clean-sim tooling

Different author, different discipline. Nearly every script here starts with **PEP 723 inline
dependency metadata**:

```python
# /// script
# dependencies = ["coloredlogs", "numpy", "pyfemm"]
# ///
```

so they run standalone with `uv run script.py` — no shared virtualenv needed. Most use `argparse`,
`logging` + `coloredlogs`, and a `if __name__ == '__main__':` guard.

### 3.1 `em_sim/` — the parallel FEMM implementation

| File | What it is |
|---|---|
| `femm_coils.py` (282) | **The clean counterpart to `compare_coilsets.py`.** Loop+tank inductance/coupling extraction, `--freq-khz` on the CLI. Notable design: `TraceTrapezoid` models the *etched* trapezoidal cross-section of a real PCB trace (45°/90° sidewalls) rather than a rectangle; `TraceCircle` for round wire; `CoilType` enum maps coil → FEMM group; `Coil.draw()`, `CoilSet.draw()`; `Result` dataclass with `pprint()` emitting R11/R22/L11/L22/M12/k. **`PositionTweak` is a context manager** that translates FEMM groups on `__enter__` and restores them on `__exit__` — a much cleaner solution than the main repo's manual ±300 mm shifts. Includes the real `225-35 RevA` geometry (5 tank turns at r = 61.928…90.376 mm, 100/125 mil traces, 1 oz copper, 1.6 mm separation). |
| `femm_caps.py` (104) | Models the **physical resonant capacitors** (2.55 × 1.9 × 3.47 mm bodies) as copper blocks in the coil field, to capture their parasitic interaction. Imports `femm_coils`. |
| `femm_toroid.py` (177) | Toroid sim. Header is explicit: *"THIS CODE IS MOSTLY JUST COPIED FROM NOEL'S REPO. It's not been edited / reviewed yet."* A partial duplicate of `toroidal_inductor.py` with locally re-declared `toroidal_core_shape`/`ferrite` dataclasses (T157, mix `-6`). |

### 3.2 `LTspice_Automation/` — circuit-side automation

Drives LTspice headlessly via `spicelib`, closing the loop from FEMM's L/M values to real converter
behaviour.

| File | What it is |
|---|---|
| `1KW_System/automate.py` (387) + `1KW_System.asc` | The main circuit automation. Runs the 1 kW system schematic, does harmonic analysis (harmonics 5 → 300 MHz/6.78 MHz for EMI), custom variable-timestep integrators (`np_vartime_avg`, `np_vartime_rms`) because LTspice output is non-uniformly sampled, `resonant_c()`, and plots amplitude-vs-frequency. |
| `1KW_System_PPTPhi2/automate.py` (184) + `PPTPhi2.asc` | Same pattern for the **Φ2 (class-Φ2) power amplifier** topology. |
| `Archive/FindMPP/ltspice_automator.py` (262) | **The MPP searcher — this is what `compare_coilsets.py` imports as `lta`** (in submodule form). Encodes the real system limits as constants: `VPA_MAX=202`, `IPA_MAX=10`, `VDS_MAX=650` (GaNFET datasheet), `VRECT_MAX=87`, `PBATT_MAX=1020`, `IRECT_MAX=PBATT_MAX/71`, `VBATT_MAX=54` (16s LiFePO4). Sweeps `RLOADDC_LIST = range(1,31)` to find maximum power point. |
| `Archive/*.asc` | `1KW_System_ACLoad`, `1KW_System_DCLoad`, `1KW_System_AC`, `1KW_System_AC_simplified`, `1KW_System_EMI`, `1KW_System_EMI2`. |

### 3.3 `TopDonTC001/` — thermal camera automation

Drives a TopDon TC001 IR camera over V4L2 with OpenCV. Raw 256 × 192 thermal frames.

| File | What it is |
|---|---|
| `gui.py` (55) | **The only GUI in the entire tree** — an OpenCV live-preview window (3× upscale) for the thermal camera. Tiny, but it is the existing precedent. |
| `capture_frames.py` (35) | Dumps raw frames to disk with timestamps (`CAP_PROP_CONVERT_RGB = 0` to get the thermal payload, not a false-colour image). |
| `print_temps.py` (30) | Streams temperature readings to stdout. |
| `frame_to_image.py` (100) | Converts raw frames to labelled images. Contains `SUBSECTIONS_TR302_PA` — hand-measured pixel rectangles mapping regions of the thermal image to specific components on the TR302 board, with `LabelType` (RectTop/RectBottom/Global). Comment notes the test fixture isn't final, so coordinates will drift. |
| `coil_temp_watchdog.py` (87) | **Closed-loop safety automation.** Watches coil temperature over a `VendorSocket` connection and trips at `BAD_TEMP_CELCIUS = 75` over a 5-minute run. Talks to hardware at `ws://192.168.2.20/ws`. |

### 3.4 `scripts/` — bench utilities

Mostly *not* FEMM. This is the working engineer's toolbox around the hardware.

**Impedance / RF analysis (the `z_*.py` + `zz.py` family):**

| File | What it is |
|---|---|
| `zz.py` (61) | Shared impedance mini-library: `Impedance(f)` with `zC`/`zL`, `parallel()`, `resonantF()`, `reactance_to_l_or_c()`, `qpretty()` (quantiphy formatting). Imported by most `z_*` scripts. |
| `z_pa.py` (527) | Largest script here — power-amplifier impedance analysis with **Smith charts** (`pysmithchart`) and symbolic maths (`sympy`). |
| `z_inductor.py` (160) | Inductor modelling against VNA data (`F0=6.78e6`, `VNA_Z0=50`, 0 dBm). |
| `z_misc.py` (155) | Assorted impedance calculations. |
| `z_oc_at_vna.py` (129) | Open-circuit matching-network analysis using directly VNA-measured capacitances at 6.78 MHz. |
| `z_vna_scattering.py` (111) | **S-parameter → Z-matrix conversion** (`Z = Z0(I+S)(I−S)⁻¹`) for 2- and 3-port VNA measurements, with port-remapping flags. This is how measured hardware gets compared against FEMM predictions. |
| `z_cpar_test.py` (110) | Parasitic capacitance test — self-flagged at runtime: *"WARNING: There seems to be a bug in this code."* |
| `z_f0_probe.py` (93) | Measures a coil's resonant frequency using a second probe coil. |
| `caps.py` (191) | Capacitor bank combination solver (`itertools` over available values). |
| `resonance.py` (29) | Give any two of L/C/F, get the third. Clean, `argparse`-driven. |
| `ppt_phi2.py` (96) | Symbolic derivation of the Φ2 amplifier component values (from L. Gu & J. Rivas-Davila). |
| `admittance.py` (36) | Reimplements the firmware's `max_ipa_crash` admittance-limit calculation in Python, to reason about firmware behaviour offline. |
| `thermal.py` (29) | Spreading/constriction thermal resistance for copper (Lee et al. 1995). Partially commented out / WIP. |

**Hardware control & firmware:**

| File | What it is |
|---|---|
| `on_off.py` (55) | Cycles the charger on/off over `VendorSocket`. |
| `power_tracking_mod.py` (52) | Power-tracking experiments against `ws://coilbot:5555/ws`. |
| `pt_mod_vpa_startup.py` (264) | Vpa startup-sequence experiments (`DeviceID`/`ParamID`/`AdcID` parameter pokes). |
| `relay_test.py` (99) | CAN-bus + Adafruit FT232H GPIO relay test. Header carries the exact `ip link set can0 ... bitrate 500000` setup commands. |
| `upload_fw.py` (91) | Firmware upload over HTTP/WebSocket, scraping the device page with BeautifulSoup. |
| `build_fw.sh` | CMake/ninja firmware build + `make ota`, plus a web-app build. |
| `setup_build_linux.sh` | Provisions a build box: apt packages, gcc-12, LLVM 17 apt repo, cmake, ninja. |
| `0001-Add-force-parameter-for-uploading-updating-firmware.patch` | Git patch by Genki Marshall (2024-11-13) adding a `force` flag to `vendorsocket.py`, against the internal `vendor` package. |
| `3d_printer.py` (63) | Serial G-code control of a 3D printer at 115200 baud — used as a **motorised XYZ positioner** to move a receive coil to precise distances during bench measurement. This is the physical analogue of the `rc_test_distances` sweep. |
| `syslog.py` (42) | Parses device syslog CSVs into readable, de-duplicated event traces. |
| `decode_flags.py` (33) | Decodes TX/RX status bitfields into named firmware flags. |
| `charge_percent.py` (14) | Battery state-of-charge → pack voltage (8s LiFePO4) via the `coilbot` battery model. |
| `compare_boms.py` (263) | BOM diffing with pandas + fuzzy designator matching. Header is honest: *"this code was generated by ChatGPT, with just a few hand-edits. TODO go through and clean it up."* |
| `plot.py` (438) | General-purpose plotting front-end (PyQt6 backend) that ingests logs/tarballs of test data. |
| `adv_param.js` | A browser **bookmarklet** that jumps the device web UI to the advanced-parameter page and pre-fills the filter box. Manual-workflow automation. |
| `coilbot` | **A broken symlink** → `/home/vendor/Desktop/coilbot_docker/coilbot_server/coilbot`. `charge_percent.py` depends on it, so that script cannot run here. |

`genki/.gitignore` is the more sensible of the two — it ignores `__pycache__`, LTspice byproducts
(`*.raw`, `*.net`, `*.log`, `*.fft`, `*.plt`), `coilsim/*.ans`, `coilsim/*.fem`, and `scripts/vendor`.

---

## 4. How the pieces connect

```
                          ferrite_and_core_info.py  ── materials + core geometry DB
                                    │
                                    ▼
  coil_configs.py  ◄──────►  femm_model.py  ── FEMM drawing / extraction / validation
   (named designs)   (circular)          │
        │                                ▼
        └──────────────►  compare_coilsets.py  ──► FEMM (pyFEMM, axisymmetric, 6.78 MHz)
                                    │                        │
                                    │                        └──► L/M matrix, flux, block losses
                                    ▼
                          ltspiceautomator (lta)  ──► LTspice  ──► MPP / Vrect / Pbatt
                                    │
                                    ▼
                          results/ID{DDHHMM}/*.svg + log.txt
```

Independently, on the bench side:

```
  z_vna_scattering.py (measured S-params) ──► Z matrix ──► compare against FEMM-predicted L/M
  3d_printer.py (positioner) ──► real distance sweep ──► mirrors rc_test_distances
  TopDonTC001/ (thermal) ──► validates the loss numbers FEMM predicts
```

The intended full loop is: **design (`coil_configs`) → EM solve (FEMM) → circuit solve (LTspice) →
build → measure (VNA / thermal / positioner) → correct the model.** Today every arrow in that chain
is a human editing a file.

### Runtime requirements

- **FEMM** (Windows software; the scripts assume a local FEMM install driven through `pyfemm`).
  Note `compare_coilsets.py` writes Windows-ish paths and expects a GUI FEMM window — HOW-TO
  warns *"do not click inside the FEMM window while it is running."*
- **Python:** `pyfemm`, `numpy`, `matplotlib`, `scipy`, `quantiphy`, `sympy`, `pandas`,
  `coloredlogs`, `spicelib`, `opencv-python`, `pyserial`, `PyQt6`, `pysmithchart`, plus the internal
  `vendor` package and the private `ltspiceautomator` submodule.
- **LTspice**, a **VNA**, a **TopDon TC001**, a **3D printer**, and Vendor hardware for the
  bench-side scripts.

---

## 5. State of the code — what stands between here and a GUI

Concrete, file-specific obstacles, ordered by how much they block automation:

1. **The private submodule breaks the main script.** `ltspiceautomator/` is empty; line 22 of
   `compare_coilsets.py` imports it unconditionally. Nothing runs until it is cloned or the
   import is made lazy/optional.
2. **No entry points.** `compare_coilsets.py`, `planar_inductor.py`,
   `stacked_air_core_inductor_designer.py` and friends execute on import and call `plt.show()` at
   the end. A GUI cannot `import` them; it can only shell out. The fix is a `run(coilset, sweep) ->
   Results` function — `genki_femm_simple.py:main()` and `em_sim/femm_coils.py:main()` already show
   what that looks like.
3. **Configuration is source code.** Design parameters live in the `parameters` dict, in
   `coil_configs.py` literals, and in constants at the top of every standalone script. A GUI needs
   these as **data** (JSON/YAML/TOML) with `CoilSet` as the schema. `CoilSet` being a dataclass makes
   this a small step: `dataclasses.asdict` / `fields()` can generate the form automatically.
4. **~90 module-level accumulator lists** in `compare_coilsets.py` are the results model. They
   should become a `Results` dataclass (one per config, one per distance point). Until then, nothing
   can be re-plotted, cached, compared, or exported without re-running the solve.
5. **Results are write-only.** 114 runs, 104 SVGs, 114 prose logs, **zero structured data.** Emitting
   CSV/JSON/Parquet alongside the SVG would immediately unlock comparison, regression checks, and any
   GUI results browser — and it is a small, isolated change at the tail of the script.
6. **Run IDs collide.** `'{:%d%H%M}'` gives day-hour-minute. Two runs a month apart at the same
   minute overwrite each other, and directories don't sort chronologically. Use a full ISO timestamp.
7. **Sweeps are serial and re-solve redundantly.** The `multiprocessing.Pool` import at line 19 is
   used only by a commented-out block at line ~584. Each distance point issues three separate
   `get_inductances` solves (one per driven coil). FEMM itself is single-instance per process, so
   parallelism means multiple FEMM instances — which is exactly what a job-queue/worker design behind
   a GUI would provide, and where the biggest wall-clock win is.
8. **Duplication to consolidate.** Three toroidal scripts (two byte-identical), two `planar_inductor.py`,
   two `CoilSet` definitions in the main repo plus a third in `genki`, two `toroidal_core_shape`/
   `ferrite` definitions (`ferrite_and_core_info.py` and `em_sim/femm_toroid.py`), and two entire FEMM
   coil-drawing implementations across the two repos. Pick one lineage per concept before wrapping a UI
   around it.
9. **Circular import** between `femm_model` and `coil_configs` — will bite as soon as import
   order changes (which a GUI's import graph will do).
10. **Repo hygiene.** No dependency manifest in `femm_coil_simulation`; `.gitignore` doesn't cover
    `*.ans`/`*.fem`/`results/`, so ~33 MB of solver output and a stray `tmp.ans` sit in the tree; a
    broken `coilbot` symlink; a filename beginning with `-`; no tests, so there is no way to verify a
    refactor preserved the physics.

### The assets that make this tractable

- `CoilSet` is already a flat dataclass with typed fields — a form generator's dream.
- `check_valid_coilset` already exists, so a GUI can validate geometry before it wastes a solve.
- `coil_configs.py` is already a design library — presets are free.
- `genki/em_sim/femm_coils.py` and `genki_femm_simple.py` are already written the way the rest should
  be: dataclasses, small functions, context managers, CLI args, `main()`.
- `em_sim/femm_coils.py::PositionTweak` is the right pattern for the group-translation trick.
- The `# /// script` PEP 723 convention in `genki/` solves the dependency problem for anything new.
- 114 archived runs give a **ready-made regression corpus** — a refactor can be checked against
  `Gen2_baseline` and `Gen3 POR Jun10_25`.
