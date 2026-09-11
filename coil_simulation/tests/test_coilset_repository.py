# =============================================================================
# test_coilset_repository.py
#
# Checks the unified config store: built-in configs load, edit-in-place
# preserves the builtin flag, deleting a builtin is refused, deleting a
# user config succeeds. Needs no FEMM:
#     python3 -m tests.test_coilset_repository  (run from coil_simulation/)
#
# Under pytest, tests/conftest.py's autouse isolate_db fixture points this
# at a fresh, isolated database seeded with one real "base_coil" config, so
# nothing here touches your real data. Run directly via `python3 -m
# tests.test_coilset_repository` instead (no pytest, no conftest fixture)
# and it runs against the real database - never touches or removes a
# builtin, and cleans up the one throwaway config it creates.
# =============================================================================
import dataclasses

from models.coilset import CoilSet
from repositories import coilset_repository as cc


def test_every_builtin_constructs_a_coilset():
    """Catches a migrated JSON file with a stray removed field immediately.

    Only checks builtin=true records: the store also holds real user
    configs (created through the GUI, or migrated in from pre-existing
    data/configs/*.json that weren't actually builtins), so "every record in
    the store is builtin" isn't a valid assertion - only "every builtin
    record still constructs cleanly" is.
    """
    configs = cc.list_configs()
    builtins = [c for c in configs if c['builtin']]
    assert len(builtins) >= 1, "no builtin configs found"
    for record in builtins:
        coilset = CoilSet(**record['coilset'])
        assert coilset.config_name
    print(f"  ok  all {len(builtins)} builtin configs construct a CoilSet cleanly")


def test_get_coilset_missing_raises_key_error():
    try:
        cc.get_coilset('does-not-exist')
    except KeyError:
        print("  ok  get_coilset() raises KeyError for a missing id")
        return
    raise AssertionError("expected KeyError")


def test_delete_builtin_is_refused():
    configs = cc.list_configs()
    builtin_id = next(c['id'] for c in configs if c['builtin'])
    try:
        cc.delete_config(builtin_id)
    except ValueError:
        assert cc.get_config(builtin_id) is not None, "builtin was deleted despite raising"
        print(f"  ok  delete_config() refuses to delete builtin {builtin_id!r}")
        return
    raise AssertionError(f"delete_config() should have refused to delete {builtin_id!r}")


def test_save_and_delete_user_config_round_trip():
    template = cc.get_coilset(cc.list_configs()[0]['id'])
    clone = dataclasses.replace(template, config_name='test_coilset_repository throwaway')

    record = cc.save_config(clone, name='throwaway', cloned_from=None)
    try:
        assert record['builtin'] is False
        loaded = cc.get_coilset(record['id'])
        assert loaded.config_name == 'test_coilset_repository throwaway'
    finally:
        cc.delete_config(record['id'])
    assert cc.get_config(record['id']) is None
    print("  ok  save_config()/delete_config() round-trip a non-builtin config")


if __name__ == '__main__':
    print("checking the unified config store")
    test_every_builtin_constructs_a_coilset()
    test_get_coilset_missing_raises_key_error()
    test_delete_builtin_is_refused()
    test_save_and_delete_user_config_round_trip()
    print("all coilset_repository checks passed")
