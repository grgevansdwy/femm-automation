import { Callout, ErrorBox, FileRef, PageHead, Pill } from "@/components/ui";
import { SchedulerLab } from "@/components/SchedulerLab";
import { tryBridge } from "@/lib/bridge";

export const dynamic = "force-dynamic";

export default async function Concurrency() {
  const o = await tryBridge<any>("overview", [], { ttlMs: 4000 });
  if (!o.ok) return <ErrorBox error={o.error} />;
  const t = o.data.timing;
  return (
    <>
      <PageHead eyebrow="How it works" title="Running many things at once"
        lede="A single AI round can need 20 simulations, while the language model takes minutes to answer. Most of the design is about keeping the window responsive and the CPU cores busy without the pieces tripping over each other." />

      <h2>Four kinds of thread, four different jobs</h2>
      <div className="grid g2">
        <div className="card" style={{ borderTop: "4px solid var(--front)" }}><Pill layer="front">GUI thread</Pill><p className="small">Draws the window about 60 times a second. It must <b>never wait</b>. Every frame it calls <code>poll()</code> to peek at progress. Anything slow is handed to another thread.</p></div>
        <div className="card" style={{ borderTop: "4px solid var(--agent)" }}><Pill layer="agent">Agent thread (one per search)</Pill><p className="small">Runs <code>optimize_loop()</code>. It spends most of its life <b>blocked</b>: waiting for the model, then waiting for solves. It has its own thread on purpose (see below).</p></div>
        <div className="card" style={{ borderTop: "4px solid var(--engine)" }}><Pill layer="engine">Worker pool ({o.data.cpu_count} threads)</Pill><p className="small">One thread per CPU core. Each takes a queued job, calls <code>simulate()</code>, which starts the solver process and waits for it. Shared by manual runs and AI searches.</p></div>
        <div className="card" style={{ borderTop: "4px solid var(--solver)" }}><Pill layer="solver">Solver processes</Pill><p className="small">Each solve is a separate <code>femmcli</code> operating-system process in its own temp folder. This is where the real parallelism is.</p></div>
      </div>

      <Callout kind="plain" title="Concurrency or parallelism?">
        <p>Both, in different places. The Python threads only <i>take turns</i> (concurrency, because of Python&apos;s global lock), but they spend their time waiting on solver processes, and those processes genuinely run <b>at the same time on different cores</b> (parallelism). The threads are just the hands that start and wait on them.</p>
      </Callout>

      <h2>Play with the scheduler</h2>
      <p>Defaults are this machine&apos;s real numbers: {o.data.cpu_count} cores, a median solve of {t.solve_median_s?.toFixed(1)} s, and a median model wait of {t.llm_median_s?.toFixed(0)} s per round, from your own history.</p>
      <SchedulerLab defaults={{ cores: o.data.cpu_count, solve: t.solve_median_s ?? 8, llm: t.llm_median_s ?? 140 }} />

      <h2>The life of one solve request</h2>
      <ol className="steps">
        <li><div><b>Ask for a solve.</b> <code>evaluate_async()</code> calls <code>job_runner.submit(coilset, distance)</code>. This returns immediately with a <code>Job</code> record (status <code>queued</code>).</div></li>
        <li><div><b>Cache first.</b> Before submitting, <code>find_cached()</code> looks for a finished solve with the same <b>hash</b> (a fingerprint of every geometry field plus the distance; the design&apos;s <i>name</i> is excluded, so renaming does not invalidate it). A hit is reused instantly and its &quot;last used&quot; time is bumped.</div></li>
        <li><div><b>Queue.</b> <code>submit()</code> hands <code>_run(job_id)</code> to the thread pool. A free worker picks it up; otherwise it waits in line.</div></li>
        <li><div><b>Work.</b> The worker sets status <code>running</code>, calls <code>simulate()</code>, stores the result on the same <code>Job</code> object, sets <code>done</code> (or <code>failed</code>), and saves the row to the database.</div></li>
        <li><div><b>Wait.</b> The agent thread checks the job&apos;s status every 50 ms (<code>wait_for_jobs</code>) until it is <code>done</code>. Two threads never call each other; they share the <code>Job</code> object.</div></li>
      </ol>
      <p className="small">Read: <FileRef file="frontend/src/frontend/services/job_runner.py" find="class JobRunner" /> and <FileRef file="backend/agent/femm_agent/services/scorer.py" find="def evaluate_async" />.</p>

      <h2>Design choices worth understanding</h2>
      <div className="grid g2">
        <div className="card"><b>Why the agent has its own thread</b><p className="small">If the search ran <i>on</i> the worker pool it would occupy a worker while waiting for solves that need a free worker. With enough concurrent searches every worker would be waiting and none solving: a deadlock. So the loop gets one dedicated thread per run.</p></div>
        <div className="card"><b>Why several candidates per reply</b><p className="small">The model call dominates a round (about {t.llm_median_s?.toFixed(0)} s versus about {t.femm_median_s?.toFixed(0)} s of physics), so asking for 5 designs per reply gets 5 results for almost the price of one. The catch: designs proposed together cannot learn from each other, which is why the prompt asks each to test a <i>different</i> idea.</p></div>
        <div className="card"><b>Why one temp folder per solve</b><p className="small">The old backend saved every model to one shared file, <code>tmp.fem</code>, so only one solve could safely run. Now each call writes to its own throwaway folder, which is what makes parallel solving safe.</p></div>
        <div className="card"><b>Cancelling</b><p className="small">Restart flags the run cancelled and drops <i>queued</i> solves. A solve already running cannot be interrupted, so it finishes and its result still enters the cache, useful to the next search.</p></div>
      </div>
      <Callout kind="watch" title="Limits and loose ends (from the project's own notes)">
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
          <li>The worker count is simply <code>os.cpu_count()</code>: not configurable, and only tested at 4 jobs on 4 cores.</li>
          <li>Each solve has a hard 120 s timeout that was never tested on a huge mesh.</li>
          <li>Every solve re-meshes from scratch; there is no mesh reuse between near-identical designs.</li>
          <li>A hard kill (out-of-memory, SIGKILL) mid-solve would leak that job&apos;s temp folder.</li>
          <li>Each extra distance costs a full extra solve <i>per candidate</i>, which is why an interview is capped at 5 distances (<code>MAX_DISTANCES</code>).</li>
        </ul>
        <p className="small">Source: <FileRef file="docs/Engine.md" /> and <FileRef file="docs/Agent.md" />.</p>
      </Callout>
    </>
  );
}
