from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.migrate import MARKER, migrate


class FictionalMigrationTests(unittest.TestCase):
    def test_repeat_migration_has_one_fictional_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "fictional.sqlite3"
            migrate(str(database))
            migrate(str(database))
            with sqlite3.connect(database) as connection:
                rows = connection.execute(
                    "SELECT name FROM release_migration_markers"
                ).fetchall()
        self.assertEqual(rows, [(MARKER,)])


if __name__ == "__main__":
    unittest.main()
