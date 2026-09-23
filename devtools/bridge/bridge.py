"""Read-only bridge between the devtools web app and the real backend.

Every command prints ONE JSON document to stdout and never writes to the
project database (it is opened with mode=ro). The only thing that touches disk
is `simulate`/`pipeline`, which run femmcli in its own temp directory - exactly
what the real app does.

    python bridge.py overview
    python bridge.py configs
    python bridge.py config <id>
    python bridge.py geometry <id> <distance>
    python bridge.py pipeline <id> <distance>
    python bridge.py simulate <id> <distance>
    python bridge.py prompts <id>
    python bridge.py tables
    python bridge.py runs
    python bridge.py run <ai_run_id>

Run it with cwd = backend/ so the backend's own bare imports (`import engine`,
`import agent`) resolve, the same way the GUI's _sim_path.py arranges.
"""

import ast
import collections
import dataclasses
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
DB_PATH = BACKEND / "data" / "femm_automation.db"


# ---------------------------------------------------------------- helpers ---

def db():
    if not DB_PATH.exists():
        raise SystemExit(json.dumps({"error": f"database not found at {DB_PATH}"}))
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def jloads(text, default=None):
    if text in (None, ""):
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def datetime_diff(a, b):
    from datetime import datetime
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
    except (TypeError, ValueError):
        return None


def emit(obj):
    print(json.dumps(obj, default=str))


def coilset_for(config_id):
    from engine.models.coilset import CoilSet
    row = db().execute("select coilset_json from configs where id=?", (config_id,)).fetchone()
    if row is None:
        raise SystemExit(json.dumps({"error": f"no config with id {config_id!r}"}))
    return CoilSet(**json.loads(row["coilset_json"]))


def num(x):
    if isinstance(x, complex):
        return {"re": x.real, "im": x.imag}
    return x


# --------------------------------------------------------------- commands ---

def cmd_overview():
    conn = db()
    counts = {t: conn.execute(f"select count(*) from {t}").fetchone()[0]
              for t in ("configs", "runs", "ai_runs", "ai_run_messages", "agent_settings")}
    ai_status = {r["status"]: r["n"] for r in conn.execute("select status, count(*) n from ai_runs group by status")}
    run_status = {r["status"]: r["n"] for r in conn.execute("select status, count(*) n from runs group by status")}
    xfemm_dir = BACKEND / "engine" / "vendor" / "xfemm-4.0-linux64"
    from agent import config as agent_config

    llm, femm, cands, valid = [], [], 0, 0
    for r in conn.execute("select payload_json from ai_run_messages where phase='optimize' and role='assistant'"):
        p = jloads(r["payload_json"], {}) or {}
        if p.get("llm_seconds") is not None:
            llm.append(p["llm_seconds"])
        if p.get("femm_seconds") is not None:
            femm.append(p["femm_seconds"])
        for c in p.get("candidates") or []:
            cands += 1
            valid += 1 if c.get("valid") else 0
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    solve_secs = [(datetime_diff(r["started_at"], r["finished_at"])) for r in conn.execute(
        "select started_at, finished_at from runs where status='done' and started_at is not null and finished_at is not null")]
    solve_secs = [x for x in solve_secs if x is not None]
    timing = {"rounds": len(llm), "llm_median_s": med(llm), "femm_median_s": med(femm),
              "llm_total_s": sum(llm), "femm_total_s": sum(femm), "candidates": cands, "valid_candidates": valid,
              "solve_median_s": med(solve_secs), "solve_count": len(solve_secs)}
    emit({
        "counts": counts,
        "ai_run_status": ai_status,
        "run_status": run_status,
        "timing": timing,
        "db_bytes": DB_PATH.stat().st_size,
        "cpu_count": os.cpu_count(),
        "femmcli_present": (xfemm_dir / "femmcli").exists(),
        "femmcli_patched_and_orig": [p.name for p in xfemm_dir.glob("femmcli*")] if xfemm_dir.exists() else [],
        "agent_defaults": {
            "api_base_url": agent_config.API_BASE_URL, "model": agent_config.MODEL,
            "max_tool_calls": agent_config.MAX_TOOL_CALLS,
            "candidates_per_round": agent_config.CANDIDATES_PER_ROUND,
            "keep_best_candidates": agent_config.KEEP_BEST_CANDIDATES,
            "round_llm_attempts": agent_config.ROUND_LLM_ATTEMPTS,
            "search_attempts": agent_config.SEARCH_ATTEMPTS,
            "llm_time_limit_s": agent_config.LLM_TIME_LIMIT_S,
            "llm_max_tokens": agent_config.LLM_MAX_TOKENS,
        },
    })


def cmd_configs():
    from engine.core import DEFAULT_RC_TEST_DISTANCE  # noqa: F401  (import check)
    out = []
    for r in db().execute("select id, name, builtin, updated_at, coilset_json from configs order by lower(name)"):
        c = jloads(r["coilset_json"], {})
        out.append({
            "id": r["id"], "name": r["name"], "builtin": bool(r["builtin"]), "updated_at": r["updated_at"],
            "turns": [c.get("num_loop_turns"), c.get("num_tank_turns"), c.get("num_rc_turns")],
            "layers": [c.get("num_loop_layers"), c.get("num_tank_layers"), c.get("num_rc_layers")],
            "ferrite": bool(c.get("tank_ferrite_present") or c.get("rc_ferrite_present")),
            "metal": bool(c.get("tc_shield") or c.get("rc_shield") or c.get("backplane") or c.get("rc_backplane")),
        })
    emit(out)


def cmd_config(config_id):
    from agent.femm_agent.services.scorer import coil_diameters
    from engine.core import turn_currents
    from engine.models.coilset import table_fields
    coil = coilset_for(config_id)
    labels = {f.name: f.metadata["label"] for f in table_fields()}
    emit({
        "id": config_id,
        "fields": dataclasses.asdict(coil),
        "labels": labels,
        "diameters": coil_diameters(coil),
        "turn_currents": dict(zip(("loop", "tank", "rc"), turn_currents(coil))),
    })


def _parse_call(line):
    """'mi_drawrectangle(1, 2, 3, 4)' -> ('mi_drawrectangle', (1, 2, 3, 4)); None if not a call."""
    line = line.strip()
    if not line.endswith(")") or "(" not in line:
        return None
    name, _, rest = line.partition("(")
    if not name.replace("_", "").isalnum():
        return None
    try:
        args = ast.literal_eval("(" + rest[:-1] + ",)")
    except (ValueError, SyntaxError):
        return None
    return name, args


def _build_script(coil, distance):
    from engine.core import build_script
    return build_script(coil, distance)


def _segment(lines):
    """Walks the generated Lua and returns (shapes, sections).

    shapes   - every rectangle the model draws, in (r, z) mm, tagged with what it is
    sections - the script cut into labelled chunks, so a person can read it
    """
    shapes = []
    kinds = []  # per line: label of the chunk it belongs to
    current_shape = None
    seen_rect = False
    solve_no = 0
    for i, line in enumerate(lines):
        call = _parse_call(line)
        label = None
        if call:
            name, args = call
            if name == "mi_drawrectangle" and len(args) == 4:
                current_shape = {"r0": args[0], "z0": args[1], "r1": args[2], "z1": args[3],
                                 "kind": "boundary" if (args[2] >= 399 and not seen_rect) else "unknown",
                                 "line": i}
                seen_rect = True
                shapes.append(current_shape)
            elif name == "mi_setblockprop" and args and isinstance(args[0], str) and current_shape is not None \
                    and current_shape["kind"] in ("unknown",):
                material = args[0]
                circuit = args[3] if len(args) > 3 and isinstance(args[3], str) else None
                if material == "Copper" and circuit:
                    current_shape.update(kind="coil", circuit=circuit,
                                         coil=("loop" if circuit.startswith("loop") else
                                               "tank" if circuit.startswith("tank") else "rc"))
                else:
                    current_shape.update(kind={"Ferrite": "ferrite", "FerriteRC": "ferrite",
                                               "AluFoil": "shield", "Copper": "backplane"}.get(material, "other"),
                                         material=material)
            elif name == "mi_analyze":
                solve_no += 1
        kinds.append((i, call[0] if call else None, solve_no))

    # Sections: label a line by what phase of the script it is in.
    first_circ = next((i for i, n, _ in kinds if n == "mi_addcircprop"), len(lines))
    first_mat = next((i for i, n, _ in kinds if n in ("mi_modifymaterial",) and i > first_circ), len(lines))
    first_rect = next((i for i, n, _ in kinds if n == "mi_drawrectangle" and i > first_mat), len(lines))
    first_curr = next((i for i, n, _ in kinds if n == "mi_modifycircprop"), len(lines))
    if first_rect > first_curr:
        first_rect = first_curr

    shape_by_line = {s["line"]: s for s in shapes}
    sections = []

    def push(label, start, end, note):
        if end > start:
            sections.append({"label": label, "start": start, "end": end, "note": note})

    push("Solver helper", 0, 1, "A tiny Lua function that adds up voltage and flux over a coil's turns.")
    push("Problem setup", 1, first_circ,
         "Units (mm), axisymmetric mode, 6.78 MHz, Air/Copper/Ferrite/AluFoil materials, the far-field boundary box.")
    push("Circuits", first_circ, first_mat, "One circuit per turn, so each turn's current, voltage and flux can be read separately.")
    push("Ferrite materials", first_mat, first_rect, "Permeability and loss angle for the tank and RC ferrite.")

    # Geometry: merge consecutive rectangles that belong to the same thing.
    geo_end = first_curr
    i = first_rect
    chunk_label, chunk_start = None, i

    def label_for(shape):
        if shape is None:
            return None
        if shape["kind"] == "coil":
            return f"{shape['coil'].upper() if shape['coil'] == 'rc' else shape['coil'].capitalize()} coil copper"
        return {"ferrite": "Ferrite", "shield": "Metal shield", "backplane": "Metal backplane",
                "boundary": "Boundary"}.get(shape["kind"], "Geometry")

    notes = {
        "RC coil copper": "The receive coil's copper traces, drawn at the chosen distance above the tank.",
        "Tank coil copper": "The tank coil's copper traces, at height 0.",
        "Loop coil copper": "The loop coil's copper traces, below the tank.",
        "Ferrite": "A ferrite block that guides magnetic flux.",
        "Metal shield": "An aluminium foil shield.",
        "Metal backplane": "A copper plate standing in for a table or chassis.",
    }
    line_label = {}
    for s in shapes:
        lb = label_for(s)
        for j in range(s["line"], min(s["line"] + 8, geo_end)):
            if j not in line_label:
                line_label[j] = lb
    for j in range(first_rect, geo_end):
        lb = line_label.get(j, chunk_label)
        if lb != chunk_label and chunk_label is not None:
            push(chunk_label, chunk_start, j, notes.get(chunk_label, ""))
            chunk_start = j
        if chunk_label is None:
            chunk_start = j
        chunk_label = lb
    if chunk_label is not None:
        push(chunk_label, chunk_start, geo_end, notes.get(chunk_label, ""))

    # Solves
    # A new solve starts at the first mi_modifycircprop of each consecutive run of them.
    solve_starts = []
    prev = None
    for i, n, _ in kinds:
        if n == "mi_modifycircprop" and prev != "mi_modifycircprop":
            solve_starts.append(i)
        prev = n if n else prev
    names = ["Solve 1: drive the LOOP coil", "Solve 2: drive the TANK coil", "Solve 3: drive the RC coil"]
    for k, s in enumerate(solve_starts):
        end = solve_starts[k + 1] if k + 1 < len(solve_starts) else len(lines)
        push(names[k] if k < 3 else f"Solve {k + 1}", s, end,
             "Set one coil's current (the others get 0 A), save the model, mesh + solve, then read flux and voltage back.")
    sections.sort(key=lambda s: s["start"])
    for s in sections:
        s["preview"] = "\n".join(lines[s["start"]:min(s["end"], s["start"] + 14)])
        s["line_count"] = s["end"] - s["start"]
    return shapes, sections


def cmd_geometry(config_id, distance):
    coil = coilset_for(config_id)
    script = _build_script(coil, float(distance))
    lines = "\n".join(script.lines).split("\n")
    shapes, _ = _segment(lines)
    emit({"config": config_id, "distance": float(distance), "shapes": shapes})


def cmd_pipeline(config_id, distance):
    """Everything simulate() does BEFORE the solver runs - no femmcli involved."""
    from engine import core
    from engine.services import femm_service as nff
    coil = coilset_for(config_id)
    distance = float(distance)

    t0 = time.perf_counter()
    reason, valid = nff.check_valid_coilset(
        coil.loop_r, coil.num_loop_turns, coil.num_parallel_loop_traces,
        coil.loop_trace_spacing, coil.loop_turn_spacing, coil.loop_trace_width,
        coil.tank_center_r, coil.num_tank_turns, coil.num_parallel_tank_traces,
        coil.tank_trace_spacing, coil.tank_turn_spacing, coil.tank_trace_width,
        coil.rc_radius, coil.num_rc_turns, coil.num_parallel_rc_traces,
        coil.rc_trace_spacing, coil.rc_turn_spacing, coil.rc_trace_width)
    t_valid = time.perf_counter() - t0

    t0 = time.perf_counter()
    currents = dict(zip(("loop", "tank", "rc"), core.turn_currents(coil)))
    t_curr = time.perf_counter() - t0

    t0 = time.perf_counter()
    script = core.build_script(coil, distance)
    t_build = time.perf_counter() - t0
    rendered = script.render()
    lines = rendered.split("\n")
    shapes, sections = _segment(script.lines and "\n".join(script.lines).split("\n"))
    # sections index into the un-rendered lines; render adds only a tail, so also return that text
    raw_lines = "\n".join(script.lines).split("\n")

    calls = collections.Counter()
    for ln in raw_lines:
        c = _parse_call(ln)
        if c:
            calls[c[0]] += 1

    emit({
        "config": config_id, "distance": distance,
        "validity": {"valid": bool(valid), "reason": reason or "", "seconds": t_valid},
        "currents": {"per_turn_amps": currents, "seconds": t_curr,
                     "targets": {"loop": core.loop_current_target, "tank": core.tank_coil_current_target,
                                 "rc": core.rc_test_current_target}},
        "script": {"seconds": t_build, "rendered_lines": len(lines), "raw_lines": len(raw_lines),
                   "bytes": len(rendered), "call_counts": dict(calls.most_common()),
                   "sections": sections, "raw_lines_text": raw_lines, "tail": rendered.split("\n")[len(raw_lines):],
                   "solve_count": calls.get("mi_analyze", 0), "shape_count": len(shapes)},
    })


def cmd_simulate(config_id, distance):
    """A real solve, stage by stage, with timings. Same calls simulate() makes."""
    from engine import core
    from engine.services import xfemm_backend
    coil = coilset_for(config_id)
    distance = float(distance)
    stages = {}

    t0 = time.perf_counter()
    script = core.build_script(coil, distance)
    stages["build_script_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    try:
        raw = xfemm_backend.run_femmcli(script.render())
    except Exception as exc:
        emit({"error": f"{type(exc).__name__}: {exc}"[:2000]})
        return
    stages["femmcli_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    result = core.extract_result(raw, coil, distance)
    stages["extract_s"] = time.perf_counter() - t0

    emit({"config": config_id, "distance": distance, "stages": stages,
          "raw": {k: num(v) for k, v in raw.items()}, "result": dataclasses.asdict(result)})


def _cached_metrics(conn, config_id, distances):
    out = {}
    for d in distances:
        row = conn.execute(
            "select result_json from runs where config_id=? and status='done' and distance=? "
            "order by finished_at desc limit 1", (config_id, d)).fetchone()
        if row:
            out[d] = jloads(row["result_json"])
    return out


def cmd_prompts(config_id):
    from agent.femm_agent.models.schema import build_tools
    from agent.femm_agent.services.prompts import system_prompt
    from agent.input_chat.models.spec import AgentSpec
    from agent.input_chat.models.variables import free_fields_for
    from agent.input_chat.services.interview import FINALIZE_SPEC_TOOL, FLAG_OFF_TOPIC_TOOL, interview_system_prompt
    from agent.settings.prompt_defs import PROMPT_DEFS, default_text
    from agent.settings.settings import AgentSettings

    coil = coilset_for(config_id)
    settings = AgentSettings()
    spec = AgentSpec.from_settings(settings, baseline_config_id=config_id)
    conn = db()
    metrics = _cached_metrics(conn, config_id, spec.distances)
    note = None
    missing = [d for d in spec.distances if d not in metrics]
    if missing:
        # No saved solve for these distances: solve them now, in parallel, exactly as the
        # search would before its first round (it always solves the baseline first).
        from concurrent.futures import ThreadPoolExecutor
        from engine.core import simulate
        with ThreadPoolExecutor(max_workers=len(missing)) as pool:
            for d, res in zip(missing, pool.map(lambda x: simulate(coil, x), missing)):
                metrics[d] = dataclasses.asdict(res)
        note = f"Baseline numbers for {missing} mm were solved live just now (no saved run existed), like the search does before round 1."
    free_fields = free_fields_for(coil, spec.variable_spec)
    rendered = system_prompt(coil, free_fields, spec, metrics)
    interview = interview_system_prompt(config_id, coil, settings)
    tools = build_tools(free_fields, config_id)

    defs = {}
    for key, d in PROMPT_DEFS.items():
        defs[key] = {"title": d.title, "help": d.help, "placeholders": d.placeholders,
                     "required": list(d.required), "switches": list(d.switches), "template": default_text(key)}
    emit({
        "config": config_id, "note": note, "defs": defs,
        "search_rendered": rendered, "interview_rendered": interview,
        "tools": {"propose_coilset": tools[0], "finalize_spec": FINALIZE_SPEC_TOOL[0], "flag_off_topic": FLAG_OFF_TOPIC_TOOL[0]},
        "free_field_count": len(free_fields),
        "objectives": [dataclasses.asdict(o) for o in spec.objectives],
        "spec_defaults": {"distances": spec.distances, "max_tool_calls": spec.max_tool_calls,
                          "candidates_per_round": spec.candidates_per_round,
                          "keep_best_candidates": spec.keep_best_candidates},
    })


def cmd_tables():
    """Schema, row count and one trimmed sample row for each table."""
    conn = db()
    out = []
    for r in conn.execute("select name, sql from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
        name = r["name"]
        cols = [{"name": c["name"], "type": c["type"], "notnull": bool(c["notnull"]), "pk": bool(c["pk"])}
                for c in conn.execute(f"pragma table_info({name})")]
        sample = conn.execute(f"select * from {name} order by rowid desc limit 1").fetchone()
        trimmed = None
        if sample is not None:
            trimmed = {k: (v[:140] + "…" if isinstance(v, str) and len(v) > 140 else v) for k, v in dict(sample).items()}
        out.append({"name": name, "columns": cols, "rows": conn.execute(f"select count(*) from {name}").fetchone()[0],
                    "sample": trimmed})
    emit(out)


def cmd_runs():
    out = []
    for r in db().execute("select * from ai_runs order by created_at desc"):
        spec = jloads(r["spec_json"], {}) or {}
        rounds = db().execute(
            "select count(*) n from ai_run_messages where ai_run_id=? and phase='optimize'", (r["id"],)).fetchone()["n"]
        best = jloads(r["best_tool_result_json"], {}) or {}
        out.append({
            "id": r["id"], "baseline": r["baseline_config_id"], "status": r["status"],
            "created_at": r["created_at"], "started_at": r["started_at"], "finished_at": r["finished_at"],
            "rounds": rounds, "best_round": r["best_round"],
            "objective_text": (spec.get("objective_text") or "")[:240],
            "n_objectives": len(spec.get("objectives") or []),
            "distances": spec.get("distances"), "goal_met": best.get("goal_met"),
        })
    emit(out)


def cmd_run(run_id):
    conn = db()
    r = conn.execute("select * from ai_runs where id=?", (run_id,)).fetchone()
    if r is None:
        emit({"error": f"no ai run {run_id!r}"})
        return
    spec = jloads(r["spec_json"], {}) or {}
    spec.pop("settings", None)  # the settings blob repeats every prompt in full; not useful here
    rounds, interview = [], []
    for m in conn.execute("select * from ai_run_messages where ai_run_id=? order by phase, seq", (run_id,)):
        payload = jloads(m["payload_json"], {}) or {}
        if m["phase"] == "interview":
            interview.append({
                "seq": m["seq"], "role": m["role"], "content": m["content"] or payload.get("content") or "",
                "reasoning": payload.get("reasoning_content") or "",
                "tool_calls": [{"name": tc.get("function", {}).get("name"),
                                "arguments": tc.get("function", {}).get("arguments")}
                               for tc in payload.get("tool_calls") or []],
            })
        else:
            cands = []
            for c in payload.get("candidates") or []:
                tr = c.get("tool_result") or {}
                cands.append({
                    "delta": c.get("delta"), "valid": c.get("valid"), "error": c.get("error"),
                    "rationale": c.get("rationale"), "goal_met": tr.get("goal_met"),
                    "objectives": [{k: o.get(k) for k in ("metric", "kind", "passed", "margin", "worst_change_pct",
                                                          "requirement", "required")}
                                   for o in tr.get("objectives") or []],
                })
            rounds.append({
                "round": m["round_num"], "reasoning": m["content"] or "", "candidates": cands,
                "search_attempt": payload.get("search_attempt"), "attempt_round": payload.get("attempt_round"),
                "llm_seconds": payload.get("llm_seconds"), "femm_seconds": payload.get("femm_seconds"),
                "llm_failures": payload.get("llm_failures") or [],
                "llm_stats": payload.get("llm_stats") or {},
            })
    emit({
        "id": r["id"], "baseline": r["baseline_config_id"], "status": r["status"], "error": r["error"],
        "created_at": r["created_at"], "started_at": r["started_at"], "finished_at": r["finished_at"],
        "best_round": r["best_round"], "spec": spec,
        "best_coilset": jloads(r["best_coilset_json"]), "best_result": jloads(r["best_tool_result_json"]),
        "interview": interview, "rounds": rounds,
    })


COMMANDS = {
    "overview": (cmd_overview, 0), "configs": (cmd_configs, 0), "config": (cmd_config, 1),
    "geometry": (cmd_geometry, 2), "pipeline": (cmd_pipeline, 2), "simulate": (cmd_simulate, 2),
    "prompts": (cmd_prompts, 1), "tables": (cmd_tables, 0), "runs": (cmd_runs, 0), "run": (cmd_run, 1),
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit(json.dumps({"error": f"usage: bridge.py {{{'|'.join(COMMANDS)}}} [args]"}))
    fn, nargs = COMMANDS[sys.argv[1]]
    args = sys.argv[2:]
    if len(args) != nargs:
        raise SystemExit(json.dumps({"error": f"{sys.argv[1]} takes {nargs} argument(s)"}))
    try:
        fn(*args)
    except SystemExit:
        raise
    except Exception as exc:  # the web app shows this instead of a stack trace
        emit({"error": f"{type(exc).__name__}: {exc}"[:2000]})
        sys.exit(0)
