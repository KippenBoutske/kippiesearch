"""FastAPI: UI + REST + MCP + scheduler in één proces."""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from kippiesearch import __version__
from kippiesearch.auth import BearerAuthMiddleware, is_ingress, protect
from kippiesearch.config import Settings, load_settings
from kippiesearch.embeddings import create_embedder
from kippiesearch.extract import extract_text
from kippiesearch.indexer import Indexer
from kippiesearch.itslearning import ItslearningSync
from kippiesearch.logging_setup import configure_logging, get_logger
from kippiesearch.mcp_server import build_mcp, mcp_asgi_app
from kippiesearch.nas import cache_smb_file, smb_available
from kippiesearch.scheduler import start_scheduler
from kippiesearch.search import SearchService
from kippiesearch.sources import resolve_safe_path
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore

log = get_logger("main")
STATIC_DIR = Path(__file__).parent / "static"


class ReindexBody(BaseModel):
    full: bool = False
    itslearning: bool = False


class AppContext:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.status = AppStatus()
        self.store = VectorStore(settings.db_dir, dim=settings.embedding_dim)
        self.embedder = create_embedder(settings)
        self.status.embedder = self.embedder.info()
        self.searcher = SearchService(self.store, self.embedder)
        self.indexer = Indexer(settings, self.store, self.embedder, self.status)
        self.syncer = ItslearningSync(settings, self.status, self.store)
        self.scheduler = None
        self._index_lock = threading.Lock()

    def kick_reindex(self, full: bool = False, itslearning: bool = False) -> None:
        if not self._index_lock.acquire(blocking=False):
            raise RuntimeError("indexering of sync loopt al")

        def worker() -> None:
            try:
                if itslearning:
                    self.syncer.sync()
                self.indexer.run(full=full)
            finally:
                self._index_lock.release()

        threading.Thread(target=worker, name="kippiesearch-reindex", daemon=True).start()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    ctx = AppContext(settings)
    mcp = build_mcp(settings, ctx.store, ctx.searcher)
    mcp_app = mcp_asgi_app(mcp)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ctx = ctx
        ctx.scheduler = start_scheduler(settings, ctx.indexer, ctx.syncer, ctx.store, ctx.status)
        log.info("kippiesearch_started", version=__version__, port=settings.port)
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            if ctx.scheduler:
                ctx.scheduler.shutdown(wait=False)

    api = FastAPI(
        title="KippieSearch",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        redirect_slashes=False,
    )
    api.add_middleware(BearerAuthMiddleware, settings=settings)
    require = protect(settings)

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @api.get("/api/health")
    def api_health() -> dict[str, str]:
        return health()

    @api.get("/api/search")
    def api_search(
        q: str = Query(..., min_length=1),
        top_k: int = Query(8, ge=1, le=50),
        source: str | None = None,
        file_type: str | None = None,
        after: str | None = None,
        before: str | None = None,
        _: None = Depends(require),
    ) -> dict[str, Any]:
        hits = ctx.searcher.search(
            q, top_k=top_k, source=source, file_type=file_type, after=after, before=before
        )
        return {"query": q, "count": len(hits), "results": hits}

    @api.get("/api/sources")
    def api_sources(_: None = Depends(require)) -> dict[str, Any]:
        stats = ctx.store.stats()
        return {
            "nas_roots": settings.nas_roots,
            "smb": {
                "enabled": settings.nas_smb_enabled,
                "host": settings.nas_smb_host,
                "shares": settings.nas_smb_shares,
                "available": smb_available(settings),
            },
            "itslearning": {
                "enabled": settings.itslearning_enabled,
                "url": settings.itslearning_url,
                "organisation": settings.itslearning_organisation,
                "dir": str(settings.itslearning_dir),
            },
            "stats": stats,
        }

    @api.get("/api/status")
    def api_status(_: None = Depends(require)) -> dict[str, Any]:
        snap = ctx.status.snapshot()
        snap["store"] = ctx.store.stats()
        snap["version"] = __version__
        snap["auth_required"] = settings.require_auth()
        snap["port"] = settings.port
        return snap

    @api.post("/api/reindex")
    def api_reindex(body: ReindexBody | None = None, _: None = Depends(require)) -> dict[str, Any]:
        payload = body or ReindexBody()
        try:
            ctx.kick_reindex(full=payload.full, itslearning=payload.itslearning)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "full": payload.full, "itslearning": payload.itslearning}

    @api.get("/api/file")
    def api_file(path: str, download: bool = False, _: None = Depends(require)):
        try:
            if path.startswith("smb://"):
                local = cache_smb_file(settings, path)
            else:
                local = resolve_safe_path(settings, path)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not local.exists():
            raise HTTPException(status_code=404, detail="bestand niet gevonden")
        media = "application/octet-stream" if download else None
        return FileResponse(local, filename=local.name if download else None, media_type=media)

    @api.get("/api/file-text")
    def api_file_text(
        path: str,
        max_chars: int = 8000,
        _: None = Depends(require),
    ) -> dict[str, Any]:
        try:
            if path.startswith("smb://"):
                local = cache_smb_file(settings, path)
            else:
                local = resolve_safe_path(settings, path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        text = extract_text(local, ocr_enabled=False)
        return {
            "path": path,
            "text": text[: max(200, min(max_chars, 50_000))],
            "truncated": len(text) > max_chars,
        }

    @api.get("/", response_class=HTMLResponse)
    def index_page() -> HTMLResponse:
        html_path = STATIC_DIR / "index.html"
        if html_path.is_file():
            return HTMLResponse(html_path.read_text(encoding="utf-8"))
        return HTMLResponse("<!doctype html><title>KippieSearch</title><h1>KippieSearch</h1>")

    if STATIC_DIR.is_dir():
        api.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    api.mount("/mcp", mcp_app)

    @api.middleware("http")
    async def mcp_no_redirect(request: Request, call_next):
        # Starlette Mount("/mcp") 307's POST /mcp → /mcp/, wat MCP-clients breekt.
        if request.scope.get("path") == "/mcp":
            request.scope["path"] = "/mcp/"
            request.scope["raw_path"] = b"/mcp/"
        _ = is_ingress(request)
        return await call_next(request)

    return api


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        access_log=False,
    )


if __name__ == "__main__":
    run()
