# component_sims/

Single-component FEMM simulations. **Not part of the 3-coil WPT link** that
`../Coil_Comparison_FEM.py` models — these size individual magnetics parts.

Kept for reference; nothing in the WPT path imports them.

| File | What it simulates |
|---|---|
| `ferrite_and_core_info.py` | **Materials & cores database** (no FEMM run of its own). Micrometals T12–T250 toroids, Fair-Rite 59xx/26xx, Ferroxcube E58/PLT58, planar I14–I64 plates, ferrite mixes -0…-10, FJ7, -61. Imported by four of the scripts below. |
| `planar_inductor.py` | PCB planar inductor on an E-core + I-plate, 100 kHz |
| `planar_air_core_inductor_designer.py` | Air-core planar coil over ferrite + AluFoil slabs |
| `toroidal_inductor.py` | Toroid on a real core (T184, mix -6) at 6.78 MHz |
| `transformer.py` | Wire-wound transformer; has the cleanest small drawing helpers in the repo (`draw_round_wire`, `draw_coil_grid`) |
| `stacked_air_core_inductor_designer.py` | Stacked flat-strip air-core inductor, 6.78 MHz / 45 A. Fully self-contained — no local imports |
| `toroid_parameter_selector.py` | Pure analytics, no FEMM. Brute-forces core x ferrite x turns against a target inductance and prints what fits. A natural "core finder" GUI tab later. |

## Running these

`planar_inductor.py` and `planar_air_core_inductor_designer.py` import
`noel_FEMM_functions` from `noelle/`. Each has a small `sys.path` bootstrap at
the top so they still run from in here:

```bash
python noelle/component_sims/planar_inductor.py
```

The other five have no dependency outside this folder.
