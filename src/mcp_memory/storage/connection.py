"""SQLite connection wrapper with a depth-counted transaction context manager."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, cast


class Connection:
    """A writable or read-only SQLite connection exposing four read/write ports and a change counter."""

    def __init__(self, db: sqlite3.Connection, path: Path) -> None:
        self._db = db
        self.path = path
        self._depth = 0

    @classmethod
    def open_writable(cls, path: str | Path) -> Connection:
        path = Path(path)
        db = sqlite3.connect(str(path))
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA cache_size=1000")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("PRAGMA foreign_keys=ON")
        # Set explicitly so a writer waits rather than failing instantly on a busy
        # lock, independent of the sqlite3 driver's connect(timeout=) default.
        db.execute("PRAGMA busy_timeout=5000")
        return cls(db, path)

    @classmethod
    def open_readonly(cls, path: str | Path) -> Connection:
        """Open an existing DB read-only.

        Safe to call from a non-main thread: the connection is opened with
        ``check_same_thread=False`` and never writes, so it can run alongside the
        main writable connection under WAL without contention.
        """
        path = Path(path)
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        return cls(db, path)

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return cast("sqlite3.Row | None", self._db.execute(sql, params).fetchone())

    def query_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self._db.execute(sql, params).fetchall()

    def write(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self._db.execute(sql, params)

    def write_many(self, sql: str, params: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        return self._db.executemany(sql, params)

    @property
    def total_changes(self) -> int:
        """Total rows changed on this connection since it was opened."""
        return self._db.total_changes

    @contextmanager
    def transaction(self, commit: bool = True) -> Iterator[None]:
        self._depth += 1
        try:
            yield
        except BaseException:
            if self._depth == 1:
                self._db.rollback()
            raise
        else:
            if self._depth == 1:
                if commit:
                    self._db.commit()
                else:
                    self._db.rollback()
        finally:
            self._depth -= 1

    def close(self) -> None:
        self._db.close()

    @property
    def raw(self) -> sqlite3.Connection:
        return self._db
