import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT } from "./repo";

const BACKEND = path.join(REPO_ROOT, "backend");
const BRIDGE = path.join(process.cwd(), "bridge", "bridge.py");

function pythonBin(): string {
  const venv = path.join(REPO_ROOT, ".venv", "bin", "python");
  return fs.existsSync(venv) ? venv : "python3";
}

const cache = new Map<string, { at: number; value: unknown }>();

/**
 * Runs one read-only bridge command (see bridge/bridge.py) and returns its JSON.
 * Cheap, deterministic commands are cached for a few seconds so a page that asks
 * for the same thing twice does not spawn Python twice.
 */
export async function bridge<T = any>(cmd: string, args: (string | number)[] = [], opts: { ttlMs?: number; timeoutMs?: number } = {}): Promise<T> {
  const key = [cmd, ...args].join("\u0000");
  const ttl = opts.ttlMs ?? 0;
  const hit = cache.get(key);
  if (ttl && hit && Date.now() - hit.at < ttl) return hit.value as T;

  const stdout = await new Promise<string>((resolve, reject) => {
    execFile(pythonBin(), [BRIDGE, cmd, ...args.map(String)],
      { cwd: BACKEND, timeout: opts.timeoutMs ?? 60_000, maxBuffer: 64 * 1024 * 1024 },
      (err, out, errOut) => {
        if (out && out.trim().startsWith("{") || out && out.trim().startsWith("[")) return resolve(out);
        reject(new Error((errOut || out || String(err)).slice(0, 1500)));
      });
  });
  const value = JSON.parse(stdout);
  if (value && typeof value === "object" && !Array.isArray(value) && "error" in value && Object.keys(value).length === 1) {
    throw new Error(String(value.error));
  }
  if (ttl) cache.set(key, { at: Date.now(), value });
  return value as T;
}

/** Same as bridge() but never throws - returns { ok:false, error } so pages can show a friendly notice. */
export async function tryBridge<T = any>(cmd: string, args: (string | number)[] = [], opts: { ttlMs?: number; timeoutMs?: number } = {}):
  Promise<{ ok: true; data: T } | { ok: false; error: string }> {
  try {
    return { ok: true, data: await bridge<T>(cmd, args, opts) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}
