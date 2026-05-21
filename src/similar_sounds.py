from __future__ import annotations

import math
import os
import platform
import sqlite3
import struct
from pathlib import Path
from typing import Any


DB_DIR_ENV_VARS = ("ABLETON_LIVE_DATABASE_DIR", "ABLETON_LIVE_DB_DIR")


def live_database_dirs(
    *,
    system: str | None = None,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
) -> list[Path]:
    environ = environ if environ is not None else os.environ
    home = home or Path.home()
    candidates: list[Path] = []
    for name in DB_DIR_ENV_VARS:
        value = environ.get(name)
        if value:
            candidates.extend(Path(item).expanduser() for item in value.split(os.pathsep) if item)

    system = system or platform.system()
    if system == "Windows":
        local_app_data = environ.get("LOCALAPPDATA")
        app_data = environ.get("APPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Ableton" / "Live Database")
        if app_data:
            candidates.append(Path(app_data) / "Ableton" / "Live Database")
    elif system == "Darwin":
        candidates.append(home / "Library" / "Application Support" / "Ableton" / "Live Database")
    else:
        xdg_data_home = environ.get("XDG_DATA_HOME")
        if xdg_data_home:
            candidates.append(Path(xdg_data_home) / "Ableton" / "Live Database")
        candidates.append(home / ".local" / "share" / "Ableton" / "Live Database")

    candidates.append(home / "Library" / "Application Support" / "Ableton" / "Live Database")
    return _dedupe(candidates)


def _dedupe(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        result.append(path.expanduser())
    return result


def latest_live_files_db() -> Path:
    db_dirs = live_database_dirs()
    candidates = sorted(
        (path for db_dir in db_dirs for path in db_dir.glob("Live-files-*.db")),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            "No Live-files database found in %s" % ", ".join(str(path) for path in db_dirs)
        )
    return candidates[0]


def encode_feature(values: list[float] | tuple[float, ...]) -> bytes:
    return b"\x12\x00\x00\x00\x40\x00\x00\x00\x00\x00\x00\x00" + struct.pack("<%df" % len(values), *values)


def decode_feature(blob: bytes) -> tuple[float, ...]:
    if len(blob) < 16 or (len(blob) - 12) % 4:
        raise ValueError("unexpected feature blob length: %s" % len(blob))
    return struct.unpack("<%df" % ((len(blob) - 12) // 4), blob[12:])


def find_similar_sounds(params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    db_path = Path(params["db_path"]) if params.get("db_path") else latest_live_files_db()
    limit = int(params.get("limit") or 12)
    if limit < 1:
        raise ValueError("limit must be >= 1")

    base_value = params.get("base")
    if base_value is None:
        base_value = params.get("query")
    if not base_value:
        raise ValueError("base is required")

    include_self = bool(params.get("include_self"))
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        base = _resolve_base(conn, str(base_value))
        results = _similar(conn, base, limit, include_self)
        return {
            "database": str(db_path),
            "base": _result_item(conn, base, 0.0),
            "results": results,
        }
    finally:
        conn.close()


def _resolve_base(conn: sqlite3.Connection, value: str) -> sqlite3.Row:
    if value.isdigit():
        row = conn.execute(
            """
            SELECT f.file_id, f.parent_id, f.name, f.file_type, f.file_kind, f.flags, f.fe_version,
                   f.file_size, f.mod_date, p.name AS place_name, fv.hash, fv.data
              FROM files AS f
              JOIN fe_values AS fv ON fv.file_id = f.file_id
              LEFT JOIN places AS p ON p.file_id = f.place_id
             WHERE f.file_id = ?
            """,
            (int(value),),
        ).fetchone()
    else:
        needle = value if any(char in value for char in "%_") else "%%%s%%" % value
        row = conn.execute(
            """
            SELECT f.file_id, f.parent_id, f.name, f.file_type, f.file_kind, f.flags, f.fe_version,
                   f.file_size, f.mod_date, p.name AS place_name, fv.hash, fv.data
              FROM files AS f
              JOIN fe_values AS fv ON fv.file_id = f.file_id
              LEFT JOIN places AS p ON p.file_id = f.place_id
             WHERE f.name LIKE ?
             ORDER BY
                   CASE WHEN lower(f.name) = lower(?) THEN 0 ELSE 1 END,
                   f.use_count DESC,
                   f.file_id
             LIMIT 1
            """,
            (needle, value),
        ).fetchone()
    if row is None:
        raise ValueError("No analyzed Live browser item found for %r" % value)
    return row


def _similar(conn: sqlite3.Connection, base: sqlite3.Row, limit: int, include_self: bool) -> list[dict[str, Any]]:
    base_vector = decode_feature(base["data"])
    rows = conn.execute(
        """
        SELECT f.file_id, f.parent_id, f.name, f.file_type, f.file_kind, f.flags, f.fe_version,
               f.file_size, f.mod_date, p.name AS place_name, fv.hash, fv.data
          FROM files AS f
          JOIN fe_values AS fv ON fv.file_id = f.file_id
          LEFT JOIN places AS p ON p.file_id = f.place_id
         WHERE f.place_id <> 0
           AND f.flags & 1 = 1
           AND f.file_kind & ? <> 0
        """,
        (int(base["file_kind"]),),
    ).fetchall()

    by_hash: dict[int, tuple[float, sqlite3.Row]] = {}
    for row in rows:
        if not include_self and (int(row["file_id"]) == int(base["file_id"]) or row["hash"] == base["hash"]):
            continue
        distance = _squared_distance(base_vector, decode_feature(row["data"]))
        old = by_hash.get(row["hash"])
        if old is None or (distance, row["file_id"]) < (old[0], old[1]["file_id"]):
            by_hash[row["hash"]] = (distance, row)

    ranked = sorted(by_hash.values(), key=lambda item: (item[0], item[1]["file_id"]))
    return [_result_item(conn, row, math.sqrt(distance)) for distance, row in ranked[:limit]]


def _squared_distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum((x - y) * (x - y) for x, y in zip(a, b))


def _result_item(conn: sqlite3.Connection, row: sqlite3.Row, distance: float) -> dict[str, Any]:
    return {
        "distance": distance,
        "file_id": int(row["file_id"]),
        "name": row["name"],
        "place": row["place_name"],
        "file_kind": int(row["file_kind"]),
        "file_type": int(row["file_type"]),
        "fe_version": int(row["fe_version"]),
        "path": _path_for(conn, int(row["file_id"])),
    }


def _path_for(conn: sqlite3.Connection, file_id: int) -> str:
    names: list[str] = []
    seen: set[int] = set()
    current = file_id
    while current and current not in seen:
        seen.add(current)
        row = conn.execute("SELECT parent_id, name FROM files WHERE file_id = ?", (current,)).fetchone()
        if row is None:
            break
        if row["name"] and row["name"] != "/":
            names.append(row["name"])
        current = row["parent_id"] or 0
    return " / ".join(reversed(names))
