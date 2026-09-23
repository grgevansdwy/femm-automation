import Link from "next/link";
import path from "node:path";
import { PageHead } from "@/components/ui";
import { DESCRIPTIONS } from "@/lib/codemap";
import { firstLine, readRepoFile, repoTree, type TreeNode } from "@/lib/repo";

export const dynamic = "force-dynamic";

function Tree({ nodes, current, depth = 0 }: { nodes: TreeNode[]; current: string; depth?: number }) {
  return (
    <ul style={{ listStyle: "none", margin: 0, paddingLeft: depth ? 14 : 0 }}>
      {nodes.map((n) => n.dir ? (
        <li key={n.path}>
          <details open={depth < 1 || current.startsWith(n.path + "/")}>
            <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 13.5 }}>{n.name}/</summary>
            <Tree nodes={n.children!} current={current} depth={depth + 1} />
          </details>
        </li>
      ) : (
        <li key={n.path} style={{ margin: 0 }}>
          <Link href={`/code?file=${encodeURIComponent(n.path)}`} style={{ fontSize: 13, padding: "1px 6px", display: "block", borderRadius: 6, background: n.path === current ? "var(--front-bg)" : undefined, fontWeight: DESCRIPTIONS[n.path] ? 600 : 400, color: n.path === current ? "var(--front)" : DESCRIPTIONS[n.path] ? "var(--ink)" : "var(--ink-2)" }}>
            {n.name}
          </Link>
        </li>
      ))}
    </ul>
  );
}

export default async function Code({ searchParams }: { searchParams: Promise<{ file?: string }> }) {
  const { file } = await searchParams;
  const tree = repoTree();
  const text = file ? readRepoFile(file) : null;
  const lines = text?.split("\n") ?? [];
  const blurb = file ? DESCRIPTIONS[file] ?? firstLine(file) : "";
  return (
    <>
      <PageHead eyebrow="Reference" title="Code map & viewer"
        lede="Every file in backend, frontend, docs and template. Bold names have a hand-written explanation. Every other page links here, straight to the relevant line." />
      <div className="codegrid">
        <div className="card tree-card"><Tree nodes={tree} current={file ?? ""} /></div>
        <div>
          {!file && (
            <div className="card">
              <p>Pick a file on the left. Good places to start, in reading order:</p>
              <ol>
                {["backend/engine/core.py", "backend/engine/services/xfemm_backend.py", "backend/agent/femm_agent/services/optimize.py", "backend/agent/femm_agent/services/scorer.py", "backend/agent/settings/prompts/search_system.txt", "frontend/src/frontend/services/job_runner.py"].map((f) => (
                  <li key={f}><Link href={`/code?file=${encodeURIComponent(f)}`}><code>{f}</code></Link><div className="small muted">{DESCRIPTIONS[f]}</div></li>
                ))}
              </ol>
            </div>
          )}
          {file && text === null && <div className="err">Cannot show &quot;{file}&quot;: it is missing, too large, or not a plain source file.</div>}
          {file && text !== null && (
            <div>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
                <b style={{ fontFamily: "var(--mono)", fontSize: 14 }}>{file}</b><span className="small muted">{lines.length} lines · read-only</span>
              </div>
              {blurb && <p className="small" style={{ margin: "4px 0 8px" }}>{blurb}</p>}
              <pre className="code" style={{ maxHeight: "none", padding: 0, overflow: "auto", maxWidth: "100%" }}>
                <code style={{ display: "block", minWidth: "max-content" }}>
                  {lines.map((l, i) => (
                    <div key={i} id={`L${i + 1}`} className="cl"><a href={`#L${i + 1}`} className="ln">{i + 1}</a><span>{l || " "}</span></div>
                  ))}
                </code>
              </pre>
              <style>{`.cl{display:flex;padding-right:16px}.cl:target{background:rgba(255,196,0,.22)}.ln{flex:0 0 52px;text-align:right;padding:0 12px 0 0;color:var(--code-dim);user-select:none;text-decoration:none}.cl span{white-space:pre}`}</style>
              <p className="small muted">Extension: {path.extname(file) || "none"} · click a line number to link to it.</p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
