"""Incrementele indexer: mtime+size fast-path, content-hash als check, deletes opruimen."""

from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Iterable

from kippiesearch.chunking import chunk_text
from kippiesearch.config import Settings
from kippiesearch.embeddings import Embedder
from kippiesearch.extract import extract_text, file_type_for
from kippiesearch.logging_setup import get_logger
from kippiesearch.sources import FileRef, iter_all_local, sha256_file
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore, utcnow

log = get_logger("indexer")


class Indexer:
    def __init__(
        self,
        settings: Settings,
        store: VectorStore,
        embedder: Embedder,
        status: AppStatus,
    ) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.status = status

    def run(self, refs: Iterable[FileRef] | None = None, full: bool = False) -> dict:
        files = list(refs) if refs is not None else list(iter_all_local(self.settings))
        self.status.begin("index", total=len(files))
        seen: set[str] = set()
        indexed = 0
        skipped = 0
        deleted = 0
        errors = 0
        batch_docs: list[dict] = []

        def flush() -> None:
            if not batch_docs:
                return
            try:
                self._write_batch(batch_docs)
            except Exception:
                paths = {str(row.get("path") or "") for row in batch_docs}
                for path in paths:
                    if path:
                        self.store.delete_file(path)
                raise
            finally:
                batch_docs.clear()

        try:
            for i, ref in enumerate(files, start=1):
                seen.add(ref.path)
                self.status.progress(ref.path, processed=i, total=len(files))
                try:
                    changed = self._index_one(ref, batch_docs, full=full)
                    if changed:
                        indexed += 1
                    else:
                        skipped += 1
                    if len(batch_docs) >= max(1, self.settings.batch_size):
                        flush()
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    tb = traceback.format_exc(limit=8)
                    log.warning(
                        "index_file_failed",
                        path=ref.path,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        traceback=tb,
                    )
                    self.status.error(ref.path, f"{type(exc).__name__}: {exc}")
                if self.settings.throttle_s:
                    time.sleep(self.settings.throttle_s)
            try:
                flush()
            except Exception as exc:  # noqa: BLE001
                errors += 1
                log.warning(
                    "index_flush_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(limit=8),
                )
                self.status.error("", f"{type(exc).__name__}: {exc}")
            if not full:
                existing = self.store.all_paths()
                for stale in existing - seen:
                    # Alleen paden van lokale/school bronnen automatisch droppen
                    if stale.startswith("smb://"):
                        continue
                    self.store.delete_file(stale)
                    deleted += 1
            self.store.set_meta("last_index", utcnow())
            if errors:
                self.store.set_meta("last_error", f"{errors} bestanden faalden bij indexeren")
            else:
                self.store.set_meta("last_error", "")
            message = (
                f"indexed={indexed} skipped={skipped} deleted={deleted} errors={errors}"
            )
            self.status.finish(message)
            log.info("index_done", indexed=indexed, skipped=skipped, deleted=deleted, errors=errors)
            return {
                "indexed": indexed,
                "skipped": skipped,
                "deleted": deleted,
                "errors": errors,
                "total": len(files),
            }
        except Exception as exc:  # noqa: BLE001
            self.store.set_meta("last_error", str(exc))
            self.status.finish(f"failed: {exc}")
            raise

    def _index_one(self, ref: FileRef, batch_docs: list[dict], full: bool) -> bool:
        previous = None if full else self.store.get_file(ref.path)
        if previous and not full:
            prev_mtime = float(previous.get("mtime") or 0)
            prev_size = int(previous.get("size") or 0)
            if prev_mtime == ref.mtime and prev_size == ref.size:
                return False
        if ref.abs_path is None or not ref.abs_path.exists():
            return False
        content_hash = sha256_file(ref.abs_path)
        if previous and previous.get("content_hash") == content_hash and not full:
            self.store.upsert_file(
                {
                    "path": ref.path,
                    "source": ref.source,
                    "file_type": file_type_for(ref.path),
                    "mtime": ref.mtime,
                    "size": ref.size,
                    "content_hash": content_hash,
                    "indexed_at": utcnow(),
                    "chunk_count": int(previous.get("chunk_count") or 0),
                }
            )
            return False

        text = extract_text(ref.abs_path, ocr_enabled=self.settings.ocr_enabled)
        chunks = chunk_text(
            text,
            chunk_chars=self.settings.chunk_chars,
            overlap=self.settings.chunk_overlap,
        )
        self.store.delete_file(ref.path, chunks=True)
        title = Path(ref.path).name
        ftype = file_type_for(ref.path)
        for chunk in chunks:
            batch_docs.append(
                {
                    "id": f"{ref.path}#{chunk.index}",
                    "path": ref.path,
                    "source": ref.source,
                    "file_type": ftype,
                    "mtime": ref.mtime,
                    "size": ref.size,
                    "chunk_index": chunk.index,
                    "content_hash": content_hash,
                    "title": title,
                    "_text": chunk.text,
                    "_title": title,
                }
            )
        self.store.upsert_file(
            {
                "path": ref.path,
                "source": ref.source,
                "file_type": ftype,
                "mtime": ref.mtime,
                "size": ref.size,
                "content_hash": content_hash,
                "indexed_at": utcnow(),
                "chunk_count": len(chunks),
            }
        )
        return True

    def _write_batch(self, batch_docs: list[dict]) -> None:
        texts = [row["_text"] for row in batch_docs]
        titles = [row.get("_title") or row.get("title") or "" for row in batch_docs]
        vectors = self.embedder.embed_documents(texts, titles=titles)
        rows = []
        for row, vector, text in zip(batch_docs, vectors, texts, strict=False):
            payload = {key: value for key, value in row.items() if not str(key).startswith("_")}
            payload["vector"] = vector.tolist()
            payload["text"] = text
            rows.append(payload)
        self.store.add_chunks(rows)
