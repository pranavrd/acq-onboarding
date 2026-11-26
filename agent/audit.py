"""Append-only audit log (SQLite).

Every tool call the agent makes is recorded: when, which run, which step, which tool,
the input, a short summary of the output, and the decision. Rows can be inserted and
read. UPDATE and DELETE are blocked by triggers inside the database, so "append-only"
does not depend on the calling code behaving.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    run_id         TEXT NOT NULL,
    step           TEXT NOT NULL,
    tool           TEXT NOT NULL,
    input_json     TEXT NOT NULL,
    output_summary TEXT NOT NULL,
    decision       TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""

MAX_SUMMARY = 500


class AuditLog:
    def __init__(self, path: str = "agent/audit.sqlite", run_id: str | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    def record(self, step: str, tool: str, input_obj, output_summary: str, decision: str) -> int:
        """Insert one row. `decision` is e.g. 'allowed', 'denied: not on allowlist', 'plan accepted'."""
        summary = (output_summary or "")[:MAX_SUMMARY]
        cur = self.conn.execute(
            "INSERT INTO audit (ts, run_id, step, tool, input_json, output_summary, decision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), self.run_id, step, tool,
             json.dumps(input_obj, default=str, sort_keys=True), summary, decision),
        )
        self.conn.commit()
        return cur.lastrowid

    def rows(self, run_id: str | None = None):
        q = "SELECT id, ts, run_id, step, tool, input_json, output_summary, decision FROM audit"
        args = ()
        if run_id is not None:
            q += " WHERE run_id = ?"
            args = (run_id,)
        return self.conn.execute(q + " ORDER BY id", args).fetchall()

    def close(self):
        self.conn.close()
