from pathlib import Path

from kippiesearch.embeddings import HashEmbedder
from kippiesearch.indexer import Indexer
from kippiesearch.search import SearchService
from kippiesearch.sources import FileRef
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore


def test_incremental_skips_unchanged_and_updates_modified(tmp_settings):
    share = Path(tmp_settings.nas_roots[0])
    file_a = share / "a.txt"
    file_a.write_text("alpha document over katten", encoding="utf-8")
    store = VectorStore(tmp_settings.db_dir, dim=tmp_settings.embedding_dim)
    embedder = HashEmbedder(dim=tmp_settings.embedding_dim)
    indexer = Indexer(tmp_settings, store, embedder, AppStatus())

    first = indexer.run(full=True)
    assert first["indexed"] == 1
    assert store.stats()["files"] == 1

    second = indexer.run(full=False)
    assert second["indexed"] == 0
    assert second["skipped"] == 1

    file_a.write_text("alpha document over katten en honden", encoding="utf-8")
    third = indexer.run(full=False)
    assert third["indexed"] == 1

    file_a.unlink()
    fourth = indexer.run(full=False)
    assert fourth["deleted"] == 1
    assert store.stats()["files"] == 0


def test_search_ranks_related_document(indexed_app):
    settings, store, embedder, _, _ = indexed_app
    searcher = SearchService(store, embedder)
    hits = searcher.search("rode planeet Mars vulkaan")
    assert hits
    top = hits[0]["path"]
    assert "planeten.md" in top or "opdracht.md" in top
    pasta = searcher.search("pasta carbonara recept")
    assert pasta
    assert "recept.txt" in pasta[0]["path"]
    school = searcher.search("valversnelling", source="school")
    assert school
    assert all(h["source"] == "school" for h in school)
