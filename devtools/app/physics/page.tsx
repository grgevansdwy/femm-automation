import { Callout, PageHead, FileRef } from "@/components/ui";
import { PhysicsLab } from "@/components/PhysicsLab";

export default function Physics() {
  return (
    <>
      <PageHead eyebrow="Start here" title="What is actually being measured?"
        lede="Before any code makes sense, you need four ideas. They are the only things the simulator returns, and the only things the AI is trying to improve." />

      <h2>Three coils on a stack of circuit boards</h2>
      <p>A design has three flat spiral coils printed on PCBs. They sit one above the other, on a shared centre axis, with a gap you choose between the top one and the rest.</p>
      <div className="grid g3">
        <div className="card" style={{ borderTop: "4px solid #2f6fdb" }}><b>Loop</b><p className="small">Bottom coil, driven directly. Together with the tank it forms the <i>transmit</i> side (called &quot;TC&quot; in the prompts).</p></div>
        <div className="card" style={{ borderTop: "4px solid #8a5ae0" }}><b>Tank</b><p className="small">The resonant coil the loop excites. This one matters most: reducing <i>its</i> resistance is a headline goal.</p></div>
        <div className="card" style={{ borderTop: "4px solid #e0812a" }}><b>RC (receive coil)</b><p className="small">Sits a chosen distance above and picks up the energy. Its distance is what the simulator sweeps (3, 5, 8, 12 mm…).</p></div>
      </div>

      <h2>See a real design, drawn by the backend</h2>
      <p>Pick any saved design. The picture is not a mock-up: it is built by running the engine&apos;s own <code>build_geometry()</code> and reading back the rectangles it tells FEMM to draw.</p>
      <PhysicsLab />

      <Callout kind="plain" title="Why the picture looks like a slice">
        <p>FEMM solves this in <b>axisymmetric</b> mode: it only models a half-slice (radius left-to-right, height up) and mathematically spins it around the axis. So each little rectangle above is really a <i>ring</i> of copper. That trick turns a 3-D problem into a 2-D one, which is why a solve takes seconds instead of minutes.</p>
      </Callout>

      <h2>The four ideas</h2>
      <div className="grid g2">
        <div className="card"><h3 style={{ marginTop: 0 }}>Self inductance <code>L</code></h3>
          <p className="small">How much magnetic field a coil creates for a given current. Bigger coils and more turns give bigger L. Named <code>L_loop</code>, <code>L_tank</code>, <code>L_rc</code>. Measured in nanohenries (nH) or microhenries (µH).</p></div>
        <div className="card"><h3 style={{ marginTop: 0 }}>Mutual inductance <code>M</code></h3>
          <p className="small">How much of <i>one</i> coil&apos;s field passes through <i>another</i>. This is the link that carries power. <code>M12</code> loop↔tank, <code>M13</code> loop↔RC, <code>M23</code> tank↔RC. It is strongest close together and fades with distance.</p></div>
        <div className="card"><h3 style={{ marginTop: 0 }}>Coupling <code>k</code></h3>
          <p className="small"><code>k = M / √(L₁ × L₂)</code>. The same idea as M, but scaled to a 0–1 range, so you can compare designs of different sizes. 0 = the coils ignore each other; 1 = perfect.</p></div>
        <div className="card"><h3 style={{ marginTop: 0 }}>Resistance <code>ESR</code></h3>
          <p className="small">Energy the coil wastes as heat. At 6.78 MHz current crowds onto the surface of the copper and towards neighbouring traces, so ESR depends on trace width, thickness and spacing, not just length. Lower is better.</p></div>
      </div>

      <Callout kind="why" title="So what is a good design?">
        <p>The project&apos;s stated goal (see <FileRef file="docs/archive/goals.md" />): <b>keep the mutual inductance the same across a range of distances</b> (within a tolerance like 5%) while <b>reducing ESR</b>, especially on the tank. In other words: same wireless link, less wasted energy. Every objective the AI is given is one of these numbers, compared against a baseline design.</p>
      </Callout>
      <Callout kind="watch"><p>The simulator drives each coil with an arbitrary test current (10 A, 10 A, 12 A in <code>data/constants.json</code>). The value cancels out, because L, M and ESR are ratios of voltage or flux to current. It only has to be non-zero to mark which coil is being driven.</p></Callout>
    </>
  );
}
