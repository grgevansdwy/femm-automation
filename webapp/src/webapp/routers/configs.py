import json
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .. import config_store, template_registry
from ..coilset_forms import (
    ADVANCED_FIELDS,
    FIELD_GROUPS,
    build_coilset,
    coilset_to_form_values,
)
from ..templating import templates

router = APIRouter()


async def _parse_form(request: Request) -> tuple[dict, dict, str]:
    """Returns (raw_grouped_field_values, advanced_json_dict, config_name)."""
    form = await request.form()
    raw_values = {name: form.get(name) for group in FIELD_GROUPS.values() for name in group}
    advanced_json = json.loads(form.get("advanced_json") or "{}")
    return raw_values, advanced_json, (form.get("name") or "").strip()


def _validate_and_build(raw_values: dict, advanced_json: dict):
    """Returns (coilset, None) on success or (None, error_message) on failure.

    Mirrors plan section 7: cast+construct the CoilSet, then re-run the same
    geometry validity check compare_coilsets.py already uses before a solve.
    """
    try:
        coilset = build_coilset(raw_values, advanced_json)
    except (TypeError, ValueError, ModuleNotFoundError) as exc:
        return None, str(exc)

    try:
        from services.femm_service import check_valid_coilset
    except ModuleNotFoundError as exc:
        return None, f"noelle validation service unavailable: {exc}"

    reason, is_valid = check_valid_coilset(
        coilset.loop_r,
        coilset.num_loop_turns,
        coilset.num_parallel_loop_traces,
        coilset.loop_trace_spacing,
        coilset.loop_turn_spacing,
        coilset.loop_trace_width,
        coilset.tank_center_r,
        coilset.num_tank_turns,
        coilset.num_parallel_tank_traces,
        coilset.tank_trace_spacing,
        coilset.tank_turn_spacing,
        coilset.tank_trace_width,
        coilset.rc_radius,
        coilset.num_rc_turns,
        coilset.num_parallel_rc_traces,
        coilset.rc_trace_spacing,
        coilset.rc_turn_spacing,
        coilset.rc_trace_width,
    )
    if not is_valid:
        return None, reason
    return coilset, None


def _form_context(**overrides) -> dict:
    context = {
        "mode": "new",
        "action_url": "/configs/new",
        "field_groups": FIELD_GROUPS,
        "coilset": {},
        "advanced_json": dict.fromkeys(ADVANCED_FIELDS, None),
        "error": None,
        "config_id": None,
        "cloned_from": None,
        "name": "",
    }
    context.update(overrides)
    return context


def _configs_list_context(**overrides) -> dict:
    saved_configs = [
        {**cfg, "updated_at": datetime.fromisoformat(cfg["updated_at"])}
        for cfg in config_store.list_configs()
    ]
    context = {
        "templates": template_registry.list_templates(),
        "saved_configs": saved_configs,
        "error": None,
    }
    context.update(overrides)
    return context


@router.get("/configs")
def list_configs(request: Request):
    return templates.TemplateResponse(request, "configs_list.html", _configs_list_context())


@router.get("/templates/{name}")
def view_template(request: Request, name: str):
    coilset = template_registry.get_template(name)
    return templates.TemplateResponse(
        request,
        "template_view.html",
        {
            "template_name": name,
            "field_groups": FIELD_GROUPS,
            "coilset": coilset_to_form_values(coilset),
        },
    )


@router.post("/configs/clone/{name}")
def clone_template(name: str):
    coilset = template_registry.clone_template(name)
    record = config_store.save_config(
        coilset,
        name=f"{coilset.config_name.strip()} (copy)",
        cloned_from=f"template:{name}",
    )
    return RedirectResponse(f"/configs/{record['id']}/edit", status_code=303)


@router.get("/configs/new")
def new_config_form(request: Request):
    from .. import coilset_forms

    return templates.TemplateResponse(
        request,
        "config_form.html",
        _form_context(advanced_json=dict(coilset_forms.DEFAULT_ADVANCED_VALUES)),
    )


@router.post("/configs/new")
async def create_config(request: Request):
    raw_values, advanced_json, name = await _parse_form(request)
    coilset, error = _validate_and_build(raw_values, advanced_json)
    if error:
        return templates.TemplateResponse(
            request,
            "config_form.html",
            _form_context(coilset=raw_values, advanced_json=advanced_json, error=error, name=name),
            status_code=422,
        )
    record = config_store.save_config(coilset, name=name or coilset.config_name.strip())
    return RedirectResponse(f"/configs/{record['id']}/edit", status_code=303)


@router.get("/configs/{config_id}/edit")
def edit_config_form(request: Request, config_id: str):
    record = config_store.load_config(config_id)
    if record is None:
        return templates.TemplateResponse(
            request,
            "configs_list.html",
            _configs_list_context(error=f"No such config: {config_id}"),
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "config_form.html",
        _form_context(
            mode="edit",
            action_url=f"/configs/{config_id}/edit",
            coilset=record["coilset"],
            advanced_json={k: record["coilset"][k] for k in ADVANCED_FIELDS},
            config_id=config_id,
            name=record["name"],
            cloned_from=record.get("cloned_from"),
        ),
    )


@router.post("/configs/{config_id}/edit")
async def update_config(request: Request, config_id: str):
    raw_values, advanced_json, name = await _parse_form(request)
    coilset, error = _validate_and_build(raw_values, advanced_json)
    if error:
        return templates.TemplateResponse(
            request,
            "config_form.html",
            _form_context(
                mode="edit",
                action_url=f"/configs/{config_id}/edit",
                coilset=raw_values,
                advanced_json=advanced_json,
                error=error,
                config_id=config_id,
                name=name,
            ),
            status_code=422,
        )
    config_store.save_config(coilset, name=name or coilset.config_name.strip(), config_id=config_id)
    return RedirectResponse(f"/configs/{config_id}/edit", status_code=303)


@router.post("/configs/{config_id}/delete")
def delete_config(config_id: str):
    config_store.delete_config(config_id)
    return RedirectResponse("/configs", status_code=303)
