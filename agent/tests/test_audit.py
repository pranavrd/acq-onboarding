import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit import AuditLog  # noqa: E402


class AuditLogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = AuditLog(str(Path(self.dir.name) / "a.sqlite"), run_id="r1")

    def tearDown(self):
        self.log.close()
        self.dir.cleanup()

    def test_insert_and_read(self):
        self.log.record("soql", "run_soql_query", {"q": "SELECT Id FROM Account"}, "50 rows", "allowed")
        rows = self.log.rows("r1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3:5], ("soql", "run_soql_query"))
        self.assertEqual(rows[0][7], "allowed")

    def test_update_is_blocked(self):
        self.log.record("s", "t", {}, "o", "allowed")
        with self.assertRaises(sqlite3.DatabaseError):
            self.log.conn.execute("UPDATE audit SET decision = 'tampered'")

    def test_delete_is_blocked(self):
        self.log.record("s", "t", {}, "o", "allowed")
        with self.assertRaises(sqlite3.DatabaseError):
            self.log.conn.execute("DELETE FROM audit")
        self.assertEqual(len(self.log.rows()), 1)

    def test_long_output_is_truncated(self):
        self.log.record("s", "t", {}, "x" * 5000, "allowed")
        self.assertLessEqual(len(self.log.rows()[0][6]), 500)

    def test_reopen_keeps_rows_and_triggers(self):
        path = str(Path(self.dir.name) / "b.sqlite")
        a = AuditLog(path, run_id="one")
        a.record("s", "t", {}, "o", "allowed")
        a.close()
        b = AuditLog(path, run_id="two")
        with self.assertRaises(sqlite3.DatabaseError):
            b.conn.execute("DELETE FROM audit")
        self.assertEqual(len(b.rows()), 1)
        b.close()


if __name__ == "__main__":
    unittest.main()
