import Link from "next/link";
import { Callout, FileRef, Pill, PageHead, ErrorBox, fmtSecs } from "@/components/ui";
import { LayerExplorer, type LayerInfo } from "@/components/LayerExplorer";
import { tryBridge } from "@/lib/bridge";
import { codeHref } from "@/lib/repo";

export const dynamic = "force-dynamic";

const LAYERS: LayerInfo[] = [
  {
    id: "front", layer: "front", title: "Frontend", sub: "Desktop app you click on (Dear ImGui)", folder: "frontend/src/frontend",
    plain: "The window you actually use. It shows your saved coil designs, lets you edit them, press Run, and chat with the AI. It does no physics itself.",
    does: ["Two modes: Manual Runs (pick a design, edit it, run a simulation) and AI Agent (describe a goal in chat, watch the search).",
      "Owns a JobRunner: a pool of worker threads that runs simulations without freezing the window.",
      "Caches results, so re-running an identical design at an identical distance is instant.",
      "Draws the coil diagram and result tables; can open any design in the real FEMM program."],
    talksTo: "Calls the Engine (to simulate and to load/save designs) and the Agent (only through agent/input_chat/services/runner.py and agent/femm_agent/services/runner.py).",
    files: [
      { label: "main.py", href: codeHref("frontend/src/frontend/main.py", "def main"), note: "Entry point: window, toolbar, the two modes." },
      { label: "services/job_runner.py", href: codeHref("frontend/src/frontend/services/job_runner.py", "class JobRunner"), note: "The queue + worker threads + result cache. The only place simulate() is called." },
      { label: "screen_agent/agent_panel.py", href: codeHref("frontend/src/frontend/screen_agent/agent_panel.py"), note: "The AI chat screen. The boundary between frontend and agent." },
      { label: "screen_manual/runs_panel.py", href: codeHref("frontend/src/frontend/screen_manual/runs_panel.py"), note: "The manual Run button and history." },
    ],
  },
  {
    id: "agent", layer: "agent", title: "Agent", sub: "Turns a goal into a search for a better coil", folder: "backend/agent",
    plain: "The AI part. Phase 1 is a chat that turns your wish into a precise, checkable goal. Phase 2 is a loop where a language model proposes design changes and plain code checks and scores every one.",
    does: ["Interview: the model asks short questions, then calls finalize_spec to produce a structured goal (the AgentSpec).",
      "Search: each round the model proposes designs (propose_coilset); code validates them, simulates them, scores them, and feeds the results back.",
      "Memory: each round the model is re-shown its last results plus the best designs so far.",
      "Safety: which fields may change, geometry validity and pass/fail scoring are ordinary Python. The model cannot override them."],
    talksTo: "Calls the Engine's simulate() (through the frontend's JobRunner) and an external LLM server over HTTP. Saves everything to the database.",
    files: [
      { label: "femm_agent/services/optimize.py", href: codeHref("backend/agent/femm_agent/services/optimize.py", "def optimize_loop"), note: "The search loop: propose → validate → simulate → score → repeat." },
      { label: "femm_agent/services/scorer.py", href: codeHref("backend/agent/femm_agent/services/scorer.py", "def score"), note: "Pass/fail and ranking. Plain code, never the model's opinion." },
      { label: "input_chat/services/interview.py", href: codeHref("backend/agent/input_chat/services/interview.py", "def run_interview_turn"), note: "The goal-gathering chat and the finalize_spec tool." },
      { label: "settings/prompts/search_system.txt", href: codeHref("backend/agent/settings/prompts/search_system.txt"), note: "The system prompt the model reads." },
      { label: "services/llm_client.py", href: codeHref("backend/agent/services/llm_client.py", "def call_llm"), note: "Talks to the model server: streaming, time limits, error kinds." },
    ],
  },
  {
    id: "engine", layer: "engine", title: "Engine", sub: "One coil design in, L / M / k / ESR out", folder: "backend/engine",
    plain: "The physics driver. It takes a CoilSet (all the numbers describing three coils) and a distance, and returns the numbers you care about: self inductance, mutual inductance, coupling, and resistance.",
    does: ["Validates the geometry (can this coil physically exist?).",
      "Builds a script that draws the coils in FEMM's language, then adds three solves (drive loop, tank, then RC).",
      "Runs the solver once, reads the result file, and computes the coupling coefficients k.",
      "Loads and saves coil designs (the CoilSet dataclass) in the database."],
    talksTo: "Runs the Solver as a subprocess. Reads and writes the database. Knows nothing about the Agent or the Frontend.",
    files: [
      { label: "core.py: simulate()", href: codeHref("backend/engine/core.py", "def simulate"), note: "The front door. Everything else in this file supports it." },
      { label: "core.py: build_geometry()", href: codeHref("backend/engine/core.py", "def build_geometry"), note: "Draws loop, tank, RC, ferrite, shields, backplane." },
      { label: "models/coilset.py", href: codeHref("backend/engine/models/coilset.py", "class CoilSet"), note: "The 55-ish numbers that define one design." },
      { label: "services/xfemm_backend.py", href: codeHref("backend/engine/services/xfemm_backend.py", "class LuaScript"), note: "Writes the Lua script and runs femmcli." },
    ],
  },
  {
    id: "solver", layer: "solver", title: "Solver", sub: "xfemm's femmcli finite-element program", folder: "backend/engine/vendor",
    plain: "The part that actually solves Maxwell's equations. It is a separate program (xfemm, an open-source version of FEMM) that reads a script, meshes the geometry, solves, and writes a result file. One process per simulation.",
    does: ["Runs headless: no window, no Wine, so many can run side by side on separate CPU cores.",
      "Is vendored (a copy kept in the repo) with a one-line patch that fixes a bug where mutual inductance came out rotated by 90 degrees.",
      "About 5 seconds for one solve on this machine (see the Engine page for a live timing)."],
    talksTo: "Only its script file in and result file out, in a private temporary folder per call.",
    files: [
      { label: "vendor/README.md", href: codeHref("backend/engine/vendor/README.md"), note: "The 90-degree bug, the patch, and how to rebuild." },
      { label: "docs/Engine.md", href: codeHref("docs/Engine.md"), note: "Why Wine was replaced, validation numbers, known concerns." },
    ],
  },
  {
    id: "data", layer: "data", title: "Data", sub: "One SQLite file", folder: "backend/data",
    plain: "Everything that has to survive closing the app lives in one SQLite database file: your coil designs, every solve, every AI search, and the settings.",
    does: ["configs: saved coil designs (built-in ones cannot be deleted).", "runs: every solve, with a hash so identical work is reused.",
      "ai_runs + ai_run_messages: every AI search, its goal, its chat, and every round.", "agent_settings: only the values you changed from the defaults."],
    talksTo: "Used by Engine (configs, runs) and Agent (ai_runs). Not checked into git.",
    files: [{ label: "engine/repositories/db.py", href: codeHref("backend/engine/repositories/db.py"), note: "Schema and connection." }],
  },
];

export default async function Home() {
  const o = await tryBridge("overview", [], { ttlMs: 4000 });
  const t = o.ok ? o.data.timing : null;
  return (
    <>
      <PageHead eyebrow="Start here" title="What is this codebase, in one page?"
        lede={<>It helps design <b>wireless-power coils</b>. You give it a coil design; it works out how strongly the coils talk to each other and how much energy they waste. Then an AI can search for a <i>better</i> design that meets a goal you describe in plain English.</>} />

      <Callout kind="plain">
        <p><b>The whole project is one loop:</b> <i>describe a coil → measure it with a physics simulator → if it isn&apos;t good enough, change it and measure again.</i> A person does that loop by hand in the app. The AI does it automatically, up to 50 rounds, trying several designs per round.</p>
      </Callout>

      <h2>The four layers</h2>
      <p>Only the top two are things you see or talk to. Each layer only calls the one below it, which is what keeps the code understandable. <b>Click a layer</b> to read what it does and jump to its files.</p>
      <LayerExplorer layers={LAYERS} />

      <h2>Three ways it gets used</h2>
      <div className="grid g3">
        <div className="card"><Pill layer="front">Manual run</Pill><h3>Pick, edit, Run</h3>
          <p className="small">In the app you choose a saved design, tweak numbers, choose a distance and press Run. A worker thread calls <code>simulate()</code>; a few seconds later the 12 result numbers appear and are saved.</p>
          <Link href="/engine">See one simulation step by step →</Link></div>
        <div className="card"><Pill layer="agent">AI search</Pill><h3>Describe a goal</h3>
          <p className="small">You chat with the AI about what you want (&quot;keep M within 5%, cut tank ESR by 20%&quot;). It turns that into a goal, then searches design changes until every required target passes or it runs out of rounds.</p>
          <Link href="/agent">See how the agent works →</Link></div>
        <div className="card"><Pill layer="gray">Command line</Pill><h3>Scripted</h3>
          <p className="small"><code>python -m agent</code> runs the same search with hardcoded defaults and no interview, printing each round. The <code>template/</code> folder wraps these as tools for the Pi coding agent.</p>
          <FileRef file="backend/agent/orchestrator.py" find="def main" /></div>
      </div>

      <h2>Follow one AI search from click to result</h2>
      <ol className="steps">
        <li><div><b>You describe your goal in chat.</b> The interview model asks short clarifying questions, then calls <code>finalize_spec</code>. That produces an <code>AgentSpec</code>: objectives, distances, which fields may change.</div></li>
        <li><div><b>You press Start Search.</b> A background thread runs <code>optimize_loop()</code>. First it simulates the <i>baseline</i> design at every distance, so every later candidate has something to be compared with.</div></li>
        <li><div><b>The model proposes designs.</b> It replies with up to 5 <code>propose_coilset</code> calls, each a small change from the baseline (e.g. &quot;tank layers 2 → 4&quot;), plus a one-sentence reason.</div></li>
        <li><div><b>Plain code checks every proposal.</b> Right types? Only allowed fields changed? Then each valid one is queued as a solve, one per distance, all at once.</div></li>
        <li><div><b>The solver runs in parallel</b> on separate CPU cores. Results come back as the 12 numbers per distance.</div></li>
        <li><div><b>The scorer judges.</b> For each objective it computes % change vs baseline at every distance; the <i>worst</i> distance decides. All required objectives must pass at once.</div></li>
        <li><div><b>Results go back to the model,</b> along with a short memory of the best designs so far. If the goal is met, or the budget ends, the search stops and everything is saved.</div></li>
      </ol>

      <h2>Live numbers from your database</h2>
      {!o.ok ? <ErrorBox error={o.error} hint="Check that .venv exists at the repo root and backend/data/femm_automation.db is present." /> : (
        <>
          <div className="grid g4">
            <div className="card stat"><div className="v">{o.data.counts.configs}</div><div className="l">saved coil designs</div></div>
            <div className="card stat"><div className="v">{o.data.counts.runs}</div><div className="l">simulations stored ({o.data.run_status.done ?? 0} ok, {o.data.run_status.failed ?? 0} failed)</div></div>
            <div className="card stat"><div className="v">{o.data.counts.ai_runs}</div><div className="l">AI searches ({o.data.ai_run_status.goal_met ?? 0} reached their goal)</div></div>
            <div className="card stat"><div className="v">{o.data.cpu_count}</div><div className="l">CPU cores = parallel solves</div></div>
          </div>
          {t && t.rounds > 0 && (
            <Callout kind="real" title="Real, from your data: where the time goes">
              <p>Across <b>{t.rounds}</b> recorded AI rounds, a typical round spent <b>{fmtSecs(t.llm_median_s)}</b> waiting for the language model but only <b>{fmtSecs(t.femm_median_s)}</b> on physics. One solve takes about <b>{fmtSecs(t.solve_median_s)}</b>. So the AI, not FEMM, is the slow part, and that is exactly why the loop asks for several candidates per reply.</p>
              <div className="bar" style={{ maxWidth: 520 }}>
                <i style={{ width: `${(100 * t.llm_total_s) / (t.llm_total_s + t.femm_total_s)}%`, background: "var(--agent)" }} />
                <i style={{ flex: 1, background: "var(--engine)" }} />
              </div>
              <div className="small muted" style={{ marginTop: 4 }}><span style={{ color: "var(--agent)" }}>■</span> model thinking &nbsp; <span style={{ color: "var(--engine)" }}>■</span> physics solving &nbsp; (total across all rounds)</div>
            </Callout>
          )}
        </>
      )}

      <h2>How the repository is laid out</h2>
      <div className="card scroll-x"><table className="t"><tbody>
        <tr><td><code>backend/engine/</code></td><td>The physics: <code>core.py</code> (simulate), <code>models/</code> (CoilSet, SimResult), <code>services/</code> (FEMM script builders, launcher), <code>repositories/</code> (database), <code>vendor/</code> (the patched solver).</td></tr>
        <tr><td><code>backend/agent/</code></td><td><code>input_chat/</code> (the interview), <code>femm_agent/</code> (the search loop), <code>services/</code> (LLM client, background runners), <code>settings/</code> (editable prompts + limits), <code>repositories/</code> (AI run storage).</td></tr>
        <tr><td><code>frontend/src/frontend/</code></td><td>The ImGui app: <code>screen_manual/</code>, <code>screen_agent/</code>, <code>screen_settings/</code>, <code>services/</code> (job runner), <code>visualization/</code> (diagram, tables), <code>files_backup/</code> (import/export).</td></tr>
        <tr><td><code>docs/</code></td><td>Design notes: architecture, xfemm migration, concurrency, agent design, a stress-test report.</td></tr>
        <tr><td><code>template/</code></td><td>A wrapper that exposes the simulator as tools for the Pi coding agent.</td></tr>
        <tr><td><code>devtools/</code></td><td>This app. Read-only; it imports the real backend through a small Python bridge.</td></tr>
      </tbody></table></div>
      <Callout kind="watch"><p>This tour is generated from the live code: every &quot;Where to look&quot; link opens the real file at the real line, and the numbers above come from your database. If the code changes, the links follow it.</p></Callout>
    </>
  );
}
