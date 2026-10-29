from pathlib import Path

from fastapi.testclient import TestClient

from kippiesearch.embeddings import HashEmbedder
from kippiesearch.indexer import Indexer
from kippiesearch.main import create_app
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore


def test_health_and_search_api(tmp_settings):
    share = Path(tmp_settings.nas_roots[0])
    (share / "note.md").write_text("De rode planeet Mars heeft twee manen.", encoding="utf-8")
    app = create_app(tmp_settings)
    store = VectorStore(tmp_settings.db_dir, dim=tmp_settings.embedding_dim)
    embedder = HashEmbedder(dim=tmp_settings.embedding_dim)
    Indexer(tmp_settings, store, embedder, AppStatus()).run(full=True)
    # recreate app so it picks up the same data dir / store
    app = create_app(tmp_settings)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        status = client.get("/api/status")
        assert status.status_code == 200
        sources = client.get("/api/sources")
        assert sources.status_code == 200
        found = client.get("/api/search", params={"q": "rode planeet Mars"})
        assert found.status_code == 200
        body = found.json()
        assert body["count"] >= 1
        assert "Mars" in body["results"][0]["text"] or "mars" in body["results"][0]["path"].lower()


def test_auth_required(tmp_settings):
    tmp_settings.api_token = "supergeheim"
    tmp_settings.auth_disabled = False
    app = create_app(tmp_settings)
    with TestClient(app) as client:
        denied = client.get("/api/status")
        assert denied.status_code == 401
        ok = client.get("/api/status", headers={"Authorization": "Bearer supergeheim"})
        assert ok.status_code == 200
        ingress = client.get("/api/status", headers={"X-Ingress-Path": "/api/hassio_ingress/xxx"})
        assert ingress.status_code == 200


def test_mcp_initialize_without_redirect(tmp_settings):
    app = create_app(tmp_settings)
    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["result"]["serverInfo"]["name"] == "KippieSearch"


def test_path_traversal_blocked(tmp_settings, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("niet hier", encoding="utf-8")
    app = create_app(tmp_settings)
    with TestClient(app) as client:
        res = client.get("/api/file", params={"path": str(secret)})
        assert res.status_code in {400, 403, 404}
        res2 = client.get("/api/file", params={"path": "../../../etc/passwd"})
        assert res2.status_code in {400, 403, 404}
