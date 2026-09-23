"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const GROUPS: { title: string; items: { href: string; label: string }[] }[] = [
  { title: "Start here", items: [{ href: "/", label: "The big picture" }, { href: "/physics", label: "What is being measured" }] },
  { title: "How it works", items: [
    { href: "/engine", label: "The engine: one simulation" },
    { href: "/agent", label: "The AI agent" },
    { href: "/concurrency", label: "Running many at once" },
  ] },
  { title: "Live data", items: [{ href: "/runs", label: "AI runs explorer" }, { href: "/data", label: "The database" }] },
  { title: "Reference", items: [{ href: "/code", label: "Code map & viewer" }, { href: "/glossary", label: "Glossary" }] },
];

export function Nav() {
  const path = usePathname();
  const [status, setStatus] = useState<null | { ok: boolean; text: string }>(null);
  const [dark, setDark] = useState(false);

  useEffect(() => {
    fetch("/api/overview").then((r) => r.json()).then((d) => {
      if (d.error) setStatus({ ok: false, text: "backend bridge failed" });
      else setStatus({ ok: true, text: `${d.counts.configs} configs · ${d.counts.runs} solves · ${d.counts.ai_runs} AI runs` });
    }).catch(() => setStatus({ ok: false, text: "backend unreachable" }));
    setDark(document.documentElement.dataset.theme === "dark");
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? "dark" : "light";
    try { localStorage.setItem("theme", next ? "dark" : "light"); } catch {}
  }
  let n = 0;
  return (
    <aside className="side">
      <div className="brand">FEMM devtools</div>
      <div className="brand-sub">A guided tour of how this codebase works</div>
      <nav className="nav">
        {GROUPS.map((g) => (
          <div key={g.title}>
            <div className="nav-group">{g.title}</div>
            {g.items.map((it) => {
              n += 1;
              const active = it.href === "/" ? path === "/" : path.startsWith(it.href);
              return <Link key={it.href} href={it.href} className={active ? "active" : ""}><span className="n">{n}</span>{it.label}</Link>;
            })}
          </div>
        ))}
      </nav>
      <div className="status">
        <div><span className={`dot ${status ? (status.ok ? "ok" : "bad") : ""}`} />{status ? (status.ok ? "Connected to backend" : "Not connected") : "Connecting…"}</div>
        {status && <div className="small muted" style={{ marginTop: 4 }}>{status.text}</div>}
        <div style={{ marginTop: 8 }}><button onClick={toggle} style={{ padding: "3px 10px", fontSize: 12 }}>{dark ? "Light" : "Dark"} mode</button></div>
      </div>
    </aside>
  );
}
