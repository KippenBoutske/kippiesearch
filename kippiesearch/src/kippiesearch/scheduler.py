"""APScheduler: dagelijkse it's learning-sync + periodieke NAS-rescan."""

from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from kippiesearch.config import Settings
from kippiesearch.indexer import Indexer
from kippiesearch.itslearning import ItslearningSync
from kippiesearch.logging_setup import get_logger
from kippiesearch.nas import iter_smb_files, smb_available
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore, utcnow
from kippiesearch.sources import FileRef, iter_all_local

log = get_logger("scheduler")


def _parse_cron(expr: str) -> CronTrigger:
    parts = (expr or "").split()
    if len(parts) != 5:
        raise ValueError(f"ongeldige cron: {expr}")
    minute, hour, day, month, dow = parts
    return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)


def start_scheduler(
    settings: Settings,
    indexer: Indexer,
    syncer: ItslearningSync,
    store: VectorStore,
    status: AppStatus,
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Europe/Amsterdam")

    def nas_job() -> None:
        if status.indexing or status.syncing:
            log.info("skip_nas_job_busy")
            return
        refs: list[FileRef] = list(iter_all_local(settings))
        if smb_available(settings):
            try:
                from kippiesearch.nas import cache_smb_file

                smb_refs = []
                for ref in iter_smb_files(settings):
                    try:
                        local = cache_smb_file(settings, ref.path)
                        smb_refs.append(
                            FileRef(
                                path=ref.path,
                                abs_path=local,
                                source="nas",
                                size=ref.size,
                                mtime=ref.mtime,
                                origin="smb",
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        status.error(ref.path, str(exc))
                refs.extend(smb_refs)
            except Exception as exc:  # noqa: BLE001
                log.warning("smb_job_failed", error=str(exc))
        indexer.run(refs=refs, full=False)
        store.set_meta("last_nas_scan", utcnow())

    def school_job() -> None:
        if status.indexing or status.syncing:
            log.info("skip_school_job_busy")
            return
        result = syncer.sync()
        if result.get("ok"):
            indexer.run(full=False)

    try:
        scheduler.add_job(nas_job, _parse_cron(settings.nas_cron), id="nas-rescan", replace_existing=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("nas_cron_invalid", expr=settings.nas_cron, error=str(exc))
        scheduler.add_job(nas_job, "interval", hours=max(1.0, settings.rescan_hours), id="nas-rescan")
    try:
        scheduler.add_job(
            school_job, _parse_cron(settings.itslearning_cron), id="itslearning-sync", replace_existing=True
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("its_cron_invalid", expr=settings.itslearning_cron, error=str(exc))
        scheduler.add_job(school_job, "cron", hour=3, minute=0, id="itslearning-sync")

    scheduler.start()
    log.info(
        "scheduler_started",
        nas_cron=settings.nas_cron,
        itslearning_cron=settings.itslearning_cron,
        now=datetime.now().isoformat(),
    )
    return scheduler
