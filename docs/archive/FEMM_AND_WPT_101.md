# FEMM & Wireless Power 101

**A from-scratch guide to the physics, the tool, the code, and where it should go next.**

> **Who this is for:** someone who can read code but has never studied electromagnetics.
> No prior physics assumed. Every concept is built up from zero and then tied to the exact
> function or variable in this repo where it appears.
>
> **Companion doc:** `CODEBASE_OVERVIEW.md` maps every file. This doc explains *why* those files
> do what they do.

---

## Table of contents

- [Part 0 — What problem is actually being solved](#part-0--what-problem-is-actually-being-solved)
- [Part 1 — Electromagnetics from zero](#part-1--electromagnetics-from-zero)
- [Part 2 — AC, resonance, and Q](#part-2--ac-resonance-and-q)
- [Part 3 — Where the energy leaks (losses)](#part-3--where-the-energy-leaks-losses)
- [Part 4 — The actual system being designed](#part-4--the-actual-system-being-designed)
- [Part 5 — FEMM 101](#part-5--femm-101)
- [Part 6 — How this repo turns FEMM into numbers](#part-6--how-this-repo-turns-femm-into-numbers)
- [Part 7 — Symbol & variable glossary](#part-7--symbol--variable-glossary)
- [Part 8 — What could be improved](#part-8--what-could-be-improved)
- [Part 9 — A learning path](#part-9--a-learning-path)

---

## Part 0 — What problem is actually being solved

A robot needs to charge without anyone plugging in a cable. So:

- A **transmitter** pad sits on the floor or wall. It contains coils of copper etched onto a PCB.
- A **receiver** coil is bolted to the robot.
- The robot parks *near* the pad — not touching, and never in exactly the same spot twice. The gap
  might be 5 mm or 100 mm, and might be off-centre.
- Power must cross that air gap. **1000 watts**, at high efficiency, without anything melting.

Power crosses the gap as a **magnetic field**. Nothing else. There is no contact, no light, no radio
"beam". The transmitter coil makes a magnetic field that wobbles 6,780,000 times a second, and that
wobbling field induces a voltage in the receiver coil.

The engineering problem is that this coupling is **weak and unpredictable**. How much power gets
across depends brutally on:

- the exact radius, width, spacing, and number of the copper turns
- the distance and alignment between the two coils
- whether there's ferrite behind the coils to shape the field
- whether there's metal nearby (a robot chassis, a shield) stealing energy and detuning everything

You cannot compute this by hand for a real geometry, and building a PCB to find out costs weeks.
So you **simulate** — and that is what this repo is: a simulator that draws a candidate coil design,
solves the magnetic field around it, and predicts how much power gets across and how much is wasted.

**The whole repo, in one sentence:** *draw the coils in FEMM → solve the magnetic field → extract
the inductances → feed those into circuit equations → get efficiency, currents, and voltages → plot.*

---

## Part 1 — Electromagnetics from zero

### 1.1 The four quantities you already half-know

| Quantity | Symbol | Unit | Intuition |
|---|---|---|---|
| Current | I | ampere (A) | how many electrons flow per second — "flow rate" |
| Voltage | V | volt (V) | electrical pressure pushing them — "pressure" |
| Resistance | R | ohm (Ω) | how much the wire fights the flow — "pipe friction" |
| Power | P | watt (W) | energy per second, `P = V·I = I²R` |

That last one matters enormously here. **`P = I²R`**: power wasted as heat grows with the *square*
of current. Double the current, quadruple the heat. This is why so much of this repo is obsessed
with keeping resistance low — at 45 A (see `stacked_air_core_inductor_designer.py`), even
0.1 Ω burns 200 W as heat.

### 1.2 Magnetic field — what it is

**Any moving charge creates a magnetic field.** Current in a wire → magnetic field wrapping around
that wire in circles. Point your right thumb along the current; your curled fingers show the field
direction. That's it. That's the whole origin of magnetism in this system — no magnets involved.

A single straight wire makes a weak, spread-out field. But bend the wire into a **loop**, and all
the field lines inside the loop point the same way and reinforce each other. Stack **N loops** on
top of each other (a coil) and you get roughly N times the field. This is why coils exist: they are
a magnetic field concentrator.

The field is described by two closely-related vectors:

- **H** — *magnetic field strength*, unit A/m. Think of it as "how hard you are pushing", set purely
  by the current and the geometry. Materials don't change H.
- **B** — *magnetic flux density*, unit **tesla (T)**. Think of it as "how much field you actually
  got". This is what materials amplify.

They're linked by the **permeability** of the material:

```
B = µ · H            µ = µ0 · µr
µ0 = 4π × 10⁻⁷       (permeability of vacuum — a constant of nature)
µr = relative permeability of the material (dimensionless)
```

- Air, copper, aluminium, plastic: `µr ≈ 1`. They do nothing magnetically.
- **Ferrite**: `µr` of ~100–200 for the high-frequency NiZn types used here. In `coil_configs.py`
  you'll see `tank_mu=184.5` (a Fair-Rite FJ7-class material) and `tank_mu=125` (Fair-Rite "-61").
  Iron would be ~1000+, but iron is useless at 6.78 MHz — it would cook itself.

**Why ferrite matters:** it behaves like a "conductor for magnetic field". Put a ferrite plate
behind a coil and the field prefers to travel through the ferrite rather than leak out the back.
Result: more field where you want it (toward the other coil), less where you don't (into the robot
chassis). This is why every `CoilSet` in the repo has ferrite thickness, radius, offset, and µ.

**Saturation:** ferrite has a limit. Above roughly **0.3 T** (for NiZn), it stops amplifying — µr
collapses toward 1 and the material starts behaving like air, plus it heats badly. This is exactly
why `femm_model.py` has `get_peak_flux_density_of_rect()` — it scans across the ferrite
plate reading B and reports the worst point, so the designer can check they haven't saturated it.

### 1.3 Flux — "how much field goes through this hoop"

Take a wire loop. Ask: how much magnetic field passes through the hole in the middle?
That's **magnetic flux**, `Φ` (Greek phi), unit **weber (Wb)**.

```
Φ = B × A          (field density × area, if B is uniform and perpendicular)
```

If you have N turns and the flux passes through all of them, the total "flux seen by the circuit" is
the **flux linkage**:

```
λ = N · Φ          unit: weber-turns
```

**This is the single most important quantity in this repo**, because it is literally what FEMM hands
back. In `femm_model.py`:

```python
flux_linkage += np.real(femm.mo_getcircuitproperties(circuit_name+str(i))[2])
```

Index `[2]` of that tuple is the flux linkage of one turn. The loop sums it over all turns of a coil.

### 1.4 Induction — Faraday's law

Here is the law that makes wireless power possible:

> **A *changing* magnetic flux through a loop of wire induces a voltage in that loop.**
>
> ```
> V = − dΦ/dt
> ```

Read it carefully. Not "a magnetic field induces a voltage" — a **changing** one. A static magnet
next to a coil does nothing. You must wiggle the field.

This is why the system runs at **6.78 MHz**. Faster change → bigger `dΦ/dt` → more induced voltage
for the same physical field. High frequency is how you get meaningful power through a small coil.
(The tradeoff is that high frequency makes losses worse — see Part 3.)

The minus sign is **Lenz's law**: the induced voltage always opposes the change that created it.
Nature resists. This shows up everywhere — it's why metal near a coil steals energy (Part 3.5).

### 1.5 Self-inductance (L)

Now the subtle bit. When current flows in a coil, it creates flux. That flux passes through the
coil's *own* turns. So if the current changes, the coil induces a voltage *in itself* that opposes
its own change.

The proportionality constant is **inductance**, `L`, unit **henry (H)**:

```
λ = L · I           ⟺           L = λ / I
V = L · dI/dt
```

**Inductance is a property of geometry alone** (plus nearby materials). More turns, bigger radius,
or ferrite nearby → more inductance. It does not depend on the current you push through.

Real coils here are in the **microhenry (µH)** and **nanohenry (nH)** range.

**This one equation is what the entire FEMM half of the repo computes.** Look at
`femm_model.py:106`:

```python
def loop_inductance(circuit_name, num_turns_of_induced, driving_current):
    flux_linkage = 0
    for i in range(num_turns_of_induced):
        flux_linkage += np.real(femm.mo_getcircuitproperties(circuit_name+str(i))[2])
    L = flux_linkage / driving_current       # ← literally L = λ / I
    return L
```

That's it. Push a known current, ask FEMM for the resulting flux linkage, divide. Everything else in
the repo is scaffolding around this line.

### 1.6 Mutual inductance (M) and coupling (k)

Two coils near each other. Push current through coil 1. Its flux passes through coil 2 as well.
The flux linkage *coil 2* sees, divided by the current in *coil 1*, is the **mutual inductance**:

```
M₂₁ = λ₂ / I₁
```

**Look again at the function above.** It takes `circuit_name` and `driving_current` as *separate*
arguments. So if you drive the loop and pass `'tank'` as the circuit name, the exact same function
returns the mutual inductance instead of a self-inductance. That is the trick the whole extraction
is built on — `get_inductances()` calls it nine times with different name/current pairings to fill in
the matrix:

```
        ┌ L11  M12  M13 ┐        1 = loop
   M =  │ M21  L22  M23 │        2 = tank
        └ M31  M32  L33 ┘        3 = receive coil
```

**Reciprocity:** physics guarantees `M12 = M21`, `M13 = M31`, `M23 = M32`. The code computes both
halves independently, from different FEMM solves. That means **comparing them is a free accuracy
check that nobody is currently using** — see Part 8.

The absolute value of M isn't very intuitive, so engineers normalise it into the **coupling
coefficient**:

```
k = M / √(L₁ · L₂)          0 ≤ k ≤ 1
```

- `k = 1` — perfect coupling, every field line from coil 1 threads coil 2 (an ideal transformer with
  a shared iron core).
- `k = 0` — completely independent.
- **A wireless power link across an air gap is typically `k ≈ 0.01 to 0.3`.**

That last number is the crux of the whole field. **Coupling is terrible.** 95%+ of the field misses
the receiver entirely. If you just wired a load straight to the receive coil, you'd get almost
nothing across. The fix is resonance.

---

## Part 2 — AC, resonance, and Q

### 2.1 Sinusoids and phasors

Everything here oscillates at one frequency, `f = 6.78 MHz`. The **angular frequency** is:

```
ω = 2πf          (in the code: omega = 2*np.pi*freq)
```

When everything is a sine wave at the same frequency, you don't need to track waveforms in time.
You only need **amplitude** and **phase** — which is exactly what a complex number holds. This is
called **phasor analysis**, and it is why complex numbers are all over this codebase (`1j` in Python).

Rule of thumb: **multiplying by `j` = "shift 90° later in time"**.

### 2.2 Impedance — resistance generalised

At AC, components oppose current in three different ways:

| Component | Impedance | Behaviour |
|---|---|---|
| Resistor R | `Z = R` | Burns energy as heat. Real. |
| Inductor L | `Z = jωL` | Stores energy in a magnetic field. Opposes *more* at high frequency. |
| Capacitor C | `Z = 1/(jωC)` | Stores energy in an electric field. Opposes *less* at high frequency. |

The imaginary part is called **reactance**. It doesn't burn energy — it just sloshes it back and
forth. Only the **real part** dissipates power.

Note the signs: `jωL` is positive-imaginary, `1/(jωC) = −j/(ωC)` is negative-imaginary.
**They point in opposite directions.** Which leads directly to:

### 2.3 Resonance — the whole point

Put an inductor and a capacitor in series. Total reactance:

```
Z = jωL − j/(ωC)
```

At one specific frequency these **exactly cancel**:

```
ωL = 1/(ωC)      →      ω₀ = 1/√(LC)      →      f₀ = 1/(2π√(LC))
```

At that **resonant frequency**, the L and C vanish from the circuit's point of view. All that's left
is the small parasitic resistance R. A small driving voltage can now push an enormous current, because
almost nothing is opposing it.

You will find exactly this in `femm_model.py`:

```python
def resonant_capacitance(inductance, frequency):
    return 1/(inductance*((2*np.pi*frequency)**2))     # C = 1/(L·ω²)

def resonant_frequency(inductance, capacitance):
    return 1/(2*np.pi*np.sqrt(inductance*capacitance))  # f₀ = 1/(2π√(LC))
```

And in `compare_coilsets.py`, after FEMM reports the inductances, the code immediately computes
the capacitor each coil needs:

```python
C1 = nff.resonant_capacitance(L_loop_fully_assembled, coilset.loop_tune_f*1e6)
C2 = nff.resonant_capacitance(L_tank_fully_assembled, coilset.tank_tune_f*1e6)
C3 = nff.resonant_capacitance(L_rc_fully_assembled,   coilset.rc_tune_f*1e6)
```

These are real physical capacitors that get soldered onto the board. **This is a primary deliverable
of the simulation** — the sim tells you which capacitor value to buy.

Notice the tuning targets aren't all 6.78 MHz: `loop_tune_f=6.85`, `tank_tune_f=6.75`,
`rc_tune_f=6.84` MHz. Deliberately staggered slightly off the drive frequency, for stability and
load-line reasons.

### 2.4 Q factor — how good is the resonator

```
Q = ω₀L / R          (energy stored per cycle ÷ energy lost per cycle)
```

High Q = low loss = a resonator that "rings" for a long time. A high-Q coil driven at resonance
builds up a **circulating current far larger than the current you feed in**. That is the amplifier
that makes weak coupling survivable.

You can compute Q for these coils directly from values in the code: at `ω = 2π·6.78e6 ≈ 4.26×10⁷`
rad/s, with a tank inductance of order 1.5 µH and the `R_TC_Tank = 1.836 Ω` used in the LTspice
config, `Q = ωL/R ≈ 35`. Every design decision in the repo — wider traces, thicker copper, more
spacing between turns, better ferrite — is ultimately an attempt to raise Q.

### 2.5 Why WPT needs both

The figure of merit for a resonant wireless link is roughly:

```
FoM  ≈  k · √(Q₁ · Q₂)
```

`k` is stuck at ~0.1 by physics and mechanical constraints. So the only lever you have is **Q**.
This is the entire strategic reason the system is built out of high-Q resonators tuned to a
common frequency instead of just two coils and a load.

It also explains the **drive loop**. Look at the three coils again:

- The power amplifier can't drive the high-Q tank directly — it would see a wildly wrong impedance.
- So a small, low-turn-count **loop** sits next to the tank, magnetically coupled to it (`k12`).
- The amplifier drives the loop; the loop feeds the tank; the tank radiates.
- Changing the loop's radius and its distance from the tank (`loop_r`, `loop_offset`) changes `k12`,
  which changes the impedance the amplifier sees. **The loop is an impedance-matching transformer
  made of air.**

That's why `loop_offset` and `loop_r` are swept parameters. They're the tuning knob for matching.

---

## Part 3 — Where the energy leaks (losses)

Efficiency is what this whole simulation is really measuring. Here's every mechanism that turns
your 1000 W into heat.

### 3.1 DC resistance

```
R = ρ · length / area
```

Copper resistivity `ρ ≈ 1.68×10⁻⁸ Ω·m`. Wider and thicker traces → less resistance. Straightforward.
Except at 6.78 MHz it's almost irrelevant, because of:

### 3.2 Skin effect

At high frequency, current does not flow through the whole cross-section of a conductor. The changing
field inside the conductor induces eddy currents that cancel the flow in the middle. **Current
crowds into a thin skin at the surface.**

```
δ = √( ρ / (π · f · µ) )
```

For copper at 6.78 MHz: **δ ≈ 25 µm**.

Now look at the code: `loop_trace_thickness = 0.07` mm = **70 µm** — that's "2 oz copper".
The conductor is only ~2.8 skin depths thick, and the current is hugging the top and bottom surfaces.
**Making the copper thicker barely helps.** The comment `#.035 - 1oz, .07 - 2oz` in
`compare_coilsets.py` is someone tracking exactly this tradeoff.

This is also why `em_sim/femm_coils.py` bothers with `TraceTrapezoid` — modelling the *actual etched
trapezoidal cross-section* of a PCB trace (45° sidewalls) rather than a rectangle, because at these
frequencies the surface shape is what carries the current.

### 3.3 Proximity effect

Same idea, but caused by *neighbouring* conductors. The field from turn 2 pushes current around
inside turn 1, crowding it to one side and raising the effective resistance further.

Consequence: **turns must not be packed too close.** Look at the parameters — `tank_turn_spacing
= 7.112` mm for a `tank_trace_width` of 3.175 mm. More than double the trace width of gap. That's
proximity effect being paid for in board area.

FEMM models both skin and proximity effect automatically, as long as you run a **harmonic** (AC)
problem rather than a magnetostatic one — which is precisely what
`femm.mi_probdef(6780000, 'millimeters', 'axi', 1e-8, 1, 30)` sets up. The non-zero frequency is the
switch that turns eddy-current physics on.

### 3.4 ESR — the number that summarises all of it

**Equivalent Series Resistance** is the single resistance value that accounts for all conduction
losses at the operating frequency. It's what you plug into `P = I²R`.

`femm_model.py:116` extracts it from the FEMM solution:

```python
def loop_ESR(circuit_name, num_turns_of_induced, driving_current):
    voltage_drop = Σ np.real(mo_getcircuitproperties(...)[1])   # real part of voltage
    R = voltage_drop / driving_current                          # R = Re(V)/I
```

The real part of the voltage is the in-phase component — the part that actually burns power.

### 3.5 Eddy currents in nearby metal — the enemy

This is why half the parameters in `CoilSet` exist.

Put a sheet of aluminium near an AC coil. The changing flux induces circulating currents *in the
sheet* (Faraday's law again — the "loop" doesn't have to be a wire). Those currents:

1. **Burn power** as heat in the metal. Pure loss, transferred to nothing.
2. **Create their own opposing field** (Lenz's law), which cancels part of the coil's field. This
   *reduces the coil's inductance* — which shifts its resonant frequency — which **detunes the
   system**, which can collapse power transfer entirely.

Effect #2 is the sneaky one and it is the reason for this whole class of parameters:

```
backplane, backplane_y_distance          # a metal plate behind the transmitter
rc_backplane, rc_backplane_y_distance    # metal behind the receiver (the robot's own chassis)
tc_shield, tc_shield_y_distance, ...     # deliberate shields inside the enclosure
rc_shield, ...
```

The sim answers: *"if the robot's aluminium body is 20 mm behind the receive coil, how far does my
resonant frequency move, and how much power do I lose?"* `compare_coilsets.py` computes exactly
this as `tank_frequency_shifts`, `rc_frequency_shifts`, etc.

In `init_femm()`, aluminium is defined explicitly with its conductivity — because conductivity is
what determines how bad the eddy currents are:

```python
femm.mi_addmaterial('AluFoil')
femm.mi_modifymaterial('AluFoil', 5, 37.7)   # property 5 = conductivity in MS/m
```

### 3.6 Ferrite losses

Ferrite isn't free either. Two mechanisms:

- **Hysteresis loss** — the material's magnetic domains flip back and forth 6.78 million times a
  second, and flipping costs energy.
- **Eddy-current loss** — small, because ferrite is deliberately a poor conductor. Hence
  `femm.mi_modifymaterial('Ferrite', 5, 1e-11)` — essentially insulating.

FEMM models hysteresis loss with a **hysteresis lag angle** (material properties 10 and 11):

```python
femm.mi_modifymaterial('Ferrite', 10, coilset.tc_loss_angle)   # FJ7: 0.012544, "-61": 0.0073
femm.mi_modifymaterial('Ferrite', 11, coilset.tc_loss_angle)
```

Physically this says "B lags behind H by a small angle", and that lag *is* the loss. A material with
a lower loss angle at 6.78 MHz is a better material — which is exactly the `Gen3_2mm_61_RC_ferrite`
vs `Gen3_2mm_RC_ferrite` comparison in the main script.

> ⚠️ **Worth verifying:** FEMM's `Phi_hx`/`Phi_hy` properties are documented in **degrees**. The
> values passed here (`0.012544`, `0.0073`) look like **loss tangents** (`tan δ`), which would
> correspond to 0.72° and 0.42°. If so, every ferrite loss number in the repo is off by a factor of
> ~57. This is a genuinely important thing to check against the FEMM manual and against a measured
> board.

### 3.7 How losses are read back out

```python
def get_block_losses(x, y):
    femm.mo_selectblock(x, y)
    losses = femm.mo_blockintegral(6)   # integral type 6 = total losses, in watts
    femm.mo_clearblock()
    return np.abs(losses)
```

You point at a coordinate, FEMM figures out which material block contains it, integrates the loss
density over that block's revolved volume, and hands back watts. The main script calls this for the
TC ferrite, RC ferrite, both shields, and every copper turn, then:

```python
total_loss = tc_ferrite_loss + rc_ferrite_loss + tc_shield_loss + rc_shield_loss \
           + loop_loss + tank_loss + rc_loss
coil_efficiency = np.abs((Pbatt_tests[0] - total_loss) / Pbatt_tests[0])
```

**That efficiency number is the headline output of the entire simulator.**

---

## Part 4 — The actual system being designed

### 4.1 The signal chain

```
  DC supply
      │
      ▼
 ┌─────────────┐   200 V, 6.78 MHz square-ish wave
 │ Power Amp   │   (GaN FETs, class-Φ2 topology)
 │   (PA)      │
 └─────────────┘
      │  Vpa = 200 V, Ipa
      ▼
 ┌─────────────┐   coax cable: RG8x / RG213 / RG214
 │ LOOP coil   │   1–2 turns, r ≈ 50 mm, series C1
 └─────────────┘   ← impedance matching, magnetically fed
      ┊ k12  (strong, ~0.2–0.5, fixed by board layout)
 ┌─────────────┐
 │ TANK coil   │   5 turns, r ≈ 72 mm, series C2, high Q
 └─────────────┘   ← the actual transmitter
      ┊ k23  (WEAK, ~0.05–0.3, varies with robot position)  ← THE AIR GAP
      ┊       5 mm … 100 mm
 ┌─────────────┐
 │ RC coil     │   8–10 turns, r ≈ 22–35 mm, series C3
 └─────────────┘
      │
      ▼
 ┌─────────────┐   L_match_OC = 185 nH, C_match_OC = 1380 pF
 │ Matching    │
 │ network     │
 └─────────────┘
      │
      ▼
 ┌─────────────┐
 │ Rectifier   │   AC → DC.  Vrect ≤ 85 V (firmware limit)
 └─────────────┘
      │
      ▼
   Battery       16s LiFePO4, ≤ 54 V, 1000 W
```

`k13` (loop → receive coil directly) also exists and is tracked, because it's a parasitic path that
messes with the intended loop→tank→RC chain.

### 4.2 The numbers, and why they are those numbers

| Value | Where | Why |
|---|---|---|
| **6.78 MHz** | `freq` everywhere | An **ISM band** (6.765–6.795 MHz) — legally usable for non-communication power without a licence. Also high enough for good `dΦ/dt`, low enough that copper and ferrite still behave. |
| **1000 W** | `Pout`, `PBATT_MAX` | The product spec. |
| **200 V** | `Vpa` | PA rail voltage; `VPA_MAX = 202` in the MPP searcher, bounded by the GaN FET's `VDS_MAX = 650`. |
| **85 V** | `VRECT_MAX` | Rectifier diode limit. |
| **54 V** | `VBATT_MAX` | 16-series LiFePO4 pack. |
| **±10 A** | `IPA_MAX` | Empirical — above this the DC-DC overheats. |
| **5–100 mm** | `rc_test_distances` | The mechanical parking tolerance of the robot. |

### 4.3 Why the simulation is 2D even though coils are 3D

At 6.78 MHz, the free-space wavelength is:

```
λ = c/f = 3×10⁸ / 6.78×10⁶ ≈ 44 metres
```

The coils are ~0.2 m across — about **1/200 of a wavelength**. Everything is deep in the
**near field**. There is no meaningful radiation, no wave propagation, no antenna behaviour. The
field can be treated as **quasi-static**: it's just an inductor problem that happens to wiggle fast.

This is a big deal. It means you can use a magnetics FEM solver (FEMM) instead of a full-wave
3D electromagnetic solver (HFSS/CST), which would be orders of magnitude slower and more expensive.

### 4.4 First Harmonic Approximation

The PA doesn't produce a clean sine wave — it produces something square-ish. But a resonant tank
is a very sharp filter: it responds to the fundamental frequency and largely ignores the harmonics.
So you approximate the drive as a pure sine at its fundamental amplitude:

```python
Vs = Vpa * (4/np.pi)   # Derived via First Harmonic Approximation
```

`4/π` is the fundamental Fourier coefficient of a square wave. This is what makes the phasor
analysis in Part 2 legitimate, and why the whole circuit solve can be done with complex algebra
instead of a time-domain simulation. (When they need the *real* waveform, harmonics, and EMI, they
switch to LTspice — that's what `genki/LTspice_Automation/` is for.)

---

## Part 5 — FEMM 101

### 5.1 What FEM is

You want to know the magnetic field everywhere in a region. The governing equations are partial
differential equations with no closed-form solution for a real shape.

**Finite Element Method** says: chop the region into thousands of small triangles ("elements"),
assume the field varies simply (e.g. linearly) inside each triangle, write down an equation for each,
and solve the resulting giant sparse linear system. The finer the mesh, the closer to the true answer.

FEMM specifically solves for the **magnetic vector potential** `A`, from which `B` is derived.
You never touch `A` directly — you ask for B, flux linkage, or losses.

### 5.2 What FEMM is

[FEMM](https://www.femm.info) — *Finite Element Method Magnetics*, by David Meeker. Free, small,
Windows-native, and mature. It solves:

| Problem type | Used here? |
|---|---|
| **Magnetostatics** (DC) | Available |
| **Magnetic harmonic** (AC, one frequency, complex-valued) | ✅ **This is what the repo uses** |
| Electrostatics | Once — `Branches_and_one_offs/electrostatics.FEE` |
| Heat flow / current flow | No |

The switch is the first argument of `mi_probdef`. Non-zero frequency → harmonic mode → complex
results, and skin/proximity/eddy-current physics all turn on automatically.

**pyFEMM** is the Python binding. Critically, it does **not** reimplement FEMM — it drives the actual
FEMM GUI application via inter-process communication. That has consequences:

- FEMM must be installed (Windows, or Wine).
- A GUI window opens and things get *drawn*, visibly, in real time.
- HOW-TO.md warns: *"Do not click inside the FEMM window while it is running."* Clicking changes the
  selection state that the script depends on.
- **One FEMM instance per process.** Parallelism means multiple processes.

### 5.3 Axisymmetric — the trick that makes this tractable

FEMM is 2D. Coils are 3D. The reconciliation:

`mi_probdef(..., 'axi', ...)` puts FEMM in **axisymmetric** mode. You draw a cross-section in the
right half-plane (`x ≥ 0`), and FEMM treats `x` as **radius from a central axis** and `y` as height.
It then solves as if your 2D drawing were **spun 360° around the y-axis**.

```
     y (height)
     ▲
     │   ▭ ▭ ▭ ▭ ▭   ← 5 rectangles = 5 turns of the tank coil
     │
  ───┼──────────────► x (radius)
     │
   axis of
  revolution
```

So a small rectangle at `x = 72 mm` isn't a bar — it's a **complete circular ring of radius 72 mm**.
Five rectangles at increasing x = a 5-turn flat spiral coil. A wide rectangle = a ferrite disc.

**This is exact for perfectly circular geometry, and free.** It turns a 3D problem into a 2D one with
no approximation, which is why the whole approach works at all.

**What it cannot represent:**

- The fact that a real spiral is a *spiral*, not concentric closed rings — the small radial jog
  between turns is invisible to the model.
- The lead-in / lead-out traces and the via structure.
- Anything non-circular: rectangular coils, DD coils, discrete radial ferrite bars.
  (This is why `Branches_and_one_offs/radial_bar_ferrite_sweep.py` exists as a separate experiment —
  discrete bars break axisymmetry and need a different formulation.)
- Lateral (off-centre) misalignment of the receiver. **A big limitation** — an axisymmetric model
  fundamentally cannot represent a receiver that's shifted sideways, only one moved along the axis.
  You'll notice `tank_lateral_tuning_pane` and `vratio_lateral_tests` in the main script are
  vestigial for this reason.

### 5.4 The FEMM object model

Everything you build is one of these:

| Concept | pyFEMM call | What it is |
|---|---|---|
| **Node** | `mi_addnode(x,y)` | A point. |
| **Segment** | `mi_addsegment` / `mi_drawline` | A straight line between nodes. |
| **Arc** | `mi_addarc` | A curved segment (used by `transformer.py:draw_round_wire`). |
| **Block label** | `mi_addblocklabel(x,y)` | A marker dropped *inside* a closed region. **It tells FEMM what material fills that region.** Without a label, a region is undefined and the solve fails. |
| **Material** | `mi_getmaterial` / `mi_addmaterial` / `mi_modifymaterial` | Air, Copper (from FEMM's library), Ferrite and AluFoil (defined by hand in `init_femm`). |
| **Circuit** | `mi_addcircprop(name, current, type)` | A named electrical circuit. `type` 0 = parallel, 1 = series. Blocks get assigned to a circuit and FEMM drives current through them. |
| **Group** | `mi_setgroup(n)` | An integer tag for bulk select/move/delete. Used here as `loop=1, tank=2, rc=3, rc_backplane=4, tc_backplane=5`. |
| **Boundary** | `mi_makeABC(4, 400, 0, 0)` | *Asymptotic Boundary Condition* — a shell at r = 400 mm that mathematically fakes "empty space extending to infinity". Without it you'd be simulating a coil inside a magic mirrored box. |

### 5.5 The three-phase workflow, and the `mi_` / `mo_` naming

FEMM has a **pre-processor** (you draw), a **solver**, and a **post-processor** (you interrogate).
The API prefix tells you which one you're talking to:

| Prefix | Meaning | Examples |
|---|---|---|
| `mi_` | **m**agnetics **i**nput — preprocessor | `mi_addnode`, `mi_drawline`, `mi_setblockprop`, `mi_probdef`, `mi_analyze` |
| `mo_` | **m**agnetics **o**utput — postprocessor | `mo_getcircuitproperties`, `mo_getb`, `mo_blockintegral`, `mo_selectblock` |

The canonical cycle, exactly as it appears in `get_inductances()`:

```python
femm.mi_modifycircprop('tank0', 1, current)   # 1. set up the drive
femm.mi_saveas('tmp.fem')                     # 2. write the model file
femm.mi_analyze()                             # 3. SOLVE (this is the slow part)
femm.mi_loadsolution()                        # 4. open the .ans result in the postprocessor
femm.mo_getcircuitproperties('tank0')         # 5. read numbers back
```

`tmp.fem` (the model) and `tmp.ans` (the solution) in the repo root are leftovers of exactly this.

### 5.6 The three post-processor calls that matter

```python
femm.mo_getcircuitproperties(name)  # → (current, voltage, flux_linkage), all complex
femm.mo_getb(x, y)                  # → (Bx, By) flux density at one point, in tesla
femm.mo_blockintegral(6)            # → integrate over selected block; 6 = total losses (W)
```

Almost every physical result in this repo comes from those three calls. Everything else is bookkeeping.

### 5.7 `mi_probdef` decoded

```python
femm.mi_probdef(6780000, 'millimeters', 'axi', 1.e-8, 1, 30)
#               │        │              │      │      │  └─ min mesh angle (30°) — element quality
#               │        │              │      │      └──── depth (ignored in axi mode)
#               │        │              │      └─────────── solver precision
#               │        │              └────────────────── axisymmetric
#               │        └───────────────────────────────── length units
#               └────────────────────────────────────────── frequency: non-zero ⇒ AC harmonic mode
```

### 5.8 FEMM's limits (important for planning)

- **2D only.** No lateral misalignment, no non-circular coils, no 3D shielding effects.
- **Single frequency per solve.** A frequency sweep = N separate solves.
- **No time domain.** No transients, no startup behaviour, no switching waveforms. (Hence LTspice.)
- **No litz wire model.** Litz would have to be modelled strand-by-strand, which is impractical.
- **One instance per process**, GUI-bound, Windows-native.
- **Mesh dependence.** A too-coarse mesh gives confidently wrong answers. Nothing in this repo
  currently performs a **mesh convergence study** — see Part 8.

---

## Part 6 — How this repo turns FEMM into numbers

### 6.1 The per-turn circuit trick

Instead of one circuit per coil, the code makes **one FEMM circuit per turn**:

```python
for i in range(coilset.num_tank_turns):
    femm.mi_addcircprop('tank'+str(i), 0, 0)     # tank0, tank1, tank2, tank3, tank4
```

Why bother? Three reasons:

1. You can read the flux linkage of each turn independently and sum it yourself — full control over
   the `λ = ΣNΦ` bookkeeping.
2. It cleanly handles **multi-layer PCBs and parallel sub-traces**, where "how much current is in
   this particular piece of copper" is genuinely nontrivial. That's what
   `get_turn_current(target, num_layers, num_parallel_traces, num_turns)` computes.
3. The `0` type flag (parallel) means each block carries the full circuit current, so the code sets
   the per-turn current explicitly rather than relying on FEMM's series bookkeeping.

### 6.2 The 300 mm translation trick

To measure a coil's inductance **on its own**, you must remove the influence of the others. Deleting
and redrawing them would be slow and would perturb the mesh. Instead:

```python
femm.mi_selectgroup(tank_group)
femm.mi_movetranslate(0, 300)     # shove the tank 300 mm away — effectively infinity
femm.mi_clearselected()
...solve...
femm.mi_movetranslate(0, -300)    # put it back
```

Called "solo" inductance in the code (`L_loop_solo`, `L_tank_solo`, `L_rc_solo`).

The same mechanism, with a *small* shift, is how the **distance sweep** works — the RC group is
translated to each value in `rc_test_distances`:

```python
rc_y_translations = [d - rc_test_distances[0] for d in rc_test_distances]
for rc_y_translation in rc_y_translations:
    ...get_inductances(..., rc_y_shift=rc_y_translation, ...)
```

> `genki/em_sim/femm_coils.py` implements the same idea far more safely, as a **context manager**
> (`PositionTweak`) that guarantees the geometry gets restored even if the solve throws. The main
> repo restores by hand after the solve — if `mi_analyze()` raises, the geometry is left displaced.

### 6.3 Filling the matrix: three solves, nine numbers

```python
if loop_turn_current > 0:            # drive the LOOP
    L11 = loop_inductance('loop', ...)    # its own flux linkage → self-inductance
    M12 = loop_inductance('tank', ...)    # tank's flux linkage → mutual
    M13 = loop_inductance('rc',   ...)    # rc's flux linkage   → mutual

if tank_turn_current > 0:            # drive the TANK
    M21, L22, M23 = ...

if rc_turn_current > 0:              # drive the RC
    M31, M32, L33 = ...
```

Each `if` block corresponds to one FEMM solve with a different coil energised. Three solves fill all
nine entries. `M12` and `M21` come from *different* solves and must agree — reciprocity.

Then coupling coefficients:

```python
k12 = M12 / np.sqrt(L_loop * L_tank)
k13 = M13 / np.sqrt(L_loop * L_rc)
k23 = M23 / np.sqrt(L_tank * L_rc)
```

### 6.4 From L/M to a working circuit

Once you have the matrix, FEMM is done. Now it's algebra. Each coil forms a series RLC loop:

```python
Z1 = 1/(1j*omega*C1) + 1j*omega*L_loop + R_loop + Z_cable_TC + Z_pa
Z2 = 1/(1j*omega*C2) + 1j*omega*L_tank + R_tank
Z3 = 1/(1j*omega*C3) + 1j*omega*L_rc   + R_rc + Z_cable_RC + ZL_eq
```

Read that as: *capacitor + inductor + parasitic resistance + cable + whatever's attached*.
Compare to Part 2.2 — it's just `Z = 1/(jωC) + jωL + R` for each of the three loops.

The three loops are coupled by mutual inductance, giving three simultaneous equations. Solving them
(Cramer's rule — the determinant is the shared denominator) yields the current in each coil:

```python
current_denominator = ( (M12**2)*Z3*(omega**2)
                      - 2j*M12*M13*M23*(omega**3)
                      + (M13**2)*Z2*(omega**2)
                      + (M23**2)*Z1*(omega**2)
                      + Z1*Z2*Z3 )

iloop = np.abs( Vs*((M23**2)*(omega**2) + Z2*Z3) / current_denominator )
itank = np.abs( Vs*(-1j*M12*Z3*omega - M13*M23*omega**2) / current_denominator )
irc   = np.abs( Vs*(-M12*M23*omega**2 - 1j*M13*Z2*omega) / current_denominator )
```

Intimidating, but it is exactly the closed-form solution of a 3×3 linear system. `Z1*Z2*Z3` is the
uncoupled case; every other term is a coupling correction.

From currents, everything else follows:

```python
Vrect   = ...                                # rectifier input voltage
PBatt   = (Vrect**2)/RL                      # power delivered to the battery
Ploss_loop = ((iloop/√2)**2 * Z1).real       # P = I²R using RMS current and the REAL part of Z
Ploss_tank = ((itank/√2)**2 * Z2).real
Ipa     = (Total_Power/pa_efficiency) / (Vpa * 2*√2/π)
```

Note `((I_rms)**2 * Z).real` — taking the real part is Part 2.2 in action: only resistance dissipates.

### 6.5 The two-stage loss accounting

There are actually **two independent loss calculations** in the code, and it's worth knowing they're
different:

1. **Circuit-level** (`Ploss_loop/tank/rc`) — from `I²·Re(Z)` using the analytical model. Fast,
   approximate, includes cable and matching-network losses.
2. **Field-level** (`get_block_losses`) — FEMM integrating actual loss density over the ferrite,
   shields, and copper. Slower, more physical, catches eddy currents in metal that the circuit model
   can't see.

The final efficiency uses the field-level total. The final solve is re-run at the **actual** solved
currents (converted from RMS back to peak with `*np.sqrt(2)`) so the loss integrals reflect real
operating conditions rather than the arbitrary probe currents used for the inductance extraction:

```python
close_tank_current = itank_tests[0]*np.sqrt(2)
nff.set_circuit_currents(close_loop_current, ..., close_tank_current, ..., close_rc_current, ...)
femm.mi_analyze(); femm.mi_loadsolution()
```

That's a nice touch and easy to miss.

### 6.6 The full per-config solve count

For one `CoilSet` with 12 distances:

| Stage | Solves |
|---|---|
| Solo self-inductances (coils separated) | 3 |
| Fully-assembled inductances (for tuning caps) | 3 |
| Distance sweep (12 distances × 3 drives) | 36 |
| Final loss/flux solve at operating currents | 1 |
| **Total** | **~43 FEMM solves per configuration** |

Each one re-meshes and re-solves from scratch. **This is where all the wall-clock time goes**, and
it's the primary target for the performance work in Part 8.

---

## Part 7 — Symbol & variable glossary

### Physics symbols

| Symbol | Name | Unit | Meaning |
|---|---|---|---|
| `B` | flux density | T (tesla) | Magnetic field strength as materials see it. Saturation limit lives here. |
| `H` | field strength | A/m | Magnetic "push", set by current alone. |
| `µ0` | vacuum permeability | H/m | `4π×10⁻⁷`, a constant of nature. |
| `µr` | relative permeability | — | Material multiplier. Air = 1, ferrite ≈ 100–200. |
| `Φ` | flux | Wb | Field passing through a loop. |
| `λ` | flux linkage | Wb·turns | `N·Φ` — what FEMM returns. |
| `L` | inductance | H | `λ/I`. Pure geometry. |
| `M` | mutual inductance | H | Flux linkage in coil B per amp in coil A. |
| `k` | coupling coefficient | — | `M/√(L₁L₂)`, 0→1. ~0.1 across an air gap. |
| `Q` | quality factor | — | `ωL/R`. How well a resonator rings. |
| `ω` | angular frequency | rad/s | `2πf` |
| `δ` | skin depth | m | ~25 µm for copper at 6.78 MHz. |
| `Z` | impedance | Ω | Complex generalisation of resistance. |
| `σ` | conductivity | S/m | Copper 58 MS/m, aluminium 37.7 MS/m, ferrite ~0. |

### Code variables you'll meet constantly

| Name | Meaning |
|---|---|
| `loop` / group 1 | Drive coil — impedance-matches the PA into the tank |
| `tank` / group 2 | Transmitter resonator — the high-Q coil doing the work |
| `rc` / group 3 | Receive coil, on the robot |
| `L11`, `L22`, `L33` | Self-inductances of loop, tank, rc |
| `M12`, `M13`, `M23` | Mutuals: loop↔tank, loop↔rc, tank↔rc |
| `k12`, `k13`, `k23` | The same, normalised |
| `C1`, `C2`, `C3` | Tuning capacitors computed for each coil — **a real deliverable** |
| `Z1`, `Z2`, `Z3` | Total series impedance of each resonant loop |
| `Vs` | Effective sine drive voltage = `Vpa · 4/π` (FHA) |
| `Vpa` / `Ipa` | Power amplifier voltage / current |
| `Vrect` | Rectifier output voltage — capped at 85 V by firmware |
| `PBatt` | Power actually delivered to the battery |
| `omega` | `2π · 6.78e6` |
| `*_solo` | Inductance measured with other coils shifted 300 mm away |
| `*_fully_assembled` | Inductance with ferrites/shields present — used for tuning caps |
| `rc_test_distances` | The air-gap sweep, `[5,10,…,50,75,100]` mm |
| `tank_mu`, `rc_mu` | Ferrite relative permeability |
| `*_loss_angle` | Ferrite hysteresis loss parameter |
| `*_trace_width`, `*_turn_spacing`, `*_trace_spacing` | PCB geometry — drives Q via skin/proximity effect |
| `*_trace_thickness` | Copper weight: 0.035 mm = 1 oz, 0.07 mm = 2 oz |
| `backplane`, `*_shield` | Nearby metal — causes eddy losses and detuning |

---

## Part 8 — What could be improved

Framed against the three stated goals: **(a)** a GUI, **(b)** a generic internal tool anyone can use,
**(c)** an LLM that can drive parameter changes itself.

The honest summary: **(c) is not gated on LLM work. It's gated on (b).** An LLM cannot optimise a
system it cannot call programmatically, cannot get structured results from, and cannot validate.
Every item in Phase 1 below is simultaneously a prerequisite for the GUI *and* for the LLM. Build it
once, and both phases become straightforward.

### Phase 0 — Unblock (days)

1. **The main script cannot run.** `compare_coilsets.py:22` does
   `import ltspiceautomator.ltspice_automator as lta`, but `ltspiceautomator/` is an empty,
   uninitialised private submodule. Either clone it, vendor it, or make the import lazy — the LTspice
   path is already commented out at the call site, so nothing needs it today.
2. **Pin the dependencies.** `femm_backend/` has no manifest at all. Adopt the PEP 723
   `# /// script` convention already used throughout `genki/`, or add a `pyproject.toml`.
3. **Fix repo hygiene.** `.gitignore` doesn't cover `*.ans`, `*.fem`, `results/`, or `__pycache__/`,
   so ~33 MB of solver output sits in the tree. Delete the stray `tmp.ans`/`tmp.fem`. Fix the broken
   `genki/scripts/coilbot` symlink. Rename `-toroidal_inductor.py` (a leading dash is a shell hazard)
   and delete it — it's byte-identical to `archive---toroidal_inductor.py`.
4. **Untangle the circular import** between `femm_model` and `coil_configs`. It works today
   only by import ordering, and a GUI's import graph will break it.

### Phase 1 — Build a real engine (the critical path)

This is the work that matters. Everything else depends on it.

**1.1 — Make the simulation a function.**

Right now `compare_coilsets.py` is 1320 lines of top-level statements that execute on import and
call `plt.show()` at the end. Nothing can import it. The target:

```python
def simulate(coilset: CoilSet, sweep: SweepSpec, opts: SolverOpts) -> SimResult:
    ...
```

Pure in, pure out, no globals, no plotting, no `input()` pauses, no `plt.show()`.
**Templates already exist in the repo**: `genki_femm_simple.py:main()` and
`genki/em_sim/femm_coils.py:main()` are written exactly this way. Follow those, not the big script.

**1.2 — Kill the ~90 accumulator lists.**

`k12s`, `M13s`, `L_rc_displays`, `tc_ferrite_losses`, `efficiency_tests`, … — these module-level lists
*are* the current results model. Replace with:

```python
@dataclass
class PointResult:      # one distance point
    distance_mm: float
    L11: float; L22: float; L33: float
    M12: float; M13: float; M23: float
    M21: float; M31: float; M32: float
    k12: float; k13: float; k23: float
    i_loop: float; i_tank: float; i_rc: float
    v_rect: float; p_batt: float; i_pa: float
    f_shift_loop: float; f_shift_tank: float; f_shift_rc: float

@dataclass
class SimResult:        # one CoilSet
    coilset: CoilSet
    points: list[PointResult]
    losses: LossBreakdown
    peak_flux_tc: float; peak_flux_rc: float
    efficiency: float
    c_tune: tuple[float, float, float]
    meta: RunMeta       # timestamp, git sha, femm version, solver settings, wall time
```

Separate **values** from **display strings**. Right now `Quantity(...)` formatting is interleaved with
computation, so the numbers are only available as pre-formatted text.

**1.3 — Emit structured results. This is the highest value-per-hour change in the repo.**

114 runs have produced 104 SVGs and 114 prose logs and **zero machine-readable data**. Every number
ever computed by this project is locked inside a picture. Adding a `results.json` + `points.csv`
next to the SVG is maybe 30 lines at the tail of the script, and it immediately unlocks:

- comparing runs without re-simulating
- regression tests against `Gen2_baseline` and `Gen3 POR Jun10_25`
- a GUI results browser
- an LLM being able to *read the outcome of its own change*

Do this first. It's small and everything downstream needs it.

**1.4 — Fix run IDs.** `'{:%d%H%M}'` is day-hour-minute only — no month, no year. Runs a month apart
at the same minute silently overwrite each other, and directories don't sort chronologically. Use
`YYYYMMDD-HHMMSS` plus a short config slug.

**1.5 — Configuration as data, not code.**

`CoilSet` is already a flat dataclass with ~55 typed fields. That's a gift: `dataclasses.fields()`
gives you a schema for free. Add:

```python
CoilSet.from_dict / to_dict          # JSON/YAML round-trip
CoilSet.json_schema()                # generated from the dataclass fields
```

Then designs live in `designs/*.json` instead of Python literals in `coil_configs.py`, and the same
schema drives the GUI form, the CLI, the file format, **and the LLM's tool definition.** One source
of truth.

Add units and valid ranges as field metadata while you're there — `dataclasses.field(metadata={...})`
— because the GUI needs them for labels, and the LLM needs them to avoid proposing nonsense.

**1.6 — Make the sweep explicit again.**

`compare_coilsets.py` builds a full-factorial `coilset_array` via `itertools.product`, then
line ~365 throws it away: `coilset_array = [cc.TR302_and_225_000056]`. The sweep machinery is
enabled and disabled by commenting lines. Make it a real, declarative `SweepSpec` object.

**1.7 — Parallelism.**

`multiprocessing.Pool` is imported at line 19 and used only inside a commented-out block at ~584.
FEMM is one-instance-per-process, so the design is a **worker pool of N processes, each owning its
own FEMM instance**, fed from a queue of (coilset, distance) jobs. At ~43 solves per config, this is
close to linear speedup and is the single biggest wall-clock win available.

**1.8 — Caching.**

Hash the geometry + material + frequency + solver settings into a key; skip the solve if you've seen
it. Sweeps re-solve enormous amounts of identical geometry — the solo inductances, in particular,
don't change with distance but are recomputed per config.

**1.9 — Correctness work.**

- **Use reciprocity as a free self-test.** The code already computes `M12` and `M21` from separate
  solves. They must be equal. Asserting `np.isclose(M12, M21, rtol=1e-3)` catches mesh problems,
  geometry errors, and bad solves for free. `genki/em_sim/femm_coils.py` already does exactly this
  (`assert np.isclose(self.M12, self.M21, rtol=1e-12)`); the main repo doesn't.
- **Run a mesh convergence study.** Nothing in this repo checks whether the answers are
  mesh-independent. Solve one reference geometry at several mesh densities and confirm L stops moving.
  Until that's done, no error bar on any number here is defensible.
- **Investigate the ferrite loss angle units** flagged in Part 3.6. If FEMM wants degrees and the
  code passes a loss tangent, ferrite losses are wrong by ~57×.
- **Delete or justify the fudge factor.** `loop_inductance` carries
  `reality_correction_factor = 1 # 1.1475 #good ol' fashioned fudge`. Either it's needed (in which
  case understand *why* — probably the missing spiral geometry or lead-ins) or it should go.
- **Validate against hardware.** `genki/scripts/z_vna_scattering.py` already converts measured VNA
  S-parameters into a Z-matrix. That is precisely comparable to FEMM's L/M matrix. Wiring those two
  together gives a real, quantified accuracy figure for the simulator — and there is a `3d_printer.py`
  positioner script sitting right there to automate the measured distance sweep.
- **Add a regression corpus.** The 114 archived runs, especially the four hand-named milestones,
  are a ready-made test suite. Once results are structured (1.3), lock the key numbers in and assert
  a refactor doesn't move them.

**1.10 — Separate compute from presentation.**

The 6×4 matplotlib grid is constructed at module import, before any simulation happens, and
`disp_pane` is passed *down into* the drawing functions so they can plot as a side effect. Plotting
should consume a finished `SimResult`, not be threaded through the solver.

### Phase 2 — The GUI

Once Phase 1 exists, the GUI is a thin client. Suggested scope:

| Screen | Contents |
|---|---|
| **Design editor** | Auto-generated form from the `CoilSet` schema, grouped by coil. Live cross-section preview (the geometry drawing is already separable — `draw_and_plot_coil_traces` renders it). Run `check_valid_coilset()` on every keystroke and show the failure reason inline — that validator already exists and returns a human-readable string. |
| **Sweep builder** | Pick fields, give ranges, see the resulting job count *before* launching. ("This will run 3,240 FEMM solves ≈ 9 hours.") |
| **Run monitor** | Job queue, per-worker progress, live-updating plots, cancel. |
| **Results browser** | Load past runs from structured output, overlay multiple configs on the same axes, diff two `CoilSet`s field by field, export CSV. |
| **Library** | Browse/clone/save named designs. `coil_configs.py` becomes the seed data. `ferrite_and_core_info.py` becomes a searchable materials picker, with `toroid_parameter_selector.py` as a "find me a core that hits 0.9 µH" tool. |

Practical notes:

- **Run the solver out-of-process.** FEMM opens a real GUI window and blocks; never run it on the UI
  thread. Client ↔ job server over HTTP/WebSocket.
- **FEMM is Windows-native.** Decide early whether the GUI is a local desktop app on the engineer's
  Windows machine, or a web UI talking to a Windows job runner. That choice constrains everything.
- **Tech:** if the team is Python-first, a local web UI (FastAPI + a small React/Svelte front end)
  reusing the existing matplotlib figures as images is the lowest-friction path and gives you the
  LLM's HTTP interface for free. A native Qt app is fine too but buys you nothing for Phase 3.
- **Don't rebuild the plots first.** The existing composite SVG is dense but genuinely informative and
  the team already reads it. Ship it as-is inside the GUI; improve later.

### Phase 3 — LLM-driven parameter search

This is the "second phase" goal, and it is *achievable* — but be clear-eyed about what makes it work.

**What the LLM actually needs (all of it from Phase 1):**

1. **A callable tool** — `simulate(coilset_json) -> result_json`. Phase 1.1 + 1.3.
2. **A schema with units and bounds** so it proposes physically meaningful values. Phase 1.5.
3. **A cheap validator** — `check_valid_coilset()` already exists. Let the LLM call it *before*
   spending a 20-minute solve on a geometry where the traces overlap. This alone will save most of
   the wasted compute.
4. **A scalar objective.** "Better" must be defined. Something like:
   `maximise efficiency at 30 mm, subject to: Vrect ≤ 85 V, peak ferrite B ≤ 0.25 T, Ipa ≤ 10 A,
   coil fits in a 200 mm envelope.` Without this the loop has no gradient to follow.
5. **Memory of what's been tried** — the structured results store from 1.3 doubles as the search
   history, so the model can see its own trajectory instead of re-proposing the same design.

**Design of the loop:**

```
  LLM proposes CoilSet delta
        │
        ▼
  check_valid_coilset()  ──fail──►  reason string back to LLM  (free, instant)
        │ pass
        ▼
  cache lookup by geometry hash  ──hit──►  return cached result  (free)
        │ miss
        ▼
  queue FEMM job  ──►  SimResult (structured)
        │
        ▼
  score against objective + constraints  ──►  back to LLM with history
```

**Things to get right:**

- **Cost discipline.** A solve is expensive (minutes). Every LLM iteration that could have been
  caught by the validator or the cache is real money and real time. Gate aggressively.
- **Don't ask the LLM to be an optimiser.** Classical methods (Bayesian optimisation, Nelder-Mead,
  or plain grid search on 2–3 variables) beat an LLM at squeezing the last 2% out of a continuous
  parameter. Where an LLM genuinely helps is the *structural* reasoning a numerical optimiser can't
  do: "the tank is detuning because the shield is too close — move it and re-tune rather than
  adding turns", "these two parameters trade off, here's why", reading the loss breakdown and
  proposing *which* knob to turn. **Consider a hybrid: LLM chooses the search direction and the
  variables; a numerical optimiser does the fine sweep.**
- **A surrogate model would change the economics.** Once you have a few thousand structured results
  (Phase 1.3 makes this accumulate automatically), fit a cheap regressor from geometry → L/M. The
  LLM can then explore thousands of candidates in milliseconds and only spend real FEMM solves
  confirming the promising ones.
- **Guardrails.** The LLM must not be able to propose something that damages hardware if built.
  Encode the hard limits (`VDS_MAX`, `VRECT_MAX`, `IPA_MAX`, `B_sat`, manufacturable trace
  width/spacing) as constraints the scorer enforces — not as advice in a prompt.
- **Always show the human the diff.** Design changes end up as physical PCBs. Every accepted
  proposal should be a reviewable `CoilSet` diff with the predicted before/after metrics.

### Consolidation to do along the way

There are currently **three** `CoilSet` definitions (`femm_model`, `genki_femm_simple`,
`em_sim/femm_coils`), **two** ferrite/core databases (`ferrite_and_core_info.py` and a duplicate
inside `em_sim/femm_toroid.py`), **two** complete FEMM coil-drawing implementations, **three**
toroidal scripts (two byte-identical), and **two** `planar_inductor.py`. Pick one lineage per
concept *before* wrapping a UI around it, or you'll wrap the wrong one.

The `genki/` implementations are structurally better (dataclasses, context managers, `argparse`,
`main()`, PEP 723 deps). The `femm_backend/` implementation is more complete and more
validated (it models ferrite, shields, three coils, losses, and the full circuit). **The likely right
answer is: keep Noel's physics, adopt Genki's structure.**

---

## Part 9 — A learning path

**If you have a weekend**, in this order:

1. **Install FEMM** (femm.info) and work through the built-in tutorial. Draw a coil by hand in the
   GUI, solve it, click around the post-processor. One hour here is worth ten hours of reading —
   you'll immediately understand what block labels and boundary conditions are for.
2. **Run `genki/em_sim/femm_coils.py --freq-khz 6780`.** It's 282 readable lines that do the complete
   inductance-and-coupling extraction. Watch FEMM draw. Then read the file top to bottom.
3. **Read `femm_model.py`** — particularly `init_femm`, `loop_inductance`, and
   `get_inductances`. That's the whole method in ~120 lines.
4. **Then** open `compare_coilsets.py`. It'll be much less intimidating once you know that the
   1320 lines are mostly plumbing around those three functions.

**Reference material:**

- **FEMM manual** — femm.info/wiki/Documentation. Short and readable. The "Lua/ActiveX command set"
  section is the API reference for everything with an `mi_`/`mo_` prefix.
- **pyFEMM manual** (PDF, ships with FEMM) — referenced by a TODO in `genki_femm_simple.py`.
- **FEMM FAQ #27** on what imaginary flux linkage means — cited directly in
  `em_sim/femm_coils.py`, and it's what makes the ESR extraction work.
- **Any first-year "Electricity and Magnetism" chapter** on Faraday's law, inductance, and AC circuits.
  You need Parts 1 and 2 of this document at a working level; you do not need Maxwell's equations in
  differential form.
- **Wireless power specifically:** search for "resonant inductive coupling", "figure of merit
  k·√(Q₁Q₂)", and the AirFuel / Rezence 6.78 MHz standard.
- **The repo's own `HOW-TO.md`** — the original author's description of the main script's flow.
