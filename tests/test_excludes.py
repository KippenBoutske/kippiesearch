from pathlib import Path

from kippiesearch.indexer import Indexer
from kippiesearch.embeddings import HashEmbedder
from kippiesearch.sources import iter_local_files, path_is_excluded
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore


def test_builtin_skips_plugins_cache_and_minified_js():
    assert path_is_excluded("jellyfin/data/plugins/Foo/frontend/app.js")
    assert path_is_excluded("speech-to-phrase/train/en/words.txt")
    assert path_is_excluded("lib/cache/omdb/tt.json")
    assert path_is_excluded("assets/app.min.js")
    assert path_is_excluded("vendor/lib.umd.js")
    assert path_is_excluded("theme/main.dart.js")
    assert path_is_excluded("bundle/app.bundle.js")
    assert path_is_excluded("src/app.js.map")
    assert not path_is_excluded("notes/planeten.md")
    assert not path_is_excluded("code/hello.py")
    assert not path_is_excluded("docs/handleiding.json")


def test_indexer_skips_appdata_under_nas_root(tmp_settings):
    share = Path(tmp_settings.nas_roots[0])
    (share / "notes").mkdir()
    (share / "notes" / "keep.txt").write_text("bewaar dit recept", encoding="utf-8")
    plugins = share / "jellyfin" / "data" / "plugins" / "Foo"
    plugins.mkdir(parents=True)
    (plugins / "app.js").write_text("console.log('skip')", encoding="utf-8")
    cache = share / "cache" / "omdb"
    cache.mkdir(parents=True)
    (cache / "tt.json").write_text('{"Title":"skip"}', encoding="utf-8")
    (share / "theme.min.js").write_text("/*minified*/", encoding="utf-8")

    paths = [ref.path for ref in iter_local_files(tmp_settings)]
    assert any(p.endswith("keep.txt") for p in paths)
    assert not any("plugins" in p for p in paths)
    assert not any("/cache/" in p or p.endswith("tt.json") for p in paths)
    assert not any(p.endswith(".min.js") for p in paths)

    store = VectorStore(tmp_settings.db_dir, dim=tmp_settings.embedding_dim)
    embedder = HashEmbedder(dim=tmp_settings.embedding_dim)
    result = Indexer(tmp_settings, store, embedder, AppStatus()).run(full=True)
    assert result["errors"] == 0
    assert store.stats()["files"] == 1
