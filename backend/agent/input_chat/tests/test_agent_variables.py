# =============================================================================
# test_agent_variables.py
#
# Checks agent/input_chat/models/variables.py: the Field Config table (which
# CoilSet fields the LLM may propose changing, and each one's limits), and
# what an interview's constraints take away from it. Needs no FEMM, no LLM.
#
# Builds its own baseline CoilSets via dataclasses.replace() off the
# 'base_coil' fixture (backend/conftest.py's autouse isolate_db seeds it).
# =============================================================================
import dataclasses

import pytest

from agent.input_chat.models import variables
from agent.input_chat.models.variables import (
    DEFAULT_SEARCH_FIELDS, FIELD_GROUPS, FREE_VARIABLES, FieldRule, VariableSpec, allowed_fields,
    apply_field_changes, check_field_changes, coerce_fixed_values, free_fields_for, legacy_allowed_fields,
    limit_problems, search_field_overrides, search_field_table,
)
from agent.settings.settings import AgentSettings
from engine.models.coilset import CoilSet
from engine.repositories.coilset_repository import get_coilset


def _baseline(**overrides):
    return dataclasses.replace(get_coilset('base_coil'), **overrides)


def _table(**rows):
    return search_field_table(AgentSettings(search_fields=rows))


def test_shipped_defaults_open_the_winding_geometry_and_nothing_else():
    enabled = {name for name, rule in DEFAULT_SEARCH_FIELDS.items() if rule.enabled}
    assert enabled == set(FREE_VARIABLES)
    assert "rc_ferrite_y_offset" not in enabled, "it sits in the Ferrite group, which ships disabled"
    closed = {f for info in FIELD_GROUPS.values() for f in [info["flag"], *info["fields"]] if f}
    assert not closed & enabled, "ferrite, shields, backplanes, PCB thickness and loss angles ship disabled"
    numeric = {f.name for f in dataclasses.fields(CoilSet) if f.type in (int, float)}
    assert set(DEFAULT_SEARCH_FIELDS) == numeric, "one row per numeric CoilSet field"


def test_what_the_baseline_has_fitted_opens_nothing():
    baseline = _baseline(tank_ferrite_present=1, rc_shield=1, backplane=1)
    assert set(free_fields_for(baseline)) == set(FREE_VARIABLES)


def test_layers_ship_with_a_maximum_of_8_which_the_user_can_clear():
    baseline = _baseline()
    for name in ("num_loop_layers", "num_tank_layers", "num_rc_layers"):
        assert free_fields_for(baseline)[name]["maximum"] == 8
    cleared = _table(num_tank_layers={"enabled": True, "min": None, "max": None})
    assert "maximum" not in free_fields_for(baseline, None, cleared)["num_tank_layers"]
    assert free_fields_for(baseline, None, cleared)["num_loop_layers"]["maximum"] == 8, "only the cleared row changes"


def test_a_disabled_field_is_not_offered_and_an_enabled_one_carries_its_limits():
    baseline = _baseline()
    table = _table(tank_trace_width={"enabled": False, "min": None, "max": None},
                   tank_mu={"enabled": True, "min": 100, "max": 2000},
                   tank_ferrite_present={"enabled": True, "min": None, "max": None})
    free = free_fields_for(baseline, None, table)
    assert "tank_trace_width" not in free
    assert free["tank_mu"]["minimum"] == 100 and free["tank_mu"]["maximum"] == 2000
    assert free["tank_ferrite_present"]["enum"] == [0, 1]


def test_code_floors_hold_whatever_the_table_says():
    baseline = _baseline()
    table = _table(num_tank_turns={"enabled": True, "min": -5, "max": None},
                   tank_center_r={"enabled": True, "min": -1, "max": None})
    free = free_fields_for(baseline, None, table)
    assert free["num_tank_turns"]["minimum"] == 1, "a count is at least 1"
    assert free["tank_center_r"]["minimum"] == 0, "a dimension is at least 0"


def test_thickness_limits_filter_the_allowed_copper_weights():
    baseline = _baseline()
    free = free_fields_for(baseline, None, _table(rc_trace_thickness={"enabled": True, "min": 0.05, "max": None}))
    assert free["rc_trace_thickness"]["enum"] == [0.07, 0.14]
    none_left = _table(rc_trace_thickness={"enabled": True, "min": 1, "max": None})
    assert "rc_trace_thickness" not in free_fields_for(baseline, None, none_left)


def test_excluded_and_pinned_fields_drop_out_of_the_free_set():
    baseline = _baseline()
    spec = VariableSpec(excluded_fields={"loop_r"}, fixed_values={"num_tank_turns": 3})
    free = free_fields_for(baseline, spec)
    assert "loop_r" not in free and "num_tank_turns" not in free
    assert "num_tank_turns" in allowed_fields(baseline, spec) and "loop_r" not in allowed_fields(baseline, spec)


def test_a_saved_row_for_an_unknown_field_or_a_broken_row_is_ignored():
    table = _table(no_such_field={"enabled": True}, loop_r="yes", tank_center_r={"enabled": True, "min": 9, "max": 3})
    assert table == DEFAULT_SEARCH_FIELDS
    assert "no_such_field" not in table


def test_only_rows_that_differ_from_the_defaults_are_stored():
    table = apply_field_changes(dict(DEFAULT_SEARCH_FIELDS), [{"field": "tank_mu", "enabled": True, "max": 2000}])
    assert search_field_overrides(table) == {"tank_mu": {"enabled": True, "min": None, "max": 2000}}
    back = apply_field_changes(table, [{"field": "tank_mu", "enabled": False, "max": None}])
    assert search_field_overrides(back) == {}


def test_a_change_keeps_what_it_does_not_mention_and_null_clears_a_limit():
    table = apply_field_changes(dict(DEFAULT_SEARCH_FIELDS), [{"field": "num_rc_layers", "min": 2}])
    assert table["num_rc_layers"] == FieldRule(enabled=True, min=2, max=8)
    assert apply_field_changes(table, [{"field": "num_rc_layers", "max": None}])["num_rc_layers"].max is None


@pytest.mark.parametrize("changes", [
    None, [], "tank_mu", [{"field": "not_a_field", "enabled": True}], [{"field": "config_name", "enabled": True}],
    [{"field": "tank_mu"}], [{"field": "tank_mu", "enabled": "yes"}], [{"field": "tank_mu", "min": "low"}],
    [{"field": "tank_mu", "min": 5, "max": 1}], [{"field": "tc_shield", "max": 3}], ["tank_mu"],
])
def test_a_malformed_change_is_refused(changes):
    with pytest.raises(ValueError):
        check_field_changes(changes)


def test_a_change_whose_minimum_ends_up_above_the_existing_maximum_is_refused():
    with pytest.raises(ValueError, match="above maximum"):
        apply_field_changes(dict(DEFAULT_SEARCH_FIELDS), [{"field": "num_rc_layers", "min": 9}])


def test_a_pin_must_be_inside_the_limits_but_need_not_be_enabled():
    assert coerce_fixed_values({"tank_mu": "900"}) == {"tank_mu": 900.0}, "a closed field can still be pinned"
    for bad in ({"num_tank_layers": 12}, {"num_tank_turns": 0}, {"loop_r": -1}, {"rc_trace_thickness": 0.1},
                {"tc_shield": 2}, {"num_tank_turns": 2.5}):
        with pytest.raises(ValueError):
            coerce_fixed_values(bad)
    with pytest.raises(ValueError, match="aren't numeric"):
        coerce_fixed_values({"config_name": 3})


def test_a_baseline_outside_a_table_limit_is_named_but_code_floors_do_not_apply_to_it():
    assert limit_problems(_baseline()) == []
    (problem,) = limit_problems(_baseline(num_tank_layers=12))
    assert "num_tank_layers" in problem and "1 to 8" in problem
    assert limit_problems(_baseline(num_tank_layers=12), VariableSpec(excluded_fields={"num_tank_layers"})) == [], \
        "an excluded field is never proposed, so its limit does not block the search"
    no_limits = {name: FieldRule(enabled=rule.enabled) for name, rule in DEFAULT_SEARCH_FIELDS.items()}
    assert limit_problems(_baseline(num_tank_turns=0), table=no_limits) == [], \
        "a tank-less baseline is below the count floor and still opens when the table sets no limit"
    (problem,) = limit_problems(_baseline(num_tank_turns=0))
    assert "num_tank_turns" in problem and "3 to 12" in problem, "the shipped limits name the field and the range"


def test_runs_stored_before_the_table_are_audited_by_the_old_rule():
    old = VariableSpec(enabled_groups={"tank_ferrite"}, excluded_fields={"loop_r"}, fixed_values={"rc_mu": 5})
    allowed = legacy_allowed_fields(old)
    assert {"tank_mu", "tank_ferrite_present", "tc_loss_angle", "rc_ferrite_y_offset", "rc_mu"} <= allowed
    assert "loop_r" not in allowed and "rc_shield" not in allowed
    assert legacy_allowed_fields(VariableSpec(enabled_groups={"gone_group"})) >= set(FREE_VARIABLES)


def test_schema_module_is_the_only_source_of_field_kinds():
    assert variables.FLAG_FIELDS == {"tank_ferrite_present", "rc_ferrite_present", "backplane", "rc_backplane",
                                     "tc_shield", "rc_shield"}
