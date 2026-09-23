import { PageHead } from "@/components/ui";

const TERMS: { group: string; items: [string, string][] }[] = [
  { group: "Physics", items: [
    ["Inductance (L)", "How much magnetic field a coil produces per amp. Bigger coil or more turns → larger L."],
    ["Mutual inductance (M)", "How much of one coil's field passes through another. It is the wireless link, and it fades with distance."],
    ["Coupling (k)", "M scaled to 0–1: k = M / √(L₁·L₂). Lets you compare designs of different sizes."],
    ["ESR", "Equivalent series resistance: energy the coil wastes as heat. Lower is better."],
    ["Skin / proximity effect", "At high frequency current crowds onto a conductor's surface and toward neighbouring traces, raising resistance beyond the DC value."],
    ["6.78 MHz", "The operating frequency (an ISM band used for wireless charging)."],
    ["Ferrite", "A magnetic ceramic that guides flux, raising L and M."],
    ["Shield / backplane", "Nearby metal. It fights the field with eddy currents, lowering L and M and adding loss."],
    ["Axisymmetric", "Solving a spinning-symmetric object by modelling one half-slice; each rectangle is really a ring."],
  ] },
  { group: "The three coils", items: [
    ["Loop", "The driven coil. With the tank it forms the transmit side (TC)."],
    ["Tank", "The resonant transmit coil. Lowering its ESR is a headline goal."],
    ["RC", "The receive coil, placed a chosen distance above the others."],
    ["M12 / M13 / M23", "Loop↔tank, loop↔RC, tank↔RC. M23 / k23 is the power-transfer link."],
    ["CoilSet", "The dataclass holding every number that defines one design."],
    ["SimResult", "The 12 outputs of one simulation at one distance."],
  ] },
  { group: "Simulation", items: [
    ["FEMM", "Finite Element Method Magnetics, a free physics solver (Windows program)."],
    ["xfemm / femmcli", "An open-source port of FEMM's solver with a headless command line. What the project uses now."],
    ["Lua script", "The list of commands (draw this, solve that) the engine writes and femmcli runs."],
    ["Wine", "A Windows compatibility layer used by the old backend, which allowed only one solve at a time."],
    ["Mesh", "The triangles the solver breaks the geometry into before solving."],
    ["Vendored", "A copy of someone else's program kept inside the repo. Here it carries a one-line bug fix."],
  ] },
  { group: "The agent", items: [
    ["LLM", "Large language model: the AI that reads text and writes text or tool calls."],
    ["System prompt", "Instructions sent at the start of every conversation, before anything you say."],
    ["Tool call", "The model asking your code to run a named function with arguments. The model never runs anything itself."],
    ["propose_coilset", "The search tool: 'try this design change'."],
    ["finalize_spec", "The interview tool: 'here is the finished goal'."],
    ["AgentSpec", "The structured goal: objectives, distances, allowed fields, budget. The only handoff from interview to search."],
    ["Objective", "One requirement: a metric, a kind (match/reduce/increase), a tolerance, required or preferred."],
    ["Margin", "How far inside (≥0) or outside (<0) a requirement a result is, as a fraction of its tolerance."],
    ["Baseline", "The starting design every candidate is compared against."],
    ["Candidate", "One proposed design: the baseline plus a small set of changes."],
    ["Round", "One model reply and the solving of its candidates. A search has up to 50."],
    ["Delta", "Only the fields a candidate changes from the baseline."],
    ["Rationale", "The model's one-sentence reason for a candidate, required by the tool."],
    ["Consent group", "Ferrite, shields, backplanes, PCB thickness: fixed unless you explicitly allow the AI to change them."],
    ["Search memory", "The rebuilt-each-round summary of the best and already-tried candidates."],
    ["Streaming", "Receiving the model's reply piece by piece, so a time limit can be enforced and reasoning shown live."],
    ["RAG", "Retrieval-augmented generation: looking up documents to add to the prompt. Not used here."],
  ] },
  { group: "Code", items: [
    ["JobRunner", "Queue + worker threads + result cache. The only caller of simulate()."],
    ["Thread pool", "A fixed set of worker threads that take queued jobs."],
    ["Cache hit", "A finished solve with the same geometry hash and distance is reused instead of recomputed."],
    ["sys.path shim", "_sim_path.py adds backend/ to Python's import path so the frontend can import it."],
    ["Bridge (devtools)", "The small read-only Python script this app uses to call the real backend."],
  ] },
];

export default function Glossary() {
  return (
    <>
      <PageHead eyebrow="Reference" title="Glossary" lede="Every term used in this tour, in one plain sentence." />
      {TERMS.map((g) => (
        <section key={g.group}>
          <h2>{g.group}</h2>
          <div className="card"><dl className="kv" style={{ gridTemplateColumns: "minmax(140px, 220px) 1fr", gap: "10px 20px" }}>
            {g.items.map(([t, d]) => <><dt key={t + "t"} style={{ color: "var(--ink)", fontWeight: 600 }}>{t}</dt><dd key={t + "d"}>{d}</dd></>)}
          </dl></div>
        </section>
      ))}
    </>
  );
}
