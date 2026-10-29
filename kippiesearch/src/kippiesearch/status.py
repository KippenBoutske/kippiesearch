"""Gedeelde runtime-status voor UI, API en scheduler."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import psutil


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AppStatus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.indexing = False
        self.syncing = False
        self.phase = "idle"
        self.current_file = ""
        self.processed = 0
        self.total = 0
        self.errors: list[dict[str, str]] = []
        self.started_at = ""
        self.finished_at = ""
        self.message = ""
        self.embedder: dict[str, Any] = {}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            proc = psutil.Process()
            mem = proc.memory_info()
            vm = psutil.virtual_memory()
            return {
                "indexing": self.indexing,
                "syncing": self.syncing,
                "phase": self.phase,
                "current_file": self.current_file,
                "processed": self.processed,
                "total": self.total,
                "errors": list(self.errors[-20:]),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "message": self.message,
                "embedder": dict(self.embedder),
                "ram": {
                    "rss_mb": round(mem.rss / (1024 * 1024), 1),
                    "vms_mb": round(mem.vms / (1024 * 1024), 1),
                    "system_used_percent": vm.percent,
                    "system_available_mb": round(vm.available / (1024 * 1024), 1),
                },
            }

    def begin(self, phase: str, total: int = 0) -> None:
        with self._lock:
            self.indexing = phase.startswith("index")
            self.syncing = phase.startswith("sync")
            self.phase = phase
            self.total = total
            self.processed = 0
            self.current_file = ""
            self.started_at = _utcnow()
            self.finished_at = ""
            self.message = ""

    def progress(self, current_file: str, processed: int | None = None, total: int | None = None) -> None:
        with self._lock:
            self.current_file = current_file
            if processed is not None:
                self.processed = processed
            if total is not None:
                self.total = total

    def error(self, path: str, message: str) -> None:
        with self._lock:
            self.errors.append({"path": path, "error": message, "at": _utcnow()})
            self.errors = self.errors[-50:]

    def finish(self, message: str = "ok") -> None:
        with self._lock:
            self.indexing = False
            self.syncing = False
            self.phase = "idle"
            self.current_file = ""
            self.finished_at = _utcnow()
            self.message = message
