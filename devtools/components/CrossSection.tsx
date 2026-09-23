"use client";
import { useMemo, useState } from "react";

export type Shape = { r0: number; z0: number; r1: number; z1: number; kind: string; circuit?: string; coil?: string; material?: string; line: number };

export const COIL_COLOR: Record<string, string> = { loop: "#2f6fdb", tank: "#8a5ae0", rc: "#e0812a" };
const KIND_COLOR: Record<string, string> = { ferrite: "#8b6f4e", shield: "#3aa6a6", backplane: "#5b6770", other: "#999" };
const KIND_LABEL: Record<string, string> = { ferrite: "Ferrite", shield: "Metal shield (aluminium)", backplane: "Metal backplane (copper)" };

function colorOf(s: Shape) { return s.kind === "coil" ? COIL_COLOR[s.coil ?? "loop"] : KIND_COLOR[s.kind] ?? "#999"; }

/**
 * The model FEMM actually solves, drawn to scale sideways and stretched vertically.
 * FEMM works in "axisymmetric" mode: it draws only a half slice (radius r, height z)
 * and spins it around the vertical axis. So each rectangle here is really a RING.
 */
export function CrossSection({ shapes, height = 380 }: { shapes: Shape[]; height?: number }) {
  const [hover, setHover] = useState<Shape | null>(null);
  const [mirror, setMirror] = useState(true);
  const draw = useMemo(() => shapes.filter((s) => s.kind !== "boundary"), [shapes]);

  const box = useMemo(() => {
    if (!draw.length) return null;
    const rMax = Math.max(...draw.map((s) => Math.max(s.r0, s.r1)));
    const zMin = Math.min(...draw.map((s) => Math.min(s.z0, s.z1)));
    const zMax = Math.max(...draw.map((s) => Math.max(s.z0, s.z1)));
    return { rMax: rMax * 1.08, zMin: zMin - (zMax - zMin) * 0.06, zMax: zMax + (zMax - zMin) * 0.06 };
  }, [draw]);
  if (!box) return <div className="muted">No geometry to draw.</div>;

  const W = 900, H = height, padL = 46, padR = 20, padT = 14, padB = 30;
  const rSpan = mirror ? box.rMax * 2 : box.rMax;
  const sx = (W - padL - padR) / rSpan;
  const zSpan = box.zMax - box.zMin || 1;
  const sy = (H - padT - padB) / zSpan;
  const X = (r: number) => padL + (mirror ? r + box.rMax : r) * sx;
  const Y = (z: number) => padT + (box.zMax - z) * sy;
  const stretch = Math.round(sy / sx);

  const rects: { s: Shape; x: number; y: number; w: number; h: number }[] = [];
  for (const s of draw) {
    const r0 = Math.min(s.r0, s.r1), r1 = Math.max(s.r0, s.r1), z0 = Math.min(s.z0, s.z1), z1 = Math.max(s.z0, s.z1);
    const h = Math.max((z1 - z0) * sy, 4);
    rects.push({ s, x: X(r0), y: Y(z1), w: Math.max((r1 - r0) * sx, 1.5), h });
    if (mirror) rects.push({ s, x: X(-r1), y: Y(z1), w: Math.max((r1 - r0) * sx, 1.5), h });
  }
  const groups = ["rc", "tank", "loop"].flatMap((c) => {
    const g = draw.filter((s) => s.kind === "coil" && s.coil === c);
    if (!g.length) return [];
    const rMax = Math.max(...g.map((s) => Math.max(s.r0, s.r1)));
    const zMid = (Math.max(...g.map((s) => Math.max(s.z0, s.z1))) + Math.min(...g.map((s) => Math.min(s.z0, s.z1)))) / 2;
    return [{ c, x: X(rMax) + 10, y: Y(zMid) + 4 }];
  });
  const present = Array.from(new Set(draw.map((s) => (s.kind === "coil" ? `coil:${s.coil}` : s.kind))));
  const ticks = Array.from({ length: 5 }, (_, i) => (box.rMax / 4) * i);

  return (
    <div>
      <div className="row" style={{ marginBottom: 6, alignItems: "center" }}>
        <label className="small" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={mirror} onChange={(e) => setMirror(e.target.checked)} /> Show both sides of the axis
        </label>
        <span className="small muted">Height stretched about {stretch}× so thin copper is visible. Width is to scale (mm).</span>
      </div>
      <svg className="diagram" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cross-section of the simulated coils">
        <rect x={0} y={0} width={W} height={H} rx={10} fill="var(--panel)" stroke="var(--line)" />
        {mirror && <line x1={X(0)} x2={X(0)} y1={padT} y2={H - padB} stroke="var(--ink-3)" strokeDasharray="4 4" />}
        {!mirror && <line x1={X(0)} x2={X(0)} y1={padT} y2={H - padB} stroke="var(--ink-3)" strokeDasharray="4 4" />}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={X(t)} x2={X(t)} y1={H - padB} y2={H - padB + 5} stroke="var(--ink-3)" />
            <text x={X(t)} y={H - padB + 18} fontSize={11} textAnchor="middle" style={{ fill: "var(--ink-3)" }}>{Math.round(t)}</text>
          </g>
        ))}
        <text x={W - padR} y={H - 6} fontSize={11} textAnchor="end" style={{ fill: "var(--ink-3)" }}>radius from the centre axis (mm)</text>
        <text x={12} y={padT + 10} fontSize={11} style={{ fill: "var(--ink-3)" }}>up ↑</text>
        {groups.map((g) => <text key={g.c} x={g.x} y={g.y} fontSize={13} fontWeight={700} style={{ fill: COIL_COLOR[g.c] }}>{g.c === "rc" ? "RC" : g.c[0].toUpperCase() + g.c.slice(1)}</text>)}
        {rects.map((r, i) => (
          <rect key={i} x={r.x} y={r.y} width={r.w} height={r.h} fill={colorOf(r.s)} opacity={hover && hover !== r.s ? 0.35 : 0.95}
            stroke={hover === r.s ? "var(--ink)" : "none"} onMouseEnter={() => setHover(r.s)} onMouseLeave={() => setHover(null)} />
        ))}
      </svg>
      <div className="row" style={{ marginTop: 8, gap: 16, alignItems: "center" }}>
        {present.map((p) => {
          const [k, c] = p.split(":");
          const col = k === "coil" ? COIL_COLOR[c] : KIND_COLOR[k];
          const name = k === "coil" ? ({ loop: "Loop coil", tank: "Tank coil", rc: "RC (receive) coil" } as Record<string, string>)[c] : KIND_LABEL[k] ?? k;
          return <span key={p} className="small"><i style={{ display: "inline-block", width: 11, height: 11, background: col, borderRadius: 3, marginRight: 6 }} />{name}</span>;
        })}
      </div>
      <div className="small muted" style={{ minHeight: 22, marginTop: 6 }}>
        {hover
          ? `${hover.kind === "coil" ? `Copper trace, circuit "${hover.circuit}"` : KIND_LABEL[hover.kind] ?? hover.kind} · r ${hover.r0.toFixed(2)}–${hover.r1.toFixed(2)} mm · height ${hover.z1.toFixed(2)} to ${hover.z0.toFixed(2)} mm`
          : "Hover a shape to see what it is."}
      </div>
    </div>
  );
}
