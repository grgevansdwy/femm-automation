import { tryBridge } from "@/lib/bridge";
export const dynamic = "force-dynamic";

const SAFE = /^[A-Za-z0-9_.-]{1,80}$/;

export async function GET(req: Request) {
  const q = new URL(req.url).searchParams;
  const names = "id".split(",").filter(Boolean);
  const args: string[] = [];
  for (const n of names) {
    const v = q.get(n) ?? "";
    if (!SAFE.test(v)) return Response.json({ error: `bad or missing parameter: ${n}` }, { status: 400 });
    args.push(v);
  }
  const r = await tryBridge("config", args, { ttlMs: 5000, timeoutMs: 30000 });
  return Response.json(r.ok ? r.data : { error: r.error }, { status: r.ok ? 200 : 500 });
}
