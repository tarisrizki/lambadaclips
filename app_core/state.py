import json
import sqlite3
import threading
import time
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): safe_value
            for key, item in value.items()
            if not str(key).startswith("_")
            and (safe_value := _json_safe(item)) is not _UNSUPPORTED
        }
    if isinstance(value, (list, tuple)):
        return [
            safe_value
            for item in value
            if (safe_value := _json_safe(item)) is not _UNSUPPORTED
        ]
    return _UNSUPPORTED


_UNSUPPORTED = object()


class PersistentNamespace(MutableMapping[str, dict]):
    def __init__(self, database: "SQLiteState", name: str):
        self.database = database
        self.name = name
        self.data = database.load_namespace(name)

    def __getitem__(self, key: str) -> dict:
        return self.data[key]

    def __setitem__(self, key: str, value: dict) -> None:
        self.data[key] = value
        self.database.save(self.name, key, value)

    def __delitem__(self, key: str) -> None:
        del self.data[key]
        self.database.delete(self.name, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def flush(self) -> None:
        self.database.replace_namespace(self.name, self.data)


class SQLiteState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.namespaces: list[PersistentNamespace] = []
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS state (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (namespace, key)
                )
                """
            )

    def namespace(self, name: str) -> PersistentNamespace:
        namespace = PersistentNamespace(self, name)
        self.namespaces.append(namespace)
        return namespace

    def load_namespace(self, namespace: str) -> dict[str, dict]:
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value FROM state WHERE namespace = ?", (namespace,)
            ).fetchall()
        loaded = {}
        for key, value in rows:
            try:
                loaded[key] = json.loads(value)
            except json.JSONDecodeError:
                continue
        return loaded

    def save(self, namespace: str, key: str, value: dict) -> None:
        safe_value = _json_safe(value)
        if safe_value is _UNSUPPORTED:
            return
        payload = json.dumps(safe_value, ensure_ascii=True)
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO state(namespace, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (namespace, key, payload, time.time()),
            )

    def delete(self, namespace: str, key: str) -> None:
        with self.lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM state WHERE namespace = ? AND key = ?",
                (namespace, key),
            )

    def replace_namespace(self, namespace: str, values: dict[str, dict]) -> None:
        with self.lock, self._connect() as connection:
            connection.execute("DELETE FROM state WHERE namespace = ?", (namespace,))
            for key, value in values.items():
                safe_value = _json_safe(value)
                if safe_value is _UNSUPPORTED:
                    continue
                connection.execute(
                    "INSERT INTO state(namespace, key, value, updated_at) VALUES (?, ?, ?, ?)",
                    (
                        namespace,
                        key,
                        json.dumps(safe_value, ensure_ascii=True),
                        time.time(),
                    ),
                )

    def flush_all(self) -> None:
        for namespace in self.namespaces:
            namespace.flush()
