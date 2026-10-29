"""SMB-fallback voor Synology wanneer HA-netwerkopslag niet gemount is."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from kippiesearch.config import Settings
from kippiesearch.extract import is_supported
from kippiesearch.logging_setup import get_logger
from kippiesearch.sources import FileRef, _excluded

log = get_logger("nas")


def smb_available(settings: Settings) -> bool:
    return settings.nas_smb_ready()


def _register(settings: Settings) -> None:
    import smbclient

    smbclient.register_session(
        settings.nas_smb_host,
        username=settings.nas_smb_username,
        password=settings.nas_smb_password,
        port=settings.nas_smb_port,
    )


def iter_smb_files(settings: Settings) -> Iterator[FileRef]:
    if not smb_available(settings):
        return
    import smbclient

    _register(settings)
    for share in settings.nas_smb_shares:
        root = rf"\\{settings.nas_smb_host}\{share}"
        try:
            walker = smbclient.walk(root)
        except Exception as exc:  # noqa: BLE001
            log.warning("smb_walk_failed", share=share, error=str(exc))
            continue
        for dirpath, _, filenames in walker:
            for name in filenames:
                rel = str(Path(dirpath.replace("\\", "/")) / name)
                if _excluded(rel, settings.exclude_globs):
                    continue
                if not is_supported(name, ocr_enabled=settings.ocr_enabled):
                    continue
                unc = str(Path(dirpath) / name)
                try:
                    info = smbclient.stat(unc)
                    size = int(getattr(info, "st_size", 0) or 0)
                    mtime = float(getattr(info, "st_mtime", 0) or 0)
                except Exception as exc:  # noqa: BLE001
                    log.warning("smb_stat_failed", path=unc, error=str(exc))
                    continue
                if size > settings.max_file_bytes:
                    continue
                logical = f"smb://{settings.nas_smb_host}/{share}/{unc.split(share, 1)[-1].lstrip('/\\\\')}"
                yield FileRef(
                    path=logical.replace("\\", "/"),
                    abs_path=None,
                    source="nas",
                    size=size,
                    mtime=mtime,
                    origin="smb",
                )


def read_smb_file(settings: Settings, smb_url: str) -> bytes:
    if not smb_url.startswith("smb://"):
        raise ValueError("geen smb-url")
    import smbclient

    _register(settings)
    rest = smb_url[len("smb://") :]
    host, _, tail = rest.partition("/")
    if host != settings.nas_smb_host:
        raise PermissionError("smb host komt niet overeen")
    unc = rf"\\{host}\{tail.replace('/', '\\')}"
    with smbclient.open_file(unc, mode="rb") as handle:
        return handle.read(settings.max_file_bytes + 1)


def cache_smb_file(settings: Settings, smb_url: str) -> Path:
    data = read_smb_file(settings, smb_url)
    if len(data) > settings.max_file_bytes:
        raise ValueError("bestand te groot")
    relative = smb_url.split("smb://", 1)[-1]
    dest = settings.nas_cache_dir / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest
