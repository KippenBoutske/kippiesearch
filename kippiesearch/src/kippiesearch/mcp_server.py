"""Streamable HTTP MCP-server, gemount op /mcp via de officiële Python MCP SDK (v2)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from kippiesearch.chat import ChatEngine
from kippiesearch.config import Settings
from kippiesearch.extract import extract_text
from kippiesearch.logging_setup import get_logger
from kippiesearch.nas import cache_smb_file
from kippiesearch.search import SearchService
from kippiesearch.sources import resolve_safe_path
from kippiesearch.store import VectorStore

log = get_logger("mcp")


def build_mcp(
    settings: Settings,
    store: VectorStore,
    searcher: SearchService,
    chat: ChatEngine | None = None,
) -> MCPServer:
    mcp = MCPServer(
        "KippieSearch",
        instructions=(
            "Lokale semantic search en RAG-chat over NAS- en schoolbestanden. "
            "Gebruik search voor treffers, ask voor een antwoord met bronnen, "
            "list_sources voor overzicht, get_file_content voor de tekst van "
            "een geïndexeerd pad."
        ),
        version="0.2.0",
    )

    @mcp.tool()
    def search(
        query: str,
        top_k: int = 8,
        source: str | None = None,
        file_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Zoek semantisch in geïndexeerde NAS- en it's learning-bestanden."""
        hits = searcher.search(query, top_k=top_k, source=source, file_type=file_type)
        return [
            {
                "path": hit["path"],
                "snippet": hit.get("text") or hit.get("snippet"),
                "score": hit["score"],
                "source": hit["source"],
                "mtime": hit["mtime"],
            }
            for hit in hits
        ]

    @mcp.tool()
    def ask(
        question: str,
        top_k: int = 6,
        source: str | None = None,
        file_type: str | None = None,
    ) -> dict[str, Any]:
        """Beantwoord een vraag met RAG: retrieval + lokaal chatmodel, plus bronnen."""
        if chat is None:
            return {"error": "chat niet beschikbaar", "answer": "", "sources": []}
        result = chat.ask(
            question, top_k=top_k, source=source, file_type=file_type
        )
        return {
            "answer": result.get("answer") or "",
            "sources": [
                {
                    "n": src.get("n"),
                    "path": src.get("path"),
                    "filename": src.get("filename"),
                    "source": src.get("source"),
                    "snippet": src.get("snippet"),
                    "score": src.get("score"),
                }
                for src in (result.get("sources") or [])
            ],
            "error": result.get("error") or "",
        }

    @mcp.tool()
    def list_sources() -> dict[str, Any]:
        """Toon bronnen, aantal bestanden/chunks en laatste sync/index."""
        stats = store.stats()
        return {
            "sources": stats.get("per_source") or {},
            "files": stats.get("files"),
            "chunks": stats.get("chunks"),
            "last_index": stats.get("last_index"),
            "last_nas_scan": stats.get("last_nas_scan"),
            "last_itslearning_sync": stats.get("last_itslearning_sync"),
            "nas_roots": settings.nas_roots,
            "smb_enabled": settings.nas_smb_enabled,
            "itslearning_enabled": settings.itslearning_enabled,
            "chat_enabled": settings.chat_enabled,
            "chat_model": settings.chat_model,
        }

    @mcp.tool()
    def get_file_content(path: str, max_chars: int = 8000) -> dict[str, Any]:
        """Haal platte tekst op van een geïndexeerd bestand (geen path-traversal)."""
        max_chars = max(200, min(int(max_chars), 50_000))
        indexed = store.get_file(path)
        if indexed is None:
            raise ValueError("bestand is niet geïndexeerd")
        if path.startswith("smb://"):
            local = cache_smb_file(settings, path)
            text = extract_text(local, ocr_enabled=False)
        else:
            local = resolve_safe_path(settings, path)
            text = extract_text(local, ocr_enabled=False)
        truncated = text[:max_chars]
        return {
            "path": path,
            "chars": len(truncated),
            "truncated": len(text) > max_chars,
            "text": truncated,
        }

    return mcp


def mcp_asgi_app(mcp: MCPServer):
    """ASGI-app met MCP-endpoint op / (te mounten op /mcp).

    DNS-rebinding-protectie uit: Grok Bot komt via LAN-IP of Tailscale-hostname,
    niet via localhost. Auth zit op Bearer-token van de FastAPI-middleware.
    """
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        host="0.0.0.0",
        transport_security=security,
    )
