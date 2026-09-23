import { fmtSecs } from "@/lib/format";

export type Obj = { metric: string; kind: string; passed: boolean; margin: number; worst_change_pct: number; requirement: string; required: boolean };
export type Cand = { delta: Record<string, any>; valid: boolean; error: string | null; rationale: string | null; goal_met: boolean | null; objectives: Obj[] };
export type Round = {
  round: number; reasoning: string; candidates: Cand[]; search_attempt: number | null; attempt_round: number | null;
  llm_seconds: number | null; femm_seconds: number | null; llm_failures: any[]; llm_stats: Record<string, any>;
};

export function passCount(c: Cand) { return c.objectives.filter((o) => o.required && o.passed).length; }
export function requiredCount(c: Cand) { return c.objectives.filter((o) => o.required).length; }

export function DeltaChips({ delta }: { delta: Record<string, any> }) {
  const e = Object.entries(delta);
  if (!e.length) return <span className="muted small">(no change)</span>;
  return <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4 }}>{e.map(([k, v]) => <span key={k} className="fileref">{k} = {String(v)}</span>)}</span>;
}

export function ObjectiveChips({ objectives }: { objectives: Obj[] }) {
  return (
    <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4 }}>
      {objectives.map((o) => (
        <span key={o.metric} className={`pill ${o.passed ? "good" : o.required ? "bad" : "warn"}`} title={`${o.requirement} · margin ${o.margin}${o.required ? "" : " · preference"}`}>
          {o.metric} {o.worst_change_pct > 0 ? "+" : ""}{o.worst_change_pct}%
        </span>
      ))}
    </span>
  );
}

export function TimeBar({ llm, femm }: { llm: number | null; femm: number | null }) {
  const a = llm ?? 0, b = femm ?? 0, t = a + b || 1;
  return (
    <div>
      <div className="bar" style={{ maxWidth: 260 }}><i style={{ width: `${(100 * a) / t}%`, background: "var(--agent)" }} /><i style={{ flex: 1, background: "var(--engine)" }} /></div>
      <div className="small muted"><span style={{ color: "var(--agent)" }}>■</span> model {fmtSecs(llm)} &nbsp;<span style={{ color: "var(--engine)" }}>■</span> FEMM {fmtSecs(femm)}</div>
    </div>
  );
}

/** Best number of required objectives passed, per round: a picture of how the search closed in on the goal. */
export function ProgressChart({ rounds }: { rounds: Round[] }) {
  const pts = rounds.map((r) => {
    const valid = r.candidates.filter((c) => c.valid && c.objectives.length);
    return { round: r.round, best: valid.length ? Math.max(...valid.map(passCount)) : null, total: valid.length ? requiredCount(valid[0]) : null, n: r.candidates.length };
  });
  const total = Math.max(...pts.map((p) => p.total ?? 0), 1);
  const W = Math.max(360, pts.length * 70), H = 170, pad = 34, bw = 34;
  const X = (i: number) => pad + i * ((W - pad - 10) / Math.max(pts.length, 1));
  const Y = (v: number) => H - 28 - (v / total) * (H - 52);
  return (
    <div className="scroll-x">
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: W, maxWidth: "100%", height: "auto" }}>
        <line x1={pad} x2={W - 6} y1={Y(total)} y2={Y(total)} stroke="var(--good)" strokeDasharray="5 4" />
        <text x={W - 8} y={Y(total) - 5} fontSize={11} textAnchor="end" style={{ fill: "var(--good)" }}>goal: all {total} required pass</text>
        {pts.map((p, i) => (
          <g key={p.round}>
            {p.best !== null && <rect x={X(i)} y={Y(p.best)} width={bw} height={H - 28 - Y(p.best)} rx={4} fill={p.best === total ? "var(--good)" : "var(--agent)"} opacity={0.85} />}
            {p.best !== null && <text x={X(i) + bw / 2} y={Y(p.best) - 4} fontSize={11} textAnchor="middle" style={{ fill: "var(--ink-2)" }}>{p.best}</text>}
            <text x={X(i) + bw / 2} y={H - 10} fontSize={11} textAnchor="middle" style={{ fill: "var(--ink-3)" }}>R{p.round}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}
