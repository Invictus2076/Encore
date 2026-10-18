"""A tiny stand-in for mysql.connector, backed by SQLite, so the tests run without a MySQL server.

It translates the few MySQL-only bits Encore uses (%s placeholders, AUTO_INCREMENT, FOR UPDATE)."""
import re
import sqlite3


class Error(Exception):
    def __init__(self, msg=""):
        super().__init__(msg)
        self.msg = msg


DATABASE = ":memory:"
_shared = None


def _translate(sql):
    sql = sql.replace("%s", "?").replace("FOR UPDATE", "")
    return re.sub(r"INT AUTO_INCREMENT PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT", sql)


class _Cursor:
    def __init__(self, conn):
        self._c = conn.cursor()

    def execute(self, sql, params=()):
        try:
            self._c.execute(_translate(sql), tuple(params))
        except sqlite3.Error as exc:
            raise Error(str(exc)) from exc

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    @property
    def description(self):
        return self._c.description

    @property
    def lastrowid(self):
        return self._c.lastrowid

    def close(self):
        self._c.close()


class _Connection:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _Cursor(self._conn)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def start_transaction(self):
        pass

    def close(self):
        pass  # one shared in-memory database for the whole test run


def connect(**_kwargs):
    global _shared
    if _shared is None:
        _shared = sqlite3.connect(DATABASE, check_same_thread=False)
    return _Connection(_shared)


def reset():
    global _shared
    if _shared is not None:
        _shared.close()
    _shared = None
