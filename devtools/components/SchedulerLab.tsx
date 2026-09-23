"use client";
import { useMemo, useState } from "react";

/** A pretend round: how long does it take for W workers to get through C candidates x D distances? Uses the same idea as the real thread pool: each worker grabs the next queued job the moment it is free. */
export function SchedulerLab({ defaults }: { defaults: { cores: number; solve: number; llm: number } }) {
  const [workers, setWorkers] = useState(defaults.cores);
  const [cands, setCands] = useState(5);
  const [dists, setDists] = useState(4);
  const [solve, setSolve] = useState(Math.round(defaults.solve));
  const [llm, setLlm] = useState(Math.round(defaults.llm));

  const { jobs, wall, lanes } = useMemo(() => {
    const total = cands * dists;
    const free = Array(workers).fill(0);
    const placed: { w: number; start: number; cand: number; dist: number }[] = [];
    for (let i = 0; i < total; i++) {
      const w = free.indexOf(Math.min(...free));
      placed.push({ w, start: free[w], cand: Math.floor(i / dists), dist: i % dists });
      free[w] += solve;
    }
    return { jobs: placed, wall: Math.max(...free, 0), lanes: workers };
  }, [workers, cands, dists, solve]);

  const serial = cands * dists * solve;
  const W = 720, laneH = 26, H = lanes * laneH + 30, left = 62;
  const X = (t: number) => left + (t / (wall || 1)) * (W - left - 12);
  const palette = ["#2f6fdb", "#8a5ae0", "#e0812a", "#1f9d6b", "#c2413a", "#d4a017", "#56707f", "#c74fa3"];

  return (
    <div className="card">
      <div className="row">
        <label className="f">CPU workers ({workers})<input type="range" min={1} max={16} value={workers} onChange={(e) => setWorkers(+e.target.value)} /></label>
        <label className="f">Candidates / round ({cands})<input type="range" min={1} max={8} value={cands} onChange={(e) => setCands(+e.target.value)} /></label>
        <label className="f">Distances ({dists})<input type="range" min={1} max={5} value={dists} onChange={(e) => setDists(+e.target.value)} /></label>
        <label className="f">Seconds per solve<input type="number" value={solve} min={1} onChange={(e) => setSolve(Math.max(1, +e.target.value))} style={{ width: 80 }} /></label>
        <label className="f">Model seconds<input type="number" value={llm} min={0} onChange={(e) => setLlm(Math.max(0, +e.target.value))} style={{ width: 80 }} /></label>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="diagram" style={{ marginTop: 12 }}>
        {Array.from({ length: lanes }, (_, w) => (
          <g key={w}>
            <text x={left - 8} y={w * laneH + 18} fontSize={11} textAnchor="end" style={{ fill: "var(--ink-3)" }}>worker {w + 1}</text>
            <line x1={left} x2={W - 12} y1={w * laneH + laneH - 2} y2={w * laneH + laneH - 2} stroke="var(--line)" />
          </g>
        ))}
        {jobs.map((j, i) => (
          <g key={i}>
            <rect x={X(j.start)} y={j.w * laneH + 3} width={Math.max(X(j.start + solve) - X(j.start) - 1.5, 1)} height={laneH - 8} rx={4} fill={palette[j.cand % palette.length]} opacity={0.9}>
              <title>{`candidate ${j.cand + 1}, distance ${j.dist + 1}`}</title>
            </rect>
          </g>
        ))}
        <text x={left} y={H - 6} fontSize={11} style={{ fill: "var(--ink-3)" }}>0 s</text>
        <text x={W - 12} y={H - 6} fontSize={11} textAnchor="end" style={{ fill: "var(--ink-3)" }}>{wall} s</text>
      </svg>
      <p className="small muted">Colour = candidate. Each block = one solve at one distance. A worker starts the next queued job the moment it is free.</p>
      <div className="grid g3" style={{ marginTop: 8 }}>
        <div className="card stat"><div className="v">{jobs.length}</div><div className="l">solves this round</div></div>
        <div className="card stat"><div className="v">{wall}s <span className="small muted" style={{ fontWeight: 400 }}>vs {serial}s one-by-one</span></div><div className="l">physics time this round ({(serial / (wall || 1)).toFixed(1)}× faster)</div></div>
        <div className="card stat"><div className="v">{llm + wall}s</div><div className="l">whole round: {llm}s model + {wall}s physics ({llm + wall > 0 ? Math.round((100 * llm) / (llm + wall)) : 0}% waiting on the model)</div></div>
      </div>
      <p className="small">Try: set workers to 1 (the old Wine backend) and see how the physics time balloons, or add candidates until the model wait stops dominating. Note the pool can never beat {Math.ceil(jobs.length / Math.max(workers, 1))} back-to-back solves per worker, and the machine only has {defaults.cores} real cores.</p>
    </div>
  );
}
