"use client";
import { useState } from "react";
import { ConfigPicker } from "./ConfigPicker";
import { CrossSection, type Shape } from "./CrossSection";
import { useApi } from "./useApi";
import { fmtHenry, fmtOhm } from "@/lib/format";

type Sim = { config: string; distance: number; stages: Record<string, number>; raw: Record<string, any>; result: Record<string, number> };

const ROWS: { key: string; label: string; what: string; kind: "H" | "R" | "k" }[] = [
  { key: "L_loop", label: "L loop", what: "self-inductance of the loop coil", kind: "H" },
  { key: "L_tank", label: "L tank", what: "self-inductance of the tank coil", kind: "H" },
  { key: "L_rc", label: "L rc", what: "self-inductance of the receive coil", kind: "H" },
  { key: "M12", label: "M12", what: "mutual inductance, loop ↔ tank", kind: "H" },
  { key: "M13", label: "M13", what: "mutual inductance, loop ↔ RC", kind: "H" },
  { key: "M23", label: "M23", what: "mutual inductance, tank ↔ RC (the power-transfer link)", kind: "H" },
  { key: "k12", label: "k12", what: "coupling, loop ↔ tank", kind: "k" },
  { key: "k13", label: "k13", what: "coupling, loop ↔ RC", kind: "k" },
  { key: "k23", label: "k23", what: "coupling, tank ↔ RC", kind: "k" },
  { key: "loop_esr", label: "ESR loop", what: "resistance lost in the loop coil at 6.78 MHz", kind: "R" },
  { key: "tank_esr", label: "ESR tank", what: "resistance lost in the tank coil", kind: "R" },
  { key: "rc_esr", label: "ESR rc", what: "resistance lost in the receive coil", kind: "R" },
];

function show(kind: "H" | "R" | "k", v: number) { return kind === "H" ? fmtHenry(v) : kind === "R" ? fmtOhm(v) : v.toFixed(4); }

export function PhysicsLab() {
  const [id, setId] = useState("Gen2_coil");
  const [distance, setDistance] = useState(5);
  const geo = useApi<{ shapes: Shape[] }>(`/api/geometry?id=${id}&distance=${distance}`);
  const cfg = useApi<{ fields: Record<string, number | string>; diameters: Record<string, number> }>(`/api/config?id=${id}`);
  const [sim, setSim] = useState<Sim | null>(null);
  const [simErr, setSimErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sweep, setSweep] = useState<Sim[] | null>(null);
  const [sweepBusy, setSweepBusy] = useState<number | null>(null);

  async function run() {
    setBusy(true); setSimErr(null); setSim(null);
    const r = await fetch(`/api/simulate?id=${id}&distance=${distance}`); const j = await r.json();
    if (j.error) setSimErr(j.error); else setSim(j);
    setBusy(false);
  }
  async function runSweep() {
    const ds = [3, 5, 8, 12]; setSweep(null); setSweepBusy(Date.now());
    const t0 = Date.now();
    const all = await Promise.all(ds.map((d) => fetch(`/api/simulate?id=${id}&distance=${d}`).then((r) => r.json())));
    const ok = all.filter((x) => !x.error) as Sim[];
    setSweep(ok.map((x) => ({ ...x, stages: { ...x.stages, wall: (Date.now() - t0) / 1000 } })));
    setSweepBusy(null);
  }
  const f = cfg.data?.fields;
  const d = cfg.data?.diameters;

  return (
    <div>
      <ConfigPicker value={id} onChange={(v) => { setId(v); setSim(null); setSweep(null); }} distance={distance} onDistance={(v) => { setDistance(v); setSim(null); }} />
      <div style={{ marginTop: 14 }}>
        <div className="card">
          {geo.error ? <div className="err">{geo.error}</div> : geo.data ? <CrossSection shapes={geo.data.shapes} /> : <div className="muted">Building the geometry with the real backend…</div>}
        </div>
      </div>
      <div className="split-side" style={{ marginTop: 14 }}>
        <div className="card">
          <b>This design</b>
          {f && d ? (
            <div className="scroll-x"><table className="t" style={{ marginTop: 6 }}><thead><tr><th></th><th>turns</th><th>layers</th><th>parallel</th><th>outer ⌀</th></tr></thead><tbody>
              <tr><td style={{ color: "#2f6fdb", fontWeight: 700 }}>Loop</td><td>{f.num_loop_turns}</td><td>{f.num_loop_layers}</td><td>{f.num_parallel_loop_traces}</td><td className="num">{d.loop_outer_diameter.toFixed(1)} mm</td></tr>
              <tr><td style={{ color: "#8a5ae0", fontWeight: 700 }}>Tank</td><td>{f.num_tank_turns}</td><td>{f.num_tank_layers}</td><td>{f.num_parallel_tank_traces}</td><td className="num">{d.tank_outer_diameter.toFixed(1)} mm</td></tr>
              <tr><td style={{ color: "#e0812a", fontWeight: 700 }}>RC</td><td>{f.num_rc_turns}</td><td>{f.num_rc_layers}</td><td>{f.num_parallel_rc_traces}</td><td className="num">{d.rc_outer_diameter.toFixed(1)} mm</td></tr>
            </tbody></table></div>
          ) : <div className="muted small">Loading…</div>}
          {f && <p className="small muted" style={{ marginTop: 10 }}>
            Ferrite: {f.tank_ferrite_present || f.rc_ferrite_present ? "yes" : "none"} · Shield/backplane: {f.tc_shield || f.rc_shield || f.backplane || f.rc_backplane ? "yes" : "none"} · Copper: loop {f.loop_trace_thickness} / tank {f.tank_trace_thickness} / rc {f.rc_trace_thickness} mm thick
          </p>}
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" onClick={run} disabled={busy}>{busy ? "Solving…" : "Run the real solver"}</button>
            <button onClick={runSweep} disabled={sweepBusy !== null}>{sweepBusy ? "Running 4 at once…" : "Sweep 3 / 5 / 8 / 12 mm"}</button>
          </div>
          <p className="small muted" style={{ marginTop: 8 }}>Runs <code>engine.simulate()</code>&apos;s exact steps through the real <code>femmcli</code>. Nothing is saved to your database.</p>
        </div>
      </div>

      {simErr && <div className="err" style={{ marginTop: 14 }}>{simErr}</div>}
      {sim && (
        <div className="card" style={{ marginTop: 14 }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
            <b>The 12 numbers, at {sim.distance} mm</b>
            <span className="small muted">solver took {sim.stages.femmcli_s.toFixed(1)} s</span>
          </div>
          <table className="t" style={{ marginTop: 6 }}><thead><tr><th>Name</th><th>Value</th><th>Meaning</th></tr></thead><tbody>
            {ROWS.map((r) => <tr key={r.key}><td><b>{r.label}</b></td><td className="num">{show(r.kind, sim.result[r.key])}</td><td className="small">{r.what}</td></tr>)}
          </tbody></table>
          <p className="small" style={{ marginTop: 10 }}>
            Check the coupling yourself: k23 = M23 / √(L tank × L rc) = {sim.result.M23.toExponential(3)} / √({sim.result.L_tank.toExponential(3)} × {sim.result.L_rc.toExponential(3)}) = <b>{(sim.result.M23 / Math.sqrt(sim.result.L_tank * sim.result.L_rc)).toFixed(4)}</b> &nbsp;(reported: {sim.result.k23.toFixed(4)}).
          </p>
        </div>
      )}
      {sweep && sweep.length > 0 && <Sweep data={sweep} />}
    </div>
  );
}

function Sweep({ data }: { data: Sim[] }) {
  const pts = [...data].sort((a, b) => a.distance - b.distance);
  const W = 520, H = 220, pad = 40;
  const xs = pts.map((p) => p.distance), xMin = Math.min(...xs), xMax = Math.max(...xs);
  const X = (x: number) => pad + ((x - xMin) / (xMax - xMin || 1)) * (W - pad - 14);
  const series = [{ key: "k23", color: "#8a5ae0", name: "k23" }, { key: "k13", color: "#e0812a", name: "k13" }, { key: "k12", color: "#2f6fdb", name: "k12" }];
  const Y = (v: number) => H - 28 - v * (H - 48);
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <b>How coupling falls with distance</b>
      <p className="small muted">These {pts.length} solves ran <i>at the same time</i> as separate processes: {pts[0].stages.wall.toFixed(1)} s total, versus about {(pts.reduce((a, p) => a + p.stages.femmcli_s, 0)).toFixed(0)} s if run one after another.</p>
      <svg className="diagram" viewBox={`0 0 ${W} ${H}`} style={{ maxWidth: 560 }}>
        {[0, 0.25, 0.5, 0.75, 1].map((v) => <g key={v}><line x1={pad} x2={W - 10} y1={Y(v)} y2={Y(v)} stroke="var(--line)" /><text x={pad - 6} y={Y(v) + 4} fontSize={10} textAnchor="end" style={{ fill: "var(--ink-3)" }}>{v}</text></g>)}
        {pts.map((p) => <text key={p.distance} x={X(p.distance)} y={H - 10} fontSize={11} textAnchor="middle" style={{ fill: "var(--ink-3)" }}>{p.distance} mm</text>)}
        {series.map((s) => (
          <g key={s.key}>
            <polyline fill="none" stroke={s.color} strokeWidth={2.4} points={pts.map((p) => `${X(p.distance)},${Y(p.result[s.key])}`).join(" ")} />
            {pts.map((p) => <circle key={p.distance} cx={X(p.distance)} cy={Y(p.result[s.key])} r={3.6} fill={s.color} />)}
            <text x={X(pts[pts.length - 1].distance) - 4} y={Y(pts[pts.length - 1].result[s.key]) - 8} fontSize={11} textAnchor="end" fontWeight={700} style={{ fill: s.color }}>{s.name}</text>
          </g>
        ))}
      </svg>
      <p className="small">This curve is what the AI&apos;s main goal is about: keep it (within a tolerance) unchanged while making the coils waste less energy.</p>
    </div>
  );
}
