#!/usr/bin/env python3
"""Apply the fictional demo migration without touching payment data."""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

MARKER = "fictional-payment-schema-v1"


def migrate(database: str) -> None:
    """Create one idempotent marker table for the fictional service."""

    if database != ":memory:":
        target = Path(database)
        target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS release_migration_markers (
                name TEXT PRIMARY KEY NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO release_migration_markers(name) VALUES (?)",
            (MARKER,),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    arguments = parser.parse_args()
    migrate(arguments.database)


if __name__ == "__main__":
    main()
