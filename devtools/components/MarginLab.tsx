"use client";
import { useState } from "react";

const MIN_SCALE = 1; // objective.MIN_SCALE_PCT is applied in code; shown here as a floor of 1%

export function MarginLab() {
  const [kind, setKind] = useState<"match" | "reduce" | "increase">("match");
  const [tol, setTol] = useState(5);
  const [target, setTarget] = useState(0);
  const [changes, setChanges] = useState("-0.3, 1.1, 4.2, 6.5");
  const ds = [3, 5, 8, 12];
  const ch = changes.split(",").map((x) => parseFloat(x));
  const margin = (c: number) => {
    const raw = kind === "match" ? tol - Math.abs(c - target) : kind === "reduce" ? -c - tol : c - tol;
    return raw / Math.max(tol, MIN_SCALE);
  };
  const rows = ds.map((d, i) => ({ d, c: ch[i], m: Number.isFinite(ch[i]) ? margin(ch[i]) : NaN }));
  const valid = rows.filter((r) => Number.isFinite(r.m));
  const worst = valid.length ? valid.reduce((a, b) => (b.m < a.m ? b : a)) : null;
  return (
    <div className="card">
      <div className="row">
        <label className="f">Kind<select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}><option>match</option><option>reduce</option><option>increase</option></select></label>
        <label className="f">tolerance %<input type="number" value={tol} onChange={(e) => setTol(Number(e.target.value))} style={{ width: 90 }} /></label>
        {kind === "match" && <label className="f">target % vs baseline<input type="number" value={target} onChange={(e) => setTarget(Number(e.target.value))} style={{ width: 90 }} /></label>}
        <label className="f">% change at 3 / 5 / 8 / 12 mm<input type="text" value={changes} onChange={(e) => setChanges(e.target.value)} style={{ width: 200 }} /></label>
      </div>
      <table className="t" style={{ marginTop: 10, maxWidth: 520 }}><thead><tr><th>Distance</th><th>Change vs baseline</th><th>Margin</th><th>Result</th></tr></thead><tbody>
        {rows.map((r) => (
          <tr key={r.d} style={worst && r.d === worst.d ? { background: "var(--warn-bg)" } : undefined}>
            <td>{r.d} mm</td><td className="num">{Number.isFinite(r.c) ? `${r.c > 0 ? "+" : ""}${r.c}%` : "–"}</td>
            <td className="num">{Number.isFinite(r.m) ? r.m.toFixed(2) : "–"}</td>
            <td>{Number.isFinite(r.m) ? (r.m >= 0 ? <span className="pill good">pass</span> : <span className="pill bad">fail</span>) : ""}</td>
          </tr>))}
      </tbody></table>
      {worst && <p style={{ marginTop: 10 }}><b>Worst distance: {worst.d} mm</b> (margin {worst.m.toFixed(2)}). The objective as a whole is <b style={{ color: worst.m >= 0 ? "var(--good)" : "var(--bad)" }}>{worst.m >= 0 ? "passed" : "failed"}</b>: the worst distance decides.</p>}
      <p className="small muted">Formulas from <code>objective.margin()</code>: match → tolerance − |change − target|; reduce → −change − tolerance; increase → change − tolerance; all divided by the tolerance (so margins of different objectives compare fairly). Try &quot;reduce&quot;, tolerance 20, changes −25, −22, −19, −30.</p>
    </div>
  );
}
