import Link from "next/link";
import { Callout, ErrorBox, PageHead, fmtDate, fmtSecs } from "@/components/ui";
import { DeltaChips, ObjectiveChips, ProgressChart, TimeBar, passCount, requiredCount, type Round } from "@/components/RunParts";
import { tryBridge } from "@/lib/bridge";

export const dynamic = "force-dynamic";
const PILL: Record<string, string> = { goal_met: "good", running: "agent", interviewing: "gray", budget_exhausted: "warn", stopped_by_model: "warn", failed: "bad" };

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const r = await tryBridge<any>("run", [id], { ttlMs: 3000 });
  if (!r.ok) return <ErrorBox error={r.error} />;
  const run = r.data;
  const rounds: Round[] = run.rounds;
  const base = await tryBridge<any>("config", [run.baseline], { ttlMs: 5000 });
  const changed = base.ok && run.best_coilset
    ? Object.entries(run.best_coilset).filter(([k, v]) => base.data.fields[k] !== v && k !== "config_name").map(([k, v]) => ({ k, from: base.data.fields[k], to: v as any }))
    : [];
  const totalLlm = rounds.reduce((a, x) => a + (x.llm_seconds ?? 0), 0), totalFemm = rounds.reduce((a, x) => a + (x.femm_seconds ?? 0), 0);
  const nCands = rounds.reduce((a, x) => a + x.candidates.length, 0);

  return (
    <>
      <PageHead eyebrow={<Link href="/runs">← AI runs</Link>} title={`Run ${run.id}`}
        lede={<><span className={`pill ${PILL[run.status] ?? "gray"}`}>{run.status.replace(/_/g, " ")}</span> &nbsp;Baseline <code>{run.baseline}</code> · started {fmtDate(run.started_at ?? run.created_at)}{run.finished_at && <> · finished {fmtDate(run.finished_at)}</>}</>} />

      {run.error && <Callout kind="watch" title="Outcome report saved with this run"><pre style={{ whiteSpace: "pre-wrap", margin: 0, font: "inherit" }}>{run.error}</pre></Callout>}

      {run.spec?.objective_text && (
        <>
          <h2>The goal</h2>
          <div className="card"><p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{run.spec.objective_text}</p></div>
          <div className="grid g2" style={{ marginTop: 14, alignItems: "start" }}>
            <div className="card scroll-x"><b>Objectives the interview produced</b>
              <table className="t"><thead><tr><th>Metric</th><th>Kind</th><th>Tolerance</th><th></th></tr></thead><tbody>
                {(run.spec.objectives ?? []).map((o: any) => <tr key={o.metric}><td><code>{o.metric}</code></td><td>{o.kind}</td><td className="num">{o.tolerance_pct}%</td><td>{o.required ? <span className="pill bad">required</span> : <span className="pill gray">preference</span>}</td></tr>)}
              </tbody></table></div>
            <div className="card"><b>Search settings frozen for this run</b>
              <dl className="kv" style={{ marginTop: 8 }}>
                <dt>distances</dt><dd>{JSON.stringify(run.spec.distances)} mm</dd>
                <dt>max rounds</dt><dd>{run.spec.max_tool_calls}</dd>
                <dt>candidates / round</dt><dd>{run.spec.candidates_per_round}</dd>
                <dt>best kept in memory</dt><dd>{run.spec.keep_best_candidates}</dd>
                <dt>free-field rules</dt><dd className="small">{JSON.stringify(run.spec.variable_spec)}</dd>
              </dl></div>
          </div>
        </>
      )}

      {rounds.length > 0 && (
        <>
          <h2>How the search progressed</h2>
          <p className="small">Height = the most required objectives any single candidate in that round passed. A search ends when one candidate passes all of them.</p>
          <div className="card"><ProgressChart rounds={rounds} /></div>
          <div className="grid g4" style={{ marginTop: 14 }}>
            <div className="card stat"><div className="v">{rounds.length}</div><div className="l">rounds</div></div>
            <div className="card stat"><div className="v">{nCands}</div><div className="l">designs proposed</div></div>
            <div className="card stat"><div className="v">{fmtSecs(totalLlm)}</div><div className="l">waiting on the model</div></div>
            <div className="card stat"><div className="v">{fmtSecs(totalFemm)}</div><div className="l">solving physics</div></div>
          </div>
        </>
      )}

      {run.best_coilset && (
        <>
          <h2>The best design it found{run.best_round ? ` (round ${run.best_round})` : ""}</h2>
          <div className="card scroll-x">
            {changed.length ? (
              <table className="t"><thead><tr><th>Field changed from the baseline</th><th>Baseline</th><th></th><th>Found</th></tr></thead><tbody>
                {changed.map((c) => <tr key={c.k}><td><code>{c.k}</code></td><td className="num">{String(c.from)}</td><td>→</td><td className="num"><b>{String(c.to)}</b></td></tr>)}
              </tbody></table>
            ) : <span className="muted">No differences from the baseline that still exists in the database.</span>}
            {run.best_result && <p style={{ marginTop: 10 }}><ObjectiveChips objectives={run.best_result.objectives} /></p>}
          </div>
        </>
      )}

      <h2>Round by round</h2>
      {rounds.length === 0 && <p className="muted">This run never reached the search phase.</p>}
      <div className="grid" style={{ gap: 12 }}>
        {rounds.map((rd) => (
          <div className="card" key={rd.round}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
              <div><b style={{ fontSize: 16 }}>Round {rd.round}</b> {rd.search_attempt && rd.search_attempt > 1 && <span className="pill warn">restart #{rd.search_attempt}</span>} {rd.round === run.best_round && <span className="pill good">best</span>}
                <div className="small muted">{rd.candidates.length} candidate{rd.candidates.length === 1 ? "" : "s"}{rd.llm_stats?.tokens ? ` · ${rd.llm_stats.tokens} tokens` : ""}{rd.llm_stats?.first_token_s ? ` · first token after ${fmtSecs(rd.llm_stats.first_token_s)}` : ""}</div></div>
              <TimeBar llm={rd.llm_seconds} femm={rd.femm_seconds} />
            </div>
            {rd.llm_failures.length > 0 && <div className="err" style={{ margin: "8px 0" }}>{rd.llm_failures.map((f: any, i: number) => <div key={i}>AI try {f.ai_try}: {f.kind} · {f.detail ?? ""}</div>)}</div>}
            <div className="grid" style={{ gap: 8, marginTop: 10 }}>
              {rd.candidates.map((c, i) => (
                <div key={i} style={{ borderTop: "1px solid var(--line)", paddingTop: 8 }}>
                  <div className="row" style={{ alignItems: "center", gap: 8 }}>
                    <span className="small muted">#{i + 1}</span><DeltaChips delta={c.delta} />
                    {c.valid ? <span className={`pill ${c.goal_met ? "good" : "gray"}`}>{c.goal_met ? "GOAL MET" : `${passCount(c)}/${requiredCount(c)} required pass`}</span> : <span className="pill bad">rejected</span>}
                  </div>
                  {c.rationale && <p className="small" style={{ margin: "4px 0", fontStyle: "italic" }}>&ldquo;{c.rationale}&rdquo;</p>}
                  {c.valid ? <ObjectiveChips objectives={c.objectives} /> : <div className="small" style={{ color: "var(--bad)" }}>{c.error}</div>}
                </div>
              ))}
            </div>
            {rd.reasoning && <details className="d" style={{ marginTop: 10 }}><summary>The model&apos;s reasoning this round</summary><div className="body"><pre style={{ whiteSpace: "pre-wrap", font: "inherit", fontSize: 13 }} className="small">{rd.reasoning}</pre></div></details>}
          </div>
        ))}
      </div>

      {run.interview.length > 0 && (
        <>
          <h2>The interview</h2>
          <div className="grid" style={{ gap: 8 }}>
            {run.interview.map((m: any) => (
              <div key={m.seq} className="card" style={{ borderLeft: `4px solid ${m.role === "user" ? "var(--front)" : m.role === "tool" ? "var(--engine)" : "var(--agent)"}` }}>
                <div className="eyebrow">{m.role === "tool" ? "validation result (code)" : m.role === "user" ? "you" : "model"}</div>
                {m.content && <p style={{ whiteSpace: "pre-wrap", margin: "4px 0 0" }}>{m.content}</p>}
                {m.tool_calls?.map((t: any, i: number) => <details className="d" key={i}><summary>called <code>{t.name}</code></summary><div className="body"><pre className="code">{JSON.stringify(t.arguments, null, 1)}</pre></div></details>)}
                {m.reasoning && <details className="d"><summary>model&apos;s reasoning</summary><div className="body"><pre className="small" style={{ whiteSpace: "pre-wrap", font: "inherit" }}>{m.reasoning}</pre></div></details>}
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
