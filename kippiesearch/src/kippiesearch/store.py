"""LanceDB: embedded vector store. Geen extra proces, één map onder /data.

Keuze: LanceDB i.p.v. Qdrant/Chroma omdat het een embedded library is (geen
server), Apache Arrow-native, en op ARM/CPU licht blijft. Chroma trekt meer
afhankelijkheden; Qdrant embedded is minder volwassen als library-in-process
op aarch64.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import lancedb
import numpy as np
import pyarrow as pa

from kippiesearch.logging_setup import get_logger

log = get_logger("store")

CHUNKS_TABLE = "chunks"
FILES_TABLE = "files"
META_TABLE = "meta"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class VectorStore:
    def __init__(self, db_dir: Path, dim: int) -> None:
        self.db_dir = Path(db_dir)
        self.dim = dim
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_dir))
        self._ensure_tables()

    def _chunk_schema(self) -> pa.Schema:
        return pa.schema(
            [
                pa.field("id", pa.utf8()),
                pa.field("vector", pa.list_(pa.float32(), self.dim)),
                pa.field("path", pa.utf8()),
                pa.field("source", pa.utf8()),
                pa.field("file_type", pa.utf8()),
                pa.field("mtime", pa.float64()),
                pa.field("size", pa.int64()),
                pa.field("chunk_index", pa.int32()),
                pa.field("text", pa.utf8()),
                pa.field("content_hash", pa.utf8()),
                pa.field("title", pa.utf8()),
            ]
        )

    def _file_schema(self) -> pa.Schema:
        return pa.schema(
            [
                pa.field("path", pa.utf8()),
                pa.field("source", pa.utf8()),
                pa.field("file_type", pa.utf8()),
                pa.field("mtime", pa.float64()),
                pa.field("size", pa.int64()),
                pa.field("content_hash", pa.utf8()),
                pa.field("indexed_at", pa.utf8()),
                pa.field("chunk_count", pa.int32()),
            ]
        )

    def _ensure_tables(self) -> None:
        if CHUNKS_TABLE not in self.db.table_names():
            empty = pa.Table.from_pylist([], schema=self._chunk_schema())
            self.db.create_table(CHUNKS_TABLE, data=empty, schema=self._chunk_schema())
        if FILES_TABLE not in self.db.table_names():
            empty = pa.Table.from_pylist([], schema=self._file_schema())
            self.db.create_table(FILES_TABLE, data=empty, schema=self._file_schema())
        if META_TABLE not in self.db.table_names():
            self.db.create_table(
                META_TABLE,
                data=[{"key": "created_at", "value": utcnow()}],
            )

    @property
    def chunks(self):
        return self.db.open_table(CHUNKS_TABLE)

    @property
    def files(self):
        return self.db.open_table(FILES_TABLE)

    @property
    def meta(self):
        return self.db.open_table(META_TABLE)

    def set_meta(self, key: str, value: str) -> None:
        table = self.meta
        try:
            table.delete(f"key = '{_esc(key)}'")
        except Exception:
            pass
        table.add([{"key": key, "value": value}])

    def get_meta(self, key: str, default: str = "") -> str:
        for row in _table_rows(self.meta):
            if row.get("key") == key:
                return str(row.get("value") or default)
        return default

    def list_files(self) -> list[dict[str, Any]]:
        return _table_rows(self.files)

    def get_file(self, path: str) -> dict[str, Any] | None:
        for row in self.list_files():
            if row.get("path") == path:
                return row
        return None

    def upsert_file(self, record: dict[str, Any]) -> None:
        self.delete_file(record["path"], chunks=False)
        self.files.add([record])

    def delete_file(self, path: str, chunks: bool = True) -> None:
        escaped = _esc(path)
        try:
            self.files.delete(f"path = '{escaped}'")
        except Exception:
            pass
        if chunks:
            try:
                self.chunks.delete(f"path = '{escaped}'")
            except Exception:
                pass

    def add_chunks(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self.chunks.add(rows)

    def search(
        self,
        vector: np.ndarray,
        top_k: int = 8,
        source: str | None = None,
        file_type: str | None = None,
        after: float | None = None,
        before: float | None = None,
    ) -> list[dict[str, Any]]:
        query = np.asarray(vector, dtype=np.float32).reshape(-1)
        table = self.chunks
        try:
            builder = table.search(query)
            if hasattr(builder, "metric"):
                builder = builder.metric("cosine")
            elif hasattr(builder, "distance_type"):
                builder = builder.distance_type("cosine")
        except TypeError:
            builder = table.search(query)
        clauses = []
        if source:
            clauses.append(f"source = '{_esc(source)}'")
        if file_type:
            clauses.append(f"file_type = '{_esc(file_type)}'")
        if after is not None:
            clauses.append(f"mtime >= {float(after)}")
        if before is not None:
            clauses.append(f"mtime <= {float(before)}")
        if clauses:
            try:
                builder = builder.where(" AND ".join(clauses), prefilter=True)
            except TypeError:
                builder = builder.where(" AND ".join(clauses))
        try:
            rows = builder.limit(top_k).to_list()
        except Exception as exc:  # noqa: BLE001
            log.warning("search_failed", error=str(exc))
            rows = []
        return rows

    def stats(self) -> dict[str, Any]:
        files = self.list_files()
        per_source: dict[str, dict[str, int]] = {}
        for row in files:
            source = str(row.get("source") or "unknown")
            bucket = per_source.setdefault(source, {"files": 0, "chunks": 0})
            bucket["files"] += 1
            bucket["chunks"] += int(row.get("chunk_count") or 0)
        try:
            chunk_count = int(self.chunks.count_rows())
        except Exception:
            chunk_count = sum(v["chunks"] for v in per_source.values())
        return {
            "files": len(files),
            "chunks": int(chunk_count),
            "per_source": per_source,
            "last_index": self.get_meta("last_index"),
            "last_nas_scan": self.get_meta("last_nas_scan"),
            "last_itslearning_sync": self.get_meta("last_itslearning_sync"),
            "last_error": self.get_meta("last_error"),
        }

    def all_paths(self) -> set[str]:
        return {str(row["path"]) for row in self.list_files() if row.get("path")}

    def chunks_for_path(self, path: str) -> list[dict[str, Any]]:
        return [row for row in _table_rows(self.chunks) if row.get("path") == path]


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _table_rows(table) -> list[dict[str, Any]]:
    try:
        return table.to_arrow().to_pylist()
    except Exception:
        try:
            return table.search().limit(100_000).to_list()
        except Exception:
            return []


def vectors_from_rows(rows: Iterable[dict[str, Any]]) -> list[list[float]]:
    out = []
    for row in rows:
        vec = row.get("vector")
        if vec is None:
            continue
        out.append(list(vec))
    return out
