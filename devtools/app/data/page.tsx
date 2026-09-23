import { Callout, ErrorBox, FileRef, PageHead } from "@/components/ui";
import { tryBridge } from "@/lib/bridge";

export const dynamic = "force-dynamic";

const PLAIN: Record<string, { what: string; who: string; notes: string }> = {
  configs: { what: "Your saved coil designs. One row per design; the design itself is one JSON blob (a CoilSet).", who: "Read and written by the Engine's coilset repository and the GUI's design list.", notes: "'builtin' designs can be edited but never deleted. 'cloned_from' remembers where a copy came from." },
  runs: { what: "Every single simulation ever run, manual or by the AI, with its 12 result numbers.", who: "Written by the frontend's JobRunner after each solve. Read by Run History and by the result cache.", notes: "coilset_hash is a fingerprint of geometry + distance: an identical request is answered from here instead of re-solving. last_used_at is bumped on every cache hit." },
  ai_runs: { what: "One row per AI search: its goal (spec_json), status, timing, and the best design found.", who: "Created when a chat opens; filled in when the search starts and finishes.", notes: "Status starts as 'interviewing'. Many rows stay that way: chats that were never started." },
  ai_run_messages: { what: "The conversation and the rounds of each AI search, in order.", who: "Interview messages are saved as they arrive; each search round is saved once, at the end of the search.", notes: "payload_json holds a round's candidates, timing and failures. This is what the Runs explorer replays." },
  agent_settings: { what: "Only the agent settings you changed from the defaults (model URL, limits, edited prompts).", who: "The Settings screen.", notes: "Empty until you change something. A run copies the settings when it starts, so editing mid-run changes nothing." },
};

export default async function Data() {
  const r = await tryBridge<any[]>("tables", [], { ttlMs: 3000 });
  return (
    <>
      <PageHead eyebrow="Live data" title="The database"
        lede={<>Everything that must survive closing the app lives in <b>one SQLite file</b>: <code>backend/data/femm_automation.db</code>. Not checked into git. This page reads it <b>read-only</b>, so it can never change your data.</>} />
      <Callout kind="plain">
        <p>Two more things sit next to it in <code>backend/data/</code>: <code>constants.json</code> (the fixed test-rig values: frequency 6.78 MHz, probe currents, backplane size) and <code>femm_models/</code> (files written only when you click &quot;Open in FEMM&quot;; safe to delete).</p>
      </Callout>
      <h2>How the tables relate</h2>
      <svg className="diagram" viewBox="0 0 860 200" role="img" aria-label="Table relationships" style={{ maxWidth: 860 }}>
        <defs><marker id="a3" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--ink-3)" /></marker></defs>
        {[{ x: 20, y: 70, t: "configs", s: "designs" }, { x: 250, y: 20, t: "runs", s: "every solve" }, { x: 250, y: 120, t: "ai_runs", s: "every AI search" }, { x: 500, y: 120, t: "ai_run_messages", s: "chat + rounds" }, { x: 700, y: 70, t: "agent_settings", s: "your overrides" }].map((b) => (
          <g key={b.t}><rect x={b.x} y={b.y} width={b.t === "ai_run_messages" ? 170 : 150} height={58} rx={12} fill="var(--data-bg)" stroke="var(--data)" strokeWidth={1.5} />
            <text x={b.x + 14} y={b.y + 26} fontSize={14} fontWeight={700} style={{ fill: "var(--data)" }}>{b.t}</text><text x={b.x + 14} y={b.y + 45} fontSize={11.5} style={{ fill: "var(--ink-2)" }}>{b.s}</text></g>
        ))}
        <line x1={250} y1={49} x2={172} y2={92} stroke="var(--ink-3)" markerEnd="url(#a3)" /><text x={196} y={62} fontSize={10.5} style={{ fill: "var(--ink-3)" }}>config_id</text>
        <line x1={250} y1={149} x2={172} y2={112} stroke="var(--ink-3)" markerEnd="url(#a3)" /><text x={168} y={150} fontSize={10.5} style={{ fill: "var(--ink-3)" }}>baseline_config_id</text>
        <line x1={500} y1={149} x2={402} y2={149} stroke="var(--ink-3)" markerEnd="url(#a3)" /><text x={412} y={140} fontSize={10.5} style={{ fill: "var(--ink-3)" }}>ai_run_id</text>
      </svg>
      <p className="small">Arrows read &quot;points at&quot;. The links are documented in the schema but <b>not enforced by the database</b> (SQLite foreign keys are off by design; see <FileRef file="backend/engine/repositories/db.py" find="def connect" />), so a deleted design can leave orphan rows.</p>

      <h2>Every table, with its newest row</h2>
      {!r.ok ? <ErrorBox error={r.error} /> : r.data.map((t) => (
        <details className="d" key={t.name} open={t.name === "runs"}>
          <summary><code>{t.name}</code> <span className="muted small">· {t.rows.toLocaleString()} rows</span></summary>
          <div className="body">
            {PLAIN[t.name] && <><p><b>{PLAIN[t.name].what}</b></p><p className="small">{PLAIN[t.name].who}</p><p className="small muted">{PLAIN[t.name].notes}</p></>}
            <div className="scroll-x"><table className="t"><thead><tr><th>Column</th><th>Type</th><th>Newest row&apos;s value</th></tr></thead><tbody>
              {t.columns.map((c: any) => <tr key={c.name}><td><code>{c.name}</code>{c.pk && <span className="pill gray" style={{ marginLeft: 6 }}>key</span>}</td><td className="small muted">{c.type}</td><td className="num" style={{ maxWidth: 420, wordBreak: "break-all" }}>{t.sample ? String(t.sample[c.name] ?? "null") : "–"}</td></tr>)}
            </tbody></table></div>
          </div>
        </details>
      ))}
    </>
  );
}
