import { Callout, PageHead, FileRef } from "@/components/ui";
import { EngineLab } from "@/components/EngineLab";
import { codeHref } from "@/lib/repo";

const C = "backend/engine/core.py";

export default function Engine() {
  const refs = {
    validate: codeHref(C, "def simulate"), currents: codeHref(C, "def turn_currents"), geometry: codeHref(C, "def build_geometry"),
    solves: codeHref(C, "def build_script"), run: codeHref("backend/engine/services/xfemm_backend.py", "def run_femmcli"), extract: codeHref(C, "def extract_result"),
  };
  return (
    <>
      <PageHead eyebrow="How it works" title="The engine: what happens inside one simulation"
        lede={<>Everything else (the app, the AI) eventually calls one function: <code>simulate(coilset, distance)</code>. This page walks through it step by step, using a <b>real design</b> and the <b>real backend</b>. Change the design or distance and every step updates.</>} />
      <Callout kind="plain">
        <p>Think of it as a recipe card. <code>simulate()</code> checks the ingredients, writes a long instruction sheet in FEMM&apos;s own language, hands the sheet to a separate program, waits, and reads the answer. Python never &quot;does&quot; the physics; it only writes the sheet and reads the result.</p>
      </Callout>
      <EngineLab refs={refs} />
      <h2>Why a script and not a live connection?</h2>
      <p>The project used to drive the real FEMM program through Wine (a Windows compatibility layer): one solve at a time, with a window that stole your mouse focus. It now uses <b>xfemm</b>, an open-source rewrite that only runs whole scripts. The catch is that Python can no longer ask &quot;what is the flux so far?&quot; halfway through, so the maths that used to run in Python after each solve is now written into the script itself (<code>sum_circuit()</code>, the helper at the top).</p>
      <div className="grid g2">
        <div className="card"><b>Gained</b><ul className="small"><li>About 13× faster per solve</li><li>Several solves at once (each is its own process)</li><li>No window, no Wine</li></ul></div>
        <div className="card"><b>Cost</b><ul className="small"><li>The Lua writer must mirror every FEMM command by hand. A missed one fails silently or gives wrong physics.</li><li>No automated test guards the two backends agreeing.</li></ul></div>
      </div>
      <p className="small">Read next: <FileRef file="docs/Engine.md" /> (full history), <FileRef file="backend/engine/vendor/README.md" /> (the 90° bug).</p>
    </>
  );
}
