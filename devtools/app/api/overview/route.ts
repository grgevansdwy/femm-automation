import { tryBridge } from "@/lib/bridge";
export const dynamic = "force-dynamic";

export async function GET() {
  const r = await tryBridge("overview", [], { ttlMs: 4000 });
  return Response.json(r.ok ? r.data : { error: r.error }, { status: r.ok ? 200 : 500 });
}
