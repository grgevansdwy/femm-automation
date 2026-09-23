# Improve FEMM Simulation

## Background

[FEMM](https://www.femm.info/) is an electromagnetics simulation tool. We use it to simulate the characteristics of our inductors (both wireless power coils and toroid circuit inductors) at 6.78 MHz.

*Figure 1: A wireless power transmit coil*

*Figure 2: Miscellaneous toroid inductors (blue cores with orange windings) used in our power amplifier*

Noel and Genki have written some code towards this end. But it needs a re-design now that we understand what we want better. Furthermore, there is potential for AI optimization.

## Map of Existing Code

Use this as a reference on an as-needed basis. You don't need to read it one-by-one.

### Noel's Code

| File | Purpose |
|---|---|
| `Branches_and_one_offs/` | Ignore everything in here for now. |
| `Inductor_and_Xfrmr Sims/planar_inductor.py` | PCB inductors? |
| `Coil_Comparison_FEM.py` | Simulate our wireless power coils |
| `coil_configs.py` | Coil configuration file |
| `ferrite_and_core_info.py` | Ferrite library |
| `noel_FEMM_functions.py` | General helper library |
| `planar_air_core_inductor_designer.py` | PCB inductors? |
| `planar_inductor.py` | PCB inductors? |
| `stacked_air_core_inductor_designer.py` | PCB inductors? |
| `toroid_parameter_selector.py` | Select a certain inductor core |
| `toroidal_inductor.py` | Simulate our circuit inductors |
| `transformer.py` | Simulate transformers for our DC-DC converters at low frequencies |

### Genki's Code

| File | Purpose |
|---|---|
| `femm_caps.py` | Simulate the capacitors used on our wireless power coils |
| `femm_coils.py` | Simulate our wireless power coils. Only does two coils. |
| `femm_toroid.py` | Simulate our circuit inductors |

## Objective #1: Simulate Coils

**Input:** Geometry describing a coilset.
**Output:** Coil self-inductance, self-resistance, and mutual inductance.

Both Noel and Genki have code to do this, but it is not organized well. The first step is to organize it well.

**Who to ask for help:** Genki.

## Objective #2: AI Optimization

Setup a local AI agent with the objective of finding a certain coilset geometry that achieves a target mutual inductance while minimizing self-resistance. Organize code to enable it to cleanly propose and test geometries.

**Who to ask for help:** Alex, Genki.

## Bonus Objective: Simulate Toroid Inductors

Do the same as above, but for our toroid inductors.

## Bonus Objective: Simulate Metal and Ferrite

Modify the coil simulation to also include metal and ferrite in the simulation. The objective will be to reduce ferrite and metal quantity while producing "non-detunable" coils, and potentially lowering coil losses.
