// FEMM coil simulator tools for pi. Thin wrapper: every tool shells out to
// backend/agent_cli.py, which prints one JSON document. To attach
// another tool (Excel, ...) later, add another extension that registers more
// tools; the workflow skill does not change.
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const SIM_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "../../../backend");

// The office AI server, so nobody has to add it to their own ~/.pi/agent/models.json.
// Same server and model as the desktop app's default (backend/agent/config.py).
const AI_SERVER = "http://192.168.1.74:8854/v1";
const AI_MODEL = "Qwen3.8-Flash-Next-UD-Q4_K_XL";

export default function (pi: ExtensionAPI) {
  pi.registerProvider("office-gpu", {
    baseUrl: AI_SERVER,
    api: "openai-completions",
    apiKey: "none",
    compat: { supportsDeveloperRole: false, supportsReasoningEffort: false },
    models: [
      {
        id: AI_MODEL,
        name: `${AI_MODEL} (office server)`,
        reasoning: true,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 262144,
        maxTokens: 32768,
      },
    ],
  });

  // Exact count of simulations run in this pi session (a sweep counts one per value). Models miscount their budget,
  // so every simulate/sweep result carries the running total.
  let simulationsUsed = 0;
  const DEFAULT_BUDGET = 12;

  async function cli(args: string[], signal?: AbortSignal, timeoutMs = 180_000, simulations = 0) {  // timeout > the solver's own 120 s limit, so its error message arrives
    const res = await pi.exec("uv", ["run", "python", "agent_cli.py", ...args], {
      cwd: SIM_DIR,
      signal,
      timeout: timeoutMs,
    });
    // agent_cli prints {"error": ...} on stdout with exit code 1; fall back to stderr for crashes.
    let text = res.stdout.trim() || res.stderr.trim() || `agent_cli exited with code ${res.code}`;
    if (simulations > 0) {
      simulationsUsed += simulations;
      try {
        const obj = JSON.parse(text);
        if (obj && typeof obj === "object" && !Array.isArray(obj)) {
          text = JSON.stringify({ ...obj, session_simulations_used: simulationsUsed, default_budget: DEFAULT_BUDGET });
        }
      } catch {
        // not JSON (a crash): leave the text as is
      }
    }
    return {
      content: [{ type: "text" as const, text }],
      details: {},
      ...(res.code !== 0 && { isError: true }),
    };
  }

  const overrides = Type.Optional(
    Type.Record(Type.String(), Type.Number(), {
      description:
        "CoilSet field overrides applied to a copy of the SAVED config, e.g. {\"num_tank_turns\": 6, \"rc_radius\": 50}. " +
        "They do not accumulate between calls: every call starts from the saved config, so resend every override you want. Get valid keys from femm_get_config.",
    }),
  );
  const setArgs = (o?: Record<string, number>) =>
    Object.entries(o ?? {}).flatMap(([k, v]) => ["--set", `${k}=${v}`]);

  pi.registerTool({
    name: "femm_list_configs",
    label: "List coil configs",
    description: "List the saved coil configurations (id, name, builtin) available to simulate.",
    parameters: Type.Object({}),
    async execute(_id, _params, signal) {
      return cli(["list"], signal);
    },
  });

  pi.registerTool({
    name: "femm_get_config",
    label: "Get coil config",
    description: "Show every geometry/material parameter (mm, unitless counts) of one coil config.",
    parameters: Type.Object({ config_id: Type.String({ description: "Config id from femm_list_configs" }) }),
    async execute(_id, params, signal) {
      return cli(["show", params.config_id], signal);
    },
  });

  pi.registerTool({
    name: "femm_simulate",
    label: "Simulate coil set",
    description:
      "Run the FEMM (6.78 MHz, axisymmetric) simulation for a config, optionally with parameter overrides, at one RC separation distance. " +
      "Returns self inductance L_loop/L_tank/L_rc (H), mutual inductance M12/M13/M23 (H), ESR loop/tank/rc (Ohm) and couplings k12/k13/k23, " +
      "plus `pct_vs_base`: the % change of L, M and ESR versus the unmodified config, already computed - use it instead of doing arithmetic. " +
      "Deterministic (same overrides = same numbers). Takes ~6-12 s and counts as 1 simulation against your budget. " +
      "A solver failure (e.g. overlapping traces) comes back as an error, not a result.",
    parameters: Type.Object({
      config_id: Type.String({ description: "Base config id" }),
      distance: Type.Optional(Type.Number({ description: "RC separation in mm (default 5)" })),
      overrides,
    }),
    async execute(_id, params, signal) {
      const args = ["simulate", "--config", params.config_id, ...setArgs(params.overrides)];
      if (params.distance !== undefined) args.push("--distance", String(params.distance));
      return cli(args, signal, undefined, 1);
    },
  });

  pi.registerTool({
    name: "femm_sweep",
    label: "Sweep one parameter",
    description:
      "Simulate the same config once per value of ONE parameter (2-8 values), other overrides held fixed. Returns a compact `summary` table (quote sweep numbers from it verbatim) and every point with `pct_vs_base` " +
      "(same fields as femm_simulate) plus the baseline. Use it to find a lever's direction and size, to bracket a target, and to check a winner's neighbours " +
      "for solver noise. A point whose solve fails carries its own `error`. Counts as one simulation PER VALUE against your budget; takes ~7 s per value.",
    parameters: Type.Object({
      config_id: Type.String({ description: "Base config id" }),
      param: Type.String({ description: "CoilSet field to vary, e.g. \"tank_trace_thickness\"" }),
      values: Type.Array(Type.Number(), { minItems: 2, maxItems: 8, description: "Values of `param` to simulate (2-8)" }),
      distance: Type.Optional(Type.Number({ description: "RC separation in mm (default 5)" })),
      overrides,
    }),
    async execute(_id, params, signal) {
      const args = ["sweep", "--config", params.config_id, "--param", params.param, "--values", params.values.join(","), ...setArgs(params.overrides)];
      if (params.distance !== undefined) args.push("--distance", String(params.distance));
      return cli(args, signal, 420_000, new Set(params.values).size);
    },
  });

  pi.registerTool({
    name: "femm_save_config",
    label: "Save coil config",
    description: "Persist a base config plus overrides as a new named config. Only when the user asks to keep a design: it writes to the shared config database.",
    parameters: Type.Object({
      config_id: Type.String({ description: "Base config id" }),
      name: Type.String({ description: "Name for the new config" }),
      overrides,
    }),
    async execute(_id, params, signal) {
      return cli(["save", "--config", params.config_id, "--name", params.name, ...setArgs(params.overrides)], signal);
    },
  });
}
