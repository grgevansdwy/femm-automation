"use client";
import { useState } from "react";
import { useApi } from "./useApi";

type Def = { title: string; help: string; placeholders: Record<string, string>; required: string[]; switches: string[]; template: string };
type Prompts = {
  note: string | null; defs: Record<string, Def>; search_rendered: string; interview_rendered: string;
  tools: Record<string, any>; free_field_count: number; objectives: any[]; spec_defaults: Record<string, any>;
};

const TAG = /(\{\{[#^/]?\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\})/g;

function Template({ text }: { text: string }) {
  return (
    <pre className="code" style={{ maxHeight: 560, whiteSpace: "pre-wrap" }}>
      {text.split(TAG).map((part, i) => {
        if (!TAG.test(part)) return part;
        TAG.lastIndex = 0;
        const isBlock = /\{\{[#^/]/.test(part);
        return <span key={i} style={{ color: isBlock ? "#ffb86b" : "#7ee0a8", background: "rgba(255,255,255,.07)", borderRadius: 4, padding: "0 2px" }}>{part}</span>;
      })}
    </pre>
  );
}

export function PromptExplorer({ configs }: { configs: { id: string; name: string }[] }) {
  const [id, setId] = useState("Gen2_coil");
  const [tab, setTab] = useState<"search" | "interview" | "tools">("search");
  const [view, setView] = useState<"rendered" | "template">("rendered");
  const [tool, setTool] = useState<"propose_coilset" | "finalize_spec" | "flag_off_topic">("propose_coilset");
  const { data, error, loading } = useApi<Prompts>(`/api/prompts?id=${id}`);

  const key = tab === "search" ? "search_system" : "interview_system";
  const def = data?.defs?.[key];

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <label className="f">Baseline design the prompt is built for
          <select value={id} onChange={(e) => setId(e.target.value)}>{configs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
        </label>
        <div className="row" style={{ gap: 6 }}>
          {([["search", "Search prompt (proposes designs)"], ["interview", "Interview prompt (gathers the goal)"], ["tools", "Tools the model can call"]] as const).map(([k, l]) => (
            <button key={k} onClick={() => setTab(k)} style={{ borderColor: tab === k ? "var(--agent)" : undefined, background: tab === k ? "var(--agent-bg)" : undefined }}>{l}</button>
          ))}
        </div>
      </div>
      {error && <div className="err" style={{ marginTop: 10 }}>{error}</div>}
      {loading && !data && <p className="muted">Rendering the prompt with the real backend (this solves the baseline first if it has never been solved, about 10 s)…</p>}
      {data && (
        <div style={{ marginTop: 12 }}>
          {data.note && tab === "search" && <div className="callout real"><div className="t">Baseline numbers</div><p className="small">{data.note}</p></div>}
          {tab !== "tools" && def && (
            <>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <span className="small muted">{def.help}</span>
                <div className="row" style={{ gap: 6 }}>
                  <button onClick={() => setView("rendered")} style={{ borderColor: view === "rendered" ? "var(--agent)" : undefined }}>What the model receives</button>
                  <button onClick={() => setView("template")} style={{ borderColor: view === "template" ? "var(--agent)" : undefined }}>The editable template</button>
                </div>
              </div>
              {view === "rendered"
                ? <pre className="code" style={{ maxHeight: 560, whiteSpace: "pre-wrap" }}>{tab === "search" ? data.search_rendered : data.interview_rendered}</pre>
                : (
                  <>
                    <Template text={def.template} />
                    <p className="small muted"><span style={{ color: "#3aa877" }}>{"{{name}}"}</span> is filled in by code for each run; <span style={{ color: "#d98a2b" }}>{"{{#name}}…{{/name}}"}</span> is a block that only appears when that value is non-empty (and <code>{"{{^name}}"}</code> the opposite).</p>
                    <table className="t"><thead><tr><th>Placeholder</th><th>Filled with</th><th></th></tr></thead><tbody>
                      {Object.entries(def.placeholders).map(([k, v]) => (
                        <tr key={k}><td><code>{`{{${k}}}`}</code></td><td className="small">{v}</td>
                          <td>{def.required.includes(k) && <span className="pill bad">required</span>} {def.switches.includes(k) && <span className="pill warn">can switch a block</span>}</td></tr>))}
                    </tbody></table>
                  </>
                )}
              <p className="small muted">{(tab === "search" ? data.search_rendered : data.interview_rendered).length.toLocaleString()} characters, about {Math.round((tab === "search" ? data.search_rendered : data.interview_rendered).length / 4).toLocaleString()} tokens.</p>
            </>
          )}
          {tab === "tools" && (
            <>
              <p className="small">A &quot;tool&quot; is a function the model may ask to call. It is described to the model as a JSON schema. The model never runs anything: it replies with the tool name and arguments, and <b>your code</b> decides what happens.</p>
              <div className="row" style={{ gap: 6 }}>
                <button onClick={() => setTool("propose_coilset")} style={{ borderColor: tool === "propose_coilset" ? "var(--agent)" : undefined }}>propose_coilset (search phase)</button>
                <button onClick={() => setTool("finalize_spec")} style={{ borderColor: tool === "finalize_spec" ? "var(--agent)" : undefined }}>finalize_spec (interview phase)</button>
                <button onClick={() => setTool("flag_off_topic")} style={{ borderColor: tool === "flag_off_topic" ? "var(--agent)" : undefined }}>flag_off_topic (interview phase)</button>
              </div>
              {tool === "propose_coilset" && <p className="small">This design exposes <b>{data.free_field_count}</b> fields the model may change (winding geometry only, by default). <code>additionalProperties: false</code> makes the server reject any other field.</p>}
              {tool === "flag_off_topic" && <p className="small">The relevancy guardrail: offered alongside finalize_spec on every interview turn. If called, the code-authored redirect shown to the user is always identical, regardless of what the model put in <code>reason</code> or in its own <code>content</code> on that reply.</p>}
              <pre className="code" style={{ maxHeight: 520 }}>{JSON.stringify(data.tools[tool], null, 2)}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
