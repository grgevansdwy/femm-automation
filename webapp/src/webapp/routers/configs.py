import json
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .. import coilset_forms
from ..coilset_forms import ADVANCED_FIELDS, FIELD_GROUPS, build_coilset
from ..templating import templates

router = APIRouter()


async def _parse_form(request: Request) -> tuple[dict, dict, str | None]:
    """Returns (raw_grouped_field_values, advanced_json_dict, error|None)."""
    form = await request.form()
    raw_values = {name: form.get(name) for group in FIELD_GROUPS.values() for name in group}
    try:
        advanced_json = json.loads(form.get("advanced_json") or "{}")
    except json.JSONDecodeError as exc:
        return raw_values, dict(coilset_forms.DEFAULT_ADVANCED_VALUES), f"Advanced JSON is not valid JSON: {exc}"
    return raw_values, advanced_json, None


def _validate_and_build(raw_values: dict, advanced_json: dict):
    """Returns (coilset, None) on success or (None, error_message) on failure."""
    try:
        coilset = build_coilset(raw_values, advanced_json)
    except (TypeError, ValueError, ModuleNotFoundError) as exc:
        return None, str(exc)

    try:
        from services.femm_service import check_valid_coilset
    except ModuleNotFoundError as exc:
        return None, f"coil_simulation validation service unavailable: {exc}"

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
        "advanced_json": dict(coilset_forms.DEFAULT_ADVANCED_VALUES),
        "error": None,
        "config_id": None,
        "cloned_from": None,
        "builtin": False,
    }
    context.update(overrides)
    return context


def _configs_list_context(**overrides) -> dict:
    from repositories import coilset_repository as cc

    configs = [
        {**cfg, "updated_at": datetime.fromisoformat(cfg["updated_at"])}
        for cfg in cc.list_configs()
    ]
    context = {"configs": configs, "error": None}
    context.update(overrides)
    return context


@router.get("/configs")
def list_configs(request: Request):
    return templates.TemplateResponse(request, "configs_list.html", _configs_list_context())


@router.post("/configs/clone/{config_id}")
def clone_config(config_id: str):
    from repositories import coilset_repository as cc

    coilset = cc.get_coilset(config_id)
    record = cc.save_config(
        coilset,
        name=f"{coilset.config_name.strip()} (copy)",
        cloned_from=config_id,
    )
    return RedirectResponse(f"/configs/{record['id']}/edit", status_code=303)


@router.get("/configs/new")
def new_config_form(request: Request):
    return templates.TemplateResponse(request, "config_form.html", _form_context())


@router.post("/configs/new")
async def create_config(request: Request):
    from repositories import coilset_repository as cc

    raw_values, advanced_json, parse_error = await _parse_form(request)
    if parse_error:
        return templates.TemplateResponse(
            request, "config_form.html",
            _form_context(coilset=raw_values, advanced_json=advanced_json, error=parse_error),
            status_code=422,
        )
    coilset, error = _validate_and_build(raw_values, advanced_json)
    if error:
        return templates.TemplateResponse(
            request,
            "config_form.html",
            _form_context(coilset=raw_values, advanced_json=advanced_json, error=error),
            status_code=422,
        )
    record = cc.save_config(coilset, name=coilset.config_name.strip())
    return RedirectResponse(f"/configs/{record['id']}/edit", status_code=303)


@router.get("/configs/{config_id}/edit")
def edit_config_form(request: Request, config_id: str):
    from repositories import coilset_repository as cc

    record = cc.get_config(config_id)
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
            cloned_from=record.get("cloned_from"),
            builtin=record.get("builtin", False),
        ),
    )


@router.post("/configs/{config_id}/edit")
async def update_config(request: Request, config_id: str):
    from repositories import coilset_repository as cc

    raw_values, advanced_json, parse_error = await _parse_form(request)
    if parse_error:
        return templates.TemplateResponse(
            request, "config_form.html",
            _form_context(
                mode="edit", action_url=f"/configs/{config_id}/edit",
                coilset=raw_values, advanced_json=advanced_json, error=parse_error,
                config_id=config_id,
            ),
            status_code=422,
        )
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
            ),
            status_code=422,
        )
    cc.save_config(coilset, name=coilset.config_name.strip(), config_id=config_id)
    return RedirectResponse(f"/configs/{config_id}/edit", status_code=303)


@router.post("/configs/{config_id}/delete")
def delete_config(request: Request, config_id: str):
    from repositories import coilset_repository as cc

    try:
        cc.delete_config(config_id)
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "configs_list.html", _configs_list_context(error=str(exc)), status_code=400,
        )
    return RedirectResponse("/configs", status_code=303)
