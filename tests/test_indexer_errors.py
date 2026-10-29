from pathlib import Path

from kippiesearch.embeddings import HashEmbedder
from kippiesearch.indexer import Indexer
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore


class _BoomThenHash(HashEmbedder):
    def __init__(self, dim: int) -> None:
        super().__init__(dim=dim)
        self.calls = 0

    def embed_documents(self, texts, titles=None):
        self.calls += 1
        if self.calls == 1:
            raise ValueError(
                "Required inputs (['image_features']) are missing from input feed "
                "(['input_ids','attention_mask'])."
            )
        return super().embed_documents(texts, titles)


def test_failed_flush_does_not_poison_later_files_with_text_keyerror(tmp_settings):
    tmp_settings.batch_size = 2
    share = Path(tmp_settings.nas_roots[0])
    (share / "a.txt").write_text("eerste document over katten", encoding="utf-8")
    (share / "b.txt").write_text("tweede document over honden", encoding="utf-8")
    (share / "c.txt").write_text("derde document over vogels", encoding="utf-8")
    store = VectorStore(tmp_settings.db_dir, dim=tmp_settings.embedding_dim)
    embedder = _BoomThenHash(dim=tmp_settings.embedding_dim)
    indexer = Indexer(tmp_settings, store, embedder, AppStatus())
    result = indexer.run(full=True)
    assert result["errors"] >= 1
    messages = [item["error"] for item in indexer.status.snapshot()["errors"]]
    joined = " ".join(messages)
    assert "KeyError" not in joined
    assert "'_text'" not in joined
    assert "ValueError" in joined
    paths = store.all_paths()
    assert any(p.endswith("c.txt") for p in paths)


def test_indexes_txt_json_js_xml(tmp_settings):
    share = Path(tmp_settings.nas_roots[0])
    (share / "note.txt").write_text("De rode planeet Mars heeft twee manen.", encoding="utf-8")
    (share / "meta.json").write_text('{"onderwerp": "pasta carbonara"}', encoding="utf-8")
    (share / "util.js").write_text("function greet() { return 'hallo'; }\n", encoding="utf-8")
    (share / "data.xml").write_text("<doc><title>valversnelling</title></doc>\n", encoding="utf-8")
    store = VectorStore(tmp_settings.db_dir, dim=tmp_settings.embedding_dim)
    embedder = HashEmbedder(dim=tmp_settings.embedding_dim)
    result = Indexer(tmp_settings, store, embedder, AppStatus()).run(full=True)
    assert result["errors"] == 0
    assert result["indexed"] == 4
    stats = store.stats()
    assert stats["files"] == 4
    assert stats["chunks"] >= 4
