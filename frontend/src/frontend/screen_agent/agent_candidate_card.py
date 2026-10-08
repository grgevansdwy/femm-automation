"""Shared per-candidate round card: geometry + input-changes table +
output-vs-goal table + a short reasoning line. Used by both the live
Search Progress feed (agent_panel.py) and the AI-run history replay
(agent_history.py) - kept in its own module, not agent_panel.py, so
agent_history.py can import it without a circular import (agent_panel.py
already imports agent_history for the "History" toggle). The finished-
search summary (draw_search_summary()) lives here for the same reason.

Works on plain dicts throughout - a candidate shaped like
agent.state.CandidateResult (delta/valid/tool_result/error/
candidate_coilset), either live (dataclasses.asdict()'d fresh) or read
back from the database as JSON - never a live dataclass, so the exact same
rendering code serves a running search and a replayed historical one.

The result table (_metric_rows) reads scorer.tool_result()'s "objectives"
list - self-describing, one entry per objective with its own requirement
and pass/fail - so it needs no spec. Runs saved before that shape existed
(an M-metric gate plus a fixed ESR cut on all three coils) still render via
_legacy_metric_rows(), from their stored spec.
"""

from imgui_bundle import imgui

from ..visualization import coil_diagram, femm_open

_PASS_COLOR = imgui.ImVec4(0.35, 0.8, 0.45, 1.0)
_FAIL_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
_MUTED_COLOR = imgui.ImVec4(0.65, 0.65, 0.7, 1.0)
_WHY_COLOR = imgui.ImVec4(0.85, 0.85, 0.9, 1.0)
_GEOMETRY_H = 150.0

# The default ImGui font atlas only covers Basic Latin (see runs_panel.py's
# _truncate) - model text routinely contains arrows/dashes/comparison signs
# that otherwise render as a missing-glyph box.
_ASCII_SUBSTITUTIONS = {
    "→": "->", "←": "<-", "≥": ">=", "≤": "<=", "—": "-", "–": "-",
    "…": "...", "±": "+/-", "×": "x", "≈": "~", "‘": "'", "’": "'",
    "“": '"', "”": '"', "µ": "u", "Ω": "Ohm",
}


def _ascii(text: str) -> str:
    for src, dst in _ASCII_SUBSTITUTIONS.items():
        text = text.replace(src, dst)
    return text.encode("ascii", "ignore").decode("ascii")


def full_text(text: str) -> str:
    """The complete text, ASCII-safe, line breaks kept - for the expandable
    full-reasoning section, which must never be cut off."""
    return "\n".join(line.rstrip() for line in _ascii(text or "").strip().splitlines())


def _fmt(value) -> str:
    return f"{value:.6g}" if isinstance(value, float) else str(value)


def design_name(candidate: dict) -> str:
    """ "C7", or "C7, built on C3" - how the search itself names a simulated
    design. "" for a rejected proposal and for runs saved before designs had labels."""
    label, base = candidate.get("label"), candidate.get("base_label")
    if not label:
        return f"built on {base}" if base else ""
    return f"{label}, built on {base}" if base else label


def _draw_input_table(table_id: str, delta: dict, baseline_buffer: dict, step_fields=None) -> None:
    """step_fields: the fields this proposal itself changed, when it was built on
    an earlier design - marked with *, the rest being that design's changes."""
    if not delta:
        imgui.text_disabled("(no fields changed)")
        return
    if imgui.begin_table(table_id, 3, imgui.TableFlags_.borders.value):
        imgui.table_setup_column("Field")
        imgui.table_setup_column("Baseline")
        imgui.table_setup_column("Proposed")
        imgui.table_headers_row()
        for field, new_value in delta.items():
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(f"{field} *" if step_fields and field in step_fields else field)
            imgui.table_next_column()
            imgui.text(_fmt(baseline_buffer.get(field, "?")))
            imgui.table_next_column()
            imgui.text(_fmt(new_value))
        imgui.end_table()


def _metric_rows(tool_result: dict, spec_summary: dict) -> list[dict]:
    """One row per objective checked this round: name, formatted change,
    requirement text, pass/fail - straight from the scorer's own numbers
    (never by re-parsing generated text) so the "Pass?" column can't drift
    from the actual threshold."""
    if "objectives" not in tool_result:
        return _legacy_metric_rows(tool_result, spec_summary)
    rows = []
    for o in tool_result["objectives"]:
        multi = len(o.get("change_pct_by_distance") or {}) > 1
        change = f"{o['worst_change_pct']:+.1f}%"
        if o.get("target_value") is not None:  # an absolute target reads best as the value itself
            change = f"{o['value']:.4g} ({change})"
        rows.append({
            "metric": o["metric"],
            "change_text": change + (" (worst distance)" if multi else ""),
            "requirement": o["requirement"] + ("" if o["required"] else " (preference)"),
            "passed": o["passed"], "required": o["required"],
        })
    return rows


def _legacy_metric_rows(tool_result: dict, spec_summary: dict) -> list[dict]:
    """The pre-objective-list shape: an M-metric gate (primary_metrics) plus
    min_esr_improvement_pct on all three coils."""
    rows = []
    metric_errors = tool_result.get("metric_error_pct_by_distance", {})
    for metric, tol in (spec_summary.get("primary_metrics") or {}).items():
        errs = list(metric_errors.get(metric, {}).values())
        # The signed error at whichever distance deviates most - showing
        # abs() with a "+" sign read as "always went up" even when it dropped.
        worst = max(errs, key=abs, default=0.0)
        rows.append({
            "metric": metric, "change_text": f"{worst:+.1f}%" + (" (worst distance)" if len(errs) > 1 else ""),
            "requirement": f"within {tol}%", "passed": abs(worst) <= tol,
        })

    min_esr = spec_summary.get("min_esr_improvement_pct", 0.0)
    for metric, key in (("loop_esr", "loop_esr_change_pct"), ("tank_esr", "tank_esr_change_pct"), ("rc_esr", "rc_esr_change_pct")):
        change = tool_result.get(key)
        rows.append({
            "metric": metric, "change_text": f"{change:+.1f}%" if change is not None else "?",
            "requirement": f"reduce >= {min_esr}%", "passed": change is not None and change <= -min_esr,
        })
    return rows


def draw_result_table(table_id: str, tool_result: dict, spec_summary: dict) -> None:
    if imgui.begin_table(table_id, 4, imgui.TableFlags_.borders.value):
        imgui.table_setup_column("Metric")
        imgui.table_setup_column("Change")
        imgui.table_setup_column("Requirement")
        imgui.table_setup_column("Pass?")
        imgui.table_headers_row()
        for row in _metric_rows(tool_result, spec_summary):
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(row["metric"])
            imgui.table_next_column()
            imgui.text(row["change_text"])
            imgui.table_next_column()
            imgui.text(row["requirement"])
            imgui.table_next_column()
            imgui.text_colored(_PASS_COLOR if row["passed"] else _FAIL_COLOR, "Yes" if row["passed"] else "No")
        imgui.end_table()

    goal_met = tool_result.get("goal_met", False)
    imgui.text_colored(_PASS_COLOR if goal_met else _FAIL_COLOR, f"Goal met: {'YES' if goal_met else 'no'}")
    fragility = fragility_text(tool_result)
    if fragility:
        imgui.text_colored(_PASS_COLOR if tool_result.get("fragility") == "passed" else _FAIL_COLOR, fragility)


def fragility_text(tool_result: dict) -> str:
    """The design's fragility check, if it has had one, as a line for a person:
    it only wins if it still meets the goal with every dimension 1-2% off."""
    summary = tool_result.get("fragility")
    if not summary:
        return ""
    if summary == "passed":
        return "Fragility check: passed (still meets the goal with every dimension changed by 1-2%)"
    return f"Fragility check: {_ascii(summary)} - not a winner"


def draw_round_block(round_id: str, header: str, timing_text: str, reasoning: str, candidates: list[dict],
                      baseline_buffer: dict, spec_summary: dict, distance: float, finished: bool = True,
                      failures: list[dict] | None = None) -> None:
    """One round: a collapsible header, then one card per candidate the
    model proposed that round (rounds can batch several). The proposed
    changes + geometry are the main content, each with a "Reasoning:" line -
    the model's own 1-2 sentence summary, written in the same tool call as
    the proposal (propose_coilset's required `rationale`), so it costs no
    extra LLM round trip. The round's raw chain-of-thought stays available,
    never truncated, in a "Full reasoning" section - collapsed when a
    summary exists, open only when there's no summary to read instead
    (older runs saved before the field existed, or a text-only round)."""
    if not imgui.collapsing_header(f"{header}##{round_id}", flags=imgui.TreeNodeFlags_.default_open.value):
        return
    if timing_text:
        imgui.text_colored(_MUTED_COLOR, timing_text)
    # Failed AI tries for this round (agent/services/llm_client.py's LLMError) - the
    # reason is shown in full, never folded into "no proposal".
    for f in failures or []:
        imgui.push_text_wrap_pos(0.0)
        imgui.text_colored(_FAIL_COLOR, f"AI try {f.get('ai_try', '?')} failed after {f.get('elapsed_s', 0):.0f} s: "
                                        f"{_ascii(f.get('message', ''))}")
        imgui.pop_text_wrap_pos()
    if reasoning:
        has_rationale = any(c.get("rationale") for c in candidates)
        flags = 0 if has_rationale else imgui.TreeNodeFlags_.default_open.value
        # "Live reasoning" while the round is still proposing (this text is
        # still growing, via agent/services/llm_client.py's on_update) vs. "Full
        # reasoning" once it's the final, settled chain-of-thought.
        label = "Full reasoning" if finished else "Live reasoning"
        if imgui.tree_node_ex(f"{label}##{round_id}_reasoning", flags):
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_MUTED_COLOR, full_text(reasoning))
            imgui.pop_text_wrap_pos()
            imgui.tree_pop()
    if not candidates:
        imgui.text_disabled("(no proposal)" if finished else "(no proposal yet)")
    for i, candidate in enumerate(candidates):
        if len(candidates) > 1:
            imgui.text(f"Candidate {i + 1} of {len(candidates)}")
        draw_candidate_card(f"{round_id}_c{i}", candidate, baseline_buffer, spec_summary, distance)
        imgui.dummy(imgui.ImVec2(0, 6))
    imgui.separator()


def draw_candidate_card(card_id: str, candidate: dict, baseline_buffer: dict, spec_summary: dict, distance: float) -> None:
    """candidate: a plain dict shaped like agent.state.CandidateResult."""
    name = design_name(candidate)
    if name:
        imgui.text_colored(_MUTED_COLOR, name)
    rationale = candidate.get("rationale")
    if rationale:
        imgui.push_text_wrap_pos(0.0)
        imgui.text_colored(_WHY_COLOR, f"Reasoning: {' '.join(_ascii(rationale).split())}")
        imgui.pop_text_wrap_pos()

    avail_w = imgui.get_content_region_avail().x
    geo_w = min(280.0, max(avail_w * 0.35, 140.0))

    # Runs saved before candidate_coilset existed only stored the delta -
    # baseline + delta is exactly what dataclasses.replace() built, so the
    # geometry is reconstructable without it.
    geometry = candidate.get("candidate_coilset")
    if not geometry and baseline_buffer and candidate.get("delta"):
        geometry = {**baseline_buffer, **candidate["delta"]}

    imgui.begin_group()
    if geometry:
        coil_diagram.draw(geometry, distance, imgui.ImVec2(geo_w, _GEOMETRY_H), child_id=f"{card_id}_geo", compact=True)
    else:
        imgui.begin_child(f"{card_id}_geo", imgui.ImVec2(geo_w, _GEOMETRY_H), imgui.ChildFlags_.borders.value)
        imgui.text_disabled("(no geometry)")
        imgui.end_child()
    imgui.end_group()

    imgui.same_line()
    imgui.begin_group()
    imgui.text_colored(_MUTED_COLOR, "Proposed changes")
    # The card's own diagram is compact (no title row for coil_diagram.draw()
    # to hang a button on - see its femm_label docstring), so this candidate's
    # "Open in FEMM" button goes here instead, against the same `geometry`
    # dict the diagram to its left was drawn from.
    if geometry:
        femm_open.button(f"{card_id}_femm", geometry, distance,
                         f"{baseline_buffer.get('config_name') or 'candidate'} {card_id}")
    base_label = candidate.get("base_label")
    _draw_input_table(f"{card_id}_input", candidate.get("delta") or {}, baseline_buffer,
                      step_fields=set(candidate.get("step") or {}) if base_label else None)
    if base_label:
        imgui.text_colored(_MUTED_COLOR, f"* changed in this step; the rest comes from {base_label}")
    imgui.end_group()

    if not candidate.get("valid", False):
        imgui.text_colored(_FAIL_COLOR, f"Invalid: {candidate.get('error', 'unknown error')}")
        return

    tool_result = candidate.get("tool_result") or {}
    imgui.text_colored(_MUTED_COLOR, "Result vs. goal")
    draw_result_table(f"{card_id}_result", tool_result, spec_summary)


def _step_outcome(candidate: dict, spec_summary: dict) -> tuple[str, imgui.ImVec4]:
    if not candidate.get("valid", False):
        return f"Invalid: {_ascii(candidate.get('error') or 'unknown error')}", _FAIL_COLOR
    tool_result = candidate.get("tool_result") or {}
    if tool_result.get("goal_met"):
        check = tool_result.get("fragility")
        if check and check != "passed":
            return "Met the goal, failed the fragility check", _FAIL_COLOR
        return ("Goal met, survived the fragility check" if check else "Goal met"), _PASS_COLOR
    # Only requirements count as "missed" - an unmet preference doesn't block the goal.
    missed = [row["metric"] for row in _metric_rows(tool_result, spec_summary)
              if not row["passed"] and row.get("required", True)]
    return f"Missed: {', '.join(missed)}", _FAIL_COLOR


_SUMMARY_DIAGRAM_H = 240.0
_STATUS_HEADLINES = {
    "goal_met": "Goal met",
    "budget_exhausted": "Budget used up - goal not met",
    "stopped_by_model": "The model stopped the search",
    "failed": "Search failed",
}


def draw_outcome_report(report: str) -> None:
    """Why a finished search didn't meet its goal (agent/femm_agent/services/outcome.py) - the
    live panel, the chat and History all show the same text."""
    imgui.push_text_wrap_pos(0.0)
    for i, line in enumerate(_ascii(report).splitlines()):
        (imgui.text_colored(_FAIL_COLOR, line) if i == 0 else imgui.text_colored(_MUTED_COLOR, line))
    imgui.pop_text_wrap_pos()


def search_steps(rounds: list[tuple[int, list[dict]]], best_buffer: dict,
                 baseline_buffer: dict) -> list[tuple[str, dict, bool]]:
    """(label, candidate dict, is_best) for every candidate in search order -
    "2" for a round's only candidate, "2.3" for the 3rd of a batched round.
    rounds: (round_num, candidate dicts) pairs."""
    steps, best_marked = [], False
    for round_num, candidates in rounds:
        for i, c in enumerate(candidates):
            label = str(round_num) if len(candidates) == 1 else f"{round_num}.{i + 1}"
            # Same baseline + delta reconstruction as draw_candidate_card(),
            # for runs saved before candidate_coilset existed.
            geometry = c.get("candidate_coilset") or {**baseline_buffer, **(c.get("delta") or {})}
            is_best = not best_marked and geometry == best_buffer
            best_marked = best_marked or is_best
            steps.append((label, c, is_best))
    return steps


_HIGHLIGHT_COLOR = imgui.ImVec4(0.30, 0.50, 0.92, 0.16)  # translucent accent tint - the row a target/change lands on


def _cached_metrics(coilset, distance: float) -> dict | None:
    """A representative-distance solve for `coilset`, from the shared
    JobRunner cache - a lookup only, never a new solve (find_cached() is a
    staticmethod, so this needs no JobRunner instance). Both baseline and
    every candidate were already solved during the search itself, so this
    should always hit; None (rendered as "-") only for a coilset this
    function can't identify (e.g. an unreconstructable historical run)."""
    if coilset is None:
        return None
    from ..services.job_runner import JobRunner

    row = JobRunner.find_cached(coilset, distance)
    return row["result"] if row else None


def draw_full_output_table(table_id: str, best_tool_result: dict,
                           baseline_metrics: dict | None, best_metrics: dict | None) -> None:
    """All 12 solved quantities, baseline vs. best, side by side with the
    percent change - not just the ones being searched for, and all 12 are
    genuinely per-distance FEMM outputs (every SIM_RESULT_METRICS entry is
    checked at every distance - see agent/input_chat/models/objective.py's is_per_distance()),
    so baseline_metrics/best_metrics is expected to be a real solve AT
    WHATEVER DISTANCE THE CALLER PICKED - draw_search_summary()'s distance
    dropdown - not always the search's first/representative one.

    An objective row's pass/fail is recomputed HERE, fresh, from that same
    distance's raw baseline/best values - not read off tool_result()'s
    stored aggregate (objective_result_dict()'s value/baseline_value/passed
    are the WORST distance across the whole search, which would silently
    keep showing that distance's verdict even after the dropdown moves to
    a different one). Falls back to the stored aggregate only when a raw
    cache lookup misses (an older run, or a coilset that can't be
    rebuilt) - degraded but not blank."""
    from agent.input_chat.models.objective import Objective, SIM_RESULT_METRICS
    from agent.input_chat.models.objective import margin as objective_margin
    from agent.femm_agent.services.scorer import pct

    objective_by_metric = {o["metric"]: o for o in (best_tool_result.get("objectives") or [])}
    if not imgui.begin_table(table_id, 5, imgui.TableFlags_.borders.value | imgui.TableFlags_.row_bg.value):
        return
    imgui.table_setup_column("Metric")
    imgui.table_setup_column("Baseline")
    imgui.table_setup_column("Proposed")
    imgui.table_setup_column("Change")
    imgui.table_setup_column("Requirement")
    imgui.table_headers_row()
    for metric in SIM_RESULT_METRICS:
        o = objective_by_metric.get(metric)
        old = (baseline_metrics or {}).get(metric)
        new = (best_metrics or {}).get(metric)
        if old is not None and new is not None:
            change_pct = pct(new, old)
            change_text = f"{change_pct:+.1f}%"
            passed = None
            if o is not None:
                obj = Objective(metric=o["metric"], kind=o["kind"], tolerance_pct=o["tolerance_pct"],
                                required=o["required"], target_pct=o.get("target_pct", 0.0),
                                target_value=o.get("target_value"))
                passed = objective_margin(obj, change_pct, new) >= 0
        elif o is not None:
            # No cache hit at this distance - fall back to the stored
            # worst-case aggregate rather than showing nothing.
            old, new = o.get("baseline_value"), o.get("value")
            change_text = f"{o['worst_change_pct']:+.1f}%" if old is not None and new is not None else "-"
            passed = o["passed"]
        else:
            change_text, passed = "-", None

        imgui.table_next_row()
        if o is not None:
            imgui.table_set_bg_color(imgui.TableBgTarget_.row_bg0.value, imgui.get_color_u32(_HIGHLIGHT_COLOR))
        imgui.table_next_column()
        (imgui.text_colored(_WHY_COLOR, metric) if o is not None else imgui.text(metric))
        imgui.table_next_column()
        imgui.text(_fmt(old) if old is not None else "-")
        imgui.table_next_column()
        imgui.text(_fmt(new) if new is not None else "-")
        imgui.table_next_column()
        imgui.text(change_text)
        imgui.table_next_column()
        if o is not None:
            color = _PASS_COLOR if passed else _FAIL_COLOR
            tag = "Required" if o["required"] else "Preferred"
            imgui.text_colored(color, f"{tag}: {o['requirement']} - {'Pass' if passed else 'MISS'}")
        else:
            imgui.text_disabled("-")
    imgui.end_table()

    goal_met = best_tool_result.get("goal_met", False)
    imgui.text_colored(_PASS_COLOR if goal_met else _FAIL_COLOR, f"Goal met: {'YES' if goal_met else 'no'}")
    if goal_met:
        # Meeting the goal is not yet winning: the run's status is 'goal met' only
        # for a design that also survived the fragility check.
        check = fragility_text(best_tool_result) or "Fragility check: not done - this design is not a winner yet"
        imgui.text_colored(_PASS_COLOR if best_tool_result.get("fragility") == "passed" else _FAIL_COLOR, check)


def draw_full_input_table(pane_id: str, baseline_buffer: dict, best_buffer: dict,
                          changed_fields: set, unexpected_fields: set) -> None:
    """Every CoilSet field, grouped the same way the manual-run config form
    groups them, baseline vs. best - a row is highlighted (and its section
    auto-opened) only where it actually changed, so a real search - which
    usually only touches a handful of fields - doesn't bury them in a wall
    of identical baseline/best pairs."""
    from ..services import field_layout

    labels = field_layout.field_labels()
    groups = [(g, names) for g, names in field_layout.FIELD_GROUPS.items() if g != "General"]
    groups.append(("Advanced", list(field_layout.ADVANCED_FIELDS)))
    for group_name, names in groups:
        flags = imgui.TreeNodeFlags_.default_open.value if any(n in changed_fields for n in names) else 0
        if not imgui.collapsing_header(f"{group_name}##{pane_id}_{group_name}", flags=flags):
            continue
        if not imgui.begin_table(f"{pane_id}_{group_name}", 3,
                                 imgui.TableFlags_.borders.value | imgui.TableFlags_.row_bg.value):
            continue
        imgui.table_setup_column("Parameter", imgui.TableColumnFlags_.width_stretch.value)
        imgui.table_setup_column("Baseline", imgui.TableColumnFlags_.width_fixed.value, 90.0)
        imgui.table_setup_column("Proposed", imgui.TableColumnFlags_.width_fixed.value, 90.0)
        imgui.table_headers_row()
        for name in names:
            changed = name in changed_fields
            old_v, new_v = baseline_buffer.get(name), best_buffer.get(name)
            if name in field_layout.PRESENCE_FIELDS:
                old_v, new_v = ("yes" if old_v else "no"), ("yes" if new_v else "no")

            imgui.table_next_row()
            if changed:
                imgui.table_set_bg_color(imgui.TableBgTarget_.row_bg0.value, imgui.get_color_u32(_HIGHLIGHT_COLOR))
            imgui.table_next_column()
            if name in unexpected_fields:
                imgui.text_colored(_FAIL_COLOR, f"{labels.get(name, name)} (not allowed)")
            elif changed:
                imgui.text_colored(_WHY_COLOR, labels.get(name, name))
            else:
                imgui.text(labels.get(name, name))
            imgui.table_next_column()
            imgui.text(_fmt(old_v))
            imgui.table_next_column()
            imgui.text(_fmt(new_v))
        imgui.end_table()


def _trace_thickness_visible(field: str, baseline_buffer: dict, best_buffer: dict) -> bool:
    """loop_trace_thickness/tank_trace_thickness only shift the TC-side
    stack outward (tank_ferrite/tc_shield/backplane, if present);
    rc_trace_thickness only shifts the RC-side stack (rc_ferrite/rc_shield/
    rc_backplane). coil_diagram.py's axial math technically reads these
    three fields (they're in DIAGRAM_FIELDS), but with none of the relevant
    side's optional layers present, changing one moves nothing the diagram
    actually draws - the coil bands themselves are fixed-size markers, not
    a to-scale render of copper thickness (see that module's docstring).
    Checked against BOTH buffers, not just baseline, since a search that
    turns a feature on mid-run is exactly the case this distinction matters
    for."""
    if field == "loop_trace_thickness" or field == "tank_trace_thickness":
        flags = ("tank_ferrite_present", "tc_shield", "backplane")
    elif field == "rc_trace_thickness":
        flags = ("rc_ferrite_present", "rc_shield", "rc_backplane")
    else:
        return True
    return any(baseline_buffer.get(f) or best_buffer.get(f) for f in flags)


def _draw_diagram_change_caption(changed_fields: set, baseline_buffer: dict, best_buffer: dict) -> None:
    """Right under the two diagrams: which of the proposal's changed
    fields the diagram actually draws, and which it can't - "the two
    pictures look the same" is a real, recurring complaint when a search
    only touches trace width/thickness/spacing or turn count, none of
    which coil_diagram.py models at any scale (see its own module
    docstring) - this spells out the real numbers either way, instead of
    leaving "why didn't the picture change" to be guessed at."""
    if not changed_fields:
        return

    shown = {f for f in changed_fields
             if f in coil_diagram.DIAGRAM_FIELDS and _trace_thickness_visible(f, baseline_buffer, best_buffer)}
    hidden = sorted(changed_fields - shown)
    shown = sorted(shown)

    def _pairs(fields):
        return ", ".join(f"{f}: {_fmt(baseline_buffer.get(f))} -> {_fmt(best_buffer.get(f))}" for f in fields)

    imgui.push_text_wrap_pos(0.0)
    if shown:
        imgui.text_colored(_WHY_COLOR, f"Reflected in the diagram: {_pairs(shown)}")
    if hidden:
        imgui.text_colored(_MUTED_COLOR, f"Not drawn above (trace/turn-level detail - see Inputs below): {_pairs(hidden)}")
    imgui.pop_text_wrap_pos()


def draw_search_summary(pane_id: str, status: str, rounds: list[tuple[int, list[dict]]], timing_text: str,
                        baseline_buffer: dict, best_buffer: dict, best_tool_result: dict, spec_summary: dict,
                        distances: list[float], selected_distance: float, audit: dict | None,
                        baseline_coilset=None, best_coilset=None, report: str | None = None) -> float:
    """A finished search in one view - shared by the live panel (the moment
    a search ends) and the history replay, from plain dicts: headline, best
    vs. baseline geometry, the full result table (baseline vs. best, every
    metric, AT ONE CHOSEN DISTANCE), the full input table (baseline vs.
    best, every field, highlighted where changed - distance-independent,
    so unaffected by the choice), and every step the search took with the
    model's reasoning.

    Every one of the 12 solved quantities is a genuinely per-distance FEMM
    result (see agent/input_chat/models/objective.py's is_per_distance()) - a search run at
    several distances has a real, different answer at each one, not just
    one "representative" value. distances/selected_distance/the returned
    float are this function's only state: it draws a dropdown among
    `distances`, defaulting to `selected_distance` (falling back to
    distances[0] if that's not actually in the list - e.g. the very first
    draw, selected_distance=None), and returns whichever is selected after
    this frame's dropdown interaction - stateless itself, so the CALLER
    persists the return value back into its own per-run/per-search state
    for next frame (agent_panel.py's AgentPanelState.summary_distance /
    agent_history.py's AgentHistoryState.summary_distance).

    audit: variables.audit_changed_fields()'s result, or None when it
    couldn't be rebuilt (a plain field diff is shown, without the check).
    baseline_coilset/best_coilset: the real CoilSet objects, if available -
    used to look up each one's full 12-metric solve, AT THE SELECTED
    DISTANCE, from the shared cache for draw_full_output_table()."""
    distance = selected_distance if selected_distance in distances else distances[0]
    steps = search_steps(rounds, best_buffer, baseline_buffer)
    best_label = next((label for label, _, is_best in steps if is_best), "?")
    if audit is None:
        changed_fields = {k for k, v in baseline_buffer.items() if v != best_buffer.get(k)}
        unexpected_fields = set()
    else:
        changed_fields, unexpected_fields = set(audit["changed_fields"]), set(audit["unexpected_fields"])

    imgui.text("Search Summary")
    imgui.separator()
    goal_met = best_tool_result.get("goal_met", False)
    headline = _STATUS_HEADLINES.get(status, status)
    imgui.text_colored(
        _PASS_COLOR if goal_met else _FAIL_COLOR,
        f"{headline} - {len(rounds)} rounds, {len(steps)} candidates tried, the proposed design was step {best_label}.",
    )
    if timing_text:
        imgui.text_disabled(timing_text)
    if report:
        draw_outcome_report(report)
    if len(distances) > 1:
        imgui.align_text_to_frame_padding()
        imgui.text("Viewing distance:")
        imgui.same_line()
        labels = [f"{d:g} mm" for d in distances]
        imgui.set_next_item_width(110)
        changed_distance, new_idx = imgui.combo(f"##{pane_id}_distance", distances.index(distance), labels)
        if changed_distance:
            distance = distances[new_idx]
    imgui.spacing()

    half_w = (imgui.get_content_region_avail().x - imgui.get_style().item_spacing.x) / 2.0
    baseline_name = baseline_buffer.get("config_name") or "baseline"
    coil_diagram.draw(baseline_buffer, distance, imgui.ImVec2(half_w, _SUMMARY_DIAGRAM_H),
                      child_id=f"{pane_id}_baseline_diagram", title="Baseline",
                      femm_label=f"{baseline_name} baseline")
    imgui.same_line()
    coil_diagram.draw(best_buffer, distance, imgui.ImVec2(half_w, _SUMMARY_DIAGRAM_H),
                      child_id=f"{pane_id}_best_diagram", title=f"Proposed (step {best_label})",
                      femm_label=f"{baseline_name} proposed step {best_label}")
    _draw_diagram_change_caption(changed_fields, baseline_buffer, best_buffer)

    imgui.spacing()
    imgui.text(f"Result: baseline vs. proposed at {distance:g} mm")
    draw_full_output_table(f"{pane_id}_result", best_tool_result,
                           _cached_metrics(baseline_coilset, distance), _cached_metrics(best_coilset, distance))

    imgui.spacing()
    imgui.text("Inputs: baseline vs. proposed")
    draw_full_input_table(f"{pane_id}_inputs", baseline_buffer, best_buffer, changed_fields, unexpected_fields)
    if audit is not None:
        if unexpected_fields:
            imgui.text_colored(_FAIL_COLOR, f"Structural check: FAILED - changed outside the allowed set: {sorted(unexpected_fields)}")
        else:
            imgui.text_colored(_PASS_COLOR, "Structural check: no out-of-scope fields changed.")

    imgui.spacing()
    imgui.text("How the search got there")
    draw_process_table(f"{pane_id}_steps", steps, baseline_buffer, spec_summary)

    return distance


def draw_process_table(table_id: str, steps: list[tuple[str, dict, bool]], baseline_buffer: dict,
                       spec_summary: dict) -> None:
    """The whole search in one table, one row per candidate: what it
    changed, how it did against the goal, and the model's 1-2 sentence
    reasoning. steps: (label, candidate dict, is_best) in search order."""
    flags = (imgui.TableFlags_.borders.value | imgui.TableFlags_.row_bg.value
             | imgui.TableFlags_.sizing_stretch_prop.value)
    if not imgui.begin_table(table_id, 4, flags):
        return
    imgui.table_setup_column("Step", imgui.TableColumnFlags_.width_fixed.value, 90.0)
    imgui.table_setup_column("Changes", 0, 1.3)
    imgui.table_setup_column("Result", 0, 0.8)
    imgui.table_setup_column("Reasoning", 0, 2.0)
    imgui.table_headers_row()
    for label, candidate, is_best in steps:
        imgui.table_next_row()
        imgui.table_next_column()
        if candidate.get("label"):
            label = f"{label}  {candidate['label']}"
        if is_best:
            imgui.text_colored(_PASS_COLOR, f"{label}\n(proposed)")  # own line: the column is too narrow for both
        else:
            imgui.text(label)

        imgui.table_next_column()
        delta = candidate.get("delta") or {}
        changes = "\n".join(f"{k}: {_fmt(baseline_buffer.get(k, '?'))} -> {_fmt(v)}" for k, v in delta.items())
        imgui.text_wrapped(changes or "(no fields changed)")

        imgui.table_next_column()
        outcome, color = _step_outcome(candidate, spec_summary)
        imgui.push_style_color(imgui.Col_.text.value, color)
        imgui.text_wrapped(outcome)
        imgui.pop_style_color()

        imgui.table_next_column()
        imgui.text_wrapped(" ".join(_ascii(candidate.get("rationale") or "").split()) or "-")
    imgui.end_table()
