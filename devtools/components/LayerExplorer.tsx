"use client";
import Link from "next/link";
import { useState } from "react";

export type LayerInfo = {
  id: string; layer: "front" | "agent" | "engine" | "solver" | "data"; title: string; sub: string; folder: string;
  plain: string; does: string[]; files: { label: string; href: string; note: string }[]; talksTo: string;
};

const POS: Record<string, { x: number; y: number }> = { front: { x: 30, y: 20 }, agent: { x: 30, y: 128 }, engine: { x: 30, y: 236 }, solver: { x: 30, y: 344 } };
const COLORS: Record<string, string> = { front: "var(--front)", agent: "var(--agent)", engine: "var(--engine)", solver: "var(--solver)", data: "var(--data)" };
const BGS: Record<string, string> = { front: "var(--front-bg)", agent: "var(--agent-bg)", engine: "var(--engine-bg)", solver: "var(--solver-bg)", data: "var(--data-bg)" };

/** The whole system as one clickable stack: click a layer to read what it does and open its files. */
export function LayerExplorer({ layers }: { layers: LayerInfo[] }) {
  const [sel, setSel] = useState("agent");
  const cur = layers.find((l) => l.id === sel)!;
  const stack = layers.filter((l) => l.layer !== "data");
  const data = layers.find((l) => l.layer === "data")!;

  return (
    <div className="split">
      <svg className="diagram" viewBox="0 0 560 470" role="img" aria-label="System layers">
        <defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--ink-3)" /></marker></defs>
        {stack.map((l) => {
          const p = POS[l.layer]; const on = sel === l.id;
          return (
            <g key={l.id} onClick={() => setSel(l.id)} style={{ cursor: "pointer" }}>
              <rect x={p.x} y={p.y} width={330} height={92} rx={14} fill={BGS[l.layer]} stroke={COLORS[l.layer]} strokeWidth={on ? 3 : 1.4} />
              <text x={p.x + 18} y={p.y + 32} fontSize={17} fontWeight={700} style={{ fill: COLORS[l.layer] }}>{l.title}</text>
              <text x={p.x + 18} y={p.y + 54} fontSize={12.5} style={{ fill: "var(--ink-2)" }}>{l.sub}</text>
              <text x={p.x + 18} y={p.y + 76} fontSize={11.5} fontFamily="var(--mono)" style={{ fill: "var(--ink-3)" }}>{l.folder}</text>
            </g>
          );
        })}
        {[[112, 128], [220, 236], [328, 344]].map(([a, b], i) => <line key={i} x1={195} x2={195} y1={a} y2={b} stroke="var(--ink-3)" strokeWidth={1.6} markerEnd="url(#arr)" markerStart="url(#arr)" />)}
        <g onClick={() => setSel(data.id)} style={{ cursor: "pointer" }}>
          <rect x={400} y={128} width={150} height={200} rx={14} fill={BGS.data} stroke={COLORS.data} strokeWidth={sel === data.id ? 3 : 1.4} />
          <text x={475} y={176} fontSize={17} fontWeight={700} textAnchor="middle" style={{ fill: COLORS.data }}>{data.title}</text>
          <text x={475} y={200} fontSize={12.5} textAnchor="middle" style={{ fill: "var(--ink-2)" }}>{data.sub}</text>
          <text x={475} y={232} fontSize={11.5} textAnchor="middle" fontFamily="var(--mono)" style={{ fill: "var(--ink-3)" }}>configs</text>
          <text x={475} y={250} fontSize={11.5} textAnchor="middle" fontFamily="var(--mono)" style={{ fill: "var(--ink-3)" }}>runs</text>
          <text x={475} y={268} fontSize={11.5} textAnchor="middle" fontFamily="var(--mono)" style={{ fill: "var(--ink-3)" }}>ai_runs</text>
          <text x={475} y={286} fontSize={11.5} textAnchor="middle" fontFamily="var(--mono)" style={{ fill: "var(--ink-3)" }}>ai_run_messages</text>
        </g>
        <line x1={360} y1={66} x2={400} y2={150} stroke="var(--ink-3)" strokeDasharray="4 4" markerEnd="url(#arr)" />
        <line x1={360} y1={174} x2={400} y2={190} stroke="var(--ink-3)" strokeDasharray="4 4" markerEnd="url(#arr)" />
        <line x1={360} y1={282} x2={400} y2={250} stroke="var(--ink-3)" strokeDasharray="4 4" markerEnd="url(#arr)" />
        <text x={30} y={462} fontSize={12} style={{ fill: "var(--ink-3)" }}>Click a layer. Arrows show who calls whom; dashed = reads/writes saved data.</text>
      </svg>

      <div className="card" style={{ borderTop: `4px solid ${COLORS[cur.layer]}` }}>
        <div className="eyebrow" style={{ color: COLORS[cur.layer] }}>{cur.title}</div>
        <p style={{ fontSize: 16.5, marginTop: 4 }}>{cur.plain}</p>
        <h3>What it does</h3>
        <ul style={{ margin: "0 0 6px", paddingLeft: 20 }}>{cur.does.map((d) => <li key={d}>{d}</li>)}</ul>
        <h3>Talks to</h3>
        <p className="small">{cur.talksTo}</p>
        <h3>Where to look</h3>
        <table className="t"><tbody>
          {cur.files.map((f) => <tr key={f.label}><td style={{ whiteSpace: "nowrap" }}><Link className="fileref" href={f.href}>{f.label}</Link></td><td className="small">{f.note}</td></tr>)}
        </tbody></table>
      </div>
    </div>
  );
}
