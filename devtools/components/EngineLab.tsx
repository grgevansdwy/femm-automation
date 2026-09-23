"use client";
import Link from "next/link";
import { useState } from "react";
import { ConfigPicker } from "./ConfigPicker";
import { CrossSection, type Shape } from "./CrossSection";
import { useApi } from "./useApi";
import { fmtHenry, fmtOhm, fmtSecs } from "@/lib/format";

type Section = { label: string; start: number; end: number; note: string; preview: string; line_count: number };
type Pipeline = {
  validity: { valid: boolean; reason: string; seconds: number };
  currents: { per_turn_amps: Record<string, number>; targets: Record<string, number>; seconds: number };
  script: { seconds: number; rendered_lines: number; raw_lines: number; bytes: number; call_counts: Record<string, number>; sections: Section[]; raw_lines_text: string[]; tail: string[]; solve_count: number; shape_count: number };
};
type Sim = { stages: Record<string, number>; raw: Record<string, { re: number; im: number }>; result: Record<string, any> };

const STEPS = [
  { id: "validate", title: "Check the coil can exist", fn: "simulate()" },
  { id: "currents", title: "Decide the drive currents", fn: "turn_currents()" },
  { id: "geometry", title: "Draw it (as a script)", fn: "build_geometry()" },
  { id: "solves", title: "Add three solves", fn: "build_script()" },
  { id: "run", title: "Run the solver", fn: "run_femmcli()" },
  { id: "extract", title: "Read out the 12 numbers", fn: "extract_result()" },
];

export function EngineLab({ refs }: { refs: Record<string, string> }) {
  const [id, setId] = useState("Gen2_coil");
  const [distance, setDistance] = useState(5);
  const [step, setStep] = useState("validate");
  const pipe = useApi<Pipeline>(`/api/pipeline?id=${id}&distance=${distance}`);
  const geo = useApi<{ shapes: Shape[] }>(step === "geometry" ? `/api/geometry?id=${id}&distance=${distance}` : null);
  const cfg = useApi<{ fields: Record<string, any> }>(`/api/config?id=${id}`);
  const [sim, setSim] = useState<Sim | null>(null);
  const [simErr, setSimErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openSec, setOpenSec] = useState<string | null>(null);

  async function run() {
    setBusy(true); setSimErr(null); setSim(null);
    const j = await fetch(`/api/simulate?id=${id}&distance=${distance}`).then((r) => r.json());
    if (j.error) setSimErr(j.error); else setSim(j);
    setBusy(false);
  }
  const P = pipe.data;
  const cur = STEPS.find((s) => s.id === step)!;

  return (
    <div>
      <ConfigPicker value={id} onChange={(v) => { setId(v); setSim(null); }} distance={distance} onDistance={(v) => { setDistance(v); setSim(null); }} />
      {pipe.error && <div className="err" style={{ marginTop: 12 }}>{pipe.error}</div>}
      <div className="lab" style={{ marginTop: 14 }}>
        <div className="grid lab-steps" style={{ gap: 6 }}>
          {STEPS.map((s, i) => (
            <button key={s.id} onClick={() => setStep(s.id)} style={{ textAlign: "left", borderColor: step === s.id ? "var(--engine)" : undefined, background: step === s.id ? "var(--engine-bg)" : undefined }}>
              <span className="muted" style={{ marginRight: 8 }}>{i + 1}</span>{s.title}
              <div className="small muted" style={{ fontFamily: "var(--mono)", fontWeight: 400 }}>{s.fn}</div>
            </button>
          ))}
        </div>
        <div className="card" style={{ borderTop: "4px solid var(--engine)", minHeight: 320 }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
            <b style={{ fontSize: 17 }}>{STEPS.findIndex((s) => s.id === step) + 1}. {cur.title}</b>
            <Link className="fileref" href={refs[step]}>open {cur.fn} in code</Link>
          </div>

          {step === "validate" && (
            <div>
              <p>Before spending seconds on a solve, <code>simulate()</code> asks a fast yes/no question: <b>is this geometry physically possible?</b> It uses simple arithmetic per coil:</p>
              <pre className="code">{`turn_width = (parallel_traces - 1) * trace_spacing + trace_width
coil_width = (turns - 1) * turn_spacing + turn_width

INVALID if  trace_spacing < trace_width   (parallel traces would overlap)
INVALID if  turn_spacing  < turn_width    (turns would overlap)
INVALID if  centre_radius < coil_width/2  (the winding would cross the axis)`}</pre>
              {P && (P.validity.valid
                ? <p><span className="pill good">valid</span> &nbsp;This design passes, in {fmtSecs(P.validity.seconds)}.</p>
                : <p><span className="pill bad">invalid</span> &nbsp;{P.validity.reason}</p>)}
              <div className="callout watch"><div className="t">Worth knowing</div><p>If this fails, <code>simulate()</code> raises an exception and nothing is drawn. During an AI search that error is turned into a message the model reads. Note the AI loop does <i>not</i> run this check itself first: it happens here, after the candidate is already queued as a job.</p></div>
            </div>
          )}

          {step === "currents" && (
            <div>
              <p>Each turn of each coil is its own electrical circuit in FEMM. The simulator needs to say how much current each turn carries. The target (set once in <code>data/constants.json</code>) is divided across the turns, so the coil has the same total &quot;amp-turns&quot; as a real coil wound from those traces.</p>
              {P && cfg.data && (
                <table className="t"><thead><tr><th>Coil</th><th>Test-current target</th><th>Turns</th><th>Current in each turn</th></tr></thead><tbody>
                  {(["loop", "tank", "rc"] as const).map((c) => (
                    <tr key={c}><td><b>{c}</b></td><td className="num">{P.currents.targets[c]} A</td><td className="num">{cfg.data!.fields[`num_${c}_turns`]}</td><td className="num"><b>{P.currents.per_turn_amps[c]} A</b></td></tr>
                  ))}
                </tbody></table>
              )}
              <div className="callout why"><div className="t">Why it's built this way</div><p>Parallel traces and layers cancel out of the formula (they split the current and then it is re-added), so the answer is just <code>target ÷ turns</code>. The value itself is arbitrary: inductance and resistance are ratios (flux or voltage per amp), so any non-zero test current gives the same L, M and ESR.</p></div>
            </div>
          )}

          {step === "geometry" && P && (
            <div>
              <p>The engine never talks to FEMM live. It <b>writes a Lua script</b>, line by line, describing the whole model. <code>build_geometry()</code> produces the first {Object.values(P.script.sections).filter((s) => !s.label.startsWith("Solve")).reduce((a, s) => a + s.line_count, 0)} lines; the picture below is that script, drawn.</p>
              {geo.data ? <CrossSection shapes={geo.data.shapes} height={300} /> : <div className="muted small">Drawing…</div>}
              <h3>The script, in sections (click to read)</h3>
              {P.script.sections.filter((s) => !s.label.startsWith("Solve")).map((s) => (
                <details className="d" key={s.label} open={openSec === s.label} onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) setOpenSec(s.label); }}>
                  <summary>{s.label} <span className="muted small">· {s.line_count} lines</span></summary>
                  <div className="body"><p className="small">{s.note}</p><pre className="code">{P.script.raw_lines_text.slice(s.start, Math.min(s.end, s.start + 24)).join("\n")}{s.line_count > 24 ? `\n… (${s.line_count - 24} more lines)` : ""}</pre></div>
                </details>
              ))}
              <p className="small muted">Every line is one FEMM command. Here is how many of each the whole script uses:</p>
              <div className="row" style={{ gap: 6 }}>{Object.entries(P.script.call_counts).map(([k, v]) => <span key={k} className="fileref">{k} ×{v}</span>)}</div>
            </div>
          )}

          {step === "solves" && P && (
            <div>
              <p>One solve can only measure <i>one row</i> of the coupling table: drive one coil with current and read what shows up in the others. So <code>build_script()</code> appends <b>three</b> solves: drive loop, then tank, then RC. In each, the other two coils are held at 0 A and their induced flux becomes the mutual inductance.</p>
              {P.script.sections.filter((s) => s.label.startsWith("Solve")).map((s) => (
                <details className="d" key={s.label}><summary>{s.label}</summary><div className="body">
                  <p className="small">{s.note}</p><pre className="code">{P.script.raw_lines_text.slice(s.start, s.end).join("\n")}</pre></div></details>
              ))}
              <details className="d"><summary>The tail: writing the result file</summary><div className="body">
                <p className="small">After the last solve, <code>render()</code> adds lines that write every named value into <code>result.txt</code>, which Python reads back.</p>
                <pre className="code">{P.script.tail.join("\n")}</pre></div></details>
              <div className="callout real"><div className="t">Real, for this design</div><p>{P.script.solve_count} solves, {P.script.shape_count} drawn shapes, {P.script.rendered_lines} lines of Lua ({(P.script.bytes / 1024).toFixed(1)} KB), assembled in {fmtSecs(P.script.seconds)}. Reciprocity check: M12 should equal M21 (the script computes both directions).</p></div>
            </div>
          )}

          {step === "run" && (
            <div>
              <p><code>run_femmcli()</code> creates a <b>fresh temporary folder</b>, writes <code>model.lua</code> into it, and starts the solver as a separate process (<code>femmcli --lua-script model.lua</code>). The solver meshes the geometry, solves three times, and writes <code>result.txt</code>. Python reads it and the folder is deleted.</p>
              <ul><li>Its own folder per call means many solves can run at once without touching each other&apos;s files.</li>
                <li>The solver is a patched copy kept in <code>backend/engine/vendor/</code>: stock xfemm rotated every mutual inductance by 90°, found and fixed during the migration off Wine.</li>
                <li>There is a hard 120-second timeout per solve.</li></ul>
              <div className="row"><button className="primary" onClick={run} disabled={busy}>{busy ? "Solving…" : "Run it for real"}</button><span className="small muted">Same steps as simulate(); nothing is saved.</span></div>
              {simErr && <div className="err" style={{ marginTop: 10 }}>{simErr}</div>}
              {sim && (
                <div style={{ marginTop: 12 }}>
                  <div className="row" style={{ gap: 20 }}>
                    <div><div className="small muted">build script</div><b>{fmtSecs(sim.stages.build_script_s)}</b></div>
                    <div><div className="small muted">solver (meshing + 3 solves)</div><b>{fmtSecs(sim.stages.femmcli_s)}</b></div>
                    <div><div className="small muted">read results</div><b>{fmtSecs(sim.stages.extract_s)}</b></div>
                  </div>
                  <div className="bar" style={{ marginTop: 8, maxWidth: 500 }}><i style={{ width: "100%", background: "var(--solver)" }} /></div>
                  <p className="small muted">Almost all the time is the solver. Everything on the Python side is under a millisecond.</p>
                  <h3>What the solver wrote to result.txt</h3>
                  <p className="small">FEMM works with complex numbers (real + imaginary part). The engine keeps only the <b>real</b> part.</p>
                  <div className="scroll-x"><table className="t"><thead><tr><th>Key</th><th>real part</th><th>imaginary part</th></tr></thead><tbody>
                    {Object.entries(sim.raw).map(([k, v]) => <tr key={k}><td><code>{k}</code></td><td className="num">{v.re.toExponential(4)}</td><td className="num muted">{v.im.toExponential(3)}</td></tr>)}
                  </tbody></table></div>
                </div>
              )}
            </div>
          )}

          {step === "extract" && (
            <div>
              <p><code>extract_result()</code> renames those raw keys into the <code>SimResult</code> the rest of the app uses, and computes the three coupling coefficients.</p>
              <pre className="code">{`L_loop, M12, M13, loop_esr = real('L_loop'), real('M_tank_loop'), real('M_rc_loop'), real('R_loop')
L_tank, M23, tank_esr      = real('L_tank'), real('M_rc_tank'),   real('R_tank')
L_rc,   rc_esr             = real('L_rc'),   real('R_rc')

k13 = M13 / sqrt(L_loop * L_rc)
k12 = M12 / sqrt(L_loop * L_tank)     # 0 if there is no tank coil
k23 = M23 / sqrt(L_tank * L_rc)`}</pre>
              {sim ? (
                <table className="t"><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>
                  {Object.entries(sim.result).filter(([k]) => k !== "config_name" && k !== "distance").map(([k, v]) => (
                    <tr key={k}><td><code>{k}</code></td><td className="num">{k.startsWith("L_") || k.startsWith("M") ? fmtHenry(v) : k.endsWith("esr") ? fmtOhm(v) : Number(v).toFixed(4)}</td></tr>))}
                </tbody></table>
              ) : <p className="muted small">Run the solver in step 5 to fill this table with real values.</p>}
              <div className="callout why"><div className="t">Why it's built this way</div><p>The <code>float()</code> around each k looks fussy. A comment in the code explains it: numpy numbers made a later comparison return <code>numpy.bool_</code>, which JSON cannot save, and that crashed an AI search on its first fresh solve.</p></div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
