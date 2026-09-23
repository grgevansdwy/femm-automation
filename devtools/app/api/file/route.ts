import { readRepoFile } from "@/lib/repo";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const rel = new URL(req.url).searchParams.get("file") ?? "";
  const text = readRepoFile(rel);
  if (text === null) return Response.json({ error: "not found, or not a readable source file" }, { status: 404 });
  return Response.json({ file: rel, text });
}
