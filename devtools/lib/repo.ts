import fs from "node:fs";
import path from "node:path";

/** Repo root = the folder above devtools/ (override with REPO_ROOT). */
export const REPO_ROOT = process.env.REPO_ROOT ?? path.resolve(process.cwd(), "..");

const BLOCKED_DIRS = new Set([".git", ".venv", "node_modules", ".next", "__pycache__", ".pytest_cache", "femm_models"]);
const TEXT_EXT = new Set([".py", ".md", ".txt", ".toml", ".json", ".ts", ".tsx", ".lua", ".ini", ".patch", ".css"]);

/** Resolve a repo-relative path, refusing anything that escapes the repo or is not plain text source. */
export function safePath(rel: string): string | null {
  const abs = path.resolve(REPO_ROOT, rel);
  if (!abs.startsWith(REPO_ROOT + path.sep)) return null;
  const parts = path.relative(REPO_ROOT, abs).split(path.sep);
  if (parts.some((p) => BLOCKED_DIRS.has(p))) return null;
  if (!TEXT_EXT.has(path.extname(abs))) return null;
  return abs;
}

export function readRepoFile(rel: string): string | null {
  const abs = safePath(rel);
  if (!abs || !fs.existsSync(abs) || !fs.statSync(abs).isFile()) return null;
  if (fs.statSync(abs).size > 600_000) return null;
  return fs.readFileSync(abs, "utf8");
}

/** 1-based line number of the first line containing `needle` (e.g. "def turn_currents"), or 1. */
export function findLine(rel: string, needle: string): number {
  const text = readRepoFile(rel);
  if (!text) return 1;
  const i = text.split("\n").findIndex((l) => l.includes(needle));
  return i === -1 ? 1 : i + 1;
}

/** A link into the code viewer at the line where `needle` is defined. */
export function codeHref(rel: string, needle?: string): string {
  const line = needle ? findLine(rel, needle) : 1;
  return `/code?file=${encodeURIComponent(rel)}#L${line}`;
}

export type TreeNode = { name: string; path: string; dir: boolean; children?: TreeNode[] };

const ROOTS = ["backend", "frontend", "docs", "template"];

export function repoTree(): TreeNode[] {
  function walk(rel: string): TreeNode[] {
    const abs = path.join(REPO_ROOT, rel);
    const entries = fs.readdirSync(abs, { withFileTypes: true })
      .filter((e) => !BLOCKED_DIRS.has(e.name) && !e.name.startsWith("."))
      .sort((a, b) => Number(b.isDirectory()) - Number(a.isDirectory()) || a.name.localeCompare(b.name));
    const out: TreeNode[] = [];
    for (const e of entries) {
      const r = path.posix.join(rel, e.name);
      if (e.isDirectory()) {
        const children = walk(r);
        if (children.length) out.push({ name: e.name, path: r, dir: true, children });
      } else if (TEXT_EXT.has(path.extname(e.name))) {
        out.push({ name: e.name, path: r, dir: false });
      }
    }
    return out;
  }
  return ROOTS.filter((r) => fs.existsSync(path.join(REPO_ROOT, r))).map((r) => ({ name: r, path: r, dir: true, children: walk(r) }));
}

/** First useful sentence of a Python/TS/MD file, used as a fallback one-liner in the code map. */
export function firstLine(rel: string): string {
  const text = readRepoFile(rel);
  if (!text) return "";
  const lines = text.split("\n").slice(0, 40);
  for (const raw of lines) {
    let l = raw.trim().replace(/^("""|'''|#+|\/\/|\/\*+|\*)\s*/, "").replace(/("""|'''|\*\/)$/, "").trim();
    if (!l || /^[=\-_*#]{3,}$/.test(l) || l.startsWith("!") || /^(import|from|export|const)\b/.test(l)) continue;
    if (/^\w+\.(py|ts|md)\b/.test(l) && l.length < 40) continue;
    return l.length > 150 ? l.slice(0, 147) + "…" : l;
  }
  return "";
}
