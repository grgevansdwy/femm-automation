# =============================================================================
# test_smoke.py
#
# Routing/form-handling smoke tests against the real app, using the real
# coil_simulation/data/configs/ store (no FEMM solve is triggered - runs are
# only submitted far enough to check the plumbing, never polled to
# completion). Needs no FEMM:
#     python3 -m tests.test_smoke  (run from webapp/)
#
# Every test that creates a config cleans it up before returning, so the
# store is left exactly as found (16 built-ins).
# =============================================================================
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def test_configs_list_shows_builtins():
    r = client.get('/configs')
    assert r.status_code == 200, r.status_code
    assert r.text.count('Built-in') >= 16, r.text.count('Built-in')
    print("  ok  GET /configs shows at least 16 built-in rows")


def test_new_config_form_renders():
    r = client.get('/configs/new')
    assert r.status_code == 200, r.status_code
    print("  ok  GET /configs/new renders")


def test_clone_then_save_unmodified_succeeds():
    """Regression test for the clone-then-save 500: the advanced_json
    textarea must round-trip through the form as valid JSON."""
    r = client.post('/configs/clone/Gen3', follow_redirects=False)
    assert r.status_code == 303, r.status_code
    edit_url = r.headers['location']
    config_id = edit_url.split('/')[2]

    r = client.get(edit_url)
    assert r.status_code == 200

    form = dict(re.findall(r'<input[^>]*name="([\w]+)"[^>]*value="([^"]*)"', r.text))
    for m in re.finditer(r'<select[^>]*name="([\w]+)"[^>]*>(.*?)</select>', r.text, re.S):
        name, body = m.groups()
        sel = re.search(r'<option value="([^"]*)" selected', body)
        if sel:
            form[name] = sel.group(1)
    adv = re.search(r'<textarea name="advanced_json">(.*?)</textarea>', r.text, re.S)
    form['advanced_json'] = adv.group(1)

    try:
        r2 = client.post(edit_url, data=form, follow_redirects=False)
        assert r2.status_code == 303, (r2.status_code, r2.text[:500])
    finally:
        client.post(f'/configs/{config_id}/delete')
    print("  ok  clone -> submit unmodified succeeds (was a 500)")


def test_malformed_advanced_json_is_422_not_500():
    r = client.get('/configs/new')
    form = dict(re.findall(r'<input[^>]*name="([\w]+)"[^>]*value="([^"]*)"', r.text))
    form.setdefault('config_name', 'smoke test throwaway')
    for name in re.findall(r'name="(num_\w+)"', r.text):
        form.setdefault(name, '1')
    form['advanced_json'] = '{bad json'

    r2 = client.post('/configs/new', data=form)
    assert r2.status_code == 422, (r2.status_code, r2.text[:500])
    assert 'not valid JSON' in r2.text
    print("  ok  malformed advanced_json returns 422 with an error banner, not a 500")


def test_delete_builtin_is_refused():
    r = client.post('/configs/Gen3/delete', follow_redirects=False)
    assert r.status_code == 400, r.status_code
    assert 'Cannot delete built-in' in r.text
    r2 = client.get('/configs/Gen3/edit')
    assert r2.status_code == 200, "Gen3 should still exist"
    print("  ok  deleting a built-in config is refused (400), config still exists")


def test_delete_user_config_succeeds():
    r = client.post('/configs/clone/Gen3', follow_redirects=False)
    config_id = r.headers['location'].split('/')[2]
    r2 = client.post(f'/configs/{config_id}/delete', follow_redirects=False)
    assert r2.status_code == 303, r2.status_code
    r3 = client.get(f'/configs/{config_id}/edit')
    assert r3.status_code == 404
    print("  ok  deleting a non-builtin config succeeds")


def test_run_new_shows_single_select_not_multi_checkbox():
    r = client.get('/runs/new')
    assert r.status_code == 200
    assert '<select id="f-config_id"' in r.text
    assert 'name="distance"' in r.text
    assert 'checkbox' not in r.text
    print("  ok  GET /runs/new shows one config select + one distance input")


if __name__ == '__main__':
    print("checking webapp routes")
    test_configs_list_shows_builtins()
    test_new_config_form_renders()
    test_clone_then_save_unmodified_succeeds()
    test_malformed_advanced_json_is_422_not_500()
    test_delete_builtin_is_refused()
    test_delete_user_config_succeeds()
    test_run_new_shows_single_select_not_multi_checkbox()
    print("all webapp smoke checks passed")
