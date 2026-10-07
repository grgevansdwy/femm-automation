"""One-time cleanup of ai_runs rows left behind before runs had a lifecycle
(SPEC.md section 4, "One-time cleanup of existing rows"). Nothing with content
is removed.

Run:  cd backend && uv run python -m agent.cleanup_runs            # dry run: prints what would change
      cd backend && uv run python -m agent.cleanup_runs --apply    # after a Backup All and the owner's go-ahead

Rules, in this order:
1. a row with no user message is deleted (the panel was opened and closed);
2. a 'running' or 'interrupted' row with a plan but no search round becomes
   'plan_ready' - before lifecycle states, a row turned 'running' when its plan
   was *proposed*, so these never searched. ('interrupted' is included because
   the app marks every 'running' row interrupted at startup, which may happen
   before this script is run.);
3. any other 'running' row becomes 'interrupted'.

--apply copies the database file to data/backups/ first. Close the app before
running it: a search in progress would be marked interrupted.
"""

import argparse
import sqlite3
from datetime import datetime

from engine.repositories import db
from agent.repositories import ai_run_repository


def plan(conn) -> list[tuple[str, str, str]]:
    """(run id, action, why) for every row a rule applies to. Actions:
    'delete', 'plan_ready', 'interrupted'."""
    changes = []
    for row in conn.execute("SELECT id, status, spec_json FROM ai_runs ORDER BY created_at").fetchall():
        run_id, status, spec_json = row["id"], row["status"], row["spec_json"]
        user_messages = conn.execute(
            "SELECT COUNT(*) FROM ai_run_messages WHERE ai_run_id = ? AND role = 'user'", (run_id,)).fetchone()[0]
        rounds = conn.execute(
            "SELECT COUNT(*) FROM ai_run_messages WHERE ai_run_id = ? AND phase = 'optimize'", (run_id,)).fetchone()[0]
        if user_messages == 0 and rounds == 0 and status in ("interviewing", "plan_ready", "running", "interrupted"):
            changes.append((run_id, "delete", f"{status}, no user message"))
        elif status in ("running", "interrupted") and rounds == 0 and spec_json:
            changes.append((run_id, "plan_ready", f"{status}, a plan but no search round"))
        elif status == "running":
            changes.append((run_id, "interrupted", f"running, {rounds} round(s) saved"))
    return changes


def apply(conn, changes) -> None:
    now = datetime.now().astimezone().isoformat()
    for run_id, action, _ in changes:
        if action == "delete":
            conn.execute("DELETE FROM ai_run_messages WHERE ai_run_id = ?", (run_id,))
            conn.execute("DELETE FROM ai_runs WHERE id = ?", (run_id,))
        elif action == "plan_ready":
            conn.execute("UPDATE ai_runs SET status = 'plan_ready', started_at = NULL, finished_at = NULL WHERE id = ?",
                         (run_id,))
        else:
            conn.execute("UPDATE ai_runs SET status = 'interrupted', finished_at = COALESCE(finished_at, ?) WHERE id = ?",
                         (now, run_id))
    conn.commit()


def _backup(conn) -> str:
    backup_dir = db.DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"{db.DB_PATH.stem}-before-cleanup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    copy = sqlite3.connect(path)
    try:
        with copy:
            conn.backup(copy)
    finally:
        copy.close()
    return str(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-time cleanup of old agent run rows.")
    parser.add_argument("--apply", action="store_true", help="make the changes (default: only print them)")
    args = parser.parse_args(argv)

    with ai_run_repository._connection() as conn:
        changes = plan(conn)
        for run_id, action, why in changes:
            print(f"{run_id}  {action:<11}  {why}")
        counts = {a: sum(1 for _, action, _ in changes if action == a) for a in ("delete", "plan_ready", "interrupted")}
        print(f"{counts['delete']} to delete, {counts['plan_ready']} to plan_ready, "
              f"{counts['interrupted']} to interrupted, in {db.DB_PATH}")
        if not args.apply:
            print("Dry run: nothing was changed. Re-run with --apply after a Backup All.")
            return 0
        if changes:
            print(f"Backup written to {_backup(conn)}")
            apply(conn, changes)
        print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
