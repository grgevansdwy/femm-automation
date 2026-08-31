"""Read-only access to the CoilSet templates baked into noelle's coilset library.

Depends on noelle's `models.coilset` / `repositories.coilset_repository`
(plan section 2/4's target layout), which may not exist yet in this
checkout - imports are deferred into each function so the rest of the webapp
still boots; calling one of these before that lands raises a plain
ModuleNotFoundError.
"""

from typing import Any

from . import _noelle_path  # noqa: F401 - ensures noelle/ is on sys.path


def list_templates() -> dict[str, Any]:
    from models.coilset import CoilSet
    from repositories import coilset_repository as cc

    return {name: obj for name, obj in vars(cc).items() if isinstance(obj, CoilSet)}


def get_template(name: str) -> Any:
    templates = list_templates()
    if name not in templates:
        raise KeyError(f"No such template: {name!r}")
    return templates[name]


def clone_template(name: str) -> Any:
    from repositories import coilset_repository as cc

    return cc.copy_coilset(get_template(name))
