"""Bronnen: lokale HA-mounts (/share, /media) plus optionele SMB-shares."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

from kippiesearch.config import Settings
from kippiesearch.extract import is_supported
from kippiesearch.logging_setup import get_logger

log = get_logger("sources")


@dataclass(frozen=True)
class FileRef:
    path: str
    abs_path: Path | None
    source: str
    size: int
    mtime: float
    origin: str  # local | smb | school


def _excluded(rel: str, patterns: list[str]) -> bool:
    posix = rel.replace("\\", "/")
    for pattern in patterns:
        if fnmatch.fnmatch(posix, pattern) or fnmatch.fnmatch(Path(posix).name, pattern):
            return True
        parts = PurePosixPath(posix).parts
        for i in range(len(parts)):
            prefix = "/".join(parts[: i + 1])
            if fnmatch.fnmatch(prefix, pattern.rstrip("/**")):
                return True
    return False


def sha256_file(path: Path, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    remaining = limit if limit is not None else -1
    with path.open("rb") as handle:
        while True:
            chunk_size = 1024 * 1024
            if remaining >= 0:
                if remaining <= 0:
                    break
                chunk_size = min(chunk_size, remaining)
            data = handle.read(chunk_size)
            if not data:
                break
            digest.update(data)
            if remaining >= 0:
                remaining -= len(data)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iter_local_files(settings: Settings) -> Iterator[FileRef]:
    roots = [Path(p) for p in settings.nas_roots if p]
    for root in roots:
        if not root.exists():
            log.info("nas_root_missing", path=str(root))
            continue
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = [
                name
                for name in dirnames
                if not _excluded(
                    str(Path(rel_dir) / name).replace("\\", "/"),
                    settings.exclude_globs,
                )
            ]
            for name in filenames:
                full = Path(dirpath) / name
                rel = str(full.relative_to(root)).replace("\\", "/")
                if _excluded(rel, settings.exclude_globs):
                    continue
                if not is_supported(full, ocr_enabled=settings.ocr_enabled):
                    continue
                try:
                    stat = full.stat()
                except OSError:
                    continue
                if stat.st_size > settings.max_file_bytes:
                    log.info("skip_too_large", path=str(full), size=stat.st_size)
                    continue
                yield FileRef(
                    path=str(full),
                    abs_path=full,
                    source="nas",
                    size=int(stat.st_size),
                    mtime=float(stat.st_mtime),
                    origin="local",
                )


def iter_school_files(settings: Settings) -> Iterator[FileRef]:
    root = settings.itslearning_dir
    if not root.exists():
        return
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            full = Path(dirpath) / name
            if not is_supported(full, ocr_enabled=settings.ocr_enabled):
                continue
            try:
                stat = full.stat()
            except OSError:
                continue
            if stat.st_size > settings.max_file_bytes:
                continue
            yield FileRef(
                path=str(full),
                abs_path=full,
                source="school",
                size=int(stat.st_size),
                mtime=float(stat.st_mtime),
                origin="school",
            )


def iter_all_local(settings: Settings) -> Iterator[FileRef]:
    yield from iter_local_files(settings)
    yield from iter_school_files(settings)


def allowed_roots(settings: Settings) -> list[Path]:
    roots = [Path(p).resolve() for p in settings.nas_roots if p]
    roots.append(settings.itslearning_dir.resolve())
    roots.append(settings.nas_cache_dir.resolve())
    return roots


def resolve_safe_path(settings: Settings, user_path: str) -> Path:
    """Blokkeer path-traversal: pad moet binnen geïndexeerde roots vallen."""
    if not user_path or user_path.startswith("smb://"):
        raise ValueError("ongeldig pad")
    candidate = Path(user_path)
    if not candidate.is_absolute():
        for root in allowed_roots(settings):
            maybe = (root / user_path).resolve()
            if maybe.exists() and _is_relative_to(maybe, root):
                return maybe
        raise FileNotFoundError(user_path)
    resolved = candidate.resolve()
    for root in allowed_roots(settings):
        if _is_relative_to(resolved, root):
            return resolved
    raise PermissionError("pad ligt buiten de geïndexeerde bronnen")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
