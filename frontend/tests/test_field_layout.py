import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

from frontend.services import field_layout


def test_field_groups_cover_exactly_coilset_fields():
    # Raises RuntimeError itself if out of sync; also assert here so pytest
    # reports a clear failure instead of an error during collection-adjacent code.
    types = field_layout.field_types()
    assert set(types) == set(field_layout._ALL_FIELDS)


def test_every_field_has_a_label():
    labels = field_layout.field_labels()
    assert set(labels) == set(field_layout._ALL_FIELDS)


def test_presence_fields_are_declared_int_on_coilset():
    types = field_layout.field_types()
    for name in field_layout.PRESENCE_FIELDS:
        assert types[name] is int, f"{name} expected int, got {types[name]}"
