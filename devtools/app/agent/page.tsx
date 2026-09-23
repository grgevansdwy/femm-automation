import Link from "next/link";
import { Callout, FileRef, PageHead, Pill } from "@/components/ui";
import { PromptExplorer } from "@/components/PromptExplorer";
import { MarginLab } from "@/components/MarginLab";
import { tryBridge } from "@/lib/bridge";

export const dynamic = "force-dynamic";

const OPT = "backend/agent/femm_agent/services/optimize.py";

export default async function AgentPage() {
  const cfgs = await tryBridge<{ id: string; name: string }[]>("configs", [], { ttlMs: 5000 });
  const runs = await tryBridge<any[]>("runs", [], { ttlMs: 4000 });
  let example: any = null;
  if (runs.ok) {
    const pick = runs.data.find((r) => r.status === "goal_met");
    if (pick) { const r = await tryBridge("run", [pick.id], { ttlMs: 4000 }); if (r.ok) example = r.data; }
  }
  const finalize = example?.interview?.flatMap((m: any) => m.tool_calls ?? []).find((t: any) => t.name === "finalize_spec");

  return (
    <>
      <PageHead eyebrow="How it works" title="The AI agent, from a sentence to a better coil"
        lede={<>The agent is <b>one language model used in two different conversations</b>, wrapped in ordinary code that decides everything important. The model is creative but unreliable; the code is strict but has no ideas. The design puts each where it is good.</>} />

      <h2>Who decides what</h2>
      <div className="grid g2">
        <div className="card" style={{ borderTop: "4px solid var(--agent)" }}>
          <Pill layer="agent">The language model decides</Pill>
          <ul className="small">
            <li>What you meant, and which question to ask next (interview)</li>
            <li>Which design changes are worth trying next (search)</li>
            <li>A one-sentence reason for each change (the <code>rationale</code>)</li>
            <li>When to stop calling the tool because it is stuck</li>
          </ul>
        </div>
        <div className="card" style={{ borderTop: "4px solid var(--engine)" }}>
          <Pill layer="engine">Plain code decides</Pill>
          <ul className="small">
            <li>Which fields the model may touch at all (<code>free_fields_for</code>)</li>
            <li>Whether an argument has the right type and allowed values (<code>coerce_delta</code>)</li>
            <li>Whether the geometry can exist, and the physics result (<code>simulate</code>)</li>
            <li>Pass or fail, ranking, and when the search stops (<code>scorer</code>, <code>optimize_loop</code>)</li>
          </ul>
        </div>
      </div>
      <Callout kind="why"><p>This is the central design rule, written in <FileRef file="docs/archive/ai_agent_design.md" /> section 5.7: <i>&quot;prompted constraints are soft, silently droppable under context pressure, and unauditable.&quot;</i> Anything that must never be violated is enforced by code the model cannot argue with. The prompt explains the rules so the model wastes fewer rounds, but the code is what actually guarantees them.</p></Callout>

      <h2>The two phases</h2>
      <svg className="diagram" viewBox="0 0 980 190" role="img" aria-label="Interview then search">
        <defs><marker id="a2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--ink-3)" /></marker></defs>
        <rect x={10} y={30} width={300} height={130} rx={14} fill="var(--agent-bg)" stroke="var(--agent)" strokeWidth={1.6} />
        <text x={28} y={60} fontSize={16} fontWeight={700} style={{ fill: "var(--agent)" }}>Phase 1 · Interview</text>
        <text x={28} y={84} fontSize={12.5} style={{ fill: "var(--ink-2)" }}>You chat. The model asks one short</text>
        <text x={28} y={102} fontSize={12.5} style={{ fill: "var(--ink-2)" }}>question at a time, then calls</text>
        <text x={28} y={120} fontSize={12.5} fontFamily="var(--mono)" style={{ fill: "var(--ink-2)" }}>finalize_spec(...)</text>
        <text x={28} y={144} fontSize={11.5} style={{ fill: "var(--ink-3)" }}>tool given: finalize_spec only</text>
        <line x1={312} y1={95} x2={396} y2={95} stroke="var(--ink-3)" strokeWidth={1.8} markerEnd="url(#a2)" />
        <rect x={400} y={50} width={180} height={90} rx={12} fill="var(--panel)" stroke="var(--line-2)" strokeWidth={1.6} />
        <text x={490} y={82} fontSize={15} fontWeight={700} textAnchor="middle">AgentSpec</text>
        <text x={490} y={102} fontSize={11.5} textAnchor="middle" style={{ fill: "var(--ink-2)" }}>objectives · distances</text>
        <text x={490} y={118} fontSize={11.5} textAnchor="middle" style={{ fill: "var(--ink-2)" }}>allowed fields · budget</text>
        <line x1={582} y1={95} x2={666} y2={95} stroke="var(--ink-3)" strokeWidth={1.8} markerEnd="url(#a2)" />
        <text x={624} y={84} fontSize={11} textAnchor="middle" style={{ fill: "var(--ink-3)" }}>Start</text>
        <text x={624} y={122} fontSize={11} textAnchor="middle" style={{ fill: "var(--ink-3)" }}>Search</text>
        <rect x={670} y={30} width={300} height={130} rx={14} fill="var(--agent-bg)" stroke="var(--agent)" strokeWidth={1.6} />
        <text x={688} y={60} fontSize={16} fontWeight={700} style={{ fill: "var(--agent)" }}>Phase 2 · Search loop</text>
        <text x={688} y={84} fontSize={12.5} style={{ fill: "var(--ink-2)" }}>propose → check → simulate →</text>
        <text x={688} y={102} fontSize={12.5} style={{ fill: "var(--ink-2)" }}>score → feed back, up to 50 rounds</text>
        <text x={688} y={124} fontSize={11.5} style={{ fill: "var(--ink-3)" }}>tool given: propose_coilset only</text>
        <text x={688} y={144} fontSize={11.5} style={{ fill: "var(--ink-3)" }}>fresh conversation, sees only the spec</text>
      </svg>
      <p className="small">The <b>only</b> handoff between the phases is the <code>AgentSpec</code> object. The search never sees your chat; that is what lets it run from the command line with no interview at all. After the search starts, the same chat continues as a <b>follow-up chat</b> that can answer questions about the run but is given <i>no tools</i>, so it structurally cannot change anything.</p>

      <h2>Phase 1 · the interview, in detail</h2>
      <ol className="steps">
        <li><div><b>Nothing is asked until you speak.</b> <code>InterviewRunner.start()</code> creates a history row but deliberately does not fire an opening question (an unprompted first turn once produced a wall of questions).</div></li>
        <li><div><b>Each message runs on a background thread.</b> The whole transcript plus a system prompt goes to the model. The prompt scopes it to FEMM objectives only, tells it to ask one short question with its default stated, and lists the metrics it may use.</div></li>
        <li><div><b>The model either replies in text (a question) or calls <code>finalize_spec</code></b> with structured arguments: objective text, distances, and a list of objectives (metric, kind, tolerance, required or preference).</div></li>
        <li><div><b>Code validates it</b> (<code>_build_spec</code>): at least one required objective, no duplicate metrics, at least one free field left. A bad spec is returned to the model as an error message so it can fix it.</div></li>
        <li><div><b>You see a plan card and press Start Search.</b> Nothing runs before you confirm.</div></li>
      </ol>
      <Callout kind="watch" title="Permission model for fields">
        <p>Winding geometry (radius, turns, layers, spacing, width, copper thickness) is free by default. Ferrite, shields, backplanes and PCB thickness stay <b>fixed unless you explicitly say the AI may change them</b>. Even a design that already has ferrite does not make ferrite free (<FileRef file="backend/agent/input_chat/models/variables.py" find="CONSENT_GROUPS" />).</p>
      </Callout>
      {finalize && example && (
        <Callout kind="real" title="Real example from your data">
          <p>Run <Link href={`/runs/${example.id}`}>{example.id}</Link> started with this message:</p>
          <pre className="code" style={{ whiteSpace: "pre-wrap" }}>{example.interview.find((m: any) => m.role === "user")?.content}</pre>
          <p>…and the model turned it into <b>{finalize.arguments?.objectives?.length ?? "?"} objectives</b> at {JSON.stringify(finalize.arguments?.distances)} mm, for example:</p>
          <pre className="code">{JSON.stringify((finalize.arguments?.objectives ?? []).slice(0, 4), null, 1)}</pre>
        </Callout>
      )}

      <h2>Phase 2 · one round of the search</h2>
      <p>This is <FileRef file={OPT} find="def optimize_loop" />. Each pass through the loop is a <b>round</b>.</p>
      <ol className="steps">
        <li><div><b>Build the message.</b> System prompt + the goal + a short memory (below) + the previous round&apos;s full results.</div></li>
        <li><div><b>Ask the model</b> (<code>_ask_for_proposals</code>): a streamed request with a time limit; retried once if the server fails.</div></li>
        <li><div><b>Read its reply.</b> Up to 5 <code>propose_coilset</code> calls. If it replied with plain text instead, it gets one nudge; two text-only replies in a row end the search (<code>STOPPED_BY_MODEL</code>).</div></li>
        <li><div><b>Prepare each candidate</b> (<code>_prepare</code>): fix argument types, reject any field outside the allowed set, build &quot;baseline + this change&quot;. Bad ones become an error result and cost no solve.</div></li>
        <li><div><b>Launch every solve at once.</b> Each valid candidate needs one solve per distance; they are all queued on the shared thread pool before any is waited on. Identical candidates within a round are solved once.</div></li>
        <li><div><b>Collect and score</b> each candidate in order. Failures in one never cost the others their results.</div></li>
        <li><div><b>Stop or continue.</b> If any candidate passes every required objective → <code>GOAL_MET</code>. If 50 rounds are used → <code>BUDGET_EXHAUSTED</code>. Otherwise the results become the next round&apos;s feedback.</div></li>
      </ol>

      <h2>What the model is told: read the real prompts</h2>
      <p>The wording lives in plain text files, editable from the app&apos;s Settings screen. Code only fills in the run-specific parts (objectives, baseline values, allowed fields, radii). Switch between what the model actually receives and the template with its placeholders.</p>
      {cfgs.ok ? <PromptExplorer configs={cfgs.data} /> : <div className="err">{cfgs.error}</div>}

      <h2>What the model remembers</h2>
      <p>A chat model has no memory of its own: every request must re-send everything it should know. The loop rebuilds this each round instead of appending forever:</p>
      <div className="card scroll-x">
        <table className="t"><tbody>
          <tr><td><Pill layer="agent">system</Pill></td><td>The search prompt (rules, physics hints, objectives, baseline). Sent every round.</td></tr>
          <tr><td><Pill layer="agent">user</Pill></td><td>&quot;Find a candidate that meets the objective. Start proposing.&quot;</td></tr>
          <tr><td><Pill layer="agent">user</Pill></td><td><b>Search memory</b>: the best 10 candidates so far (one line each) plus a shorter line for every other candidate tried (&quot;don&apos;t propose these again&quot;).</td></tr>
          <tr><td><Pill layer="agent">last round</Pill></td><td>The model&apos;s previous reply and every full tool result, exactly as sent.</td></tr>
        </tbody></table>
      </div>
      <Callout kind="why"><p>Earlier versions resent every round in full. A batched run reached about <b>129 KB by round 5 and timed out</b> (see <FileRef file="docs/archive/concurrency.md" />). Now the model&apos;s chain-of-thought (about 28% of that) is dropped before resending, and only the latest round is kept raw. The model learns from <i>results</i>, not from its own earlier reasoning. Each search also starts with <b>zero memory of earlier searches</b>; nothing is retrieved from past runs.</p></Callout>

      <h2>How a candidate is judged</h2>
      <p>Each objective has a <b>kind</b>: <code>match</code> (stay within ± a band), <code>reduce</code> (drop by at least X%), or <code>increase</code>. Simulated quantities are checked at <b>every distance</b>, and the <b>worst distance decides</b>. All <i>required</i> objectives must pass together; preferences only break ties. The number that makes objectives comparable is the <b>margin</b>: how far inside (≥ 0) or outside (&lt; 0) the requirement, as a fraction of its tolerance. The model is told to fix the most negative margin first.</p>
      <MarginLab />
      <p className="small">Ranking (<FileRef file="backend/agent/femm_agent/services/scorer.py" find="def rank_key" />) has two tiers: candidates that hold every required <code>match</code> band come first, ordered by their weakest improvement; the rest are ranked by how far outside the band they landed, so there is always a &quot;best attempt&quot; to report.</p>

      <h2>When things go wrong</h2>
      <div className="card scroll-x"><table className="t"><thead><tr><th>Situation</th><th>What the code does</th></tr></thead><tbody>
        <tr><td>Model proposes a bad type or a forbidden field</td><td>Rejected before any solve; the error text goes back to the model as that call&apos;s result.</td></tr>
        <tr><td>Geometry impossible (winding crosses the axis…)</td><td>Fails inside <code>simulate()</code>, becomes an error result. (Checked late: the job is already queued.)</td></tr>
        <tr><td>LLM server error, no reply, too slow, or reply hit the token cap</td><td>Retry the round once (<code>ROUND_LLM_ATTEMPTS = 2</code>, 5 s apart). Every failure is recorded with its kind.</td></tr>
        <tr><td>A round fails every try</td><td>The whole search restarts from zero memory (<code>SEARCH_ATTEMPTS = 2</code>); earlier rounds stay in history.</td></tr>
        <tr><td>Model replies with text twice in a row</td><td>Treated as &quot;I&apos;m done&quot;: status <code>stopped_by_model</code>.</td></tr>
        <tr><td>You press Restart</td><td>The run is flagged cancelled; queued solves are dropped; a solve or LLM call already in flight finishes but is thrown away.</td></tr>
        <tr><td>Any ending except goal met</td><td>A plain-language <b>outcome report</b> (<code>outcome.py</code>) explains why and names the closest candidate.</td></tr>
      </tbody></table></div>

      <h2>What this harness does not have (yet)</h2>
      <div className="grid g3">
        <div className="card"><b>No tracing tool</b><p className="small">Per-round timing, token stats, failures and reasoning are saved to SQLite, but there is no logging, OpenTelemetry or trace viewer, and nothing is aggregated across runs. The <Link href="/runs">Runs explorer</Link> is a first step.</p></div>
        <div className="card"><b>No automated evals</b><p className="small">Unit tests use fake model replies to test the loop. Nothing replays fixed tasks against the real model and scores it. Five scripted interview cases exist in <FileRef file="backend/agent/docs/interview_test_cases.md" /> but are run by hand.</p></div>
        <div className="card"><b>No retrieval (RAG)</b><p className="small">The physics guidance is static text inside the prompt. Past runs are not consulted by new ones.</p></div>
      </div>
    </>
  );
}
