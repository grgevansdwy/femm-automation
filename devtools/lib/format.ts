export function fmtSci(n: number | null | undefined, digits = 3): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  if (n === 0) return "0";
  const a = Math.abs(n);
  if (a >= 0.01 && a < 10000) return n.toPrecision(digits).replace(/\.?0+$/, "");
  return n.toExponential(digits - 1);
}

export function fmtSecs(s: number | null | undefined): string {
  if (s === null || s === undefined) return "–";
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 120) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function fmtHenry(h: number | null | undefined): string {
  if (h === null || h === undefined) return "–";
  const a = Math.abs(h);
  if (a >= 1e-3) return `${(h * 1e3).toPrecision(4)} mH`;
  if (a >= 1e-6) return `${(h * 1e6).toPrecision(4)} µH`;
  return `${(h * 1e9).toPrecision(4)} nH`;
}

export function fmtOhm(r: number | null | undefined): string {
  if (r === null || r === undefined) return "–";
  return Math.abs(r) < 1 ? `${(r * 1e3).toPrecision(4)} mΩ` : `${r.toPrecision(4)} Ω`;
}
