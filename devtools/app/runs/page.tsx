import Link from "next/link";
import { Callout, ErrorBox, PageHead, fmtDate, fmtSecs } from "@/components/ui";
import { tryBridge } from "@/lib/bridge";

export const dynamic = "force-dynamic";
const PILL: Record<string, string> = { goal_met: "good", running: "agent", interviewing: "gray", budget_exhausted: "warn", stopped_by_model: "warn", failed: "bad" };

export default async function Runs({ searchParams }: { searchParams: Promise<{ all?: string }> }) {
  const { all } = await searchParams;
  const r = await tryBridge<any[]>("runs", [], { ttlMs: 3000 });
  if (!r.ok) return <><PageHead eyebrow="Live data" title="AI runs explorer" lede="" /><ErrorBox error={r.error} /></>;
  const rows = r.data.filter((x) => all || x.rounds > 0 || x.status !== "interviewing");
  const hidden = r.data.length - rows.length;
  return (
    <>
      <PageHead eyebrow="Live data" title="AI runs explorer"
        lede="Every AI search you have ever started, read straight from the database. Open one to replay it: what you asked, what the model proposed each round, how each design scored, and where the time went." />
      <Callout kind="plain"><p>A row exists as soon as a chat is opened, so many rows are chats that never became a search. {hidden > 0 && <>{hidden} such abandoned chat{hidden === 1 ? "" : "s"} hidden. <Link href="/runs?all=1">Show all</Link>.</>}{all && <> <Link href="/runs">Hide abandoned chats</Link>.</>}</p></Callout>
      <div className="card scroll-x"><table className="t"><thead><tr><th>Run</th><th>Status</th><th>Started</th><th>Rounds</th><th>Duration</th><th>Goal (from the interview)</th></tr></thead><tbody>
        {rows.map((x) => {
          const dur = x.started_at && x.finished_at ? (new Date(x.finished_at).getTime() - new Date(x.started_at).getTime()) / 1000 : null;
          return (
            <tr key={x.id}>
              <td><Link href={`/runs/${x.id}`} className="num">{x.id}</Link><div className="small muted">{x.baseline}</div></td>
              <td><span className={`pill ${PILL[x.status] ?? "gray"}`}>{x.status.replace(/_/g, " ")}</span></td>
              <td className="small">{fmtDate(x.started_at ?? x.created_at)}</td>
              <td className="num">{x.rounds || "–"}{x.best_round ? <span className="muted"> · best R{x.best_round}</span> : ""}</td>
              <td className="small">{fmtSecs(dur)}</td>
              <td className="small" style={{ maxWidth: 420 }}>{x.objective_text || <span className="muted">no goal set</span>}</td>
            </tr>);
        })}
      </tbody></table></div>
    </>
  );
}
