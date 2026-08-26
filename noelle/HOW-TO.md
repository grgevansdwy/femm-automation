
# How-To Guide: FEMM WPT Coil Simulation Automation

## 1. Introduction
This Python script automates Finite Element Method Magnetics (FEMM) simulations to evaluate Wireless Power Transfer (WPT) coil designs. Specifically, it simulates a 3-coil system consisting of a **Loop (Drive) Coil**, a **Tank (Transmitter) Coil**, and a **Receive Coil (RC/Rx)** operating at 6.78 MHz. 

The script dynamically draws the coils, ferrites, and shielding in FEMM, calculates self and mutual inductances across various coupling distances, computes the resulting electrical parameters (currents, voltages, efficiency) via analytical models or LTspice, and outputs a comprehensive set of diagnostic plots and data tables as an SVG image.

## 2. Prerequisites & Dependencies
To run this script, your environment must have the following installed:
*   **FEMM (Finite Element Method Magnetics):** Software for solving low-frequency electromagnetic problems.
*   **Python Dependencies:** `matplotlib`, `numpy`, `quantiphy`, `scipy`, `multiprocessing`.
*   **Custom Local Modules:** The script relies on specific local Python files that must be in the same directory or Python path:
    *   `femm`: The Python API to control FEMM (usually pyFEMM).
    *   `coil_configs` (`cc`): Contains predefined dataclasses/objects defining coil geometries.
    *   `noel_FEMM_functions` (`nff`): Contains custom helper functions to interact with FEMM (drawing coils, extracting flux, reading inductances).
    *   `ltspiceautomator` (`lta`): A custom wrapper to run SPICE simulations for evaluating the Maximum Power Point (MPP).

## 3. How the Code Works
The code's execution flow can be broken down into the following phases:

1.  **Environment Setup & Constants Definition:** 
    *   Initializes the operating frequency (6.78 MHz), angular frequency, and source/load impedances (including matching networks).
    *   Defines target currents and geometry boundaries.
2.  **Plotting Grid Configuration:**
    *   Sets up a complex `matplotlib` figure (`fig2`) with a 6x4 grid. It assigns specific subplots to specific metrics (e.g., $V_{PA}$, $I_{PA}$, loop currents, tuning) and merges other subplots to make room for data tables.
3.  **Configuration Parameter Matrix:**
    *   Defines a dictionary (`parameters`) of design permutations (ferrite dimensions, coil turns, spacings, metal shields). 
    *   Loads specific, predefined configurations (e.g., `Gen2_coil`, `Gen3_4mm_RC_ferrite`) from the `coil_configs` file into a list `coilset_array`.
4.  **FEMM Simulation Loop:**
    *   Iterates through each coil configuration.
    *   **Drawing Phase:** Uses custom functions to draw the PCB traces, ferrites, and backplanes in the 2D FEMM space.
    *   **Inductance & Tuning Phase:** Calculates the baseline self-inductance of the coils, then calculates the capacitance required to tune them to their target resonant frequencies.
    *   **Sweep Phase:** Sweeps the Receive Coil (RC) across different longitudinal distances (Z-axis gaps). At each distance, it extracts the mutual inductances ($M_{12}, M_{13}, M_{23}$).
    *   **Circuit Analysis:** Uses analytical circuit equations (First Harmonic Approximation) to solve for the currents in each coil, the optimal load voltage ($V_{rect}$), and total losses.
    *   **Loss & Flux Analysis:** Extracts peak magnetic flux densities (to check for ferrite saturation) and calculates Equivalent Series Resistance (ESR) and block losses in the ferrites and shields.
5.  **Visualization & Output:**
    *   Plots the extracted metrics against distance.
    *   Populates two large tables on the plot: one for the configuration parameters of each run, and one for the resulting output metrics.
    *   Saves the final composite figure as a high-resolution SVG file in a timestamped `results` directory.

## 4. How to Use the Script
1.  **Set Up the Workspace:** Ensure FEMM is installed on your Windows machine and `pyFEMM` is installed in your Python environment. Make sure `coil_configs.py` and `noel_FEMM_functions.py` are present in your working directory.
2.  **Define Your Coils:** To test new coils, either:
    *   Modify the `parameters` dictionary to test permutations.
    *   Add new coil configurations to the `coilset_array` directly (e.g., `new_coil = cc.copy_coilset(cc.Gen2_coil)` and modify its attributes).
3.  **Set the Z-Distance Sweep:** Locate the variable `rc_test_distances` and set it to an array of distances you want to test (e.g., `rc_test_distances = [10, 20, 30, 40]`). *Note: In the current script, it is hardcoded to just `[30]` for a single distance test.*
4.  **Run the Script:** Execute the script via your terminal or IDE (`python script_name.py`). FEMM will open automatically, draw the geometries, and analyze them. *Do not click inside the FEMM window while it is running, as it may interrupt the script.*
5.  **Review the Output:** Once complete, navigate to the `./results/ID[TIMESTAMP]/` directory. Open the `.svg` file to view the graphs showing how currents, efficiencies, and tuning vary across distances, alongside the table of raw values.

